import pytest
from pydantic import ValidationError as PydanticValidationError

from intent_router.skills import SkillRegistry
from intent_router.types import (
    CandidateScore,
    ContextualizedRequest,
    IntentCandidate,
    IntentCandidateSet,
    RerankDecision,
    SkillCandidate,
    SkillCandidateSet,
)
from intent_router.validation import (
    ValidationError,
    validate_intent_candidate,
    validate_rerank_decision,
)


def test_validate_rejects_unknown_intent() -> None:
    registry = SkillRegistry.from_path("skills")
    candidate = IntentCandidate(
        candidate_id="bad",
        skill_id="mcloud_search_skill",
        intent="不存在",
        code="012",
    )

    with pytest.raises(ValidationError):
        validate_intent_candidate(registry, candidate)


def test_validate_rejects_wrong_code() -> None:
    registry = SkillRegistry.from_path("skills")
    candidate = IntentCandidate(
        candidate_id="bad",
        skill_id="mcloud_search_skill",
        intent="搜图片",
        code="999",
    )

    with pytest.raises(ValidationError):
        validate_intent_candidate(registry, candidate)


def test_validate_accepts_ordinary_dialogue_candidate() -> None:
    registry = SkillRegistry.from_path("skills")
    candidate = IntentCandidate(
        candidate_id="ordinary",
        skill_id=None,
        intent="普通对话",
        code="000",
    )

    assert validate_intent_candidate(registry, candidate) is candidate


def test_validate_rerank_rejects_candidate_outside_set() -> None:
    decision = RerankDecision(
        verdict="select",
        selected_candidate_id="missing",
        confidence=0.9,
        ranking=[CandidateScore(candidate_id="known", score=0.8)],
    )

    with pytest.raises(ValidationError):
        validate_rerank_decision(decision, {"known"})


def test_validate_rerank_rejects_unknown_ranking_id() -> None:
    decision = RerankDecision(
        verdict="expand",
        confidence=0.9,
        ranking=[CandidateScore(candidate_id="missing", score=0.8)],
        expand_scope="intent_recall_gap",
    )

    with pytest.raises(ValidationError):
        validate_rerank_decision(decision, {"known"})


def test_contextualized_request_normalizes_legacy_status_and_history_turns() -> None:
    request = ContextualizedRequest.model_validate(
        {
            "status": "continuation",
            "resolved_query": "搜索AI助手测评报告PPT",
            "used_history_turns": [1, 2],
        },
    )

    assert request.relation_to_history == "continuation"
    assert request.semantic_frame.inherited_turns == [1, 2]


def test_structured_output_models_reject_unknown_fields() -> None:
    with pytest.raises(PydanticValidationError):
        SkillCandidate.model_validate(
            {
                "candidate_id": "skill:mcloud_search_skill",
                "skill_id": "mcloud_search_skill",
                "intent_domain": "搜索",
                "unexpected": "extra",
            },
        )


def test_candidate_sets_accept_known_extra_fields_and_json_string_lists() -> None:
    skill_set = SkillCandidateSet.model_validate(
        {
            "candidates": (
                '[{"candidate_id":"skill:mcloud_search_skill",'
                '"skill_id":"mcloud_search_skill",'
                '"intent_domain":"搜索",'
                '"risk_flags_detail":{"reason":"debug only"},'
                '"risk_flags_note":"debug only"}]'
            ),
            "analysis": "debug only",
        },
    )
    intent_set = IntentCandidateSet.model_validate(
        {
            "candidates": (
                '[{"candidate_id":"mcloud_search_skill:搜综合:018:1",'
                '"skill_id":"mcloud_search_skill",'
                '"intent":"搜综合",'
                '"code":"018",'
                '"risk_flags_detail":{"reason":"debug only"}}]'
            ),
            "notes": "debug only",
        },
    )

    assert skill_set.candidates[0].skill_id == "mcloud_search_skill"
    assert intent_set.candidates[0].code == "018"


def test_rerank_decision_accepts_known_extra_fields_and_json_string_ranking() -> None:
    decision = RerankDecision.model_validate(
        {
            "verdict": "select",
            "selected_candidate_id": "known",
            "confidence": 0.9,
            "ranking": '[{"candidate_id":"known","score":0.8}]',
            "risk_flags_detail": {"debug": True},
        },
    )

    assert decision.ranking == [CandidateScore(candidate_id="known", score=0.8)]
