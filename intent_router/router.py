from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from .model_client import AgentScopeStructuredClient, StructuredModelClient
from .prompts import (
    CONTEXTUALIZER_SYSTEM_PROMPT,
    DIALOGUE_HISTORY_LIMIT,
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_contextualizer_prompt,
    build_evaluator_prompt,
    build_intent_prompt,
    build_loop_exhausted_clarifier_prompt,
    build_router_prompt,
)
from .skills import SkillRegistry
from .types import (
    ContextualizedRequest,
    DialogueHistory,
    EvaluationDecision,
    IntentDecision,
    IntentDecisionNoClarify,
    LoopExhaustedClarification,
    RouteResult,
    SkillRef,
    SkillRouteDecision,
    SkillRouteDecisionNoClarify,
)
from .validation import (
    ValidationError,
    validate_evaluation_decision,
    validate_intent_decision,
)


ALLOWED_CLARIFY_SCOPES_BY_STAGE = {
    "router": {"route_boundary"},
    "intent": {"intent_code_boundary"},
    "evaluator": {"route_boundary", "intent_code_boundary"},
}


class IntentRouter:
    def __init__(
        self,
        registry: SkillRegistry,
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        mode: Literal["code_eval", "production"] = "production",
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
        self.mode = mode

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_attempts: int = 3,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        mode: Literal["code_eval", "production"] = "production",
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            evaluator_client=evaluator_client,
            max_attempts=max_attempts,
            dialogue_history_limit=dialogue_history_limit,
            mode=mode,
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

            if retry_scope == "retry_intent" and locked_skill_id:
                skill_id = locked_skill_id
                route_confidence = float(state.get("locked_skill_confidence") or 1.0)
                if not self.registry.has(skill_id) or (
                    self.mode == "production" and skill_id in rejected
                ):
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
                    if _should_suppress_clarify(
                        "router",
                        route_decision,
                        mode=self.mode,
                    ):
                        _trace(
                            trace,
                            "clarify_suppressed",
                            attempt=attempt,
                            stage="router",
                            clarify_scope=route_decision.clarify_scope,
                            reason=route_decision.reason,
                            question=route_decision.question,
                        )
                        route_decision = await self._select_skills_no_clarify(
                            resolved_query,
                            rejected,
                            context,
                            contextualized_request,
                            query,
                            suppressed=route_decision,
                        )
                        _trace(
                            trace,
                            "skill_route_no_clarify",
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
                    fallback = (
                        _best_candidate_result(state)
                        if self.mode == "code_eval"
                        else None
                    )
                    if fallback is not None:
                        _trace(
                            trace,
                            "route_no_match_fallback_best_candidate",
                            attempt=attempt,
                            reason=route_decision.reason,
                            candidate=fallback.model_dump(mode="json"),
                        )
                        return _traced_result(trace, fallback)
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
                if not skill_id or not self.registry.has(skill_id) or (
                    self.mode == "production" and skill_id in rejected
                ):
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

            intent_decision = await self._select_intent(
                resolved_query,
                skill_id,
                rejected_intents.get(skill_id, []),
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
                if _should_suppress_clarify(
                    "intent",
                    intent_decision,
                    mode=self.mode,
                ):
                    _trace(
                        trace,
                        "clarify_suppressed",
                        attempt=attempt,
                        stage="intent",
                        skill_id=skill_id,
                        clarify_scope=intent_decision.clarify_scope,
                        reason=intent_decision.reason,
                        question=intent_decision.question,
                    )
                    intent_decision = await self._select_intent_no_clarify(
                        resolved_query,
                        skill_id,
                        rejected_intents.get(skill_id, []),
                        contextualized_request,
                        query,
                        suppressed=intent_decision,
                    )
                    _trace(
                        trace,
                        "intent_select_no_clarify",
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
                if (
                    self.mode == "production"
                    and retry_scope == "reroute_skill"
                    and skill_id not in rejected
                ):
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

            try:
                validated = validate_intent_decision(skill, intent_decision)
            except ValidationError as exc:
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

            _record_candidate_attempt(
                state,
                skill_id=skill_id,
                skill_name=skill.name,
                route_confidence=route_confidence,
                decision=validated,
                attempt=attempt,
                source="intent_select",
            )

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
            if _should_suppress_clarify(
                "evaluator",
                raw_evaluation,
                mode=self.mode,
            ):
                _trace(
                    trace,
                    "clarify_suppressed",
                    attempt=attempt,
                    stage="evaluator",
                    skill_id=skill_id,
                    intent=validated.intent,
                    code=validated.code,
                    clarify_scope=raw_evaluation.clarify_scope,
                    reason=raw_evaluation.reason or raw_evaluation.clarity_reason,
                    question=raw_evaluation.question,
                )
                raw_evaluation = EvaluationDecision(
                    verdict="accept",
                    skill_check="pass",
                    intent_check="pass",
                    params_check="pass",
                    confidence=raw_evaluation.confidence,
                    reason=(
                        "suppressed non-boundary evaluator clarify: "
                        f"{raw_evaluation.reason or raw_evaluation.clarity_reason or ''}"
                    ),
                )
                _trace(
                    trace,
                    "evaluation_no_clarify_accept",
                    attempt=attempt,
                    skill_id=skill_id,
                    candidate=validated.model_dump(mode="json"),
                    evaluation=raw_evaluation.model_dump(mode="json"),
                )
            try:
                evaluation = validate_evaluation_decision(raw_evaluation)
            except ValidationError as exc:
                if self.mode == "code_eval":
                    fallback = _best_candidate_result(state)
                    if fallback is not None:
                        _trace(
                            trace,
                            "invalid_evaluation_accept_best_candidate",
                            attempt=attempt,
                            skill_id=skill_id,
                            intent=validated.intent,
                            reason=str(exc),
                            candidate=fallback.model_dump(mode="json"),
                        )
                        return _traced_result(trace, fallback)
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

        result = _best_candidate_result(state) if self.mode == "code_eval" else None
        if result is not None:
            _trace(
                trace,
                "loop_exhausted_best_candidate",
                candidate=result.model_dump(mode="json"),
            )
            result.termination_reason = "loop_exhausted_best_candidate"
        elif self.mode == "code_eval":
            result = _fallback_dialogue_result(
                state,
                "Loop exhausted without a usable candidate",
            )
            result.termination_reason = "loop_exhausted_fallback"
            _trace(trace, "loop_exhausted_fallback_dialogue")
        else:
            result = await self._clarify_loop_exhausted(
                current_user_query=query,
                resolved_query=resolved_query,
                contextualized_request=contextualized_request,
                state=state,
                trace=trace,
            )
        return _traced_result(trace, result)

    async def route_no_loop(
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
            mode="no_loop",
            query=query,
            dialogue_turns=len(history.turns),
        )
        context = context or {}
        contextualized_request = await self._contextualize(query, history)
        _trace(
            trace,
            "contextualize",
            mode="no_loop",
            current_user_query=query,
            status=contextualized_request.status,
            resolved_query=contextualized_request.resolved_query,
            relation_to_history=contextualized_request.relation_to_history,
            used_history_turns=list(contextualized_request.used_history_turns),
            reason=contextualized_request.reason,
        )
        state["resolved_query"] = contextualized_request.resolved_query or query
        state["context_relation"] = contextualized_request.relation_to_history

        resolved_query = contextualized_request.resolved_query or query
        _trace(
            trace,
            "attempt_start",
            mode="no_loop",
            attempt=1,
            retry_scope="no_loop",
            locked_skill_id=None,
        )

        route_decision = await self._select_skills(
            resolved_query,
            [],
            context,
            contextualized_request,
            query,
        )
        _trace(
            trace,
            "skill_route",
            mode="no_loop",
            attempt=1,
            decision=route_decision.model_dump(mode="json"),
            rejected_skill_ids=[],
        )
        if route_decision.status == "clarify":
            if _should_suppress_clarify(
                "router",
                route_decision,
                mode=self.mode,
            ):
                _trace(
                    trace,
                    "clarify_suppressed",
                    mode="no_loop",
                    attempt=1,
                    stage="router",
                    clarify_scope=route_decision.clarify_scope,
                    reason=route_decision.reason,
                    question=route_decision.question,
                )
                route_decision = await self._select_skills_no_clarify(
                    resolved_query,
                    [],
                    context,
                    contextualized_request,
                    query,
                    suppressed=route_decision,
                )
                _trace(
                    trace,
                    "skill_route_no_clarify",
                    mode="no_loop",
                    attempt=1,
                    decision=route_decision.model_dump(mode="json"),
                    rejected_skill_ids=[],
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
                _fallback_dialogue_result(
                    state,
                    route_decision.reason or "No matching skill",
                ),
            )

        skill_id = route_decision.skill_id
        route_confidence = route_decision.confidence
        visited: list[str] = state.setdefault("visited_skills", [])
        if not skill_id or not self.registry.has(skill_id):
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
        visited.append(skill_id)
        skill = self.registry.get(skill_id)

        intent_decision = await self._select_intent(
            resolved_query,
            skill_id,
            [],
            contextualized_request,
            query,
        )
        _trace(
            trace,
            "intent_select",
            mode="no_loop",
            attempt=1,
            skill_id=skill_id,
            decision=intent_decision.model_dump(mode="json"),
            rejected_intents=[],
        )
        if intent_decision.status == "clarify":
            if _should_suppress_clarify(
                "intent",
                intent_decision,
                mode=self.mode,
            ):
                _trace(
                    trace,
                    "clarify_suppressed",
                    mode="no_loop",
                    attempt=1,
                    stage="intent",
                    skill_id=skill_id,
                    clarify_scope=intent_decision.clarify_scope,
                    reason=intent_decision.reason,
                    question=intent_decision.question,
                )
                intent_decision = await self._select_intent_no_clarify(
                    resolved_query,
                    skill_id,
                    [],
                    contextualized_request,
                    query,
                    suppressed=intent_decision,
                )
                _trace(
                    trace,
                    "intent_select_no_clarify",
                    mode="no_loop",
                    attempt=1,
                    skill_id=skill_id,
                    decision=intent_decision.model_dump(mode="json"),
                    rejected_intents=[],
                )
            if intent_decision.status == "clarify":
                return _traced_result(
                    trace,
                    self._clarify(
                        intent_decision.question,
                        intent_decision.options,
                        state,
                    ),
                )
        if intent_decision.status == "no_match":
            return _traced_result(
                trace,
                _fallback_dialogue_result(
                    state,
                    intent_decision.reason or "No matching intent",
                ),
            )

        return _traced_result(
            trace,
            RouteResult(
                status="matched",
                skill=SkillRef(id=skill_id, name=skill.name),
                intent=intent_decision.intent,
                code=intent_decision.code,
                params=intent_decision.params,
                confidence=min(route_confidence, intent_decision.confidence),
                visited_skills=visited,
                resolved_query=state.get("resolved_query"),
                context_relation=state.get("context_relation"),
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
        if self.mode == "code_eval":
            return self._apply_code_eval_evaluation_result(
                evaluation,
                skill_id=skill_id,
                skill_name=skill_name,
                validated=validated,
                route_confidence=route_confidence,
                state=state,
                trace=trace,
                attempt=attempt,
            )

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

        if (
            evaluation.reject_scope == "intent_mismatch"
            and self._preferred_code_matches_candidate(
                evaluation,
                skill_id=skill_id,
                candidate_code=validated.code,
            )
        ):
            _trace(
                trace,
                "accept_same_code_intent_mismatch",
                attempt=attempt,
                skill_id=skill_id,
                intent=validated.intent,
                code=validated.code,
                preferred_intent=evaluation.preferred_intent,
                preferred_code=evaluation.preferred_code,
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

        if evaluation.reject_scope == "param_mismatch":
            _trace(
                trace,
                "accept_param_mismatch",
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
            _apply_retry_scope(
                state,
                evaluation.reject_scope,
                skill_id=skill_id,
                route_confidence=route_confidence,
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
            _record_intent_rejection(
                state,
                skill_id,
                validated.intent,
                evaluation.reason,
                scope="intent_mismatch",
            )
            return None

        _apply_retry_scope(
            state,
            evaluation.reject_scope,
            skill_id=skill_id,
            route_confidence=route_confidence,
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

    def _apply_code_eval_evaluation_result(
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
    ) -> RouteResult:
        if evaluation.verdict == "accept":
            return _candidate_result(
                state,
                skill_id=skill_id,
                skill_name=skill_name,
                validated=validated,
                route_confidence=route_confidence,
            )

        if evaluation.verdict == "clarify":
            _trace(
                trace,
                "weak_evaluation_accept",
                attempt=attempt,
                verdict="clarify",
                skill_id=skill_id,
                intent=validated.intent,
                code=validated.code,
                clarify_scope=evaluation.clarify_scope,
                reason=evaluation.reason or evaluation.clarity_reason,
            )
            return _candidate_result(
                state,
                skill_id=skill_id,
                skill_name=skill_name,
                validated=validated,
                route_confidence=route_confidence,
            )

        if evaluation.reject_scope == "param_mismatch":
            _trace(
                trace,
                "accept_param_mismatch",
                attempt=attempt,
                skill_id=skill_id,
                intent=validated.intent,
                code=validated.code,
                reason=evaluation.reason,
            )
            return _candidate_result(
                state,
                skill_id=skill_id,
                skill_name=skill_name,
                validated=validated,
                route_confidence=route_confidence,
            )

        if self._preferred_code_matches_candidate(
            evaluation,
            skill_id=skill_id,
            candidate_code=validated.code,
        ):
            _trace(
                trace,
                "accept_same_code_intent_mismatch",
                attempt=attempt,
                skill_id=skill_id,
                intent=validated.intent,
                code=validated.code,
                preferred_intent=evaluation.preferred_intent,
                preferred_code=evaluation.preferred_code,
                reason=evaluation.reason,
            )
            return _candidate_result(
                state,
                skill_id=skill_id,
                skill_name=skill_name,
                validated=validated,
                route_confidence=route_confidence,
            )

        preferred = self._result_for_preferred_code(
            evaluation,
            state=state,
            route_confidence=route_confidence,
        )
        if (
            preferred is not None
            and evaluation.confidence >= 0.8
            and evaluation.is_code_blocking
        ):
            _trace(
                trace,
                "accept_preferred_code",
                attempt=attempt,
                rejected_skill_id=skill_id,
                rejected_intent=validated.intent,
                rejected_code=validated.code,
                preferred_skill_id=preferred.skill.id if preferred.skill else None,
                preferred_intent=preferred.intent,
                preferred_code=preferred.code,
                confidence=evaluation.confidence,
                reason=evaluation.reason,
            )
            state.setdefault("candidate_attempts", []).append(
                {
                    "attempt": attempt,
                    "source": "evaluator_preferred_code",
                    "skill_id": preferred.skill.id if preferred.skill else None,
                    "skill_name": preferred.skill.name if preferred.skill else None,
                    "intent": preferred.intent,
                    "code": preferred.code,
                    "params": preferred.params,
                    "confidence": preferred.confidence,
                    "evaluator_verdict": evaluation.verdict,
                    "evaluator_reason": evaluation.reason,
                },
            )
            return preferred

        event = "invalid_reject_accept" if not preferred else "weak_evaluation_accept"
        _trace(
            trace,
            event,
            attempt=attempt,
            skill_id=skill_id,
            intent=validated.intent,
            code=validated.code,
            reject_scope=evaluation.reject_scope,
            preferred_skill_id=evaluation.preferred_skill_id,
            preferred_intent=evaluation.preferred_intent,
            preferred_code=evaluation.preferred_code,
            is_code_blocking=evaluation.is_code_blocking,
            confidence=evaluation.confidence,
            reason=evaluation.reason,
        )
        return _candidate_result(
            state,
            skill_id=skill_id,
            skill_name=skill_name,
            validated=validated,
            route_confidence=route_confidence,
        )

    def _result_for_preferred_code(
        self,
        evaluation: EvaluationDecision,
        *,
        state: dict[str, Any],
        route_confidence: float,
    ) -> RouteResult | None:
        if not evaluation.preferred_code:
            return None

        preferred_skill_id = evaluation.preferred_skill_id
        if preferred_skill_id and self.registry.has(preferred_skill_id):
            skill = self.registry.get(preferred_skill_id)
            for intent_name, schema in skill.intents.items():
                if schema.code == evaluation.preferred_code and (
                    not evaluation.preferred_intent
                    or evaluation.preferred_intent == intent_name
                ):
                    visited = state.setdefault("visited_skills", [])
                    if skill.id not in visited:
                        visited.append(skill.id)
                    return RouteResult(
                        status="matched",
                        skill=SkillRef(id=skill.id, name=skill.name),
                        intent=intent_name,
                        code=schema.code,
                        params={},
                        confidence=route_confidence,
                        visited_skills=visited,
                        resolved_query=state.get("resolved_query"),
                        context_relation=state.get("context_relation"),
                    )

        for skill in self.registry._skills.values():
            for intent_name, schema in skill.intents.items():
                if schema.code == evaluation.preferred_code and (
                    not evaluation.preferred_intent
                    or evaluation.preferred_intent == intent_name
                ):
                    visited = state.setdefault("visited_skills", [])
                    if skill.id not in visited:
                        visited.append(skill.id)
                    return RouteResult(
                        status="matched",
                        skill=SkillRef(id=skill.id, name=skill.name),
                        intent=intent_name,
                        code=schema.code,
                        params={},
                        confidence=route_confidence,
                        visited_skills=visited,
                        resolved_query=state.get("resolved_query"),
                        context_relation=state.get("context_relation"),
                    )
        return None

    def _preferred_code_matches_candidate(
        self,
        evaluation: EvaluationDecision,
        *,
        skill_id: str,
        candidate_code: str | None,
    ) -> bool:
        if not candidate_code:
            return False
        if evaluation.preferred_code:
            return evaluation.preferred_code == candidate_code
        if not evaluation.preferred_intent or not self.registry.has(skill_id):
            return False
        skill = self.registry.get(skill_id)
        intent_schema = skill.intents.get(evaluation.preferred_intent)
        return intent_schema is not None and intent_schema.code == candidate_code

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

    async def _select_skills_no_clarify(
        self,
        resolved_query: str,
        rejected: list[str],
        context: dict[str, Any],
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
        *,
        suppressed: SkillRouteDecision,
    ) -> SkillRouteDecision:
        prompt = build_router_prompt(
            resolved_query,
            self.registry.cards(),
            rejected,
            contextualized_request,
            current_user_query,
        )
        if context:
            prompt = json.dumps(
                {"routing_input": json.loads(prompt), "context": context},
                ensure_ascii=False,
            )
        decision = await self.model_client.structured(
            system_prompt=ROUTER_SYSTEM_PROMPT,
            user_prompt=_with_no_clarify_policy(prompt, suppressed),
            response_model=SkillRouteDecisionNoClarify,
        )
        return SkillRouteDecision.model_validate(decision.model_dump())

    async def _select_intent(
        self,
        resolved_query: str,
        skill_id: str,
        rejected_intents: list[dict[str, str]],
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
                contextualized_request,
                current_user_query,
            ),
            response_model=IntentDecision,
        )

    async def _select_intent_no_clarify(
        self,
        resolved_query: str,
        skill_id: str,
        rejected_intents: list[dict[str, str]],
        contextualized_request: ContextualizedRequest | None = None,
        current_user_query: str | None = None,
        *,
        suppressed: IntentDecision,
    ) -> IntentDecision:
        skill = self.registry.get(skill_id)
        decision = await self.model_client.structured(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_prompt=_with_no_clarify_policy(
                build_intent_prompt(
                    resolved_query,
                    skill,
                    rejected_intents,
                    contextualized_request,
                    current_user_query,
                ),
                suppressed,
            ),
            response_model=IntentDecisionNoClarify,
        )
        return IntentDecision.model_validate(decision.model_dump())

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
        "locked_skill_id": None,
        "locked_skill_confidence": None,
        "locked_intent": None,
        "locked_code": None,
        "retry_scope": "reroute_skill",
        "rejections": [],
        "candidate_attempts": [],
    }


def _should_suppress_clarify(
    stage: str,
    decision: Any,
    *,
    mode: str = "code_eval",
) -> bool:
    status = getattr(decision, "status", None)
    verdict = getattr(decision, "verdict", None)
    if status != "clarify" and verdict != "clarify":
        return False
    if mode == "code_eval":
        return True
    return getattr(decision, "clarify_scope", None) not in ALLOWED_CLARIFY_SCOPES_BY_STAGE[stage]


def _with_no_clarify_policy(prompt: str, suppressed: Any) -> str:
    payload = json.loads(prompt)
    payload["clarify_policy"] = {
        "mode": "no_clarify_retry",
        "instruction": (
            "本次重试禁止输出 clarify。若缺少主体、关键词、联系人、主题、"
            "操作对象、参数对象、真实文件/图片/邮件句柄或唯一资源选择，但 skill/intent/code 可判断，"
            "必须输出最可能的可路由结果；只有能力不支持时输出 no_match。"
        ),
        "suppressed_clarify": {
            "scope": getattr(suppressed, "clarify_scope", None),
            "reason": getattr(suppressed, "reason", None)
            or getattr(suppressed, "clarity_reason", None),
            "question": getattr(suppressed, "question", None),
            "options": getattr(suppressed, "options", None) or [],
        },
    }
    return json.dumps(payload, ensure_ascii=False)


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


def _candidate_result(
    state: dict[str, Any],
    *,
    skill_id: str,
    skill_name: str,
    validated: IntentDecision,
    route_confidence: float,
) -> RouteResult:
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


def _record_candidate_attempt(
    state: dict[str, Any],
    *,
    skill_id: str,
    skill_name: str,
    route_confidence: float,
    decision: IntentDecision,
    attempt: int,
    source: str,
) -> None:
    state.setdefault("candidate_attempts", []).append(
        {
            "attempt": attempt,
            "source": source,
            "skill_id": skill_id,
            "skill_name": skill_name,
            "intent": decision.intent,
            "code": decision.code,
            "params": decision.params,
            "confidence": min(route_confidence, decision.confidence),
        },
    )


def _best_candidate_result(state: dict[str, Any]) -> RouteResult | None:
    candidates = state.get("candidate_attempts") or []
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda item: (
            float(item.get("confidence") or 0.0),
            -int(item.get("attempt") or 0),
        ),
    )
    skill_id = best.get("skill_id")
    skill_name = best.get("skill_name")
    skill = SkillRef(id=skill_id, name=skill_name) if skill_id and skill_name else None
    return RouteResult(
        status="matched",
        skill=skill,
        intent=best.get("intent"),
        code=best.get("code"),
        params=best.get("params") or {},
        confidence=float(best.get("confidence") or 0.0),
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

        elif event_name == "intent_select":
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
        if scope and scope not in scopes:
            scopes.append(scope)
    return scopes


def _apply_retry_scope(
    state: dict[str, Any],
    scope: str,
    *,
    skill_id: str,
    route_confidence: float,
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
