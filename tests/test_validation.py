import pytest

from intent_router.skills import SkillRegistry
from intent_router.types import IntentDecision
from intent_router.validation import ValidationError, validate_intent_decision


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
