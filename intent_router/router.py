from __future__ import annotations

import base64
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
        top_k: int = 3,
    ) -> None:
        self.registry = registry
        self.model_client = model_client or AgentScopeStructuredClient.from_env()
        self.max_attempts = max_attempts
        self.top_k = top_k

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
        top_k: int = 3,
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            max_attempts=max_attempts,
            top_k=top_k,
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

        augmented_query = _augment_query(state)
        rejected: list[str] = state.setdefault("rejected_skills", [])
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

            candidates = [
                skill_id
                for skill_id in route_decision.candidate_skill_ids[: self.top_k]
                if self.registry.has(skill_id) and skill_id not in rejected
            ]
            if not candidates:
                return RouteResult(
                    status="no_match",
                    reason="Router did not return any usable skill candidates",
                    visited_skills=visited,
                )

            for skill_id in candidates:
                skill = self.registry.get(skill_id)
                if skill_id not in visited:
                    visited.append(skill_id)

                intent_decision = await self._select_intent(augmented_query, skill_id)
                if intent_decision.status == "clarify":
                    return self._clarify(intent_decision.question, intent_decision.options, state)
                if intent_decision.status == "no_match":
                    rejected.append(skill_id)
                    continue

                try:
                    validated = validate_intent_decision(skill, intent_decision)
                except ValidationError as exc:
                    state.setdefault("rejections", []).append(
                        {"skill_id": skill_id, "reason": str(exc)},
                    )
                    rejected.append(skill_id)
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

                state.setdefault("rejections", []).append(
                    {"skill_id": skill_id, "reason": evaluation.reason},
                )
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

    async def _select_intent(self, query: str, skill_id: str) -> IntentDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=build_intent_prompt(query, skill),
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
    raw = json.dumps(state, ensure_ascii=False).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_state(token: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(token.encode("ascii"))
    return json.loads(raw.decode("utf-8"))
