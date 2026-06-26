import pytest
from pydantic import ValidationError as PydanticValidationError

from intent_router.skills import SkillRegistry
from intent_router.types import (
    ContextualizedRequest,
    EvaluationDecision,
    IntentDecision,
    SkillRouteDecision,
)
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


def test_validate_evaluation_rejects_unclear_checks_with_scope() -> None:
    decision = EvaluationDecision(
        verdict="reject",
        reject_scope="param_mismatch",
        skill_check="pass",
        intent_check="unclear",
        params_check="fail",
    )

    with pytest.raises(ValidationError):
        validate_evaluation_decision(decision)


def test_validate_evaluation_accepts_layered_param_mismatch() -> None:
    decision = EvaluationDecision(
        verdict="reject",
        reject_scope="param_mismatch",
        skill_check="pass",
        intent_check="pass",
        params_check="fail",
    )

    assert validate_evaluation_decision(decision) is decision


def test_contextualized_request_normalizes_status_relation_mixup() -> None:
    request = ContextualizedRequest.model_validate(
        {
            "status": "new_request",
            "resolved_query": "项目进展如何",
        },
    )

    assert request.status == "resolved"
    assert request.relation_to_history == "new_request"
    assert request.resolved_query == "项目进展如何"


def test_contextualized_request_normalizes_continuation_status() -> None:
    request = ContextualizedRequest.model_validate(
        {
            "status": "continuation",
            "resolved_query": "搜索AI助手测评报告PPT",
        },
    )

    assert request.status == "resolved"
    assert request.relation_to_history == "continuation"


def test_contextualized_request_normalizes_ambiguous_status_to_clarify() -> None:
    request = ContextualizedRequest.model_validate(
        {
            "status": "ambiguous",
            "resolved_query": "蓝色",
            "question": "您是要搜索蓝色图片还是蓝色文档？",
            "options": [{"label": "图片", "value": "图片"}],
        },
    )

    assert request.status == "clarify"
    assert request.relation_to_history == "ambiguous"
    assert request.question == "您是要搜索蓝色图片还是蓝色文档？"


def test_structured_output_models_reject_unknown_fields() -> None:
    with pytest.raises(PydanticValidationError):
        SkillRouteDecision.model_validate(
            {
                "status": "route",
                "skill_id": "mcloud_search_skill",
                "unexpected": "extra",
            },
        )
