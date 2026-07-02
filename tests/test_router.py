import json

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
    RerankDecision,
    SemanticFrame,
    SkillCandidate,
    SkillCandidateSet,
)


def _skill_candidate(skill_id: str, *, confidence: float = 0.9) -> SkillCandidate:
    return SkillCandidate(
        candidate_id=f"skill:{skill_id}",
        skill_id=skill_id,
        intent_domain=skill_id,
        confidence=confidence,
        matched_cues=["搜索"] if skill_id == "mcloud_search_skill" else ["文件"],
        risk_flags=[],
        reason="matched skill",
    )


def _ordinary_skill_candidate(*, confidence: float = 0.3) -> SkillCandidate:
    return SkillCandidate(
        candidate_id="skill:ordinary_dialogue",
        skill_id=None,
        intent_domain="普通对话",
        confidence=confidence,
        matched_cues=[],
        risk_flags=[],
        reason="ordinary dialogue candidate",
    )


def _intent_candidate(
    *,
    skill_id: str,
    intent: str,
    code: str,
    confidence: float = 0.8,
    cues: list[str] | None = None,
    risk_flags: list[str] | None = None,
) -> IntentCandidate:
    return IntentCandidate(
        candidate_id=f"{skill_id}:{intent}",
        skill_id=skill_id,
        skill_name=None,
        intent=intent,
        code=code,
        params={},
        confidence=confidence,
        matched_cues=cues or [],
        risk_flags=risk_flags or [],
        reason="matched intent",
    )


async def test_route_critical_tool_query_completes_specific_function_and_dialogue_candidates() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(candidates=[_skill_candidate("mcloud_search_skill")]),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜综合",
                        code="018",
                        cues=["知识库"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="knowledge_base_skill",
                        intent="知识库入口",
                        code="035",
                        cues=["知识库", "工具"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="function_skill",
                        intent="AI超市",
                        code="021",
                        cues=["工具"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="knowledge_base_skill:知识库入口:035:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="knowledge_base_skill:知识库入口:035:1",
                        score=0.9,
                    ),
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜综合:018:1",
                        score=0.6,
                    ),
                    CandidateScore(
                        candidate_id="function_skill:AI超市:021:1",
                        score=0.5,
                    ),
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.1),
                ],
                reason="具体业务工具优于通用搜索",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("推荐知识库工具")

    assert result.skill is not None
    assert result.skill.id == "knowledge_base_skill"
    assert result.code == "035"
    assert [call[2] for call in model.calls] == [
        SkillCandidateSet,
        IntentCandidateSet,
        IntentCandidateSet,
        IntentCandidateSet,
        RerankDecision,
    ]
    evaluator_prompt = json.loads(model.calls[-1][1])
    assert "knowledge_base_skill:知识库入口:035:1" in evaluator_prompt["candidate_ids"]
    assert "function_skill:AI超市:021:1" in evaluator_prompt["candidate_ids"]
    assert "ordinary_dialogue:000" in evaluator_prompt["candidate_ids"]


async def test_single_entity_resource_query_completes_search_candidate() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(candidates=[_ordinary_skill_candidate()]),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜综合",
                        code="018",
                        cues=["权力的游戏"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="mcloud_search_skill:搜综合:018:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜综合:018:1",
                        score=0.95,
                    ),
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.2),
                ],
                reason="纯实体名默认资源搜索",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("权力的游戏")

    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.code == "018"
    assert [call[2] for call in model.calls] == [
        SkillCandidateSet,
        IntentCandidateSet,
        RerankDecision,
    ]


async def test_route_critical_switch_uses_lower_margin_for_specific_business_route() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("function_skill", confidence=0.9),
                    _skill_candidate("knowledge_base_skill", confidence=0.88),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="function_skill",
                        intent="AI超市",
                        code="021",
                        cues=["工具"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="knowledge_base_skill",
                        intent="知识库入口",
                        code="035",
                        cues=["知识库", "工具"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="knowledge_base_skill:知识库入口:035:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="knowledge_base_skill:知识库入口:035:1",
                        score=0.59,
                    ),
                    CandidateScore(
                        candidate_id="function_skill:AI超市:021:1",
                        score=0.5,
                    ),
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.1),
                ],
                reason="具体业务工具优于通用入口",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("推荐知识库工具")

    assert result.skill is not None
    assert result.skill.id == "knowledge_base_skill"
    assert result.code == "035"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch"


async def test_search_vs_tool_entry_risk_does_not_block_switch_but_label_conflict_does() -> None:
    allowed_model = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("function_skill", confidence=0.9),
                    _skill_candidate("knowledge_base_skill", confidence=0.88),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="function_skill",
                        intent="AI超市",
                        code="021",
                        cues=["工具"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    IntentCandidate(
                        candidate_id="knowledge_base_skill:知识库入口",
                        skill_id="knowledge_base_skill",
                        intent="知识库入口",
                        code="035",
                        confidence=0.8,
                        matched_cues=["知识库", "工具"],
                        risk_flags=["search_vs_tool_entry"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="knowledge_base_skill:知识库入口:035:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="knowledge_base_skill:知识库入口:035:1",
                        score=0.7,
                    ),
                    CandidateScore(
                        candidate_id="function_skill:AI超市:021:1",
                        score=0.5,
                    ),
                ],
                reason="具体业务工具优于通用入口",
            ),
        ],
    )
    blocked_model = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("function_skill", confidence=0.9),
                    _skill_candidate("knowledge_base_skill", confidence=0.88),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="function_skill",
                        intent="AI超市",
                        code="021",
                        cues=["工具"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    IntentCandidate(
                        candidate_id="knowledge_base_skill:知识库入口",
                        skill_id="knowledge_base_skill",
                        intent="知识库入口",
                        code="035",
                        confidence=0.8,
                        matched_cues=["知识库", "工具"],
                        risk_flags=["label_conflict"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="knowledge_base_skill:知识库入口:035:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="knowledge_base_skill:知识库入口:035:1",
                        score=0.7,
                    ),
                    CandidateScore(
                        candidate_id="function_skill:AI超市:021:1",
                        score=0.5,
                    ),
                ],
                reason="具体业务工具优于通用入口",
            ),
        ],
    )

    allowed = await IntentRouter.from_config(
        "skills",
        model_client=allowed_model,
    ).route("推荐知识库工具")
    blocked = await IntentRouter.from_config(
        "skills",
        model_client=blocked_model,
    ).route("推荐知识库工具")

    assert allowed.diagnostics is not None
    assert allowed.diagnostics.evaluator_action == "switch"
    assert allowed.code == "035"
    assert blocked.diagnostics is not None
    assert blocked.diagnostics.evaluator_action == "switch_blocked"
    assert blocked.code == "021"


async def test_router_returns_reranked_matched_result() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("mcloud_search_skill", confidence=0.9),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜图片",
                        code="012",
                        confidence=0.8,
                        cues=["猫", "照片"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="mcloud_search_skill:搜图片:012:1",
                confidence=0.8,
                ranking=[
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜图片:012:1",
                        score=0.9,
                    ),
                ],
                reason="图片搜索最匹配",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找猫照片")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜图片"
    assert result.code == "012"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "select_top"

    evaluator_prompt = json.loads(model.calls[2][1])
    assert evaluator_prompt["candidate_ids"] == [
        "mcloud_search_skill:搜图片:012:1",
        "ordinary_dialogue:000",
    ]
    assert "dialogue_history" not in evaluator_prompt


async def test_evaluator_can_switch_to_candidate_when_gate_is_satisfied() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("file_skill", confidence=0.9),
                    _skill_candidate("mcloud_search_skill", confidence=0.88),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="file_skill",
                        intent="文件",
                        code="021",
                        cues=["文件"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜综合",
                        code="018",
                        cues=["搜索", "合同"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="mcloud_search_skill:搜综合:018:1",
                confidence=0.92,
                ranking=[
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜综合:018:1",
                        score=0.95,
                    ),
                    CandidateScore(candidate_id="file_skill:文件:021:1", score=0.5),
                ],
                reason="当前查询明确要求搜索合同",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.code == "018"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch"
    assert result.diagnostics.first_candidate_id == "file_skill:文件:021:1"


async def test_ordinary_answer_type_blocks_business_switch() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="画质修复是修复哪些的",
                relation_to_history="new_request",
                semantic_frame=SemanticFrame(
                    action="咨询",
                    expected_result_type="ordinary_answer",
                    subjects=["画质修复"],
                ),
                reason="用户询问功能说明，期望语言答案",
            ),
            SkillCandidateSet(
                candidates=[
                    _ordinary_skill_candidate(confidence=0.7),
                    _skill_candidate("image_skill", confidence=0.9),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="image_skill",
                        intent="画质修复",
                        code="009",
                        cues=["画质修复"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="image_skill:画质修复:009:1",
                confidence=0.95,
                ranking=[
                    CandidateScore(
                        candidate_id="image_skill:画质修复:009:1",
                        score=0.95,
                    ),
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.5),
                ],
                reason="功能候选名称匹配",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="修复这张照片",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="image_skill",
                    intent="画质修复",
                    code="009",
                ),
            ),
        ],
    )

    result = await router.route(
        "画质修复是修复哪些的",
        dialogue_history=history,
    )

    assert result.skill is None
    assert result.code == "000"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch_blocked"


async def test_final_selection_keeps_evaluator_selected_specialized_resource() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="搜索小云果园活动",
                relation_to_history="revision",
                semantic_frame=SemanticFrame(
                    action="搜索",
                    expected_result_type="resource",
                    object_types=["活动"],
                    subjects=["小云果园"],
                ),
                reason="继承历史活动搜索语境",
            ),
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("activity_search_skill", confidence=0.95),
                    _skill_candidate("mcloud_search_skill", confidence=0.6),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="activity_search_skill",
                        intent="搜活动",
                        code="020",
                        confidence=0.95,
                        cues=["小云果园"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜综合",
                        code="018",
                        confidence=0.6,
                        cues=["小云果园"],
                        risk_flags=["answer_vs_resource"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="activity_search_skill:搜活动:020:1",
                confidence=0.95,
                ranking=[
                    CandidateScore(
                        candidate_id="activity_search_skill:搜活动:020:1",
                        score=0.95,
                    ),
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜综合:018:1",
                        score=0.7,
                    ),
                ],
                reason="活动搜索候选直接命中小云果园",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="搜种树活动",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="activity_search_skill",
                    intent="搜活动",
                    code="020",
                ),
            ),
        ],
    )

    result = await router.route("对了叫小云果园", dialogue_history=history)

    assert result.skill is not None
    assert result.skill.id == "activity_search_skill"
    assert result.code == "020"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "select_top"


async def test_final_selection_blocks_low_confidence_risky_selected_candidate() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="搜索126邮箱文件夹",
                relation_to_history="revision",
                semantic_frame=SemanticFrame(
                    action="搜索",
                    expected_result_type="resource",
                    object_types=["文件夹"],
                    subjects=["126邮箱"],
                ),
                reason="修正文件夹主体",
            ),
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("mcloud_search_skill", confidence=0.92),
                    _skill_candidate("mail_skill", confidence=0.45),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜文件夹",
                        code="016",
                        confidence=0.92,
                        cues=["文件夹"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mail_skill",
                        intent="搜邮件",
                        code="040001",
                        confidence=0.45,
                        cues=["126邮箱"],
                        risk_flags=["answer_vs_resource"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="mail_skill:搜邮件:040001:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="mail_skill:搜邮件:040001:1",
                        score=0.95,
                    ),
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜文件夹:016:1",
                        score=0.7,
                    ),
                ],
                reason="126邮箱也可理解为邮件域",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="找到139邮箱文件夹",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜文件夹",
                    code="016",
                ),
            ),
        ],
    )

    result = await router.route("搞错了，我是想要126邮箱的", dialogue_history=history)

    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.code == "016"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch_blocked"


async def test_final_selection_rescues_explicit_document_suffix_resource() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="打开12月市场调研报告.docx文档",
                relation_to_history="continuation",
                semantic_frame=SemanticFrame(
                    action="打开文档",
                    expected_result_type="resource",
                    object_types=["文档"],
                    subjects=["12月市场调研报告.docx"],
                ),
                reason="明确打开文档资源",
            ),
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("file_skill", confidence=0.92),
                    _skill_candidate("mcloud_search_skill", confidence=0.75),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="file_skill",
                        intent="文档",
                        code="021",
                        confidence=0.92,
                        cues=["文档"],
                    ),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜文档",
                        code="013",
                        confidence=0.75,
                        cues=["docx", "文档"],
                    ),
                ],
            ),
            IntentCandidateSet(candidates=[]),
            RerankDecision(
                verdict="select",
                selected_candidate_id="file_skill:文档:021:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(candidate_id="file_skill:文档:021:1", score=0.95),
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜文档:013:1",
                        score=0.75,
                    ),
                ],
                reason="文件管理候选可打开文档",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="打开这个文件夹",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="file_skill",
                    intent="文件",
                    code="021",
                ),
            ),
        ],
    )

    result = await router.route(
        "帮我打开这个里面的12月市场调研报告.docx文档",
        dialogue_history=history,
    )

    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.code == "013"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "switch"


async def test_expansion_adds_missing_skill_candidates_before_rerank() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(candidates=[_skill_candidate("file_skill")]),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="file_skill",
                        intent="文件",
                        code="021",
                        cues=["文件"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="expand",
                confidence=0.7,
                ranking=[
                    CandidateScore(candidate_id="file_skill:文件:021:1", score=0.45),
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.1),
                ],
                reason="缺少搜索方向候选",
                expand_scope="skill_recall_gap",
                expansion_hint="补充云盘搜索 skill",
            ),
            SkillCandidateSet(
                candidates=[_skill_candidate("mcloud_search_skill", confidence=0.9)],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜综合",
                        code="018",
                        cues=["搜索", "合同"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="mcloud_search_skill:搜综合:018:1",
                confidence=0.92,
                ranking=[
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜综合:018:1",
                        score=0.95,
                    ),
                    CandidateScore(candidate_id="file_skill:文件:021:1", score=0.4),
                ],
                reason="扩充后搜索候选最匹配",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    trace: list[dict] = []

    result = await router.route("搜索合同文件", trace=trace)

    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.code == "018"
    assert result.loop_count == 2
    assert result.correction_scopes == ["skill_recall_gap"]
    assert result.diagnostics is not None
    assert result.diagnostics.expansion_rounds == 1
    assert any(event["event"] == "expansion" for event in trace)


async def test_route_no_loop_returns_first_candidate_without_evaluator() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(candidates=[_skill_candidate("mcloud_search_skill")]),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜综合",
                        code="018",
                        cues=["搜索"],
                    ),
                ],
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route_no_loop("搜索合同")

    assert result.code == "018"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "select_top"
    assert [call[2] for call in model.calls] == [
        SkillCandidateSet,
        IntentCandidateSet,
    ]


async def test_contextualizer_semantic_frame_is_passed_to_later_prompts() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="搜索最近的猫图片",
                relation_to_history="continuation",
                semantic_frame=SemanticFrame(
                    action="搜索",
                    expected_result_type="resource",
                    object_types=["图片"],
                    subjects=["猫"],
                    inherited_turns=[1],
                ),
                reason="继承历史主体",
            ),
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("mcloud_search_skill", confidence=0.9),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="mcloud_search_skill",
                        intent="搜图片",
                        code="012",
                        cues=["图片", "猫"],
                    ),
                ],
            ),
            RerankDecision(
                verdict="select",
                selected_candidate_id="mcloud_search_skill:搜图片:012:1",
                confidence=0.9,
                ranking=[
                    CandidateScore(
                        candidate_id="mcloud_search_skill:搜图片:012:1",
                        score=0.95,
                    ),
                ],
                reason="图片搜索最匹配",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="帮我找猫",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜图片",
                    code="012",
                    resolved_query="搜索猫图片",
                ),
            ),
        ],
    )

    result = await router.route("最近的", dialogue_history=history)

    assert result.resolved_query == "搜索最近的猫图片"
    contextualizer_prompt = json.loads(model.calls[0][1])
    assert contextualizer_prompt["dialogue_history"][0]["semantic_state"][
        "resolved_query"
    ] == "搜索猫图片"
    for call in model.calls[1:]:
        prompt = json.loads(call[1])
        assert prompt["current_user_query"] == "最近的"
        assert prompt["resolved_query"] == "搜索最近的猫图片"
        assert prompt["contextualized_request"]["semantic_frame"]["subjects"] == ["猫"]
        assert "dialogue_history" not in prompt


async def test_empty_business_candidates_fall_back_to_ordinary_dialogue_candidate() -> None:
    model = FakeStructuredClient(
        [
            SkillCandidateSet(candidates=[]),
            RerankDecision(
                verdict="select",
                selected_candidate_id="ordinary_dialogue:000",
                confidence=0.5,
                ranking=[
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.5),
                ],
                reason="没有业务候选",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("今天心情怎么样")

    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert [call[2] for call in model.calls] == [
        SkillCandidateSet,
        RerankDecision,
    ]


async def test_invalid_skill_candidate_set_degrades_to_ordinary_dialogue() -> None:
    model = FakeStructuredClient(
        [
            {"candidates": "not valid json"},
            RerankDecision(
                verdict="select",
                selected_candidate_id="ordinary_dialogue:000",
                confidence=0.5,
                ranking=[
                    CandidateScore(candidate_id="ordinary_dialogue:000", score=0.5),
                ],
                reason="fallback candidate",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("今天心情怎么样")

    assert result.skill is None
    assert result.code == "000"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "select_top"


async def test_invalid_evaluator_output_uses_numeric_fallback() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                resolved_query="画质修复是修复哪些的",
                relation_to_history="new_request",
                semantic_frame=SemanticFrame(
                    action="咨询",
                    expected_result_type="ordinary_answer",
                    subjects=["画质修复"],
                ),
                reason="用户询问功能说明，期望语言答案",
            ),
            SkillCandidateSet(
                candidates=[
                    _skill_candidate("image_skill", confidence=0.9),
                ],
            ),
            IntentCandidateSet(
                candidates=[
                    _intent_candidate(
                        skill_id="image_skill",
                        intent="画质修复",
                        code="009",
                        cues=["画质修复"],
                    ),
                ],
            ),
            {"verdict": "select", "selected_candidate_id": "missing", "ranking": "bad"},
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="修复这张照片",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="image_skill",
                    intent="画质修复",
                    code="009",
                ),
            ),
        ],
    )

    result = await router.route(
        "画质修复是修复哪些的",
        dialogue_history=history,
    )

    assert result.skill is None
    assert result.code == "000"
    assert result.diagnostics is not None
    assert result.diagnostics.evaluator_action == "fallback"
    assert result.diagnostics.fallback_reason
