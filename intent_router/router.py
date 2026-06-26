from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .model_client import AgentScopeStructuredClient, StructuredModelClient
from .prompts import (
    CONTEXTUALIZER_SYSTEM_PROMPT,
    DIALOGUE_HISTORY_LIMIT,
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT,
    PARAM_REPAIR_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_contextualizer_prompt,
    build_evaluator_prompt,
    build_intent_prompt,
    build_loop_exhausted_clarifier_prompt,
    build_param_repair_prompt,
    build_router_prompt,
)
from .skills import SkillRegistry
from .types import (
    ContextualizedRequest,
    DialogueHistory,
    EvaluationDecision,
    IntentDecision,
    LoopExhaustedClarification,
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
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        intent_only: bool = False,
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
        self.dialogue_history_limit = dialogue_history_limit
        self.intent_only = intent_only

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        intent_only: bool = False,
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            evaluator_client=evaluator_client,
            max_attempts=max_attempts,
            dialogue_history_limit=dialogue_history_limit,
            intent_only=intent_only,
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
        contextualized_request = await self._contextualize(query, history)
        _trace(
            trace,
            "contextualize",
            current_user_query=query,
            status=contextualized_request.status,
            resolved_query=contextualized_request.resolved_query,
            relation_to_history=contextualized_request.relation_to_history,
            used_history_turns=list(contextualized_request.used_history_turns),
            reason=contextualized_request.reason,
        )
        state["resolved_query"] = contextualized_request.resolved_query or query
        state["context_relation"] = contextualized_request.relation_to_history
        if contextualized_request.status == "clarify":
            return _traced_result(
                trace,
                self._clarify(
                    contextualized_request.question,
                    contextualized_request.options,
                    state,
                ),
            )
        resolved_query = contextualized_request.resolved_query or query

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
                    resolved_query,
                    rejected,
                    context,
                    contextualized_request,
                    query,
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
                    if state.get("invalid_evaluation_retried"):
                        return _traced_result(
                            trace,
                            RouteResult(
                                status="no_match",
                                reason=route_decision.reason or "No matching skill",
                                visited_skills=visited,
                                resolved_query=state.get("resolved_query"),
                                context_relation=state.get("context_relation"),
                            ),
                        )
                    return _traced_result(
                        trace,
                        _fallback_dialogue_result(
                            state,
                            route_decision.reason or "No matching skill",
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
                            resolved_query=state.get("resolved_query"),
                            context_relation=state.get("context_relation"),
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
                    resolved_query,
                    skill_id,
                    locked_intent,
                    locked_code,
                    param_rejections.get(_param_rejection_key(skill_id, locked_intent), []),
                    contextualized_request,
                    query,
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
                    resolved_query,
                    skill_id,
                    rejected_intents.get(skill_id, []),
                    _skill_param_rejections(param_rejections, skill_id),
                    contextualized_request,
                    query,
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
                    _fallback_dialogue_result(
                        state,
                        intent_decision.reason or "No matching intent",
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
                resolved_query,
                skill_id,
                validated,
                state,
                contextualized_request,
                query,
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

        result = await self._clarify_loop_exhausted(
            current_user_query=query,
            resolved_query=resolved_query,
            contextualized_request=contextualized_request,
            state=state,
            trace=trace,
        )
        return _traced_result(trace, result)

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
                resolved_query=state.get("resolved_query"),
                context_relation=state.get("context_relation"),
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
        if self.intent_only and evaluation.reject_scope == "param_mismatch":
            _trace(
                trace,
                "intent_only_accept_param_mismatch",
                attempt=attempt,
                skill_id=skill_id,
                intent=validated.intent,
                code=validated.code,
                reason=evaluation.reason,
            )
            return RouteResult(
                status="matched",
                skill=SkillRef(id=skill_id, name=skill_name),
                intent=validated.intent,
                code=validated.code,
                params=validated.params,
                confidence=min(route_confidence, validated.confidence),
                visited_skills=state.get("visited_skills", []),
                resolved_query=state.get("resolved_query"),
                context_relation=state.get("context_relation"),
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
        resolved_query: str,
        rejected: list[str],
        context: dict[str, Any],
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
    ) -> SkillRouteDecision:
        prompt = build_router_prompt(
            resolved_query,
            self.registry.cards(),
            rejected,
            contextualized_request,
            current_user_query,
            intent_only=self.intent_only,
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
        resolved_query: str,
        skill_id: str,
        rejected_intents: list[dict[str, str]],
        param_rejections: list[dict[str, str]] | None = None,
        contextualized_request: ContextualizedRequest | None = None,
        current_user_query: str | None = None,
    ) -> IntentDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=build_intent_prompt(
                resolved_query,
                skill,
                rejected_intents,
                param_rejections,
                contextualized_request,
                current_user_query,
                intent_only=self.intent_only,
            ),
            response_model=IntentDecision,
        )

    async def _repair_params(
        self,
        resolved_query: str,
        skill_id: str,
        locked_intent: str,
        locked_code: str,
        param_rejections: list[dict[str, str]],
        contextualized_request: ContextualizedRequest | None = None,
        current_user_query: str | None = None,
    ) -> IntentDecision:
        skill = self.registry.get(skill_id)
        return await self.model_client.structured(
            system_prompt=PARAM_REPAIR_SYSTEM_PROMPT,
            user_prompt=build_param_repair_prompt(
                resolved_query,
                skill,
                locked_intent,
                locked_code,
                param_rejections,
                contextualized_request,
                current_user_query,
                intent_only=self.intent_only,
            ),
            response_model=IntentDecision,
        )

    async def _evaluate(
        self,
        resolved_query: str,
        skill_id: str,
        decision: IntentDecision,
        state: dict[str, Any],
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
    ) -> EvaluationDecision:
        skill = self.registry.get(skill_id)
        return await self.evaluator_client.structured(
            system_prompt=EVALUATOR_SYSTEM_PROMPT,
            user_prompt=build_evaluator_prompt(
                resolved_query,
                self.registry.cards(),
                skill,
                decision.model_dump(),
                contextualized_request=contextualized_request,
                current_user_query=current_user_query,
                intent_only=self.intent_only,
            ),
            response_model=EvaluationDecision,
        )

    async def _contextualize(
        self,
        query: str,
        dialogue_history: DialogueHistory,
    ) -> ContextualizedRequest:
        if not dialogue_history.turns:
            return ContextualizedRequest(
                status="resolved",
                resolved_query=query,
                relation_to_history="new_request",
            )
        return await self.model_client.structured(
            system_prompt=CONTEXTUALIZER_SYSTEM_PROMPT,
            user_prompt=build_contextualizer_prompt(
                query,
                dialogue_history,
                history_limit=self.dialogue_history_limit,
            ),
            response_model=ContextualizedRequest,
        )

    async def _clarify_loop_exhausted(
        self,
        *,
        current_user_query: str,
        resolved_query: str,
        contextualized_request: ContextualizedRequest,
        state: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> RouteResult:
        attempts = _loop_exhausted_attempts(trace, self.registry)
        clarification = await self.model_client.structured(
            system_prompt=LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT,
            user_prompt=build_loop_exhausted_clarifier_prompt(
                current_user_query=current_user_query,
                resolved_query=resolved_query,
                contextualized_request=contextualized_request,
                available_skills=self.registry.cards(),
                attempts=attempts,
            ),
            response_model=LoopExhaustedClarification,
        )
        _trace(
            trace,
            "loop_exhausted_clarify",
            attempts=attempts,
            clarification=clarification.model_dump(mode="json"),
        )
        return RouteResult(
            status="clarify",
            question=clarification.question,
            options=clarification.options,
            reason=clarification.reason,
            visited_skills=state.get("visited_skills", []),
            resolved_query=state.get("resolved_query"),
            context_relation=state.get("context_relation"),
            termination_reason="loop_exhausted",
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
            resolved_query=state.get("resolved_query"),
            context_relation=state.get("context_relation"),
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


def _fallback_dialogue_result(
    state: dict[str, Any],
    reason: str | None = None,
) -> RouteResult:
    return RouteResult(
        status="matched",
        skill=None,
        intent="普通对话",
        code="000",
        params={},
        confidence=0.0,
        reason=reason,
        visited_skills=state.get("visited_skills", []),
        resolved_query=state.get("resolved_query"),
        context_relation=state.get("context_relation"),
    )


def _loop_exhausted_attempts(
    trace: list[dict[str, Any]],
    registry: SkillRegistry,
) -> list[dict[str, Any]]:
    attempts: dict[int, dict[str, Any]] = {}
    for event in trace:
        attempt = int(event.get("attempt") or 0)
        if not attempt:
            continue
        item = attempts.setdefault(attempt, {"attempt": attempt})
        event_name = event.get("event")

        if event_name == "skill_route":
            decision = event.get("decision") or {}
            skill_id = decision.get("skill_id")
            if skill_id:
                item["skill_id"] = skill_id
                item["skill_name"] = (
                    registry.get(skill_id).name if registry.has(skill_id) else skill_id
                )
            item["skill_route_status"] = decision.get("status")
            if decision.get("reason"):
                item["skill_route_reason"] = decision.get("reason")

        elif event_name == "locked_skill":
            skill_id = event.get("skill_id")
            if skill_id:
                item["skill_id"] = skill_id
                item["skill_name"] = (
                    registry.get(skill_id).name if registry.has(skill_id) else skill_id
                )

        elif event_name in {"intent_select", "param_repair"}:
            decision = event.get("decision") or {}
            item["intent_status"] = decision.get("status")
            item["intent"] = decision.get("intent")
            item["code"] = decision.get("code")
            item["params"] = decision.get("params") or {}
            if decision.get("reason"):
                item["intent_reason"] = decision.get("reason")

        elif event_name == "evaluation":
            evaluation = event.get("evaluation") or {}
            item["evaluator_verdict"] = evaluation.get("verdict")
            item["reject_scope"] = evaluation.get("reject_scope")
            if evaluation.get("reason"):
                item["reject_reason"] = evaluation.get("reason")

        elif event_name == "validation_error":
            item["reject_scope"] = event.get("correction_scope")
            item["reject_reason"] = event.get("reason")

        elif event_name == "invalid_param_repair":
            item["reject_scope"] = "param_mismatch"
            item["reject_reason"] = event.get("reason")

        elif event_name == "evaluation_invalid":
            item["reject_scope"] = "invalid_evaluation"
            item["reject_reason"] = event.get("reason")

    return [attempts[key] for key in sorted(attempts)]


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
