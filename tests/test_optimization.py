from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from intent_router.candidate_validator import (
    dynamic_business_candidate_count,
    validate_and_merge_intent_candidates,
)
from intent_router.config import DecisionConfig, RetryConfig
from intent_router.decision_gate import decide, evaluator_switch_allowed
from intent_router.dialogue import summarize_route_result
from intent_router.model_governor import (
    ModelCallGovernor,
    RouteDeadline,
    StageCallTimeout,
)
from intent_router.parameter_validator import validate_parameters
from intent_router.session_store import InMemorySessionStore, SessionKey
from intent_router.skills import SkillRegistry
from intent_router.types import (
    ContextualizedRequest,
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    IntentCandidate,
    IntentSchema,
    ParameterExtractionResult,
    ParamItemsSchema,
    ParamSchema,
    SemanticFrame,
    SkillCandidateSet,
    RouteResult,
)


def test_candidate_merge_retains_highest_candidate_and_all_metadata() -> None:
    registry = SkillRegistry.from_path("skills")
    first = IntentCandidate(
        candidate_id="source-a",
        skill_id="mcloud_search_skill",
        intent="搜图片",
        code="012",
        confidence=0.7,
        matched_evidence=["猫"],
        risk_flags=["context_unclear"],
        alternatives=["搜综合"],
    )
    second = IntentCandidate(
        candidate_id="source-b",
        skill_id="mcloud_search_skill",
        intent="搜图片",
        code="012",
        confidence=0.9,
        matched_evidence=["照片"],
        alternatives=["找合照"],
    )

    result = validate_and_merge_intent_candidates(
        registry,
        [first, second],
        contextualized_request=ContextualizedRequest(
            resolved_query="找猫照片",
            semantic_frame=SemanticFrame(),
        ),
        current_user_query="找猫照片",
    )

    assert len(result.candidates) == 1
    merged = result.candidates[0]
    assert merged.confidence == 0.9
    assert merged.matched_evidence == ["猫", "照片"]
    assert merged.risk_flags == ["context_unclear"]
    assert merged.alternatives == ["搜综合", "找合照"]
    assert merged.source_ids == ["source-a", "source-b"]


def test_dynamic_skill_count_uses_high_and_low_confidence_bands() -> None:
    def candidates(scores: list[float]) -> list[Any]:
        return [
            type("Candidate", (), {"skill_id": f"skill-{index}", "confidence": score})()
            for index, score in enumerate(scores)
        ]

    kwargs = {
        "high_confidence_count": 2,
        "default_count": 3,
        "max_count": 5,
        "high_confidence_threshold": 0.8,
        "high_margin_threshold": 0.2,
        "low_confidence_threshold": 0.55,
        "low_margin_threshold": 0.1,
    }

    assert dynamic_business_candidate_count(
        candidates([0.9, 0.6, 0.5]),
        **kwargs,
    ) == 2
    assert dynamic_business_candidate_count(
        candidates([0.5, 0.49, 0.48, 0.47, 0.46]),
        **kwargs,
    ) == 5


def test_decision_gate_requires_evaluator_for_cross_skill_candidates() -> None:
    candidates = [
        IntentCandidate(
            candidate_id="file_skill:文件",
            skill_id="file_skill",
            intent="文件",
            code="021",
            confidence=0.95,
            matched_evidence=["文件"],
        ),
        IntentCandidate(
            candidate_id="mcloud_search_skill:搜综合",
            skill_id="mcloud_search_skill",
            intent="搜综合",
            code="018",
            confidence=0.70,
            matched_evidence=["搜索"],
        ),
    ]
    gate = decide(
        candidates,
        ContextualizedRequest(resolved_query="搜索文件"),
        DecisionConfig(),
    )

    assert gate.action == "evaluate"
    assert "cross skill" in gate.reason


def test_switch_gate_enforces_confidence_margin_and_blocking_risk() -> None:
    first = IntentCandidate(
        candidate_id="a",
        skill_id="file_skill",
        intent="文件",
        code="021",
    )
    selected = IntentCandidate(
        candidate_id="b",
        skill_id="mcloud_search_skill",
        intent="搜综合",
        code="018",
        risk_flags=["label_conflict"],
    )

    allowed, margin, _ = evaluator_switch_allowed(
        first_candidate=first,
        selected_candidate=selected,
        evaluator_confidence=0.99,
        score_by_id={"a": 0.2, "b": 0.9},
        config=DecisionConfig(),
    )

    assert allowed is False
    assert margin == 0.7


def test_parameter_validator_rejects_unknown_and_untrusted_handle() -> None:
    schema = IntentSchema(
        name="测试",
        code="001",
        desc="测试",
        params={
            "file_id": ParamSchema(
                name="file_id",
                type="string",
                required=True,
                desc="文件句柄",
            ),
            "tags": ParamSchema(
                name="tags",
                type="array",
                required=False,
                desc="标签",
                items=ParamItemsSchema(type="string"),
            ),
        },
    )
    extraction = ParameterExtractionResult(
        params={"file_id": "invented", "tags": ["猫"], "extra": "x"},
        evidence={"file_id": ["invented"], "tags": ["猫"], "extra": ["猫"]},
    )

    invalid = validate_parameters(
        schema,
        extraction,
        current_user_query="给猫文件加标签",
        resolved_query="给猫文件加标签",
    )
    trusted = validate_parameters(
        schema,
        extraction,
        current_user_query="给猫文件加标签",
        resolved_query="给猫文件加标签",
        trusted_params={"file_id": "trusted-1"},
    )

    assert invalid.missing_required == ["file_id"]
    assert "file_id" in invalid.rejected_fields
    assert "extra" in invalid.rejected_fields
    assert trusted.params["file_id"] == "trusted-1"
    assert trusted.params["tags"] == ["猫"]


async def test_model_governor_retries_transient_error_and_records_attempts() -> None:
    class FlakyClient:
        def __init__(self) -> None:
            self.attempts = 0

        async def structured(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            response_model: type[BaseModel],
        ) -> Any:
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("temporary")
            return SkillCandidateSet()

    client = FlakyClient()
    records = []
    governor = ModelCallGovernor(
        global_concurrency=2,
        retry=RetryConfig(max_retries=1, base_backoff_ms=0),
    )

    result = await governor.structured(
        client,
        stage="skill_selector",
        timeout_ms=500,
        deadline=RouteDeadline.after_ms(1000),
        records=records,
        system_prompt="system",
        user_prompt="user",
        response_model=SkillCandidateSet,
    )

    assert result == SkillCandidateSet()
    assert records[0].attempts == 2
    assert records[0].status == "ok"


async def test_model_governor_enforces_stage_timeout() -> None:
    class SlowClient:
        async def structured(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            response_model: type[BaseModel],
        ) -> Any:
            import asyncio

            await asyncio.sleep(0.1)
            return SkillCandidateSet()

    records = []
    governor = ModelCallGovernor(
        global_concurrency=1,
        retry=RetryConfig(max_retries=0),
    )

    with pytest.raises(StageCallTimeout):
        await governor.structured(
            SlowClient(),
            stage="skill_selector",
            timeout_ms=10,
            deadline=RouteDeadline.after_ms(100),
            records=records,
            system_prompt="system",
            user_prompt="user",
            response_model=SkillCandidateSet,
        )

    assert records[0].status == "timeout"


async def test_session_store_isolates_three_part_keys_and_copies_history() -> None:
    store = InMemorySessionStore()
    first_key = SessionKey("tenant", "user-a", "session")
    second_key = SessionKey("tenant", "user-b", "session")
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="你好",
                result=DialogueRouteSummary(
                    status="matched",
                    intent="普通对话",
                    code="000",
                ),
            ),
        ],
    )
    await store.save(first_key, history)
    loaded = await store.load(first_key)
    loaded.turns.clear()

    assert len((await store.load(first_key)).turns) == 1
    assert (await store.load(second_key)).turns == []


def test_dialogue_summary_does_not_persist_extracted_params() -> None:
    summary = summarize_route_result(
        RouteResult(
            status="matched",
            intent="搜图片",
            code="012",
            params={"file_id": "secret", "metadataList": ["猫"]},
        ),
    )

    assert summary.params == {}
