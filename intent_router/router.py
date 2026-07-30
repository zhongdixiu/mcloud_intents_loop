from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .candidate_validator import (
    dynamic_business_candidate_count,
    normalize_skill_candidates,
    validate_and_merge_intent_candidates,
)
from .config import RouterConfig
from .decision_gate import (
    decide,
    evaluator_switch_allowed,
    final_confidence,
)
from .model_client import AgentScopeStructuredClient, StructuredModelClient
from .model_governor import (
    ModelCallGovernor,
    RouteDeadline,
)
from .parameter_validator import validate_parameters
from .prompts import (
    CONTEXTUALIZER_SYSTEM_PROMPT,
    DIALOGUE_HISTORY_LIMIT,
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    PARAMETER_EXTRACTOR_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_contextualizer_prompt,
    build_evaluator_prompt,
    build_intent_prompt,
    build_parameter_prompt,
    build_router_prompt,
)
from .skills import SkillRegistry
from .types import (
    CandidateScore,
    ContextualizedRequest,
    DecisionGateResult,
    DialogueHistory,
    IntentCandidate,
    IntentCandidateSet,
    ModelCallRecord,
    ParameterExtractionResult,
    RerankDecision,
    RouteDiagnostics,
    RouteResult,
    SemanticFrame,
    SkillCandidate,
    SkillCandidateSet,
    SkillRef,
)
from .validation import ValidationError, validate_rerank_decision


ORDINARY_CANDIDATE_ID = "ordinary_dialogue:普通对话"
ORDINARY_INTENT = "普通对话"
ORDINARY_CODE = "000"


class IntentRouter:
    def __init__(
        self,
        registry: SkillRegistry,
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        parameter_client: StructuredModelClient | None = None,
        *,
        config: RouterConfig | dict[str, Any] | None = None,
        max_expansion_rounds: int | None = None,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        top_skills: int | None = None,
        top_intents: int | None = None,
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
        self.parameter_client = parameter_client or self.model_client
        self.config = (
            config
            if isinstance(config, RouterConfig)
            else RouterConfig.model_validate(config or {})
        )
        if max_expansion_rounds is not None:
            self.config.candidates.max_expansion_rounds = max_expansion_rounds
        if top_skills is not None:
            self.config.candidates.default_skill_candidates = top_skills
            self.config.candidates.max_skill_candidates = max(
                top_skills,
                self.config.candidates.max_skill_candidates,
            )
        if top_intents is not None:
            self.config.candidates.max_intent_candidates_per_skill = top_intents
        if dialogue_history_limit is not None:
            self.config.session.history_limit = dialogue_history_limit
        self.dialogue_history_limit = (
            dialogue_history_limit
            if dialogue_history_limit is not None
            else self.config.session.history_limit
        )
        self.governor = ModelCallGovernor(
            global_concurrency=self.config.concurrency.global_model_calls,
            retry=self.config.retry,
        )

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        parameter_client: StructuredModelClient | None = None,
        *,
        config: RouterConfig | dict[str, Any] | None = None,
        max_expansion_rounds: int | None = None,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        top_skills: int | None = None,
        top_intents: int | None = None,
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            evaluator_client=evaluator_client,
            parameter_client=parameter_client,
            config=config,
            max_expansion_rounds=max_expansion_rounds,
            dialogue_history_limit=dialogue_history_limit,
            top_skills=top_skills,
            top_intents=top_intents,
        )

    async def route(
        self,
        query: str,
        *,
        dialogue_history: DialogueHistory | dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        return await self._route(
            query,
            dialogue_history=dialogue_history,
            context=context,
            trace=trace,
            force_first_candidate=False,
        )

    async def route_no_loop(
        self,
        query: str,
        *,
        dialogue_history: DialogueHistory | dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        trace: list[dict[str, Any]] | None = None,
    ) -> RouteResult:
        """Legacy benchmark path: select the first validated candidate."""
        return await self._route(
            query,
            dialogue_history=dialogue_history,
            context=context,
            trace=trace,
            force_first_candidate=True,
        )

    async def _route(
        self,
        query: str,
        *,
        dialogue_history: DialogueHistory | dict[str, Any] | None,
        context: dict[str, Any] | None,
        trace: list[dict[str, Any]] | None,
        force_first_candidate: bool,
    ) -> RouteResult:
        trace = [] if trace is None else trace
        context = context or {}
        records: list[ModelCallRecord] = []
        deadline = RouteDeadline.after_ms(self.config.timeouts.route_deadline_ms)
        history = _normalize_dialogue_history(dialogue_history)
        _trace(
            trace,
            "route_start",
            query=query,
            dialogue_turns=len(history.turns),
            route_path="first_candidate" if force_first_candidate else "conditional",
        )

        contextualized = await self._contextualize(
            query,
            history,
            deadline=deadline,
            records=records,
        )
        _add_semantic_frame_conflicts(contextualized, query)
        resolved_query = contextualized.resolved_query or query
        _trace(
            trace,
            "contextualize",
            current_user_query=query,
            resolved_query=resolved_query,
            relation_to_history=contextualized.relation_to_history,
            confidence=contextualized.confidence,
            conflict_flags=contextualized.conflict_flags,
            semantic_frame=contextualized.semantic_frame.model_dump(mode="json"),
            reason=contextualized.reason,
        )

        try:
            skill_candidates, discarded_skills = await self._generate_skill_candidates(
                resolved_query,
                contextualized,
                query,
                context=context,
                deadline=deadline,
                records=records,
            )
        except Exception as exc:
            return _traced_result(
                trace,
                _terminal_result(
                    "error",
                    resolved_query=resolved_query,
                    contextualized=contextualized,
                    reason=f"skill selector failed: {exc}",
                    diagnostics=RouteDiagnostics(
                        evaluator_action="not_called",
                        fallback_reason=str(exc),
                        model_calls=records,
                    ),
                ),
            )
        _trace(
            trace,
            "skill_candidates",
            candidates=[item.model_dump(mode="json") for item in skill_candidates],
            discarded=discarded_skills,
        )

        raw_intents, failed_skills = await self._generate_intent_candidates(
            resolved_query,
            skill_candidates,
            contextualized,
            query,
            deadline=deadline,
            records=records,
            trace=trace,
        )
        aggregation = validate_and_merge_intent_candidates(
            self.registry,
            raw_intents,
            contextualized_request=contextualized,
            current_user_query=query,
        )
        candidates = sorted(
            aggregation.candidates,
            key=lambda item: item.confidence,
            reverse=True,
        )
        _trace(
            trace,
            "intent_candidates",
            candidates=[item.model_dump(mode="json") for item in candidates],
            discarded=aggregation.discarded,
            failed_skills=failed_skills,
        )

        diagnostics = RouteDiagnostics(
            first_candidate_id=candidates[0].candidate_id if candidates else None,
            failed_skills=failed_skills,
            discarded_candidates=[*discarded_skills, *aggregation.discarded],
            model_calls=records,
        )
        if failed_skills and not any(
            candidate.skill_id is not None for candidate in candidates
        ):
            diagnostics.evaluator_action = "not_called"
            return _traced_result(
                trace,
                _terminal_result(
                    "error",
                    resolved_query=resolved_query,
                    contextualized=contextualized,
                    reason="all selected business intent generators failed",
                    diagnostics=diagnostics,
                    alternatives=candidates,
                ),
            )
        if not candidates:
            diagnostics.evaluator_action = "not_called"
            return _traced_result(
                trace,
                _terminal_result(
                    "error",
                    resolved_query=resolved_query,
                    contextualized=contextualized,
                    reason="no valid intent candidates",
                    diagnostics=diagnostics,
                ),
            )

        if force_first_candidate:
            diagnostics.evaluator_action = "select_top"
            diagnostics.gate_reason = "legacy first-candidate benchmark path"
            return await self._finalize_selected(
                candidates[0],
                candidates,
                query=query,
                resolved_query=resolved_query,
                contextualized=contextualized,
                context=context,
                diagnostics=diagnostics,
                deadline=deadline,
                records=records,
                trace=trace,
            )

        expansion_rounds = 0
        visited_skill_candidates = list(skill_candidates)
        while True:
            gate = decide(candidates, contextualized, self.config.decision)
            _apply_gate_diagnostics(diagnostics, gate)
            _trace(trace, "decision_gate", decision=gate.model_dump(mode="json"))

            if gate.action == "direct":
                diagnostics.evaluator_action = "not_called"
                diagnostics.final_candidate_id = candidates[0].candidate_id
                return await self._finalize_selected(
                    candidates[0],
                    candidates,
                    query=query,
                    resolved_query=resolved_query,
                    contextualized=contextualized,
                    context=context,
                    diagnostics=diagnostics,
                    deadline=deadline,
                    records=records,
                    trace=trace,
                )
            if gate.action in {"abstain", "error"}:
                diagnostics.evaluator_action = "not_called"
                return _traced_result(
                    trace,
                    _terminal_result(
                        gate.action,
                        resolved_query=resolved_query,
                        contextualized=contextualized,
                        reason=gate.reason,
                        diagnostics=diagnostics,
                        alternatives=candidates,
                    ),
                )

            try:
                rerank = await self._rerank_candidates(
                    resolved_query,
                    candidates,
                    contextualized,
                    query,
                    expansion_round=expansion_rounds,
                    deadline=deadline,
                    records=records,
                )
                rerank = _validated_rerank(rerank, candidates)
            except Exception as exc:
                diagnostics.evaluator_called = True
                diagnostics.evaluator_action = "unavailable"
                diagnostics.fallback_reason = str(exc)
                diagnostics.rerank_reason = f"evaluator unavailable: {exc}"
                return _traced_result(
                    trace,
                    _terminal_result(
                        "abstain",
                        resolved_query=resolved_query,
                        contextualized=contextualized,
                        reason=diagnostics.rerank_reason,
                        diagnostics=diagnostics,
                        alternatives=candidates,
                    ),
                )

            diagnostics.evaluator_called = True
            diagnostics.rerank_reason = rerank.reason
            _trace(
                trace,
                "rerank",
                expansion_round=expansion_rounds,
                decision=rerank.model_dump(mode="json"),
            )

            if rerank.verdict in {"abstain", "unsupported"}:
                diagnostics.evaluator_action = rerank.verdict
                diagnostics.risk_flags = _dedupe(
                    [*diagnostics.risk_flags, *rerank.risk_flags],
                )
                return _traced_result(
                    trace,
                    _terminal_result(
                        rerank.verdict,
                        resolved_query=resolved_query,
                        contextualized=contextualized,
                        reason=rerank.reason,
                        diagnostics=diagnostics,
                        alternatives=candidates,
                        confidence=rerank.confidence,
                    ),
                )

            if rerank.verdict == "expand":
                if expansion_rounds >= self.config.candidates.max_expansion_rounds:
                    diagnostics.evaluator_action = "expand_exhausted"
                    diagnostics.expansion_rounds = expansion_rounds
                    return _traced_result(
                        trace,
                        _terminal_result(
                            "abstain",
                            resolved_query=resolved_query,
                            contextualized=contextualized,
                            reason="candidate expansion limit reached",
                            diagnostics=diagnostics,
                            alternatives=candidates,
                        ),
                    )
                new_candidates = await self._expand_candidates(
                    resolved_query,
                    visited_skill_candidates,
                    candidates,
                    rerank,
                    contextualized,
                    query,
                    context=context,
                    deadline=deadline,
                    records=records,
                    trace=trace,
                )
                merged = validate_and_merge_intent_candidates(
                    self.registry,
                    [*candidates, *new_candidates],
                    contextualized_request=contextualized,
                    current_user_query=query,
                )
                new_keys = {
                    (item.skill_id, item.intent, item.code)
                    for item in new_candidates
                }
                existing_keys = {
                    (item.skill_id, item.intent, item.code)
                    for item in candidates
                }
                expansion_rounds += 1
                diagnostics.expansion_rounds = expansion_rounds
                if rerank.expand_scope:
                    diagnostics.expansion_scopes = _dedupe(
                        [*diagnostics.expansion_scopes, rerank.expand_scope],
                    )
                diagnostics.discarded_candidates.extend(merged.discarded)
                if not (new_keys - existing_keys):
                    diagnostics.evaluator_action = "expand_empty"
                    return _traced_result(
                        trace,
                        _terminal_result(
                            "abstain",
                            resolved_query=resolved_query,
                            contextualized=contextualized,
                            reason="candidate expansion produced no new route",
                            diagnostics=diagnostics,
                            alternatives=candidates,
                        ),
                    )
                candidates = sorted(
                    merged.candidates,
                    key=lambda item: item.confidence,
                    reverse=True,
                )
                _trace(
                    trace,
                    "expansion",
                    expansion_round=expansion_rounds,
                    expand_scope=rerank.expand_scope,
                    expansion_hint=rerank.expansion_hint,
                    candidates=[item.model_dump(mode="json") for item in candidates],
                )
                continue

            first = candidates[0]
            selected = _candidate_by_id(candidates, rerank.selected_candidate_id)
            if selected is None:
                diagnostics.evaluator_action = "invalid"
                return _traced_result(
                    trace,
                    _terminal_result(
                        "abstain",
                        resolved_query=resolved_query,
                        contextualized=contextualized,
                        reason="evaluator selected an unknown candidate",
                        diagnostics=diagnostics,
                        alternatives=candidates,
                    ),
                )
            score_by_id = {item.candidate_id: item.score for item in rerank.ranking}
            allowed, switch_margin, switch_reason = evaluator_switch_allowed(
                first_candidate=first,
                selected_candidate=selected,
                evaluator_confidence=rerank.confidence,
                score_by_id=score_by_id,
                config=self.config.decision,
            )
            diagnostics.evaluator_score = rerank.confidence
            diagnostics.evaluator_margin = switch_margin
            diagnostics.risk_flags = _dedupe(
                [
                    *diagnostics.risk_flags,
                    *rerank.risk_flags,
                    *first.risk_flags,
                    *selected.risk_flags,
                ],
            )
            if (
                selected.candidate_id == first.candidate_id
                and rerank.confidence < self.config.decision.evaluator_min_confidence
            ):
                allowed = False
                switch_reason += "; evaluator confidence below acceptance threshold"
            if not allowed:
                diagnostics.evaluator_action = "switch_blocked"
                diagnostics.rerank_reason = f"{rerank.reason}; {switch_reason}"
                return _traced_result(
                    trace,
                    _terminal_result(
                        "abstain",
                        resolved_query=resolved_query,
                        contextualized=contextualized,
                        reason=diagnostics.rerank_reason,
                        diagnostics=diagnostics,
                        alternatives=candidates,
                    ),
                )
            diagnostics.evaluator_action = (
                "select_top"
                if selected.candidate_id == first.candidate_id
                else "switch"
            )
            diagnostics.final_candidate_id = selected.candidate_id
            diagnostics.rerank_reason = f"{rerank.reason}; {switch_reason}"
            return await self._finalize_selected(
                selected,
                candidates,
                query=query,
                resolved_query=resolved_query,
                contextualized=contextualized,
                context=context,
                diagnostics=diagnostics,
                deadline=deadline,
                records=records,
                trace=trace,
            )

    async def _contextualize(
        self,
        query: str,
        history: DialogueHistory,
        *,
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
    ) -> ContextualizedRequest:
        if not history.turns:
            return ContextualizedRequest(
                resolved_query=query,
                relation_to_history="new_request",
                semantic_frame=SemanticFrame(),
            )
        try:
            return await self.governor.structured(
                self.model_client,
                stage="contextualizer",
                timeout_ms=self.config.timeouts.contextualizer_ms,
                deadline=deadline,
                records=records,
                system_prompt=CONTEXTUALIZER_SYSTEM_PROMPT,
                user_prompt=build_contextualizer_prompt(
                    query,
                    history,
                    history_limit=self.dialogue_history_limit,
                ),
                response_model=ContextualizedRequest,
            )
        except Exception as exc:
            return ContextualizedRequest(
                resolved_query=query,
                relation_to_history="ambiguous",
                semantic_frame=SemanticFrame(
                    uncertainty_notes=["contextualizer failed; history was not inherited"],
                ),
                confidence=0.0,
                conflict_flags=["contextualizer_error"],
                reason=str(exc),
            )

    async def _generate_skill_candidates(
        self,
        resolved_query: str,
        contextualized: ContextualizedRequest,
        current_user_query: str,
        *,
        context: dict[str, Any],
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
        existing_candidates: list[SkillCandidate] | None = None,
        expansion_scope: str | None = None,
        expansion_hint: str | None = None,
    ) -> tuple[list[SkillCandidate], list[dict[str, str]]]:
        decision = await self.governor.structured(
            self.model_client,
            stage="skill_selector",
            timeout_ms=self.config.timeouts.skill_selector_ms,
            deadline=deadline,
            records=records,
            system_prompt=ROUTER_SYSTEM_PROMPT,
            user_prompt=build_router_prompt(
                resolved_query,
                self.registry.cards(),
                contextualized,
                current_user_query,
                top_n=self.config.candidates.max_skill_candidates,
                existing_candidates=existing_candidates,
                expansion_scope=expansion_scope,
                expansion_hint=expansion_hint,
                context=context,
            ),
            response_model=SkillCandidateSet,
        )
        normalized, discarded = normalize_skill_candidates(
            self.registry,
            decision.candidates,
            contextualized_request=contextualized,
            current_user_query=current_user_query,
            max_business_candidates=self.config.candidates.max_skill_candidates,
        )
        business_count = dynamic_business_candidate_count(
            normalized,
            high_confidence_count=self.config.candidates.high_confidence_skill_candidates,
            default_count=self.config.candidates.default_skill_candidates,
            max_count=self.config.candidates.max_skill_candidates,
            high_confidence_threshold=self.config.candidates.high_confidence_threshold,
            high_margin_threshold=self.config.candidates.high_margin_threshold,
            low_confidence_threshold=self.config.candidates.low_confidence_threshold,
            low_margin_threshold=self.config.candidates.low_margin_threshold,
        )
        business = [item for item in normalized if item.skill_id is not None][
            :business_count
        ]
        ordinary = next(item for item in normalized if item.skill_id is None)
        return [*business, ordinary], discarded

    async def _generate_intent_candidates(
        self,
        resolved_query: str,
        skill_candidates: list[SkillCandidate],
        contextualized: ContextualizedRequest,
        current_user_query: str,
        *,
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
        trace: list[dict[str, Any]] | None,
        existing_candidates: list[IntentCandidate] | None = None,
        expansion_scope: str | None = None,
        expansion_hint: str | None = None,
    ) -> tuple[list[IntentCandidate], dict[str, str]]:
        ordinary = [
            _ordinary_intent_candidate(item)
            for item in skill_candidates
            if item.skill_id is None
        ]
        business = [item for item in skill_candidates if item.skill_id is not None]
        request_semaphore = asyncio.Semaphore(
            self.config.concurrency.intent_generation_per_request,
        )

        async def generate(
            skill_candidate: SkillCandidate,
        ) -> tuple[str, list[IntentCandidate] | Exception]:
            assert skill_candidate.skill_id is not None
            skill = self.registry.get(skill_candidate.skill_id)
            try:
                async with request_semaphore:
                    decision = await self.governor.structured(
                        self.model_client,
                        stage=f"intent_selector:{skill.id}",
                        timeout_ms=self.config.timeouts.intent_selector_ms,
                        deadline=deadline,
                        records=records,
                        system_prompt=INTENT_SYSTEM_PROMPT,
                        user_prompt=build_intent_prompt(
                            resolved_query,
                            skill,
                            contextualized,
                            current_user_query,
                            skill_candidate=skill_candidate,
                            top_m=self.config.candidates.max_intent_candidates_per_skill,
                            existing_candidates=existing_candidates,
                            expansion_scope=expansion_scope,
                            expansion_hint=expansion_hint,
                            routing_context=self.registry.routing_context(skill.id),
                        ),
                        response_model=IntentCandidateSet,
                    )
            except Exception as exc:
                return skill.id, exc

            normalized: list[IntentCandidate] = []
            for candidate in decision.candidates[
                : self.config.candidates.max_intent_candidates_per_skill
            ]:
                candidate.skill_id = skill.id
                candidate.skill_name = skill.name
                candidate.source_ids = [candidate.candidate_id]
                candidate.confidence = min(
                    skill_candidate.confidence,
                    candidate.confidence,
                )
                candidate.matched_evidence = _dedupe(
                    [
                        *skill_candidate.matched_evidence,
                        *candidate.matched_evidence,
                    ],
                )
                candidate.risk_flags = _dedupe(
                    [*skill_candidate.risk_flags, *candidate.risk_flags],
                )
                normalized.append(candidate)
            return skill.id, normalized

        results = await asyncio.gather(
            *(generate(candidate) for candidate in business),
        )
        candidates = list(ordinary)
        failed: dict[str, str] = {}
        for skill_id, value in results:
            if isinstance(value, Exception):
                failed[skill_id] = str(value)
                _trace(
                    trace,
                    "intent_candidate_set_error",
                    skill_id=skill_id,
                    reason=str(value),
                )
                continue
            candidates.extend(value)
            _trace(
                trace,
                "intent_candidate_set",
                skill_id=skill_id,
                candidates=[item.model_dump(mode="json") for item in value],
            )
        return candidates, failed

    async def _rerank_candidates(
        self,
        resolved_query: str,
        candidates: list[IntentCandidate],
        contextualized: ContextualizedRequest,
        current_user_query: str,
        *,
        expansion_round: int,
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
    ) -> RerankDecision:
        return await self.governor.structured(
            self.evaluator_client,
            stage="evaluator",
            timeout_ms=self.config.timeouts.evaluator_ms,
            deadline=deadline,
            records=records,
            system_prompt=EVALUATOR_SYSTEM_PROMPT,
            user_prompt=build_evaluator_prompt(
                resolved_query,
                self.registry.cards(),
                candidates,
                contextualized_request=contextualized,
                current_user_query=current_user_query,
                expansion_round=expansion_round,
            ),
            response_model=RerankDecision,
        )

    async def _expand_candidates(
        self,
        resolved_query: str,
        skill_candidates: list[SkillCandidate],
        intent_candidates: list[IntentCandidate],
        rerank: RerankDecision,
        contextualized: ContextualizedRequest,
        current_user_query: str,
        *,
        context: dict[str, Any],
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
        trace: list[dict[str, Any]],
    ) -> list[IntentCandidate]:
        if rerank.expand_scope == "skill_recall_gap":
            expanded, _ = await self._generate_skill_candidates(
                resolved_query,
                contextualized,
                current_user_query,
                context=context,
                deadline=deadline,
                records=records,
                existing_candidates=skill_candidates,
                expansion_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
            )
            known = {item.skill_id for item in skill_candidates}
            new_skills = [item for item in expanded if item.skill_id not in known]
            skill_candidates.extend(new_skills)
            generated, _ = await self._generate_intent_candidates(
                resolved_query,
                new_skills,
                contextualized,
                current_user_query,
                deadline=deadline,
                records=records,
                trace=trace,
                existing_candidates=intent_candidates,
                expansion_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
            )
            return generated

        if rerank.expand_scope == "intent_recall_gap":
            skill_by_id = {
                item.skill_id: item
                for item in skill_candidates
                if item.skill_id is not None
            }
            candidate_by_id = {item.candidate_id: item for item in intent_candidates}
            ordered_ids = [
                *([rerank.selected_candidate_id] if rerank.selected_candidate_id else []),
                *[item.candidate_id for item in rerank.ranking],
            ]
            target_ids = _dedupe(
                [
                    candidate_by_id[candidate_id].skill_id
                    for candidate_id in ordered_ids
                    if candidate_id in candidate_by_id
                    and candidate_by_id[candidate_id].skill_id
                ],
            )[:2]
            generated, _ = await self._generate_intent_candidates(
                resolved_query,
                [
                    skill_by_id[skill_id]
                    for skill_id in target_ids
                    if skill_id in skill_by_id
                ],
                contextualized,
                current_user_query,
                deadline=deadline,
                records=records,
                trace=trace,
                existing_candidates=intent_candidates,
                expansion_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
            )
            return generated
        return []

    async def _finalize_selected(
        self,
        candidate: IntentCandidate,
        candidates: list[IntentCandidate],
        *,
        query: str,
        resolved_query: str,
        contextualized: ContextualizedRequest,
        context: dict[str, Any],
        diagnostics: RouteDiagnostics,
        deadline: RouteDeadline,
        records: list[ModelCallRecord],
        trace: list[dict[str, Any]],
    ) -> RouteResult:
        diagnostics.final_candidate_id = candidate.candidate_id
        best_alternative_score = max(
            (
                item.confidence
                for item in candidates
                if item.candidate_id != candidate.candidate_id
            ),
            default=0.0,
        )
        selector_margin = candidate.confidence - best_alternative_score
        confidence, features = final_confidence(
            selector_score=candidate.confidence,
            selector_margin=selector_margin,
            contextualized_request=contextualized,
            risk_flags=diagnostics.risk_flags,
            config=self.config.confidence,
            evaluator_confidence=diagnostics.evaluator_score,
            evaluator_margin=diagnostics.evaluator_margin,
        )
        diagnostics.selector_score = candidate.confidence
        diagnostics.final_confidence_features = features
        diagnostics.model_calls = records
        params: dict[str, Any] = {}

        if candidate.skill_id is not None:
            intent_schema = self.registry.get(candidate.skill_id).intents[candidate.intent]
            if intent_schema.params:
                trusted_params = context.get("trusted_params", {})
                if not isinstance(trusted_params, dict):
                    trusted_params = {}
                try:
                    extraction = await self.governor.structured(
                        self.parameter_client,
                        stage="parameter_extractor",
                        timeout_ms=self.config.timeouts.parameter_extractor_ms,
                        deadline=deadline,
                        records=records,
                        system_prompt=PARAMETER_EXTRACTOR_SYSTEM_PROMPT,
                        user_prompt=build_parameter_prompt(
                            current_user_query=query,
                            resolved_query=resolved_query,
                            skill_id=candidate.skill_id,
                            intent=candidate.intent,
                            code=candidate.code,
                            parameter_schema={
                                name: schema.model_dump(mode="json")
                                for name, schema in intent_schema.params.items()
                            },
                            execution_instructions=self.registry.execution_context(
                                candidate.skill_id,
                            ).execution_instructions,
                            trusted_params=trusted_params,
                        ),
                        response_model=ParameterExtractionResult,
                    )
                except Exception as exc:
                    diagnostics.fallback_reason = f"parameter extractor failed: {exc}"
                    if any(item.required for item in intent_schema.params.values()):
                        return _traced_result(
                            trace,
                            _selected_result(
                                candidate,
                                status="error",
                                params={},
                                confidence=confidence,
                                resolved_query=resolved_query,
                                contextualized=contextualized,
                                candidates=candidates,
                                diagnostics=diagnostics,
                                reason=diagnostics.fallback_reason,
                            ),
                        )
                    extraction = ParameterExtractionResult()

                validation = validate_parameters(
                    intent_schema,
                    extraction,
                    current_user_query=query,
                    resolved_query=resolved_query,
                    trusted_params=trusted_params,
                )
                diagnostics.parameter_validation = validation
                params = validation.params
                if validation.missing_required:
                    return _traced_result(
                        trace,
                        _selected_result(
                            candidate,
                            status="clarify",
                            params=params,
                            confidence=confidence,
                            resolved_query=resolved_query,
                            contextualized=contextualized,
                            candidates=candidates,
                            diagnostics=diagnostics,
                            reason="required parameters are missing",
                            question=_clarification_question(
                                intent_schema.params,
                                validation.missing_required,
                            ),
                            options=_clarification_options(
                                intent_schema.params,
                                validation.missing_required,
                            ),
                        ),
                    )

        return _traced_result(
            trace,
            _selected_result(
                candidate,
                status="matched",
                params=params,
                confidence=confidence,
                resolved_query=resolved_query,
                contextualized=contextualized,
                candidates=candidates,
                diagnostics=diagnostics,
                reason=candidate.reason,
            ),
        )


def _ordinary_intent_candidate(skill_candidate: SkillCandidate) -> IntentCandidate:
    return IntentCandidate(
        candidate_id=ORDINARY_CANDIDATE_ID,
        skill_id=None,
        skill_name=None,
        intent=ORDINARY_INTENT,
        code=ORDINARY_CODE,
        confidence=skill_candidate.confidence,
        matched_evidence=list(skill_candidate.matched_evidence),
        risk_flags=list(skill_candidate.risk_flags),
        reason=skill_candidate.reason,
        source_ids=[skill_candidate.candidate_id],
    )


def _validated_rerank(
    rerank: RerankDecision,
    candidates: list[IntentCandidate],
) -> RerankDecision:
    aliases: dict[str, str] = {}
    for candidate in candidates:
        aliases[candidate.candidate_id] = candidate.candidate_id
        aliases.update(
            {source_id: candidate.candidate_id for source_id in candidate.source_ids}
        )
        if candidate.skill_id is None:
            aliases["ordinary_dialogue:000"] = candidate.candidate_id
        else:
            aliases[
                f"{candidate.skill_id}:{candidate.intent}:{candidate.code}:1"
            ] = candidate.candidate_id
    if rerank.selected_candidate_id in aliases:
        rerank.selected_candidate_id = aliases[rerank.selected_candidate_id]
    for item in rerank.ranking:
        if item.candidate_id in aliases:
            item.candidate_id = aliases[item.candidate_id]

    ranked_ids = {item.candidate_id for item in rerank.ranking}
    rerank.ranking.extend(
        CandidateScore(
            candidate_id=candidate.candidate_id,
            score=candidate.confidence,
            reason="defaulted from selector confidence",
        )
        for candidate in candidates
        if candidate.candidate_id not in ranked_ids
    )
    return validate_rerank_decision(
        rerank,
        {candidate.candidate_id for candidate in candidates},
    )


def _apply_gate_diagnostics(
    diagnostics: RouteDiagnostics,
    gate: DecisionGateResult,
) -> None:
    diagnostics.gate_reason = gate.reason
    diagnostics.gate_margin = gate.margin
    diagnostics.selector_score = gate.top1_score
    diagnostics.risk_flags = _dedupe(
        [*diagnostics.risk_flags, *gate.risk_flags],
    )


def _terminal_result(
    status: str,
    *,
    resolved_query: str,
    contextualized: ContextualizedRequest,
    reason: str,
    diagnostics: RouteDiagnostics,
    alternatives: list[IntentCandidate] | None = None,
    confidence: float = 0.0,
) -> RouteResult:
    alternatives = alternatives or []
    return RouteResult(
        status=status,
        confidence=max(0.0, min(1.0, confidence)),
        reason=reason,
        resolved_query=resolved_query,
        context_relation=contextualized.relation_to_history,
        visited_skills=_dedupe(
            [
                candidate.skill_id
                for candidate in alternatives
                if candidate.skill_id is not None
            ],
        ),
        loop_count=1 + diagnostics.expansion_rounds,
        correction_scopes=list(diagnostics.expansion_scopes),
        alternatives=alternatives,
        diagnostics=diagnostics,
        termination_reason=status,
    )


def _selected_result(
    candidate: IntentCandidate,
    *,
    status: str,
    params: dict[str, Any],
    confidence: float,
    resolved_query: str,
    contextualized: ContextualizedRequest,
    candidates: list[IntentCandidate],
    diagnostics: RouteDiagnostics,
    reason: str,
    question: str | None = None,
    options: list[dict[str, str]] | None = None,
) -> RouteResult:
    return RouteResult(
        status=status,
        skill=(
            SkillRef(
                id=candidate.skill_id,
                name=candidate.skill_name or candidate.skill_id,
            )
            if candidate.skill_id
            else None
        ),
        intent=candidate.intent,
        code=candidate.code,
        params=params,
        confidence=confidence,
        question=question,
        options=options or [],
        reason=reason,
        visited_skills=_dedupe(
            [
                item.skill_id
                for item in candidates
                if item.skill_id is not None
            ],
        ),
        loop_count=1 + diagnostics.expansion_rounds,
        correction_scopes=list(diagnostics.expansion_scopes),
        resolved_query=resolved_query,
        context_relation=contextualized.relation_to_history,
        alternatives=[
            item
            for item in candidates
            if item.candidate_id != candidate.candidate_id
        ],
        diagnostics=diagnostics,
        termination_reason=status,
    )


def _clarification_question(
    schemas: dict[str, Any],
    missing: list[str],
) -> str:
    labels = [schemas[name].desc or name for name in missing]
    return f"请补充以下信息：{'、'.join(labels)}"


def _clarification_options(
    schemas: dict[str, Any],
    missing: list[str],
) -> list[dict[str, str]]:
    if len(missing) != 1:
        return []
    schema = schemas[missing[0]]
    return [
        {"label": str(value), "value": str(value)}
        for value in schema.allowed_values
    ]


def _candidate_by_id(
    candidates: list[IntentCandidate],
    candidate_id: str | None,
) -> IntentCandidate | None:
    return next(
        (item for item in candidates if item.candidate_id == candidate_id),
        None,
    )


def _add_semantic_frame_conflicts(
    contextualized: ContextualizedRequest,
    current_user_query: str,
) -> None:
    action = (contextualized.semantic_frame.action or "").lower()
    expected = contextualized.semantic_frame.expected_result_type
    text = f"{current_user_query} {action}"
    business_actions = ("搜索", "查找", "打开", "进入", "发送", "生成", "编辑", "处理")
    processing_actions = ("生成", "编辑", "处理", "翻译", "总结", "识别")
    conflict = (
        expected == "ordinary_answer"
        and any(item in text for item in business_actions)
    ) or (
        expected == "resource"
        and any(item in text for item in processing_actions)
    )
    if conflict and "semantic_frame_conflict" not in contextualized.conflict_flags:
        contextualized.conflict_flags.append("semantic_frame_conflict")


def _normalize_dialogue_history(
    history: DialogueHistory | dict[str, Any] | None,
) -> DialogueHistory:
    if history is None:
        return DialogueHistory()
    if isinstance(history, DialogueHistory):
        return history
    return DialogueHistory.model_validate(history)


def _dedupe(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(value for value in values if value is not None))


def _trace(
    trace: list[dict[str, Any]] | None,
    event: str,
    **payload: Any,
) -> None:
    if trace is not None:
        trace.append({"event": event, **payload})


def _traced_result(
    trace: list[dict[str, Any]],
    result: RouteResult,
) -> RouteResult:
    _trace(trace, "result", result=result.model_dump(mode="json"))
    return result
