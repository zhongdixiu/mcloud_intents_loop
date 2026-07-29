from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from .model_client import AgentScopeStructuredClient, StructuredModelClient
from .prompts import (
    CONTEXTUALIZER_SYSTEM_PROMPT,
    DIALOGUE_HISTORY_LIMIT,
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_contextualizer_prompt,
    build_evaluator_prompt,
    build_intent_prompt,
    build_router_prompt,
)
from .skills import SkillRegistry
from .types import (
    CandidateScore,
    ContextualizedRequest,
    DialogueHistory,
    IntentCandidate,
    IntentCandidateSet,
    RerankDecision,
    RouteDiagnostics,
    RouteResult,
    SemanticFrame,
    SkillCandidate,
    SkillCandidateSet,
    SkillRef,
)
from .validation import (
    ValidationError,
    validate_intent_candidate,
    validate_rerank_decision,
)


ORDINARY_CANDIDATE_ID = "ordinary_dialogue:000"
ORDINARY_INTENT = "普通对话"
ORDINARY_CODE = "000"
SEARCH_SKILL_ID = "mcloud_search_skill"
FUNCTION_SKILL_ID = "function_skill"
GENERIC_SEARCH_INTENT = "搜综合"
GENERIC_FUNCTION_INTENT = "AI超市"
RESOURCE_ROUTE_CODES = {
    "012",
    "013",
    "014",
    "015",
    "016",
    "017",
    "018",
    "020",
    "022",
    "023",
    "040001",
}
RESOURCE_SUFFIX_CODE_MAP = {
    ".docx": "013",
    ".doc": "013",
    ".pdf": "013",
    ".xlsx": "013",
    ".xls": "013",
    ".pptx": "013",
    ".ppt": "013",
    ".png": "012",
    ".jpg": "012",
    ".jpeg": "012",
    ".gif": "012",
    ".bmp": "012",
    ".webp": "012",
    ".mp4": "014",
    ".mov": "014",
    ".avi": "014",
    ".mkv": "014",
    ".mp3": "015",
    ".wav": "015",
    ".flac": "015",
}
BLOCKING_CONFLICT_RISK_FLAGS = {"label_conflict"}
NON_BLOCKING_RISK_PENALTY_FLAGS = {
    "answer_vs_resource",
    "context_unclear",
    "context_inheritance_risk",
    "generic_content_vs_tool",
    "ontology_conflict",
    "search_vs_tool_entry",
    "tool_entry_vs_consultation",
}
ORDINARY_ANSWER_CUES = (
    "是什么",
    "为什么",
    "哪里",
    "怎么看",
    "介绍",
    "推荐",
    "如何",
    "怎么",
    "讲讲",
)
ROUTE_CRITICAL_CUES = (
    "入口",
    "工具",
    "功能",
    "打开",
    "进入",
    "使用",
    "开启",
    "找出来",
    "推荐入口",
)
SPECIFIC_BUSINESS_REASON_CUES = (
    "具体业务",
    "具体工具",
    "专用",
    "更具体",
)
GENERIC_ROUTE_REASON_CUES = (
    "通用入口",
    "通用搜索",
    "普通对话",
    "通用",
    "搜索",
)
STRONG_BUSINESS_ACTIONS = (
    "搜索",
    "搜",
    "查找",
    "找",
    "打开",
    "进入",
    "入口",
    "工具",
    "发送",
    "整理",
    "筛选",
    "生成",
    "创作",
    "编辑",
    "处理",
    "翻译",
    "总结",
    "识别",
)


class IntentRouter:
    def __init__(
        self,
        registry: SkillRegistry,
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_expansion_rounds: int = 1,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        top_skills: int = 3,
        top_intents: int = 2,
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
        self.max_expansion_rounds = max_expansion_rounds
        self.dialogue_history_limit = dialogue_history_limit
        self.top_skills = top_skills
        self.top_intents = top_intents

    @classmethod
    def from_config(
        cls,
        skills_path: str | Path = "skills",
        model_client: StructuredModelClient | None = None,
        evaluator_client: StructuredModelClient | None = None,
        *,
        max_expansion_rounds: int = 1,
        dialogue_history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
        top_skills: int = 3,
        top_intents: int = 2,
    ) -> "IntentRouter":
        return cls(
            SkillRegistry.from_path(skills_path),
            model_client=model_client,
            evaluator_client=evaluator_client,
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
        trace = [] if trace is None else trace
        history = _normalize_dialogue_history(dialogue_history)
        context = context or {}
        _trace(trace, "route_start", query=query, dialogue_turns=len(history.turns))

        contextualized_request = await self._contextualize(query, history)
        resolved_query = contextualized_request.resolved_query or query
        _trace(
            trace,
            "contextualize",
            current_user_query=query,
            resolved_query=resolved_query,
            relation_to_history=contextualized_request.relation_to_history,
            semantic_frame=contextualized_request.semantic_frame.model_dump(mode="json"),
            reason=contextualized_request.reason,
        )

        skill_candidates = await self._generate_skill_candidates(
            resolved_query,
            contextualized_request,
            query,
            context=context,
        )
        _trace(
            trace,
            "skill_candidates",
            candidates=[candidate.model_dump(mode="json") for candidate in skill_candidates],
        )

        intent_candidates = await self._generate_intent_candidates(
            resolved_query,
            skill_candidates,
            contextualized_request,
            query,
            trace=trace,
        )
        if not intent_candidates:
            intent_candidates = [_ordinary_intent_candidate()]
        intent_candidates = _dedupe_intent_candidates(intent_candidates)
        _trace(
            trace,
            "intent_candidates",
            candidates=[candidate.model_dump(mode="json") for candidate in intent_candidates],
        )

        first_candidate = intent_candidates[0]
        expansion_rounds = 0
        invalid_rerank_reason: str | None = None
        try:
            rerank = await self._rerank_candidates(
                resolved_query,
                intent_candidates,
                contextualized_request,
                query,
                expansion_round=expansion_rounds,
            )
        except PydanticValidationError as exc:
            invalid_rerank_reason = f"invalid evaluator output: {exc}"
            rerank = _fallback_rerank(
                intent_candidates,
                contextualized_request=contextualized_request,
                current_user_query=query,
                reason=invalid_rerank_reason,
            )
        try:
            rerank = _validated_rerank(rerank, intent_candidates)
        except ValidationError as exc:
            invalid_rerank_reason = str(exc)
            rerank = _fallback_rerank(
                intent_candidates,
                contextualized_request=contextualized_request,
                current_user_query=query,
                reason=str(exc),
            )
        _trace(
            trace,
            "rerank",
            expansion_round=expansion_rounds,
            decision=rerank.model_dump(mode="json"),
            invalid_reason=invalid_rerank_reason,
        )

        expanded_scopes: list[str] = []
        while (
            rerank.verdict == "expand"
            and expansion_rounds < self.max_expansion_rounds
        ):
            new_candidates = await self._expand_candidates(
                resolved_query,
                skill_candidates,
                intent_candidates,
                rerank,
                contextualized_request,
                query,
                context=context,
                trace=trace,
            )
            new_candidates = _new_route_candidates(intent_candidates, new_candidates)
            _trace(
                trace,
                "expansion",
                expansion_round=expansion_rounds + 1,
                expand_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
                new_candidate_count=len(new_candidates),
            )
            if not new_candidates:
                break

            expansion_rounds += 1
            if rerank.expand_scope:
                expanded_scopes.append(rerank.expand_scope)
            intent_candidates = _dedupe_intent_candidates(
                [*intent_candidates, *new_candidates],
            )
            try:
                rerank = await self._rerank_candidates(
                    resolved_query,
                    intent_candidates,
                    contextualized_request,
                    query,
                    expansion_round=expansion_rounds,
                )
            except PydanticValidationError as exc:
                invalid_rerank_reason = f"invalid evaluator output: {exc}"
                rerank = _fallback_rerank(
                    intent_candidates,
                    contextualized_request=contextualized_request,
                    current_user_query=query,
                    reason=invalid_rerank_reason,
                )
            try:
                rerank = _validated_rerank(rerank, intent_candidates)
                invalid_rerank_reason = None
            except ValidationError as exc:
                invalid_rerank_reason = str(exc)
                rerank = _fallback_rerank(
                    intent_candidates,
                    contextualized_request=contextualized_request,
                    current_user_query=query,
                    reason=str(exc),
                )
            _trace(
                trace,
                "rerank",
                expansion_round=expansion_rounds,
                decision=rerank.model_dump(mode="json"),
                invalid_reason=invalid_rerank_reason,
            )

        final_candidate, diagnostics = _apply_switch_gate(
            first_candidate=first_candidate,
            candidates=intent_candidates,
            rerank=rerank,
            contextualized_request=contextualized_request,
            current_user_query=query,
            expansion_rounds=expansion_rounds,
            invalid_rerank_reason=invalid_rerank_reason,
        )
        _trace(
            trace,
            "switch_gate",
            final_candidate_id=final_candidate.candidate_id,
            diagnostics=diagnostics.model_dump(mode="json"),
        )

        result = _result_from_candidate(
            final_candidate,
            resolved_query=resolved_query,
            context_relation=contextualized_request.relation_to_history,
            alternatives=intent_candidates,
            diagnostics=diagnostics,
            visited_skills=_visited_skills(skill_candidates, intent_candidates),
            loop_count=1 + expansion_rounds,
            correction_scopes=expanded_scopes,
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
        history = _normalize_dialogue_history(dialogue_history)
        context = context or {}
        _trace(
            trace,
            "route_start",
            route_path="first_candidate",
            query=query,
            dialogue_turns=len(history.turns),
        )

        contextualized_request = await self._contextualize(query, history)
        resolved_query = contextualized_request.resolved_query or query
        _trace(
            trace,
            "contextualize",
            route_path="first_candidate",
            current_user_query=query,
            resolved_query=resolved_query,
            relation_to_history=contextualized_request.relation_to_history,
            semantic_frame=contextualized_request.semantic_frame.model_dump(mode="json"),
            reason=contextualized_request.reason,
        )

        skill_candidates = await self._generate_skill_candidates(
            resolved_query,
            contextualized_request,
            query,
            context=context,
        )
        intent_candidates = await self._generate_intent_candidates(
            resolved_query,
            skill_candidates,
            contextualized_request,
            query,
            trace=trace,
        )
        if not intent_candidates:
            intent_candidates = [_ordinary_intent_candidate()]
        intent_candidates = _dedupe_intent_candidates(intent_candidates)
        first_candidate = intent_candidates[0]
        diagnostics = RouteDiagnostics(
            first_candidate_id=first_candidate.candidate_id,
            final_candidate_id=first_candidate.candidate_id,
            evaluator_action="select_top",
            expansion_rounds=0,
            risk_flags=list(first_candidate.risk_flags),
            rerank_reason="first candidate path",
        )
        _trace(
            trace,
            "first_candidate",
            candidate=first_candidate.model_dump(mode="json"),
        )
        result = _result_from_candidate(
            first_candidate,
            resolved_query=resolved_query,
            context_relation=contextualized_request.relation_to_history,
            alternatives=intent_candidates,
            diagnostics=diagnostics,
            visited_skills=_visited_skills(skill_candidates, intent_candidates),
            loop_count=1,
            correction_scopes=[],
        )
        return _traced_result(trace, result)

    async def _generate_skill_candidates(
        self,
        resolved_query: str,
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
        *,
        context: dict[str, Any],
        existing_candidates: list[SkillCandidate] | None = None,
        expansion_scope: str | None = None,
        expansion_hint: str | None = None,
    ) -> list[SkillCandidate]:
        try:
            decision = await self.model_client.structured(
                system_prompt=ROUTER_SYSTEM_PROMPT,
                user_prompt=build_router_prompt(
                    resolved_query,
                    self.registry.cards(),
                    contextualized_request,
                    current_user_query,
                    top_n=self.top_skills,
                    existing_candidates=existing_candidates,
                    expansion_scope=expansion_scope,
                    expansion_hint=expansion_hint,
                    context=context,
                ),
                response_model=SkillCandidateSet,
            )
        except PydanticValidationError:
            decision = SkillCandidateSet(candidates=[])
        candidates = self._normalize_skill_candidates(decision.candidates)
        return self._complete_skill_candidates(
            candidates,
            contextualized_request=contextualized_request,
            current_user_query=current_user_query,
        )

    def _normalize_skill_candidates(
        self,
        candidates: list[SkillCandidate],
    ) -> list[SkillCandidate]:
        normalized: list[SkillCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = candidate.skill_id or ORDINARY_CODE
            if key in seen:
                continue
            if candidate.skill_id is not None and not self.registry.has(
                candidate.skill_id,
                active_only=True,
            ):
                continue
            seen.add(key)
            normalized.append(candidate)
            if len([item for item in normalized if item.skill_id is not None]) >= self.top_skills:
                break

        if not any(candidate.skill_id is None for candidate in normalized):
            normalized.append(_ordinary_skill_candidate())
        if not normalized:
            normalized.append(_ordinary_skill_candidate())
        return normalized

    def _complete_skill_candidates(
        self,
        candidates: list[SkillCandidate],
        *,
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
    ) -> list[SkillCandidate]:
        completed = list(candidates)
        seen = {candidate.skill_id for candidate in completed}

        if (
            _should_complete_search_for_single_entity(
                contextualized_request,
                current_user_query,
            )
            and self.registry.has(SEARCH_SKILL_ID, active_only=True)
            and SEARCH_SKILL_ID not in seen
        ):
            completed.append(
                _synthetic_skill_candidate(
                    SEARCH_SKILL_ID,
                    confidence=0.55,
                    matched_cues=[current_user_query],
                    risk_flags=[],
                    reason="single entity resource query keeps generic search available",
                ),
            )
            seen.add(SEARCH_SKILL_ID)

        if not _has_route_critical_query(
            contextualized_request,
            current_user_query,
        ):
            if not any(candidate.skill_id is None for candidate in completed):
                completed.append(
                    _ordinary_skill_candidate(
                        confidence=_ordinary_confidence(contextualized_request),
                    ),
                )
            else:
                _boost_ordinary_candidate(completed, contextualized_request)
            return completed

        specific_skill_id = _specific_business_skill_for_query(
            self.registry,
            contextualized_request,
            current_user_query,
        )
        if specific_skill_id and specific_skill_id not in seen:
            completed.append(
                _synthetic_skill_candidate(
                    specific_skill_id,
                    confidence=0.62,
                    matched_cues=_route_critical_matched_cues(
                        contextualized_request,
                        current_user_query,
                    ),
                    risk_flags=[],
                    reason="route-critical cue matched a specific business skill",
                ),
            )
            seen.add(specific_skill_id)

        if (
            self.registry.has(FUNCTION_SKILL_ID, active_only=True)
            and FUNCTION_SKILL_ID not in seen
        ):
            completed.append(
                _synthetic_skill_candidate(
                    FUNCTION_SKILL_ID,
                    confidence=0.45,
                    matched_cues=_route_critical_matched_cues(
                        contextualized_request,
                        current_user_query,
                    ),
                    risk_flags=["generic_entry"],
                    reason="route-critical cue keeps generic function entry available",
                ),
            )

        if not any(candidate.skill_id is None for candidate in completed):
            completed.append(
                _ordinary_skill_candidate(
                    confidence=_ordinary_confidence(contextualized_request),
                ),
            )
        else:
            _boost_ordinary_candidate(completed, contextualized_request)

        return completed

    async def _generate_intent_candidates(
        self,
        resolved_query: str,
        skill_candidates: list[SkillCandidate],
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
        *,
        trace: list[dict[str, Any]] | None = None,
        expansion_scope: str | None = None,
        expansion_hint: str | None = None,
    ) -> list[IntentCandidate]:
        intent_candidates: list[IntentCandidate] = []
        used_ids: set[str] = set()
        for skill_candidate in skill_candidates:
            if skill_candidate.skill_id is None:
                candidate = _ordinary_intent_candidate(skill_candidate)
                candidate.candidate_id = _unique_candidate_id(candidate.candidate_id, used_ids)
                used_ids.add(candidate.candidate_id)
                intent_candidates.append(candidate)
                continue

            skill = self.registry.get(skill_candidate.skill_id)
            try:
                decision = await self.model_client.structured(
                    system_prompt=INTENT_SYSTEM_PROMPT,
                    user_prompt=build_intent_prompt(
                        resolved_query,
                        skill,
                        contextualized_request,
                        current_user_query,
                        skill_candidate=skill_candidate,
                        top_m=self.top_intents,
                        existing_candidates=intent_candidates,
                        expansion_scope=expansion_scope,
                        expansion_hint=expansion_hint,
                        routing_context=self.registry.routing_context(skill.id),
                    ),
                    response_model=IntentCandidateSet,
                )
            except PydanticValidationError:
                _trace(
                    trace,
                    "intent_candidate_set_error",
                    skill_id=skill.id,
                    reason="invalid structured intent candidate set",
                )
                continue
            _trace(
                trace,
                "intent_candidate_set",
                skill_id=skill.id,
                candidates=[
                    candidate.model_dump(mode="json")
                    for candidate in decision.candidates
                ],
            )
            for index, raw_candidate in enumerate(decision.candidates):
                candidate = self._normalize_intent_candidate(
                    raw_candidate,
                    skill_candidate=skill_candidate,
                    index=index,
                    used_ids=used_ids,
                )
                if candidate is None:
                    continue
                used_ids.add(candidate.candidate_id)
                intent_candidates.append(candidate)
        return intent_candidates

    def _normalize_intent_candidate(
        self,
        candidate: IntentCandidate,
        *,
        skill_candidate: SkillCandidate,
        index: int,
        used_ids: set[str],
    ) -> IntentCandidate | None:
        skill_id = skill_candidate.skill_id
        if skill_id is None or not self.registry.has(skill_id, active_only=True):
            return None
        skill = self.registry.get(skill_id)
        candidate.skill_id = skill_id
        candidate.skill_name = skill.name
        candidate.confidence = min(
            _clamp_confidence(skill_candidate.confidence),
            _clamp_confidence(candidate.confidence),
        )
        candidate.matched_cues = _dedupe_strings(
            [*skill_candidate.matched_cues, *candidate.matched_cues],
        )
        candidate.risk_flags = _dedupe_strings(
            [*skill_candidate.risk_flags, *candidate.risk_flags],
        )
        candidate.candidate_id = _unique_candidate_id(
            _canonical_candidate_id(candidate, index),
            used_ids,
        )
        try:
            return validate_intent_candidate(self.registry, candidate)
        except ValidationError:
            return None

    async def _rerank_candidates(
        self,
        resolved_query: str,
        candidates: list[IntentCandidate],
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
        *,
        expansion_round: int,
    ) -> RerankDecision:
        return await self.evaluator_client.structured(
            system_prompt=EVALUATOR_SYSTEM_PROMPT,
            user_prompt=build_evaluator_prompt(
                resolved_query,
                self.registry.cards(),
                candidates,
                contextualized_request=contextualized_request,
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
        contextualized_request: ContextualizedRequest,
        current_user_query: str,
        *,
        context: dict[str, Any],
        trace: list[dict[str, Any]] | None,
    ) -> list[IntentCandidate]:
        if rerank.expand_scope == "skill_recall_gap":
            expanded_skills = await self._generate_skill_candidates(
                resolved_query,
                contextualized_request,
                current_user_query,
                context=context,
                existing_candidates=skill_candidates,
                expansion_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
            )
            new_skill_candidates = _new_skill_candidates(skill_candidates, expanded_skills)
            skill_candidates.extend(new_skill_candidates)
            _trace(
                trace,
                "skill_expansion_candidates",
                candidates=[
                    candidate.model_dump(mode="json")
                    for candidate in new_skill_candidates
                ],
            )
            return await self._generate_intent_candidates(
                resolved_query,
                new_skill_candidates,
                contextualized_request,
                current_user_query,
                trace=trace,
                expansion_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
            )

        if rerank.expand_scope == "intent_recall_gap":
            skill_candidates_by_id = {
                candidate.skill_id: candidate
                for candidate in skill_candidates
                if candidate.skill_id is not None
            }
            target_skill_ids = _target_skill_ids_for_intent_expansion(
                rerank,
                intent_candidates,
            )
            target_skill_candidates = [
                skill_candidates_by_id[skill_id]
                for skill_id in target_skill_ids
                if skill_id in skill_candidates_by_id
            ]
            return await self._generate_intent_candidates(
                resolved_query,
                target_skill_candidates,
                contextualized_request,
                current_user_query,
                trace=trace,
                expansion_scope=rerank.expand_scope,
                expansion_hint=rerank.expansion_hint,
            )

        return []

    async def _contextualize(
        self,
        query: str,
        dialogue_history: DialogueHistory,
    ) -> ContextualizedRequest:
        if not dialogue_history.turns:
            return ContextualizedRequest(
                resolved_query=query,
                relation_to_history="new_request",
                semantic_frame=SemanticFrame(),
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


def _ordinary_skill_candidate(confidence: float = 0.15) -> SkillCandidate:
    return SkillCandidate(
        candidate_id="skill:ordinary_dialogue",
        skill_id=None,
        intent_domain=ORDINARY_INTENT,
        confidence=confidence,
        matched_cues=[],
        risk_flags=[],
        reason="ordinary dialogue fallback candidate",
    )


def _ordinary_confidence(contextualized_request: ContextualizedRequest) -> float:
    expected = contextualized_request.semantic_frame.expected_result_type
    if expected == "ordinary_answer":
        return 0.7
    if expected == "content_generation":
        return 0.55
    return 0.15


def _boost_ordinary_candidate(
    candidates: list[SkillCandidate],
    contextualized_request: ContextualizedRequest,
) -> None:
    confidence = _ordinary_confidence(contextualized_request)
    for candidate in candidates:
        if candidate.skill_id is None and candidate.confidence < confidence:
            candidate.confidence = confidence
            return


def _synthetic_skill_candidate(
    skill_id: str,
    *,
    confidence: float,
    matched_cues: list[str],
    risk_flags: list[str],
    reason: str,
) -> SkillCandidate:
    return SkillCandidate(
        candidate_id=f"skill:{skill_id}:route_critical",
        skill_id=skill_id,
        intent_domain=skill_id,
        confidence=confidence,
        matched_cues=matched_cues,
        risk_flags=risk_flags,
        reason=reason,
    )


def _ordinary_intent_candidate(
    skill_candidate: SkillCandidate | None = None,
) -> IntentCandidate:
    return IntentCandidate(
        candidate_id=ORDINARY_CANDIDATE_ID,
        skill_id=None,
        skill_name=None,
        intent=ORDINARY_INTENT,
        code=ORDINARY_CODE,
        params={},
        confidence=(
            _clamp_confidence(skill_candidate.confidence)
            if skill_candidate is not None
            else 0.15
        ),
        matched_cues=list(skill_candidate.matched_cues) if skill_candidate else [],
        risk_flags=list(skill_candidate.risk_flags) if skill_candidate else [],
        reason=skill_candidate.reason if skill_candidate else "ordinary dialogue fallback",
    )


def _validated_rerank(
    rerank: RerankDecision,
    candidates: list[IntentCandidate],
) -> RerankDecision:
    if not rerank.ranking:
        rerank.ranking = [
            CandidateScore(
                candidate_id=candidate.candidate_id,
                score=_clamp_confidence(candidate.confidence),
                reason="defaulted from selector confidence",
            )
            for candidate in candidates
        ]
    ranked_ids = {score.candidate_id for score in rerank.ranking}
    rerank.ranking.extend(
        CandidateScore(
            candidate_id=candidate.candidate_id,
            score=_clamp_confidence(candidate.confidence),
            reason="defaulted missing ranking from selector confidence",
        )
        for candidate in candidates
        if candidate.candidate_id not in ranked_ids
    )
    return validate_rerank_decision(
        rerank,
        {candidate.candidate_id for candidate in candidates},
    )


def _fallback_rerank(
    candidates: list[IntentCandidate],
    *,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
    reason: str,
) -> RerankDecision:
    ordered = sorted(
        candidates,
        key=lambda candidate: _combined_score(
            candidate,
            evaluator_score=0.0,
            contextualized_request=contextualized_request,
            current_user_query=current_user_query,
        ),
        reverse=True,
    )
    selected = ordered[0]
    return RerankDecision(
        verdict="select",
        selected_candidate_id=selected.candidate_id,
        confidence=0.0,
        ranking=[
            CandidateScore(
                candidate_id=candidate.candidate_id,
                score=0.0,
                reason="fallback without evaluator score",
            )
            for candidate in ordered
        ],
        reason=reason,
    )


def _apply_switch_gate(
    *,
    first_candidate: IntentCandidate,
    candidates: list[IntentCandidate],
    rerank: RerankDecision,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
    expansion_rounds: int,
    invalid_rerank_reason: str | None,
) -> tuple[IntentCandidate, RouteDiagnostics]:
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    score_by_id = {score.candidate_id: score.score for score in rerank.ranking}
    selected = candidate_by_id.get(rerank.selected_candidate_id or "")

    if invalid_rerank_reason:
        fallback_candidate = _best_combined_candidate(
            candidates,
            rerank=rerank,
            contextualized_request=contextualized_request,
            current_user_query=current_user_query,
        )
        return fallback_candidate, RouteDiagnostics(
            first_candidate_id=first_candidate.candidate_id,
            final_candidate_id=fallback_candidate.candidate_id,
            evaluator_action="fallback",
            expansion_rounds=expansion_rounds,
            risk_flags=_dedupe_strings(fallback_candidate.risk_flags),
            rerank_reason=invalid_rerank_reason,
            fallback_reason=invalid_rerank_reason,
        )

    if rerank.verdict == "expand":
        fallback_candidate = _best_combined_candidate(
            candidates,
            rerank=rerank,
            contextualized_request=contextualized_request,
            current_user_query=current_user_query,
        )
        return fallback_candidate, RouteDiagnostics(
            first_candidate_id=first_candidate.candidate_id,
            final_candidate_id=fallback_candidate.candidate_id,
            evaluator_action="expand",
            expansion_rounds=expansion_rounds,
            risk_flags=_dedupe_strings(
                [*first_candidate.risk_flags, *fallback_candidate.risk_flags],
            ),
            rerank_reason=rerank.reason,
        )

    selected = selected or _best_combined_candidate(
        candidates,
        rerank=rerank,
        contextualized_request=contextualized_request,
        current_user_query=current_user_query,
    )
    if _should_block_selected_switch(
        first_candidate=first_candidate,
        selected_candidate=selected,
        contextualized_request=contextualized_request,
        current_user_query=current_user_query,
    ):
        risk_flags = _dedupe_strings([*first_candidate.risk_flags, *selected.risk_flags])
        return first_candidate, RouteDiagnostics(
            first_candidate_id=first_candidate.candidate_id,
            final_candidate_id=first_candidate.candidate_id,
            evaluator_action="switch_blocked",
            expansion_rounds=expansion_rounds,
            risk_flags=risk_flags,
            rerank_reason=(
                f"blocked evaluator switch to {selected.candidate_id}; "
                f"blocking_risk={sorted(set(selected.risk_flags) & BLOCKING_CONFLICT_RISK_FLAGS)}. "
                f"{rerank.reason}"
            ).strip(),
        )

    final_candidate = _exact_resource_rescue(
        selected,
        candidates=candidates,
        contextualized_request=contextualized_request,
        current_user_query=current_user_query,
    )
    if final_candidate.candidate_id == first_candidate.candidate_id:
        return final_candidate, RouteDiagnostics(
            first_candidate_id=first_candidate.candidate_id,
            final_candidate_id=final_candidate.candidate_id,
            evaluator_action="select_top",
            expansion_rounds=expansion_rounds,
            risk_flags=_dedupe_strings(final_candidate.risk_flags),
            rerank_reason=rerank.reason,
        )

    final_score = _combined_score(
        final_candidate,
        evaluator_score=float(score_by_id.get(final_candidate.candidate_id, 0.0)),
        contextualized_request=contextualized_request,
        current_user_query=current_user_query,
    )
    first_score = _combined_score(
        first_candidate,
        evaluator_score=float(score_by_id.get(first_candidate.candidate_id, 0.0)),
        contextualized_request=contextualized_request,
        current_user_query=current_user_query,
    )
    margin = final_score - first_score
    risk_flags = _dedupe_strings([*first_candidate.risk_flags, *final_candidate.risk_flags])

    switch_allowed = (
        final_candidate.candidate_id == selected.candidate_id
        or margin >= _switch_margin_threshold(
            first_candidate=first_candidate,
            selected_candidate=final_candidate,
            rerank=rerank,
            contextualized_request=contextualized_request,
            current_user_query=current_user_query,
        )
        and not (set(final_candidate.risk_flags) & BLOCKING_CONFLICT_RISK_FLAGS)
    )
    if switch_allowed:
        return final_candidate, RouteDiagnostics(
            first_candidate_id=first_candidate.candidate_id,
            final_candidate_id=final_candidate.candidate_id,
            evaluator_action="switch",
            expansion_rounds=expansion_rounds,
            risk_flags=risk_flags,
            rerank_reason=(
                f"{rerank.reason} final_score={final_score:.3f}, "
                f"first_score={first_score:.3f}, evaluator_selected="
                f"{selected.candidate_id}"
                f"{'; resource_rescue' if final_candidate.candidate_id != selected.candidate_id else ''}"
            ).strip(),
        )

    return first_candidate, RouteDiagnostics(
        first_candidate_id=first_candidate.candidate_id,
        final_candidate_id=first_candidate.candidate_id,
        evaluator_action="switch_blocked",
        expansion_rounds=expansion_rounds,
        risk_flags=risk_flags,
        rerank_reason=(
            f"blocked score switch to {final_candidate.candidate_id}; "
            f"final_score={final_score:.3f}, first_score={first_score:.3f}, "
            f"margin={margin:.3f}, "
            f"evaluator_selected={selected.candidate_id}. {rerank.reason}"
        ).strip(),
    )


def _should_block_selected_switch(
    *,
    first_candidate: IntentCandidate,
    selected_candidate: IntentCandidate,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> bool:
    if selected_candidate.candidate_id == first_candidate.candidate_id:
        return False
    if set(selected_candidate.risk_flags) & BLOCKING_CONFLICT_RISK_FLAGS:
        return True

    expected = contextualized_request.semantic_frame.expected_result_type
    first_confidence = _clamp_confidence(first_candidate.confidence)
    selected_confidence = _clamp_confidence(selected_candidate.confidence)
    confidence_gap = first_confidence - selected_confidence
    selected_risks = set(selected_candidate.risk_flags)

    if (
        expected == "ordinary_answer"
        and first_candidate.code == ORDINARY_CODE
        and selected_candidate.code != ORDINARY_CODE
    ):
        return True

    if selected_risks & NON_BLOCKING_RISK_PENALTY_FLAGS and confidence_gap >= 0.15:
        return True

    if (
        expected == "resource"
        and _is_resource_route(first_candidate)
        and not _is_resource_route(selected_candidate)
        and confidence_gap >= 0.08
    ):
        return True

    if (
        expected == "resource"
        and _is_resource_route(first_candidate)
        and _is_resource_route(selected_candidate)
        and selected_risks
        and confidence_gap >= 0.12
    ):
        return True

    text = _semantic_evidence_text(contextualized_request, current_user_query)
    if (
        expected == "resource"
        and _explicit_resource_code(text) == first_candidate.code
        and not _is_resource_route(selected_candidate)
    ):
        return True

    return False


def _exact_resource_rescue(
    anchor_candidate: IntentCandidate,
    *,
    candidates: list[IntentCandidate],
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> IntentCandidate:
    if contextualized_request.semantic_frame.expected_result_type != "resource":
        return anchor_candidate

    text = _semantic_evidence_text(contextualized_request, current_user_query)
    target_code = _explicit_resource_code(text)
    if not target_code or anchor_candidate.code == target_code:
        return anchor_candidate

    if _is_resource_route(anchor_candidate) and not _is_generic_route(anchor_candidate):
        return anchor_candidate

    rescue_candidates = [
        candidate
        for candidate in candidates
        if candidate.skill_id == SEARCH_SKILL_ID
        and candidate.code == target_code
        and not (set(candidate.risk_flags) & BLOCKING_CONFLICT_RISK_FLAGS)
    ]
    if not rescue_candidates:
        return anchor_candidate

    return max(rescue_candidates, key=lambda candidate: candidate.confidence)


def _explicit_resource_code(text: str) -> str | None:
    compact_text = _compact_text(text)
    lower_text = text.lower()
    for suffix, code in RESOURCE_SUFFIX_CODE_MAP.items():
        if suffix in lower_text or suffix.strip(".") + "格式" in compact_text:
            return code
    return None


def _best_combined_candidate(
    candidates: list[IntentCandidate],
    *,
    rerank: RerankDecision,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> IntentCandidate:
    score_by_id = {score.candidate_id: score.score for score in rerank.ranking}
    return max(
        candidates,
        key=lambda candidate: _combined_score(
            candidate,
            evaluator_score=float(score_by_id.get(candidate.candidate_id, 0.0)),
            contextualized_request=contextualized_request,
            current_user_query=current_user_query,
        ),
    )


def _combined_score(
    candidate: IntentCandidate,
    *,
    evaluator_score: float,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> float:
    cue_score = _cue_coverage_score(
        candidate,
        contextualized_request=contextualized_request,
        current_user_query=current_user_query,
    )
    type_alignment = _type_alignment_score(candidate, contextualized_request)
    return (
        0.45 * _clamp_confidence(evaluator_score)
        + 0.30 * _clamp_confidence(candidate.confidence)
        + 0.20 * type_alignment
        + 0.05 * cue_score
        - _candidate_penalty(candidate, contextualized_request, current_user_query)
    )


def _type_alignment_score(
    candidate: IntentCandidate,
    contextualized_request: ContextualizedRequest,
) -> float:
    expected = contextualized_request.semantic_frame.expected_result_type
    if expected == "ordinary_answer":
        return 1.0 if candidate.code == ORDINARY_CODE else 0.0
    if expected == "resource":
        if _is_resource_route(candidate):
            if _is_generic_route(candidate):
                return 0.85
            return 1.0
        return 0.35 if candidate.code == ORDINARY_CODE else 0.1
    if expected == "function_entry":
        if candidate.skill_id and candidate.skill_id not in {SEARCH_SKILL_ID, FUNCTION_SKILL_ID}:
            return 1.0
        if candidate.skill_id == FUNCTION_SKILL_ID:
            return 0.75
        return 0.25 if candidate.code == ORDINARY_CODE else 0.1
    if expected in {"content_processing", "mail_action", "social_share"}:
        if candidate.skill_id and candidate.skill_id not in {SEARCH_SKILL_ID, FUNCTION_SKILL_ID}:
            return 1.0
        return 0.35 if candidate.code == ORDINARY_CODE else 0.1
    if expected == "content_generation":
        if candidate.code == ORDINARY_CODE:
            return 0.85
        if candidate.skill_id and candidate.skill_id not in {SEARCH_SKILL_ID, FUNCTION_SKILL_ID}:
            return 0.65
        return 0.1
    if candidate.skill_id and candidate.skill_id not in {SEARCH_SKILL_ID, FUNCTION_SKILL_ID}:
        return 0.75
    if candidate.skill_id in {SEARCH_SKILL_ID, FUNCTION_SKILL_ID}:
        return 0.5
    return 0.5


def _candidate_penalty(
    candidate: IntentCandidate,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> float:
    penalty = 0.0
    text = f"{current_user_query} {contextualized_request.resolved_query}".lower()
    expected = contextualized_request.semantic_frame.expected_result_type
    if (
        candidate.code == ORDINARY_CODE
        and expected
        in {
            "resource",
            "function_entry",
            "content_processing",
            "mail_action",
            "social_share",
        }
        and _has_strong_business_action(text)
    ):
        penalty += 0.2
    if expected == "ordinary_answer" and candidate.code != ORDINARY_CODE:
        penalty += 0.45
    if expected == "resource" and not _is_resource_route(candidate) and candidate.code != ORDINARY_CODE:
        penalty += 0.25
    if expected == "function_entry" and candidate.skill_id == SEARCH_SKILL_ID:
        penalty += 0.25
    if candidate.skill_id == "function_skill" and any(
        flag in candidate.risk_flags for flag in ("specific_tool_available", "generic_entry")
    ):
        penalty += 0.1
    if "current_query_conflict" in candidate.risk_flags:
        penalty += 0.3
    if set(candidate.risk_flags) & BLOCKING_CONFLICT_RISK_FLAGS:
        penalty += 0.2
    if set(candidate.risk_flags) & NON_BLOCKING_RISK_PENALTY_FLAGS:
        penalty += 0.05
    return penalty


def _has_strong_business_action(text: str) -> bool:
    return any(action.lower() in text for action in STRONG_BUSINESS_ACTIONS)


def _should_complete_search_for_single_entity(
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> bool:
    frame = contextualized_request.semantic_frame
    if frame.expected_result_type == "ordinary_answer":
        return False

    text = current_user_query.strip()
    evidence = _semantic_evidence_text(contextualized_request, current_user_query)
    if _has_strong_business_action(evidence):
        return False
    if any(cue in evidence for cue in ORDINARY_ANSWER_CUES):
        return False
    if any(mark in text for mark in "？?，,。；;！! "):
        return False

    compact = _compact_text(text)
    if compact.endswith("呢"):
        compact = compact[:-1]
    return 2 <= len(compact) <= 20


def _has_route_critical_query(
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> bool:
    evidence = _semantic_evidence_text(contextualized_request, current_user_query)
    return any(cue.lower() in evidence for cue in ROUTE_CRITICAL_CUES)


def _has_route_critical_cue(
    candidate: IntentCandidate,
    *,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> bool:
    evidence = _semantic_evidence_text(contextualized_request, current_user_query)
    return any(cue and cue.lower() in evidence for cue in candidate.matched_cues)


def _switch_margin_threshold(
    *,
    first_candidate: IntentCandidate,
    selected_candidate: IntentCandidate,
    rerank: RerankDecision,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> float:
    if (
        _has_route_critical_query(contextualized_request, current_user_query)
        and _is_more_specific_business_route(selected_candidate, first_candidate)
        and _reason_prefers_specific_business(rerank.reason)
    ):
        return 0.08
    return 0.15


def _is_more_specific_business_route(
    selected_candidate: IntentCandidate,
    first_candidate: IntentCandidate,
) -> bool:
    return (
        selected_candidate.skill_id is not None
        and not _is_generic_route(selected_candidate)
        and _is_generic_route(first_candidate)
    )


def _is_generic_route(candidate: IntentCandidate) -> bool:
    if candidate.skill_id is None or candidate.code == ORDINARY_CODE:
        return True
    if candidate.skill_id == SEARCH_SKILL_ID and candidate.intent == GENERIC_SEARCH_INTENT:
        return True
    return (
        candidate.skill_id == FUNCTION_SKILL_ID
        and candidate.intent == GENERIC_FUNCTION_INTENT
    )


def _is_resource_route(candidate: IntentCandidate) -> bool:
    return candidate.code in RESOURCE_ROUTE_CODES


def _reason_prefers_specific_business(reason: str) -> bool:
    return any(cue in reason for cue in SPECIFIC_BUSINESS_REASON_CUES) and any(
        cue in reason for cue in GENERIC_ROUTE_REASON_CUES
    )


def _cue_coverage_score(
    candidate: IntentCandidate,
    *,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> float:
    cues = [cue for cue in candidate.matched_cues if cue]
    if not cues:
        return 0.0
    evidence = _semantic_evidence_text(contextualized_request, current_user_query)
    hits = sum(1 for cue in cues if cue.lower() in evidence)
    return hits / len(cues)


def _semantic_evidence_text(
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> str:
    frame = contextualized_request.semantic_frame
    parts = [
        current_user_query,
        contextualized_request.resolved_query,
        frame.action or "",
        frame.expected_result_type,
        *frame.object_types,
        *frame.subjects,
        *frame.qualifiers,
        *frame.explicit_overrides,
    ]
    return " ".join(parts).lower()


def _specific_business_skill_for_query(
    registry: SkillRegistry,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> str | None:
    evidence = _compact_text(
        _semantic_evidence_text(contextualized_request, current_user_query),
    )
    for card in registry.cards():
        if card.id in {SEARCH_SKILL_ID, FUNCTION_SKILL_ID}:
            continue
        if _skill_card_matches_evidence(card, evidence):
            return card.id
    return None


def _skill_card_matches_evidence(card: Any, evidence: str) -> bool:
    terms = [
        card.name,
        card.name.replace("工具", ""),
        card.name.replace("功能", ""),
        card.description,
        *card.aliases,
        *card.scope,
    ]
    for term in terms:
        compact_term = _compact_text(str(term))
        if len(compact_term) >= 2 and compact_term in evidence:
            return True
    return False


def _route_critical_matched_cues(
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> list[str]:
    evidence = _semantic_evidence_text(contextualized_request, current_user_query)
    return [
        cue
        for cue in ROUTE_CRITICAL_CUES
        if cue.lower() in evidence
    ]


def _compact_text(text: str) -> str:
    return "".join(text.lower().split())


def _result_from_candidate(
    candidate: IntentCandidate,
    *,
    resolved_query: str,
    context_relation: str,
    alternatives: list[IntentCandidate],
    diagnostics: RouteDiagnostics,
    visited_skills: list[str],
    loop_count: int,
    correction_scopes: list[str],
) -> RouteResult:
    skill = (
        SkillRef(id=candidate.skill_id, name=candidate.skill_name or candidate.skill_id)
        if candidate.skill_id
        else None
    )
    return RouteResult(
        status="matched",
        skill=skill,
        intent=candidate.intent,
        code=candidate.code,
        params=candidate.params,
        confidence=_clamp_confidence(candidate.confidence),
        reason=candidate.reason,
        visited_skills=visited_skills,
        loop_count=loop_count,
        correction_scopes=correction_scopes,
        resolved_query=resolved_query,
        context_relation=context_relation,
        alternatives=[
            alternative
            for alternative in alternatives
            if alternative.candidate_id != candidate.candidate_id
        ],
        diagnostics=diagnostics,
    )


def _normalize_dialogue_history(
    dialogue_history: DialogueHistory | dict[str, Any] | None,
) -> DialogueHistory:
    if dialogue_history is None:
        return DialogueHistory()
    if isinstance(dialogue_history, DialogueHistory):
        return dialogue_history
    return DialogueHistory.model_validate(dialogue_history)


def _dedupe_intent_candidates(
    candidates: list[IntentCandidate],
) -> list[IntentCandidate]:
    deduped: list[IntentCandidate] = []
    seen: dict[tuple[str | None, str, str], int] = {}
    used_ids: set[str] = set()
    for candidate in candidates:
        key = _route_key(candidate)
        if key in seen:
            existing = deduped[seen[key]]
            if candidate.confidence > existing.confidence:
                deduped[seen[key]] = candidate
            existing.alternatives = _dedupe_strings(
                [*existing.alternatives, *candidate.alternatives],
            )
            continue
        candidate.candidate_id = _unique_candidate_id(candidate.candidate_id, used_ids)
        used_ids.add(candidate.candidate_id)
        seen[key] = len(deduped)
        deduped.append(candidate)
    return deduped


def _new_route_candidates(
    existing: list[IntentCandidate],
    candidates: list[IntentCandidate],
) -> list[IntentCandidate]:
    existing_keys = {_route_key(candidate) for candidate in existing}
    return [
        candidate
        for candidate in candidates
        if _route_key(candidate) not in existing_keys
    ]


def _new_skill_candidates(
    existing: list[SkillCandidate],
    candidates: list[SkillCandidate],
) -> list[SkillCandidate]:
    existing_keys = {candidate.skill_id for candidate in existing}
    return [
        candidate
        for candidate in candidates
        if candidate.skill_id not in existing_keys
    ]


def _target_skill_ids_for_intent_expansion(
    rerank: RerankDecision,
    candidates: list[IntentCandidate],
) -> list[str]:
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    ordered_ids = [score.candidate_id for score in rerank.ranking]
    if rerank.selected_candidate_id:
        ordered_ids.insert(0, rerank.selected_candidate_id)
    skill_ids: list[str] = []
    for candidate_id in ordered_ids:
        candidate = candidate_by_id.get(candidate_id)
        if not candidate or not candidate.skill_id:
            continue
        if candidate.skill_id not in skill_ids:
            skill_ids.append(candidate.skill_id)
    if skill_ids:
        return skill_ids[:2]
    return _dedupe_strings(
        [candidate.skill_id for candidate in candidates if candidate.skill_id],
    )[:2]


def _route_key(candidate: IntentCandidate) -> tuple[str | None, str, str]:
    return (candidate.skill_id, candidate.intent, candidate.code)


def _canonical_candidate_id(candidate: IntentCandidate, index: int) -> str:
    skill = candidate.skill_id or "ordinary_dialogue"
    return f"{skill}:{candidate.intent}:{candidate.code}:{index + 1}"


def _unique_candidate_id(candidate_id: str, used_ids: set[str]) -> str:
    if candidate_id not in used_ids:
        return candidate_id
    index = 2
    while f"{candidate_id}:{index}" in used_ids:
        index += 1
    return f"{candidate_id}:{index}"


def _visited_skills(
    skill_candidates: list[SkillCandidate],
    intent_candidates: list[IntentCandidate],
) -> list[str]:
    return _dedupe_strings(
        [
            *[
                candidate.skill_id
                for candidate in skill_candidates
                if candidate.skill_id is not None
            ],
            *[
                candidate.skill_id
                for candidate in intent_candidates
                if candidate.skill_id is not None
            ],
        ],
    )


def _clamp_confidence(value: float | int | None) -> float:
    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _dedupe_strings(values: list[str | None]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped


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
    _trace(trace, "result", result=result.model_dump(mode="json"))
    return result
