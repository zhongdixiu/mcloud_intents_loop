import pytest

from intent_router.skills import SkillRegistry
from intent_router.types import EvaluationDecision, IntentDecision
from intent_router.validation import (
    ValidationError,
    validate_evaluation_decision,
    validate_intent_decision,
)


def test_validate_rejects_unknown_intent() -> None:
    registry = SkillRegistry.from_path("skills")
    skill = registry.get("mcloud_search_skill")
    decision = IntentDecision(
        status="matched",
        intent="不存在",
        code="012",
        params={},
    )

    with pytest.raises(ValidationError):
        validate_intent_decision(skill, decision)


def test_validate_rejects_wrong_code() -> None:
    registry = SkillRegistry.from_path("skills")
    skill = registry.get("mcloud_search_skill")
    decision = IntentDecision(
        status="matched",
        intent="搜图片",
        code="999",
        params={},
    )

    with pytest.raises(ValidationError):
        validate_intent_decision(skill, decision)


def test_validate_rejects_unknown_param() -> None:
    registry = SkillRegistry.from_path("skills")
    skill = registry.get("mcloud_search_skill")
    decision = IntentDecision(
        status="matched",
        intent="搜图片",
        code="012",
        params={"suffixList": ["jpg"]},
    )

    with pytest.raises(ValidationError):
        validate_intent_decision(skill, decision)


def test_validate_rejects_activity_outside_enum() -> None:
    registry = SkillRegistry.from_path("skills")
    skill = registry.get("activity_search_skill")
    decision = IntentDecision(
        status="matched",
        intent="搜活动",
        code="020",
        params={"metadataList": ["不存在的活动"]},
    )

    with pytest.raises(ValidationError):
        validate_intent_decision(skill, decision)


def test_validate_evaluation_rejects_missing_scope() -> None:
    decision = EvaluationDecision(verdict="reject", skill_check="fail")

    with pytest.raises(ValidationError):
        validate_evaluation_decision(decision)


def test_validate_evaluation_allows_unclear_checks_with_scope() -> None:
    decision = EvaluationDecision(
        verdict="reject",
        reject_scope="param_mismatch",
        skill_check="pass",
        intent_check="unclear",
        params_check="fail",
    )

    assert validate_evaluation_decision(decision) is decision


def test_validate_evaluation_accepts_layered_param_mismatch() -> None:
    decision = EvaluationDecision(
        verdict="reject",
        reject_scope="param_mismatch",
        skill_check="pass",
        intent_check="pass",
        params_check="fail",
    )

    assert validate_evaluation_decision(decision) is decision
