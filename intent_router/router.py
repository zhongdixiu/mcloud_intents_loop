from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_client import AgentScopeStructuredClient, StructuredModelClient
from .prompts import (
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    PARAM_REPAIR_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_evaluator_prompt,
    build_intent_prompt,
    build_param_repair_prompt,
    build_router_prompt,
)
from .skills import SkillRegistry
from .types import (
    DialogueHistory,
    EvaluationDecision,
    IntentDecision,
    RouteResult,
    SkillRef,
    SkillRouteDecision,
)
from .validation import (
    ValidationError,
    validate_evaluation_decision,
    validate_intent_decision,
)


class IntentRouter:
    def __init__(
        self,
        registry: SkillRegistry,
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
    ) -> None:
        self.registry = registry
        self.model_client = model_client or AgentScopeStructuredClient.from_env()
        self.evaluator_client = (
            evaluator_client
            or (
                AgentScopeStructuredClient.evaluator_from_env_if_configured()
                if model_client is None
                else None
            )
            or self.model_client
        )
        self.max_attempts = max_attempts

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            evaluator_client=evaluator_client,
            max_attempts=max_attempts,
        )

    async def route(
        self,
        query: str,
        *,
        dialogue_history: DialogueHistory | dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        trace = [] if trace is None else trace
        state = _new_state(query)
        history = _normalize_dialogue_history(dialogue_history)
        _trace(
            trace,
            "route_start",
            query=query,
            dialogue_turns=len(history.turns),
        )
        rejected: list[str] = state.setdefault("rejected_skills", [])
        rejected_intents: dict[str, list[dict[str, str]]] = state.setdefault(
            "rejected_intents",
            {},
        )
        param_rejections: dict[str, list[dict[str, str]]] = state.setdefault(
            "param_rejections",
            {},
        )
        visited: list[str] = state.setdefault("visited_skills", [])
        context = context or {}

        for _ in range(self.max_attempts):
            attempt = _ + 1
            retry_scope = state.get("retry_scope") or "reroute_skill"
            locked_skill_id = state.get("locked_skill_id")
            _trace(
                trace,
                "attempt_start",
                attempt=attempt,
                retry_scope=retry_scope,
                locked_skill_id=locked_skill_id,
            )

            if retry_scope in {"retry_intent", "retry_params"} and locked_skill_id:
                skill_id = locked_skill_id
                route_confidence = float(state.get("locked_skill_confidence") or 1.0)
                if not self.registry.has(skill_id) or skill_id in rejected:
                    return _traced_result(
                        trace,
                        RouteResult(
                            status="no_match",
                            reason="Locked skill is not usable",
                            visited_skills=visited,
                        ),
                    )
                _trace(
                    trace,
                    "locked_skill",
                    attempt=attempt,
                    skill_id=skill_id,
                    route_confidence=route_confidence,
                    retry_scope=retry_scope,
                )
            else:
                route_decision = await self._select_skills(
                    query,
                    rejected,
                    context,
                    history,
                )
                _trace(
                    trace,
                    "skill_route",
                    attempt=attempt,
                    decision=route_decision.model_dump(mode="json"),
                    rejected_skill_ids=list(rejected),
                )
                if route_decision.status == "clarify":
                    return _traced_result(
                        trace,
                        self._clarify(
                            route_decision.question,
                            route_decision.options,
                            state,
                        ),
                    )
                if route_decision.status == "no_match":
                    return _traced_result(
                        trace,
                        RouteResult(
                            status="no_match",
                            reason=route_decision.reason or "No matching skill",
                            visited_skills=visited,
                        ),
                    )

                skill_id = route_decision.skill_id
                route_confidence = route_decision.confidence
                if not skill_id or not self.registry.has(skill_id) or skill_id in rejected:
                    return _traced_result(
                        trace,
                        RouteResult(
                            status="no_match",
                            reason="Router did not return a usable skill",
                            visited_skills=visited,
                        ),
                    )
                state["locked_skill_id"] = skill_id
                state["locked_skill_confidence"] = route_confidence

            skill = self.registry.get(skill_id)
            if skill_id not in visited:
                visited.append(skill_id)

            locked_intent = state.get("locked_intent")
            locked_code = state.get("locked_code")
            if retry_scope == "retry_params" and locked_intent and locked_code:
                intent_decision = await self._repair_params(
                    query,
                    skill_id,
                    locked_intent,
                    locked_code,
                    param_rejections.get(_param_rejection_key(skill_id, locked_intent), []),
                    history,
                )
                _trace(
                    trace,
                    "param_repair",
                    attempt=attempt,
                    skill_id=skill_id,
                    locked_intent=locked_intent,
                    locked_code=locked_code,
                    decision=intent_decision.model_dump(mode="json"),
                )
            else:
                intent_decision = await self._select_intent(
                    query,
                    skill_id,
                    rejected_intents.get(skill_id, []),
                    _skill_param_rejections(param_rejections, skill_id),
                    history,
                )
                _trace(
                    trace,
                    "intent_select",
                    attempt=attempt,
                    skill_id=skill_id,
                    decision=intent_decision.model_dump(mode="json"),
                    rejected_intents=rejected_intents.get(skill_id, []),
                )
            if intent_decision.status == "clarify":
                return _traced_result(
                    trace,
                    self._clarify(intent_decision.question, intent_decision.options, state),
                )
            if intent_decision.status == "no_match":
                _trace(
                    trace,
                    "intent_no_match",
                    attempt=attempt,
                    skill_id=skill_id,
                    retry_scope=retry_scope,
                    reason=intent_decision.reason,
                )
                if retry_scope == "reroute_skill" and skill_id not in rejected:
                    rejected.append(skill_id)
                    state["retry_scope"] = "reroute_skill"
                    state["locked_skill_id"] = None
                    state["locked_skill_confidence"] = None
                    _trace(
                        trace,
                        "retry",
                        attempt=attempt,
                        scope="skill_no_match",
                        next_retry_scope="reroute_skill",
                        rejected_skill_ids=list(rejected),
                    )
                    continue
                return _traced_result(
                    trace,
                    RouteResult(
                        status="no_match",
                        reason=intent_decision.reason or "No matching intent",
                        visited_skills=visited,
                    ),
                )

            if retry_scope == "retry_params":
                try:
                    _validate_locked_param_repair(intent_decision, locked_intent, locked_code)
                except ValidationError as exc:
                    state.setdefault("rejections", []).append(
                        {
                            "scope": "invalid_param_repair",
                            "skill_id": skill_id,
                            "intent": intent_decision.intent or "",
                            "reason": str(exc),
                        },
                    )
                    state["retry_scope"] = "retry_params"
                    _trace(
                        trace,
                        "invalid_param_repair",
                        attempt=attempt,
                        skill_id=skill_id,
                        intent=intent_decision.intent,
                        reason=str(exc),
                    )
                    continue

            try:
                validated = validate_intent_decision(skill, intent_decision)
            except ValidationError as exc:
                if _looks_like_param_error(skill, intent_decision):
                    state["locked_skill_id"] = skill_id
                    state["locked_skill_confidence"] = route_confidence
                    state["locked_intent"] = intent_decision.intent
                    state["locked_code"] = intent_decision.code
                    state["retry_scope"] = "retry_params"
                    _record_param_rejection(state, skill_id, intent_decision.intent, str(exc))
                    correction_scope = "param_mismatch"
                else:
                    state["locked_skill_id"] = skill_id
                    state["locked_skill_confidence"] = route_confidence
                    state["locked_intent"] = None
                    state["locked_code"] = None
                    state["retry_scope"] = "retry_intent"
                    _record_intent_rejection(
                        state,
                        skill_id,
                        intent_decision.intent,
                        str(exc),
                    )
                    correction_scope = "intent_mismatch"
                _trace(
                    trace,
                    "validation_error",
                    attempt=attempt,
                    skill_id=skill_id,
                    intent=intent_decision.intent,
                    code=intent_decision.code,
                    reason=str(exc),
                    correction_scope=correction_scope,
                    next_retry_scope=state.get("retry_scope"),
                )
                continue

            raw_evaluation = await self._evaluate(
                query,
                skill_id,
                validated,
                state,
                history,
            )
            _trace(
                trace,
                "evaluation",
                attempt=attempt,
                skill_id=skill_id,
                candidate=validated.model_dump(mode="json"),
                evaluation=raw_evaluation.model_dump(mode="json"),
            )
            try:
                evaluation = validate_evaluation_decision(raw_evaluation)
            except ValidationError as exc:
                result = self._invalid_evaluation_result(
                    state,
                    skill_id,
                    validated.intent,
                    str(exc),
                )
                _trace(
                    trace,
                    "evaluation_invalid",
                    attempt=attempt,
                    skill_id=skill_id,
                    intent=validated.intent,
                    reason=str(exc),
                    next_retry_scope=state.get("retry_scope"),
                )
                if result is not None:
                    return _traced_result(trace, result)
                continue

            result = self._apply_evaluation_result(
                evaluation,
                skill_id=skill_id,
                skill_name=skill.name,
                validated=validated,
                route_confidence=route_confidence,
                state=state,
                trace=trace,
                attempt=attempt,
            )
            if result is not None:
                return _traced_result(trace, result)
            continue

        return _traced_result(
            trace,
            RouteResult(
                status="no_match",
                reason="Exceeded maximum routing attempts",
                visited_skills=visited,
            ),
        )

    def _apply_evaluation_result(
        self,
        evaluation: EvaluationDecision,
        *,
        skill_id: str,
        skill_name: str,
        validated: IntentDecision,
        route_confidence: float,
        state: dict[str, Any],
        trace: list[dict[str, Any]] | None = None,
        attempt: int | None = None,
    ) -> RouteResult | None:
        if evaluation.verdict == "clarify":
            return self._clarify(
                evaluation.question or evaluation.clarity_reason,
                evaluation.options,
                state,
            )

        if evaluation.verdict == "accept":
            return RouteResult(
                status="matched",
                skill=SkillRef(id=skill_id, name=skill_name),
                intent=validated.intent,
                code=validated.code,
                params=validated.params,
                confidence=min(route_confidence, validated.confidence),
                visited_skills=state.get("visited_skills", []),
            )

        if not evaluation.reject_scope:
            return self._invalid_evaluation_result(
                state,
                skill_id,
                validated.intent,
                "rejected evaluation misses reject_scope",
            )

        _apply_retry_scope(
            state,
            evaluation.reject_scope,
            skill_id=skill_id,
            route_confidence=route_confidence,
            intent=validated.intent,
            code=validated.code,
        )
        _trace(
            trace,
            "retry",
            attempt=attempt,
            scope=evaluation.reject_scope,
            skill_id=skill_id,
            intent=validated.intent,
            code=validated.code,
            reason=evaluation.reason,
            next_retry_scope=state.get("retry_scope"),
        )
        if evaluation.reject_scope == "intent_mismatch":
            _record_intent_rejection(
                state,
                skill_id,
                validated.intent,
                evaluation.reason,
                scope="intent_mismatch",
            )
            return None

        if evaluation.reject_scope == "param_mismatch":
            _record_param_rejection(
                state,
                skill_id,
                validated.intent,
                evaluation.reason,
            )
            return None

        state.setdefault("rejections", []).append(
            {
                "scope": "skill_mismatch",
                "skill_id": skill_id,
                "reason": evaluation.reason,
            },
        )
        rejected: list[str] = state.setdefault("rejected_skills", [])
        if skill_id not in rejected:
            rejected.append(skill_id)
        return None

    def _invalid_evaluation_result(
        self,
        state: dict[str, Any],
        skill_id: str,
        intent: str | None,
        reason: str,
    ) -> RouteResult | None:
        state.setdefault("rejections", []).append(
            {
                "scope": "invalid_evaluation",
                "skill_id": skill_id,
                "intent": intent or "",
                "reason": reason,
            },
        )
        if not state.get("invalid_evaluation_retried"):
            state["invalid_evaluation_retried"] = True
            state["retry_scope"] = "reroute_skill"
            state["locked_skill_id"] = None
            state["locked_skill_confidence"] = None
            state["locked_intent"] = None
            state["locked_code"] = None
            return None
        return RouteResult(
            status="no_match",
            reason="Evaluator produced inconsistent decision",
            visited_skills=state.get("visited_skills", []),
        )

    async def _select_skills(
        self,
        query: str,
        rejected: list[str],
        context: dict[str, Any],
        dialogue_history: DialogueHistory,
    ) -> SkillRouteDecision:
        prompt = build_router_prompt(
            query,
            self.registry.cards(),
            rejected,
            dialogue_history,
        )
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
        param_rejections: list[dict[str, str]] | None = None,
        dialogue_history: DialogueHistory | None = None,
    ) -> IntentDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=build_intent_prompt(
                query,
                skill,
                rejected_intents,
                param_rejections,
                dialogue_history,
            ),
            response_model=IntentDecision,
        )

    async def _repair_params(
        self,
        query: str,
        skill_id: str,
        locked_intent: str,
        locked_code: str,
        param_rejections: list[dict[str, str]],
        dialogue_history: DialogueHistory | None = None,
    ) -> IntentDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=PARAM_REPAIR_SYSTEM_PROMPT,
            user_prompt=build_param_repair_prompt(
                query,
                skill,
                locked_intent,
                locked_code,
                param_rejections,
                dialogue_history,
            ),
            response_model=IntentDecision,
        )

    async def _evaluate(
        self,
        query: str,
        skill_id: str,
        decision: IntentDecision,
        state: dict[str, Any],
        dialogue_history: DialogueHistory,
    ) -> EvaluationDecision:
        skill = self.registry.get(skill_id)
        return await self.evaluator_client.structured(
            system_prompt=EVALUATOR_SYSTEM_PROMPT,
            user_prompt=build_evaluator_prompt(
                query,
                self.registry.cards(),
                skill,
                decision.model_dump(),
                rejected_skill_ids=state.get("rejected_skills", []),
                rejected_intents=state.get("rejected_intents", {}),
                visited_skills=state.get("visited_skills", []),
                dialogue_history=dialogue_history,
            ),
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
            visited_skills=state.get("visited_skills", []),
        )


def _new_state(query: str) -> dict[str, Any]:
    return {
        "original_query": query,
        "visited_skills": [],
        "rejected_skills": [],
        "rejected_intents": {},
        "param_rejections": {},
        "locked_skill_id": None,
        "locked_skill_confidence": None,
        "locked_intent": None,
        "locked_code": None,
        "retry_scope": "reroute_skill",
        "rejections": [],
    }


def _normalize_dialogue_history(
    dialogue_history: DialogueHistory | dict[str, Any] | None,
) -> DialogueHistory:
    if dialogue_history is None:
        return DialogueHistory()
    if isinstance(dialogue_history, DialogueHistory):
        return dialogue_history
    return DialogueHistory.model_validate(dialogue_history)


def _trace(
    trace: list[dict[str, Any]] | None,
    event: str,
    **payload: Any,
) -> None:
    if trace is None:
        return
    trace.append({"event": event, **payload})


def _traced_result(
    trace: list[dict[str, Any]] | None,
    result: RouteResult,
) -> RouteResult:
    _attach_loop_stats(trace, result)
    _trace(trace, "result", result=result.model_dump(mode="json"))
    return result


def _attach_loop_stats(
    trace: list[dict[str, Any]] | None,
    result: RouteResult,
) -> None:
    if trace is None:
        return
    attempts = [
        int(event.get("attempt") or 0)
        for event in trace
        if event.get("event") == "attempt_start"
    ]
    result.loop_count = max(attempts, default=1)
    result.correction_scopes = _correction_scopes(trace)


def _correction_scopes(trace: list[dict[str, Any]]) -> list[str]:
    scopes: list[str] = []
    for event in trace:
        scope = None
        if event.get("event") == "retry":
            scope = event.get("scope")
        elif event.get("event") == "validation_error":
            scope = event.get("correction_scope")
        elif event.get("event") == "evaluation_invalid":
            scope = "invalid_evaluation"
        elif event.get("event") == "invalid_param_repair":
            scope = "invalid_param_repair"
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def _apply_retry_scope(
    state: dict[str, Any],
    scope: str,
    *,
    skill_id: str,
    route_confidence: float,
    intent: str | None,
    code: str | None,
) -> None:
    if scope == "skill_mismatch":
        state["locked_skill_id"] = None
        state["locked_skill_confidence"] = None
        state["locked_intent"] = None
        state["locked_code"] = None
        state["retry_scope"] = "reroute_skill"
        return

    if scope == "intent_mismatch":
        state["locked_skill_id"] = skill_id
        state["locked_skill_confidence"] = route_confidence
        state["locked_intent"] = None
        state["locked_code"] = None
        state["retry_scope"] = "retry_intent"
        return

    if scope == "param_mismatch":
        state["locked_skill_id"] = skill_id
        state["locked_skill_confidence"] = route_confidence
        state["locked_intent"] = intent
        state["locked_code"] = code
        state["retry_scope"] = "retry_params"
        return

    raise ValueError(f"unsupported retry scope: {scope}")


def _record_intent_rejection(
    state: dict[str, Any],
    skill_id: str,
    intent: str | None,
    reason: str,
    *,
    scope: str = "intent_mismatch",
) -> None:
    rejection = {
        "scope": scope,
        "skill_id": skill_id,
        "intent": intent or "",
        "reason": reason,
    }
    state.setdefault("rejections", []).append(rejection)
    state.setdefault("rejected_intents", {}).setdefault(skill_id, []).append(
        {
            "intent": intent or "",
            "reason": reason,
            "scope": scope,
        },
    )


def _record_param_rejection(
    state: dict[str, Any],
    skill_id: str,
    intent: str | None,
    reason: str,
) -> None:
    rejection = {
        "scope": "param_mismatch",
        "skill_id": skill_id,
        "intent": intent or "",
        "reason": reason,
    }
    state.setdefault("rejections", []).append(rejection)
    key = _param_rejection_key(skill_id, intent or "")
    state.setdefault("param_rejections", {}).setdefault(key, []).append(
        {
            "intent": intent or "",
            "reason": reason,
            "scope": "param_mismatch",
        },
    )


def _param_rejection_key(skill_id: str, intent: str) -> str:
    return f"{skill_id}:{intent}"


def _skill_param_rejections(
    param_rejections: dict[str, list[dict[str, str]]],
    skill_id: str,
) -> list[dict[str, str]]:
    prefix = f"{skill_id}:"
    merged: list[dict[str, str]] = []
    for key, rejections in param_rejections.items():
        if key.startswith(prefix):
            merged.extend(rejections)
    return merged


def _looks_like_param_error(
    skill: Any,
    decision: IntentDecision,
) -> bool:
    if decision.status != "matched" or not decision.intent:
        return False
    if decision.intent not in skill.intents:
        return False
    schema = skill.intents[decision.intent]
    return decision.code == schema.code


def _validate_locked_param_repair(
    decision: IntentDecision,
    locked_intent: str | None,
    locked_code: str | None,
) -> None:
    if decision.status != "matched":
        return
    if decision.intent != locked_intent:
        raise ValidationError(
            f"param repair must keep intent {locked_intent!r}, got {decision.intent!r}",
        )
    if decision.code != locked_code:
        raise ValidationError(
            f"param repair must keep code {locked_code!r}, got {decision.code!r}",
        )
