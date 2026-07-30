import asyncio
import json
import time
from typing import Any

import pytest
from pydantic import BaseModel

from intent_router.config import RouterConfig
from intent_router.model_client import FakeStructuredClient
from intent_router.router import IntentRouter
from intent_router.types import (
    CandidateScore,
    ContextualizedRequest,
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    IntentCandidate,
    IntentCandidateSet,
    ParameterExtractionResult,
    RerankDecision,
    SemanticFrame,
    SkillCandidate,
    SkillCandidateSet,
)


def skill_candidate(
    skill_id: str | None,
    *,
    confidence: float,
    evidence: list[str],
    risks: list[str] | None = None,
) -> SkillCandidate:
    return SkillCandidate(
        candidate_id=f"skill:{skill_id or 'ordinary_dialogue'}",
        skill_id=skill_id,
        intent_domain=skill_id or "普通对话",
        confidence=confidence,
        matched_evidence=evidence,
        risk_flags=risks or [],
    )


def intent_candidate(
    skill_id: str,
    intent: str,
    code: str,
    *,
    confidence: float,
    evidence: list[str],
    risks: list[str] | None = None,
) -> IntentCandidate:
    return IntentCandidate(
        candidate_id=f"raw:{skill_id}:{intent}",
        skill_id=skill_id,
        intent=intent,
        code=code,
        confidence=confidence,
        matched_evidence=evidence,
        risk_flags=risks or [],
    )


def evaluator_select(
    selected: str,
    *,
    confidence: float,
    scores: dict[str, float],
) -> RerankDecision:
    return RerankDecision(
        verdict="select",
        selected_candidate_id=selected,
        confidence=confidence,
        ranking=[
            CandidateScore(candidate_id=candidate_id, score=score)
            for candidate_id, score in scores.items()
        ],
    )


async def test_high_confidence_route_skips_evaluator() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "function_skill",
                        confidence=0.95,
                        evidence=["AI超市"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "function_skill",
                        "AI超市",
                        "021",
                        confidence=0.94,
                        evidence=["AI超市"],
                    ),
                ],
            ),
        ],
    )
    evaluator = FakeStructuredClient([])
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route("进入AI超市")

    assert result.status == "matched"
    assert result.code == "021"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_called is False
    assert [call[2] for call in main.calls] == [
        SkillCandidateSet,
        IntentCandidateSet,
    ]
    assert evaluator.calls == []


async def test_cross_skill_conflict_calls_evaluator_and_switches_with_double_gate() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate("file_skill", confidence=0.90, evidence=["文件"]),
                    skill_candidate(
                        "mcloud_search_skill",
                        confidence=0.88,
                        evidence=["搜索", "合同"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "file_skill",
                        "文件",
                        "021",
                        confidence=0.90,
                        evidence=["文件"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "mcloud_search_skill",
                        "搜综合",
                        "018",
                        confidence=0.88,
                        evidence=["搜索", "合同"],
                    ),
                ],
            ),
            ParameterExtractionResult(),
        ],
    )
    evaluator = FakeStructuredClient(
        [
            evaluator_select(
                "mcloud_search_skill:搜综合",
                confidence=0.92,
                scores={
                    "mcloud_search_skill:搜综合": 0.95,
                    "file_skill:文件": 0.50,
                    "ordinary_dialogue:普通对话": 0.10,
                },
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch"
    assert result.diagnostics.evaluator_margin == pytest.approx(0.45)


async def test_evaluator_switch_below_margin_returns_abstain() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate("file_skill", confidence=0.90, evidence=["文件"]),
                    skill_candidate(
                        "mcloud_search_skill",
                        confidence=0.88,
                        evidence=["搜索"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "file_skill",
                        "文件",
                        "021",
                        confidence=0.90,
                        evidence=["文件"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "mcloud_search_skill",
                        "搜综合",
                        "018",
                        confidence=0.88,
                        evidence=["搜索"],
                    ),
                ],
            ),
        ],
    )
    evaluator = FakeStructuredClient(
        [
            evaluator_select(
                "mcloud_search_skill:搜综合",
                confidence=0.95,
                scores={
                    "mcloud_search_skill:搜综合": 0.70,
                    "file_skill:文件": 0.60,
                },
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route("搜索文件")

    assert result.status == "abstain"
    assert result.code is None
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch_blocked"


async def test_evaluator_can_return_unsupported() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(candidates=[]),
        ],
    )
    evaluator = FakeStructuredClient(
        [
            RerankDecision(
                verdict="unsupported",
                confidence=0.91,
                reason="当前能力集合不支持",
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route("控制家里的空调")

    assert result.status == "unsupported"
    assert result.confidence == 0.91
    assert result.intent is None


async def test_targeted_expansion_runs_once_and_reenters_gate() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate("file_skill", confidence=0.70, evidence=["文件"]),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "file_skill",
                        "文件",
                        "021",
                        confidence=0.70,
                        evidence=["文件"],
                    ),
                ],
            ),
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "mcloud_search_skill",
                        confidence=0.90,
                        evidence=["搜索", "合同"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "mcloud_search_skill",
                        "搜综合",
                        "018",
                        confidence=0.90,
                        evidence=["搜索", "合同"],
                    ),
                ],
            ),
            ParameterExtractionResult(),
        ],
    )
    evaluator = FakeStructuredClient(
        [
            RerankDecision(
                verdict="expand",
                confidence=0.75,
                ranking=[
                    CandidateScore(candidate_id="file_skill:文件", score=0.50),
                    CandidateScore(
                        candidate_id="ordinary_dialogue:普通对话",
                        score=0.10,
                    ),
                ],
                reason="缺少搜索方向",
                expand_scope="skill_recall_gap",
                expansion_hint="补充云盘搜索",
            ),
            evaluator_select(
                "mcloud_search_skill:搜综合",
                confidence=0.92,
                scores={
                    "mcloud_search_skill:搜综合": 0.95,
                    "file_skill:文件": 0.40,
                },
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert result.code == "018"
    assert result.loop_count == 2
    assert result.correction_scopes == ["skill_recall_gap"]
    assert len(evaluator.calls) == 2


async def test_one_intent_skill_failure_is_isolated() -> None:
    class PartialFailureClient:
        calls: list[str]

        def __init__(self) -> None:
            self.calls = []

        async def structured(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            response_model: type[BaseModel],
        ) -> Any:
            payload = json.loads(user_prompt)
            if response_model is SkillCandidateSet:
                return SkillCandidateSet(
                    candidates=[
                        skill_candidate(
                            "function_skill",
                            confidence=0.95,
                            evidence=["AI超市"],
                        ),
                        skill_candidate(
                            "file_skill",
                            confidence=0.70,
                            evidence=["入口"],
                        ),
                    ],
                )
            skill_id = payload["skill_id"]
            self.calls.append(skill_id)
            if skill_id == "file_skill":
                raise ConnectionError("file selector unavailable")
            return IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "function_skill",
                        "AI超市",
                        "021",
                        confidence=0.95,
                        evidence=["AI超市"],
                    ),
                ],
            )

    config = RouterConfig()
    config.retry.max_retries = 0
    client = PartialFailureClient()
    router = IntentRouter.from_config(
        "skills",
        model_client=client,
        evaluator_client=FakeStructuredClient([]),
        config=config,
    )

    result = await router.route("进入AI超市入口")

    assert result.status == "matched"
    assert result.code == "021"
    assert result.diagnostics is not None
    assert "file_skill" in result.diagnostics.failed_skills


async def test_all_selected_business_intent_generators_failing_returns_error() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "function_skill",
                        confidence=0.90,
                        evidence=["AI超市"],
                    ),
                ],
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=FakeStructuredClient([]),
    )

    result = await router.route("进入AI超市")

    assert result.status == "error"
    assert "all selected business" in (result.reason or "")


async def test_intent_generation_runs_in_parallel() -> None:
    class DelayedClient:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0

        async def structured(
            self,
            *,
            system_prompt: str,
            user_prompt: str,
            response_model: type[BaseModel],
        ) -> Any:
            payload = json.loads(user_prompt)
            if response_model is SkillCandidateSet:
                return SkillCandidateSet(
                    candidates=[
                        skill_candidate("file_skill", confidence=0.7, evidence=["入口"]),
                        skill_candidate("function_skill", confidence=0.69, evidence=["入口"]),
                        skill_candidate("note_skill", confidence=0.68, evidence=["入口"]),
                    ],
                )
            skill_id = payload["skill_id"]
            route = {
                "file_skill": ("文件", "021"),
                "function_skill": ("AI超市", "021"),
                "note_skill": ("笔记", "026"),
            }[skill_id]
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
            return IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        skill_id,
                        route[0],
                        route[1],
                        confidence=0.68,
                        evidence=["入口"],
                    ),
                ],
            )

    config = RouterConfig()
    config.decision.evaluator_mode = "disabled"
    client = DelayedClient()
    router = IntentRouter.from_config("skills", model_client=client, config=config)
    started = time.perf_counter()

    result = await router.route("打开入口")
    elapsed = time.perf_counter() - started

    assert result.status == "abstain"
    assert client.max_active == 3
    assert elapsed < 0.13


async def test_parameter_extraction_runs_once_after_route_selection() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "mcloud_search_skill",
                        confidence=0.95,
                        evidence=["照片"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "mcloud_search_skill",
                        "搜图片",
                        "012",
                        confidence=0.94,
                        evidence=["猫", "照片"],
                    ),
                ],
            ),
            ParameterExtractionResult(
                params={"metadataList": ["猫"], "unknown": "x"},
                evidence={"metadataList": ["猫"], "unknown": ["猫"]},
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=FakeStructuredClient([]),
    )

    result = await router.route("找猫的照片")

    assert result.status == "matched"
    assert result.params == {"metadataList": ["猫"]}
    assert result.diagnostics is not None
    assert result.diagnostics.parameter_validation is not None
    assert "unknown" in result.diagnostics.parameter_validation.rejected_fields
    assert [call[2] for call in main.calls].count(ParameterExtractionResult) == 1


async def test_missing_required_parameter_returns_clarify_without_changing_route() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "mcloud_search_skill",
                        confidence=0.95,
                        evidence=["照片"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "mcloud_search_skill",
                        "搜图片",
                        "012",
                        confidence=0.94,
                        evidence=["照片"],
                    ),
                ],
            ),
            ParameterExtractionResult(),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=FakeStructuredClient([]),
    )
    router.registry.get("mcloud_search_skill").intents[
        "搜图片"
    ].params["metadataList"].required = True

    result = await router.route("找照片")

    assert result.status == "clarify"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜图片"
    assert result.code == "012"
    assert "metadataList" in (
        result.diagnostics.parameter_validation.missing_required
        if result.diagnostics and result.diagnostics.parameter_validation
        else []
    )


async def test_contextualizer_conflict_is_visible_to_evaluator() -> None:
    main = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="生成猫图片",
                relation_to_history="revision",
                semantic_frame=SemanticFrame(
                    action="生成",
                    expected_result_type="resource",
                ),
            ),
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "image_skill",
                        confidence=0.90,
                        evidence=["生成", "图片"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "image_skill",
                        "文生图",
                        "002",
                        confidence=0.90,
                        evidence=["生成", "图片"],
                    ),
                ],
            ),
            ParameterExtractionResult(),
        ],
    )
    evaluator = FakeStructuredClient(
        [
            evaluator_select(
                "image_skill:文生图",
                confidence=0.90,
                scores={"image_skill:文生图": 0.90},
            ),
        ],
    )
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="找猫图片",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜图片",
                    code="012",
                ),
            ),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route("改成生成猫图片", dialogue_history=history)

    assert result.status == "matched"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_called is True
    evaluator_payload = json.loads(evaluator.calls[0][1])
    assert "semantic_frame_conflict" in evaluator_payload[
        "contextualized_request"
    ]["conflict_flags"]


async def test_route_no_loop_keeps_first_candidate_benchmark_path() -> None:
    main = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    skill_candidate(
                        "function_skill",
                        confidence=0.90,
                        evidence=["AI超市"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    intent_candidate(
                        "function_skill",
                        "AI超市",
                        "021",
                        confidence=0.90,
                        evidence=["AI超市"],
                    ),
                ],
            ),
        ],
    )
    evaluator = FakeStructuredClient([])
    router = IntentRouter.from_config(
        "skills",
        model_client=main,
        evaluator_client=evaluator,
    )

    result = await router.route_no_loop("打开AI超市")

    assert result.status == "matched"
    assert result.code == "021"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "select_top"
    assert evaluator.calls == []
