from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_client import AgentScopeStructuredClient, StructuredModelClient
from .prompts import (
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_evaluator_prompt,
    build_intent_prompt,
    build_router_prompt,
)
from .skills import SkillRegistry
from .types import (
    EvaluationDecision,
    IntentDecision,
    RouteResult,
    SkillRef,
    SkillRouteDecision,
)
from .validation import ValidationError, validate_intent_decision


class IntentRouter:
    def __init__(
        self,
        registry: SkillRegistry,
        model_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.registry = registry
        self.model_client = model_client or AgentScopeStructuredClient.from_env()
        self.max_attempts = max_attempts

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            max_attempts=max_attempts,
        )

    async def route(
        self,
        query: str,
        *,
        resume_token: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> RouteResult:
        state = _decode_state(resume_token) if resume_token else _new_state(query)
        if resume_token:
            state.setdefault("clarifications", []).append(query)
            state["rejected_skills"] = []
            state["rejected_intents"] = {}

        augmented_query = _augment_query(state)
        rejected: list[str] = state.setdefault("rejected_skills", [])
        rejected_intents: dict[str, list[dict[str, str]]] = state.setdefault(
            "rejected_intents",
            {},
        )
        visited: list[str] = state.setdefault("visited_skills", [])
        context = context or {}

        for _ in range(self.max_attempts):
            route_decision = await self._select_skills(
                augmented_query,
                rejected,
                context,
            )
            if route_decision.status == "clarify":
                return self._clarify(route_decision.question, route_decision.options, state)
            if route_decision.status == "no_match":
                return RouteResult(
                    status="no_match",
                    reason=route_decision.reason or "No matching skill",
                    visited_skills=visited,
                )

            skill_id = route_decision.skill_id
            if not skill_id or not self.registry.has(skill_id) or skill_id in rejected:
                return RouteResult(
                    status="no_match",
                    reason="Router did not return a usable skill",
                    visited_skills=visited,
                )

            skill = self.registry.get(skill_id)
            if skill_id not in visited:
                visited.append(skill_id)

            intent_decision = await self._select_intent(
                augmented_query,
                skill_id,
                rejected_intents.get(skill_id, []),
            )
            if intent_decision.status == "clarify":
                return self._clarify(intent_decision.question, intent_decision.options, state)
            if intent_decision.status == "no_match":
                if skill_id not in rejected:
                    rejected.append(skill_id)
                continue

            try:
                validated = validate_intent_decision(skill, intent_decision)
            except ValidationError as exc:
                _record_intent_rejection(
                    state,
                    skill_id,
                    intent_decision.intent,
                    str(exc),
                )
                continue

            evaluation = await self._evaluate(augmented_query, skill_id, validated)
            if evaluation.verdict == "clarify":
                return self._clarify(evaluation.question, evaluation.options, state)
            if evaluation.verdict == "accept":
                return RouteResult(
                    status="matched",
                    skill=SkillRef(id=skill.id, name=skill.name),
                    intent=validated.intent,
                    code=validated.code,
                    params=validated.params,
                    confidence=min(
                        route_decision.confidence,
                        validated.confidence,
                        evaluation.confidence,
                    ),
                    visited_skills=visited,
                )

            if evaluation.reject_scope == "intent_mismatch":
                _record_intent_rejection(
                    state,
                    skill_id,
                    validated.intent,
                    evaluation.reason,
                )
                continue

            state.setdefault("rejections", []).append(
                {
                    "scope": "skill_mismatch",
                    "skill_id": skill_id,
                    "reason": evaluation.reason,
                },
            )
            if skill_id not in rejected:
                rejected.append(skill_id)

        return RouteResult(
            status="no_match",
            reason="Exceeded maximum routing attempts",
            visited_skills=visited,
        )

    async def _select_skills(
        self,
        query: str,
        rejected: list[str],
        context: dict[str, Any],
    ) -> SkillRouteDecision:
        prompt = build_router_prompt(query, self.registry.cards(), rejected)
        if context:
            prompt = json.dumps(
                {"routing_input": json.loads(prompt), "context": context},
                ensure_ascii=False,
            )
        return await self.model_client.structured(
            system_prompt=ROUTER_SYSTEM_PROMPT,
            user_prompt=prompt,
            response_model=SkillRouteDecision,
        )

    async def _select_intent(
        self,
        query: str,
        skill_id: str,
        rejected_intents: list[dict[str, str]],
    ) -> IntentDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=build_intent_prompt(query, skill, rejected_intents),
            response_model=IntentDecision,
        )

    async def _evaluate(
        self,
        query: str,
        skill_id: str,
        decision: IntentDecision,
    ) -> EvaluationDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=EVALUATOR_SYSTEM_PROMPT,
            user_prompt=build_evaluator_prompt(query, skill, decision.model_dump()),
            response_model=EvaluationDecision,
        )

    def _clarify(
        self,
        question: str | None,
        options: list[dict[str, str]],
        state: dict[str, Any],
    ) -> RouteResult:
        return RouteResult(
            status="clarify",
            question=question or "请补充更多信息，以便确定要使用的功能。",
            options=options,
            resume_token=_encode_state(state),
            visited_skills=state.get("visited_skills", []),
        )


def _new_state(query: str) -> dict[str, Any]:
    return {
        "original_query": query,
        "clarifications": [],
        "visited_skills": [],
        "rejected_skills": [],
        "rejected_intents": {},
        "rejections": [],
    }


def _augment_query(state: dict[str, Any]) -> str:
    query = state["original_query"]
    clarifications = state.get("clarifications") or []
    if not clarifications:
        return query
    joined = "\n".join(f"- {item}" for item in clarifications)
    return f"{query}\n用户澄清:\n{joined}"


def _encode_state(state: dict[str, Any]) -> str:
    return json.dumps(state, ensure_ascii=False, separators=(",", ":"))


def _decode_state(token: str) -> dict[str, Any]:
    try:
        state = json.loads(token)
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid resume token: expected JSON state") from exc
    if not isinstance(state, dict):
        raise ValueError("Invalid resume token: expected JSON state")
    return state


def _record_intent_rejection(
    state: dict[str, Any],
    skill_id: str,
    intent: str | None,
    reason: str,
) -> None:
    rejection = {
        "scope": "intent_mismatch",
        "skill_id": skill_id,
        "intent": intent or "",
        "reason": reason,
    }
    state.setdefault("rejections", []).append(rejection)
    state.setdefault("rejected_intents", {}).setdefault(skill_id, []).append(
        {
            "intent": intent or "",
            "reason": reason,
        },
    )
