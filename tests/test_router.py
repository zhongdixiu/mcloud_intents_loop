import json

from intent_router.dialogue import IntentDialogueAgent
from intent_router.model_client import FakeStructuredClient
from intent_router.router import IntentRouter
from intent_router.types import (
    ContextualizedRequest,
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    EvaluationDecision,
    IntentDecision,
    LoopExhaustedClarification,
    SkillRouteDecision,
)


def _matched_search_history(count: int) -> DialogueHistory:
    return DialogueHistory(
        turns=[
            DialogueTurn(
                user_query=f"历史第{index}轮",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜图片",
                    code="012",
                    params={"metadataList": [f"历史{index}"]},
                ),
                metadata={"internal": index},
            )
            for index in range(count)
        ],
    )


async def test_router_returns_matched_result() -> None:
    """：一级 skill、二级 intent、参数、Evaluator accept 后返回 matched；
    同时验证 evaluator prompt 带了可用 skills 和 schema，但不注入 loop state。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={
                    "timeList": ["上个月"],
                    "metadataList": ["猫"],
                    "placeList": ["北京"],
                },
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.7),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找上个月北京拍的猫照片")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜图片"
    assert result.code == "012"
    assert result.params["metadataList"] == ["猫"]

    evaluator_prompt = json.loads(model.calls[2][1])
    assert "available_skills" in evaluator_prompt
    assert any(
        skill["id"] == "mcloud_search_skill"
        for skill in evaluator_prompt["available_skills"]
    )
    assert "tools_schema" in evaluator_prompt["skill"]
    assert "loop_state" not in evaluator_prompt
    assert "dialogue_history" not in evaluator_prompt
    assert evaluator_prompt["resolved_query"] == "帮我找上个月北京拍的猫照片"
    assert evaluator_prompt["current_user_query"] == "帮我找上个月北京拍的猫照片"


async def test_router_retries_after_skill_no_match() -> None:
    """选中的 skill 内部返回 no_match 后，会把该 skill 排除并重新选择其他 skill。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="file_skill",
                confidence=0.9,
            ),
            IntentDecision(status="no_match", reason="search request"),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同", "文件"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜综合"
    assert result.visited_skills == ["file_skill", "mcloud_search_skill"]
    assert result.loop_count == 2
    assert result.correction_scopes == ["skill_no_match"]
    assert model.calls[0][2] is SkillRouteDecision
    assert [call[2] for call in model.calls].count(SkillRouteDecision) == 2


async def test_evaluator_accept_ignores_confidence_gate() -> None:
    """ Evaluator 只要输出 accept 就直接通过，不再因为 confidence 低转澄清。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="accept",
                confidence=0.4,
                clarity_reason="搜索范围不明确",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert result.intent == "搜综合"
    assert result.confidence == 0.8


async def test_router_retries_after_skill_mismatch_evaluation() -> None:
    """Evaluator 判断 skill_mismatch 后，记录 rejected skill，并重新走一级 skill 路由。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="file_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="文件",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="skill_mismatch",
                skill_check="fail",
                confidence=0.8,
                reason="搜索资源应选择云盘搜索",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    trace: list[dict] = []

    result = await router.route("搜索合同文件", trace=trace)

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜综合"
    assert result.visited_skills == ["file_skill", "mcloud_search_skill"]
    assert result.loop_count == 2
    assert result.correction_scopes == ["skill_mismatch"]

    second_router_prompt = json.loads(model.calls[3][1])
    assert second_router_prompt["current_loop_rejected_skill_ids"] == ["file_skill"]
    assert any(
        event.get("event") == "retry" and event.get("scope") == "skill_mismatch"
        for event in trace
    )
    assert trace[-1]["event"] == "result"


async def test_skill_reject_records_rejected_skill_even_with_low_confidence() -> None:
    """ 即使 evaluator confidence 低，只要 verdict=reject + skill_mismatch，也按明确拒绝处理；最终无可用 skill 时返回普通对话兜底。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(status="route", skill_id="file_skill", confidence=0.9),
            IntentDecision(
                status="matched",
                intent="文件",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="skill_mismatch",
                skill_check="fail",
                confidence=0.4,
                reason="可能应该搜索",
                clarity_reason="搜索和入口表达都可能成立",
            ),
            SkillRouteDecision(status="no_match", reason="no remaining skill"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert result.reason == "no remaining skill"
    assert result.visited_skills == ["file_skill"]
    assert result.loop_count == 2
    assert result.correction_scopes == ["skill_mismatch"]
    second_router_prompt = json.loads(model.calls[3][1])
    assert second_router_prompt["current_loop_rejected_skill_ids"] == ["file_skill"]


async def test_router_retries_same_skill_after_intent_mismatch() -> None:
    """ Evaluator 判断 intent_mismatch 后，锁定当前 skill，只重新选择二级 intent，不重新 route skill。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI 修图",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="intent_mismatch",
                skill_check="pass",
                intent_check="fail",
                confidence=0.8,
                reason="具体修图操作应使用 AI改图",
            ),
            IntentDecision(
                status="matched",
                intent="AI改图",
                code="037",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我修一下这张图，把背景换成海边")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "image_skill"
    assert result.intent == "AI改图"
    assert result.code == "037"
    assert result.visited_skills == ["image_skill"]
    assert result.loop_count == 2
    assert result.correction_scopes == ["intent_mismatch"]
    assert [call[2] for call in model.calls].count(SkillRouteDecision) == 1

    second_intent_prompt = model.calls[3][1]
    assert "AI 修图" in second_intent_prompt
    assert "具体修图操作应使用 AI改图" in second_intent_prompt


async def test_intent_reject_records_rejected_intent_even_with_low_confidence() -> None:
    """即使 confidence 低，intent_mismatch 也会写入 rejected intent，下一轮 intent prompt 能看到该负反馈。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(status="route", skill_id="image_skill", confidence=0.9),
            IntentDecision(
                status="matched",
                intent="AI 修图",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="intent_mismatch",
                skill_check="pass",
                intent_check="fail",
                confidence=0.4,
                reason="可能需要具体改图",
                clarity_reason="用户是否要入口不确定",
            ),
            IntentDecision(
                status="matched",
                intent="AI改图",
                code="037",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我修一下这张图")

    assert result.status == "matched"
    second_intent_prompt = json.loads(model.calls[3][1])
    assert second_intent_prompt["rejected_intents"] == [
        {
            "intent": "AI 修图",
            "reason": "可能需要具体改图",
            "scope": "intent_mismatch",
        },
    ]


async def test_router_retries_after_param_mismatch() -> None:
    """ Evaluator 判断 param_mismatch 后，锁定 skill / intent / code，只进入参数修正流程。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦"], "suffixList": ["mp3"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="param_mismatch",
                skill_check="pass",
                intent_check="pass",
                params_check="fail",
                confidence=0.8,
                reason="用户未明确 mp3 后缀，suffixList 存在过度补全",
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦", "歌"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找周杰伦的歌")

    assert result.status == "matched"
    assert result.intent == "搜音频"
    assert "suffixList" not in result.params
    assert result.loop_count == 2
    assert result.correction_scopes == ["param_mismatch"]
    assert [call[2] for call in model.calls].count(SkillRouteDecision) == 1

    param_repair_prompt = json.loads(model.calls[3][1])
    assert param_repair_prompt["locked_intent"] == "搜音频"
    assert param_repair_prompt["locked_code"] == "015"
    assert param_repair_prompt["param_rejections"] == [
        {
            "intent": "搜音频",
            "reason": "用户未明确 mp3 后缀，suffixList 存在过度补全",
            "scope": "param_mismatch",
        },
    ]


async def test_router_injects_dialogue_history_into_all_model_prompts() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                status="resolved",
                resolved_query="只找最近的猫图片",
                relation_to_history="continuation",
                used_history_turns=[1, 2, 3, 4, 5],
                reason="当前输入需要继承历史搜索对象",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"suffixList": ["jpg"]},
                confidence=0.8,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["猫"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query=f"历史第{index}轮",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜图片",
                    code="012",
                    params={"metadataList": [f"历史{index}"]},
                ),
                metadata={"internal": index},
            )
            for index in range(6)
        ],
    )

    result = await router.route("只找最近的", dialogue_history=history)

    assert result.status == "matched"
    contextualizer_prompt = json.loads(model.calls[0][1])
    assert contextualizer_prompt["current_user_query"] == "只找最近的"
    assert len(contextualizer_prompt["dialogue_history"]) == 5
    assert contextualizer_prompt["dialogue_history"][0]["user_query"] == "历史第1轮"
    assert contextualizer_prompt["dialogue_history"][-1]["user_query"] == "历史第5轮"
    assert (
        contextualizer_prompt["dialogue_history"][0]["assistant_result"]["status"]
        == "matched"
    )
    assert "metadata" not in contextualizer_prompt["dialogue_history"][0]
    assert "agent_result" not in contextualizer_prompt["dialogue_history"][0]
    assert "user_feedback" not in contextualizer_prompt["dialogue_history"][0]
    assert "result" not in contextualizer_prompt["dialogue_history"][0]

    for call in model.calls[1:]:
        prompt = json.loads(call[1])
        assert prompt["current_user_query"] == "只找最近的"
        assert prompt["resolved_query"] == "只找最近的猫图片"
        assert "dialogue_history" not in prompt
        assert "last_matched_turn" not in prompt
        assert "decision_query" not in prompt

    assert "上下文语义归一节点" in model.calls[0][0]
    assert "不要伪造 image/content/file" in model.calls[1][0]
    assert "执行载体缺失不算 param_mismatch" in model.calls[-1][0]


async def test_router_uses_configured_dialogue_history_limit() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                status="resolved",
                resolved_query="只找最近的猫图片",
                relation_to_history="continuation",
                used_history_turns=[0, 1, 2, 3, 4, 5, 6],
                reason="当前输入需要继承历史搜索对象",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["猫"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=model,
        dialogue_history_limit=7,
    )
    history = _matched_search_history(7)

    result = await router.route("只找最近的", dialogue_history=history)

    assert result.status == "matched"
    contextualizer_prompt = json.loads(model.calls[0][1])
    assert len(contextualizer_prompt["dialogue_history"]) == 7
    assert contextualizer_prompt["dialogue_history"][0]["user_query"] == "历史第0轮"
    assert contextualizer_prompt["dialogue_history"][-1]["user_query"] == "历史第6轮"


async def test_router_allows_disabling_dialogue_history_injection() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                status="resolved",
                resolved_query="只找最近的",
                relation_to_history="new_request",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["最近"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=model,
        dialogue_history_limit=0,
    )
    history = _matched_search_history(3)

    result = await router.route("只找最近的", dialogue_history=history)

    assert result.status == "matched"
    contextualizer_prompt = json.loads(model.calls[0][1])
    assert contextualizer_prompt["dialogue_history"] == []


async def test_param_reject_records_param_rejection_even_with_low_confidence() -> None:
    """ 即使 confidence 低，param_mismatch 也会记录参数拒绝原因，并传给参数修正 prompt；修正失败时返回普通对话兜底。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦"], "suffixList": ["mp3"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="param_mismatch",
                skill_check="pass",
                intent_check="pass",
                params_check="fail",
                confidence=0.4,
                reason="可能过度补全 mp3",
                clarity_reason="用户没有明确文件后缀",
            ),
            IntentDecision(status="no_match", reason="cannot repair params"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找周杰伦的歌")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert result.reason == "cannot repair params"
    param_repair_prompt = json.loads(model.calls[3][1])
    assert param_repair_prompt["param_rejections"] == [
        {
            "intent": "搜音频",
            "reason": "可能过度补全 mp3",
            "scope": "param_mismatch",
        },
    ]


async def test_max_attempts_reject_returns_guided_clarify() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦"], "suffixList": ["mp3"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="param_mismatch",
                skill_check="pass",
                intent_check="pass",
                params_check="fail",
                reason="用户未明确 mp3 后缀",
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦"], "suffixList": ["mp3"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="param_mismatch",
                skill_check="pass",
                intent_check="pass",
                params_check="fail",
                reason="仍然过度补全 mp3 后缀",
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦"], "suffixList": ["mp3"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reject_scope="param_mismatch",
                skill_check="pass",
                intent_check="pass",
                params_check="fail",
                reason="仍然无法确认文件后缀",
            ),
            LoopExhaustedClarification(
                question="您想按哪些关键词或文件类型搜索这首歌？",
                options=[
                    {"label": "只按关键词搜索", "value": "只按关键词搜索"},
                    {"label": "指定音频格式", "value": "指定音频格式"},
                ],
                reason="多次卡在搜索条件是否包含文件格式",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找周杰伦的歌")

    assert result.status == "clarify"
    assert result.question == "您想按哪些关键词或文件类型搜索这首歌？"
    assert result.termination_reason == "loop_exhausted"
    assert result.reason == "多次卡在搜索条件是否包含文件格式"
    assert result.loop_count == 3
    assert result.correction_scopes == ["param_mismatch"]
    assert model.calls[-1][2] is LoopExhaustedClarification

    clarifier_prompt = json.loads(model.calls[-1][1])
    assert clarifier_prompt["resolved_query"] == "帮我找周杰伦的歌"
    assert len(clarifier_prompt["attempts"]) == 3
    assert clarifier_prompt["attempts"][-1]["reject_scope"] == "param_mismatch"
    assert clarifier_prompt["attempts"][-1]["reject_reason"] == "仍然无法确认文件后缀"


async def test_router_reject_without_scope_returns_stable_no_match() -> None:
    """ Evaluator 输出 reject 但没有 reject_scope 时，视为无效评估，不猜测错误层级，并进入稳定失败路径。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同"]},
                confidence=0.8,
            ),
            EvaluationDecision(
                verdict="reject",
                reason="invalid evaluator output",
            ),
            SkillRouteDecision(status="no_match", reason="no remaining skill"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.status == "no_match"
    assert result.reason == "no remaining skill"
    assert [call[2] for call in model.calls].count(SkillRouteDecision) == 2


async def test_router_can_use_separate_evaluator_client() -> None:
    """支持主识别模型和 evaluator 模型分开配置；router / intent 用主模型，evaluation 用 evaluator 模型。"""
    route_model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同"]},
                confidence=0.8,
            ),
        ],
    )
    evaluator_model = FakeStructuredClient(
        [
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config(
        "skills",
        model_client=route_model,
        evaluator_client=evaluator_model,
    )

    result = await router.route("搜索合同文件")

    assert result.status == "matched"
    assert [call[2] for call in route_model.calls] == [
        SkillRouteDecision,
        IntentDecision,
    ]
    assert [call[2] for call in evaluator_model.calls] == [EvaluationDecision]


async def test_router_returns_dialogue_fallback_for_unsupported_capability() -> None:
    """ 一级路由判断能力不支持时，直接返回普通对话兜底意图。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="现有 skills 不支持文生视频",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("生成一段小狗奔跑的视频")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert result.params == {}
    assert result.confidence == 0.0
    assert result.reason == "现有 skills 不支持文生视频"


async def test_router_returns_dialogue_fallback_for_small_talk() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="普通寒暄，不需要业务 skill",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("你好")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert result.reason == "普通寒暄，不需要业务 skill"


async def test_router_treats_public_news_search_as_dialogue_fallback() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="公共互联网资讯查询，不是云盘资源搜索",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜一下今天的 AI 新闻")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert result.reason == "公共互联网资讯查询，不是云盘资源搜索"

    router_prompt = json.loads(model.calls[0][1])
    assert router_prompt["resolved_query"] == "搜一下今天的 AI 新闻"
    assert "资讯问答" in model.calls[0][0]
    assert "普通对话 code=000" in model.calls[0][0]


async def test_router_prompt_separates_answer_targets_from_resource_targets() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="推荐建议属于普通对话",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("推荐几部刘德华的电影和歌曲")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"

    system_prompt = model.calls[0][0]
    assert "主动作、目标对象和期望结果形态" in system_prompt
    assert "语言答案或信息服务" in system_prompt
    assert "推荐建议" in system_prompt
    assert "由系统映射为普通对话 code=000" in system_prompt


async def test_router_routes_resource_target_search_to_mcloud_search() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜影视",
                code="014",
                params={"metadataList": ["刘德华"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索刘德华的电影")

    assert result.status == "matched"
    assert result.skill is not None
    assert result.skill.id == "mcloud_search_skill"
    assert result.intent == "搜影视"
    assert result.code == "014"

    router_system_prompt = model.calls[0][0]
    assert "主动作是查找、搜索、定位、获取" in router_system_prompt
    assert "已有资源载体" in router_system_prompt
    intent_prompt = json.loads(model.calls[1][1])
    assert "资源目标 vs 答案目标" in intent_prompt["skill_markdown"]
    assert "默认理解为云盘资源检索" in intent_prompt["skill_markdown"]


async def test_dialogue_agent_injects_000_as_non_tool_history() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="普通寒暄，不需要业务 skill",
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="帮我找图片",
                relation_to_history="new_request",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("你好")
    second = await agent.send("帮我找图片")

    assert first.status == "matched"
    assert first.skill is None
    assert first.intent == "普通对话"
    assert first.code == "000"
    assert second.status == "matched"
    assert second.intent == "搜图片"

    contextualizer_prompt = json.loads(model.calls[1][1])
    assistant_result = contextualizer_prompt["dialogue_history"][0]["assistant_result"]
    assert assistant_result["status"] == "matched"
    assert assistant_result["skill_id"] is None
    assert assistant_result["intent"] == "普通对话"
    assert assistant_result["code"] == "000"


async def test_dialogue_agent_returns_clarification_and_uses_history() -> None:
    """一级路由返回 clarify 后，外层对话 Agent 将历史注入下一轮 route。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="clarify",
                question="你想搜索资源还是打开入口？",
                options=[
                    {"label": "搜索资源", "value": "search"},
                    {"label": "打开入口", "value": "open"},
                ],
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="打开文件入口",
                relation_to_history="new_request",
                reason="当前输入已能独立表达入口诉求",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="file_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="文件",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("帮我找一下")
    assert first.status == "clarify"
    assert len(agent.history.turns) == 1
    assert agent.history.turns[0].user_query == "帮我找一下"
    assert agent.history.turns[0].result.question == "你想搜索资源还是打开入口？"

    second = await agent.send("打开文件入口")
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "file_skill"
    assert second.intent == "文件"
    second_context_prompt = json.loads(model.calls[1][1])
    assert second_context_prompt["dialogue_history"][0]["assistant_result"]["status"] == (
        "clarify"
    )
    assert second_context_prompt["dialogue_history"][0]["assistant_result"]["question"] == (
        "你想搜索资源还是打开入口？"
    )
    second_router_prompt = json.loads(model.calls[2][1])
    assert second_router_prompt["resolved_query"] == "打开文件入口"


async def test_dialogue_agent_uses_matched_history_for_followup_image_caption() -> None:
    """搜索结果后的继续处理应由历史意图语义承接，不因缺少真实图片句柄而澄清。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["蓝色天空"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            ContextualizedRequest(
                status="resolved",
                resolved_query="给蓝色天空图片配上文字",
                relation_to_history="continuation",
                used_history_turns=[0],
                reason="当前输入中的它指向上一轮搜索图片语义",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI相机拍照问答",
                code="036006",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("帮我找蓝色天空的图片")
    second = await agent.send("帮我给它配上文字")

    assert first.status == "matched"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "image_skill"
    assert second.intent == "AI相机拍照问答"
    assert second.code == "036006"
    assert second.params == {}
    assert second.loop_count == 1
    second_context_prompt = json.loads(model.calls[3][1])
    assert second_context_prompt["dialogue_history"][0]["user_query"] == (
        "帮我找蓝色天空的图片"
    )
    assert second_context_prompt["dialogue_history"][0]["assistant_result"]["intent"] == (
        "搜图片"
    )
    assert second_context_prompt["dialogue_history"][0]["assistant_result"]["params"] == {
        "metadataList": ["蓝色天空"],
    }
    second_router_prompt = json.loads(model.calls[4][1])
    assert second_router_prompt["resolved_query"] == "给蓝色天空图片配上文字"


async def test_dialogue_agent_continues_ordinary_recommendation_context() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="推荐建议属于普通对话",
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="推荐刘德华的电影",
                relation_to_history="continuation",
                used_history_turns=[0],
                reason="当前短输入替换对象类型，沿用上一轮推荐任务和主体",
            ),
            SkillRouteDecision(
                status="no_match",
                reason="推荐建议属于普通对话",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("推荐刘德华的歌曲")
    second = await agent.send("有没有电影")

    assert first.status == "matched"
    assert first.skill is None
    assert first.intent == "普通对话"
    assert first.code == "000"
    assert second.status == "matched"
    assert second.skill is None
    assert second.intent == "普通对话"
    assert second.code == "000"
    assert second.resolved_query == "推荐刘德华的电影"
    assert second.context_relation == "continuation"

    contextualizer_system_prompt = model.calls[1][0]
    contextualizer_prompt = json.loads(model.calls[1][1])
    assistant_result = contextualizer_prompt["dialogue_history"][0]["assistant_result"]
    assert assistant_result["status"] == "matched"
    assert assistant_result["code"] == "000"
    assert "任务框架" in contextualizer_system_prompt
    assert "继承历史主动作和核心主体" in contextualizer_system_prompt
    assert "code=000" in contextualizer_system_prompt
    assert "可用于承接推荐、问答、解释等答案型语义" in contextualizer_system_prompt


async def test_dialogue_agent_switches_from_search_history_to_image_generation() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["谢娜"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            ContextualizedRequest(
                status="resolved",
                resolved_query="生成一些近期的谢娜图片，素材来源是之前保存的图片",
                relation_to_history="revision",
                used_history_turns=[0],
                reason="当前输入明确切换主动作为生成，历史只补充谢娜图片主体和素材来源",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="文生图",
                code="002",
                params={"keywords": "近期的谢娜图片"},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("搜索谢娜的图片")
    second = await agent.send("再帮我生成一些近期的，这些都是之前保存的")

    assert first.status == "matched"
    assert first.skill is not None
    assert first.skill.id == "mcloud_search_skill"
    assert first.intent == "搜图片"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "image_skill"
    assert second.intent == "文生图"
    assert second.code == "002"
    assert second.resolved_query == "生成一些近期的谢娜图片，素材来源是之前保存的图片"
    assert second.context_relation == "revision"

    contextualizer_system_prompt = model.calls[3][0]
    assert "当前动作优先" in contextualizer_system_prompt
    assert "不应归一为历史主动作" in contextualizer_system_prompt
    router_system_prompt = model.calls[4][0]
    assert "主动作是生成、创作、编辑、处理、配文、识别、翻译、鉴伪、修复、总结或问答" in router_system_prompt
    second_router_prompt = json.loads(model.calls[4][1])
    assert second_router_prompt["resolved_query"] == (
        "生成一些近期的谢娜图片，素材来源是之前保存的图片"
    )
    image_intent_prompt = json.loads(model.calls[5][1])
    assert "历史指代、素材来源、时间范围" in image_intent_prompt["skill_markdown"]
    assert "输出\"文生图\"工具" in image_intent_prompt["skill_markdown"]


async def test_dialogue_agent_keeps_search_refinement_when_action_is_still_search() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["谢娜"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            ContextualizedRequest(
                status="resolved",
                resolved_query="查找近期保存的谢娜图片",
                relation_to_history="continuation",
                used_history_turns=[0],
                reason="当前输入仍是搜索细化，只补充时间和保存来源限定",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["谢娜"], "timeList": ["近期"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("搜索谢娜的图片")
    second = await agent.send("只找近期保存的")

    assert first.status == "matched"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "mcloud_search_skill"
    assert second.intent == "搜图片"
    assert second.resolved_query == "查找近期保存的谢娜图片"


async def test_dialogue_agent_current_search_action_overrides_ordinary_history() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="no_match",
                reason="推荐建议属于普通对话",
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="搜索刘德华的电影资源",
                relation_to_history="revision",
                used_history_turns=[0],
                reason="当前输入明确切换为搜索动作，历史只补充刘德华主体",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜影视",
                code="014",
                params={"metadataList": ["刘德华"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("推荐刘德华的歌曲")
    second = await agent.send("搜索他的电影资源")

    assert first.status == "matched"
    assert first.intent == "普通对话"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "mcloud_search_skill"
    assert second.intent == "搜影视"
    assert second.resolved_query == "搜索刘德华的电影资源"


async def test_dialogue_agent_contextualizes_short_followup_after_clarify() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="您是想搜索包含“蓝色天空”文字的文档，还是图片？",
                options=[
                    {"label": "搜文档", "value": "搜文档"},
                    {"label": "搜图片", "value": "搜图片"},
                ],
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="搜索包含蓝色天空文字的图片",
                relation_to_history="continuation",
                used_history_turns=[0],
                reason="当前短输入与上一轮搜索歧义兼容，归一为完整搜索图片请求",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["蓝色天空", "文字"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("搜 蓝色天空文字")
    second = await agent.send("蓝图片")

    assert first.status == "clarify"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "mcloud_search_skill"
    assert second.intent == "搜图片"
    assert second.resolved_query == "搜索包含蓝色天空文字的图片"
    assert second.context_relation == "continuation"

    second_router_prompt = json.loads(model.calls[3][1])
    assert second_router_prompt["resolved_query"] == "搜索包含蓝色天空文字的图片"
    assert "dialogue_history" not in second_router_prompt


async def test_dialogue_agent_resolves_clarify_answer_to_complete_query() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜综合",
                code="018",
                params={"metadataList": ["合同"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            ContextualizedRequest(
                status="clarify",
                relation_to_history="ambiguous",
                used_history_turns=[0],
                question="“蓝色”是指搜索蓝色的图片，还是蓝色的合同文件？",
                options=[
                    {"label": "搜蓝色的图片", "value": "搜蓝色的图片"},
                    {"label": "搜蓝色的合同文件", "value": "搜蓝色的合同文件"},
                ],
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="搜蓝色的图片",
                relation_to_history="answer_to_previous",
                used_history_turns=[1],
                reason="当前输入是在回答上一轮澄清的对象类型维度",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"metadataList": ["蓝色"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("搜合同文件")
    second = await agent.send("蓝色")
    third = await agent.send("图片")

    assert first.status == "matched"
    assert first.resolved_query == "搜合同文件"
    assert second.status == "clarify"
    assert second.resolved_query == "蓝色"
    assert second.context_relation == "ambiguous"
    assert third.status == "matched"
    assert third.intent == "搜图片"
    assert third.params == {"metadataList": ["蓝色"]}
    assert third.resolved_query == "搜蓝色的图片"
    assert third.context_relation == "answer_to_previous"

    third_context_prompt = json.loads(model.calls[4][1])
    clarify_result = third_context_prompt["dialogue_history"][-1]["assistant_result"]
    assert clarify_result["status"] == "clarify"
    assert clarify_result["question"] == (
        "“蓝色”是指搜索蓝色的图片，还是蓝色的合同文件？"
    )
    assert clarify_result["options"] == [
        {"label": "搜蓝色的图片", "value": "搜蓝色的图片"},
        {"label": "搜蓝色的合同文件", "value": "搜蓝色的合同文件"},
    ]
    assert clarify_result["resolved_query"] == "蓝色"
    assert clarify_result["context_relation"] == "ambiguous"

    third_router_prompt = json.loads(model.calls[5][1])
    assert third_router_prompt["resolved_query"] == "搜蓝色的图片"
    assert "dialogue_history" not in third_router_prompt


async def test_contextualizer_clarifies_ambiguous_search_refinement() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                status="clarify",
                relation_to_history="ambiguous",
                used_history_turns=[0],
                reason="无法判断蓝色是在替换历史关键词还是追加筛选合同文件",
                question="您是要重新搜索蓝色相关内容，还是搜索蓝色合同文件？",
                options=[
                    {"label": "重新搜索蓝色相关内容", "value": "new_blue_search"},
                    {"label": "搜索蓝色合同文件", "value": "blue_contract_files"},
                ],
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="搜合同文件",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    skill_name="云盘搜索",
                    intent="搜综合",
                    code="018",
                    params={"metadataList": ["合同", "文件"]},
                ),
            ),
        ],
    )

    result = await router.route("搜蓝色", dialogue_history=history)

    assert result.status == "clarify"
    assert result.context_relation == "ambiguous"
    assert "蓝色合同文件" in (result.question or "")
    assert [call[2] for call in model.calls] == [ContextualizedRequest]

    system_prompt = model.calls[0][0]
    contextualizer_prompt = json.loads(model.calls[0][1])
    assistant_result = contextualizer_prompt["dialogue_history"][0]["assistant_result"]
    assert contextualizer_prompt["current_user_query"] == "搜蓝色"
    assert assistant_result["params"] == {"metadataList": ["合同", "文件"]}
    assert "判别性主体" in system_prompt
    assert "主动作、目标类型、核心主体、对象类型、限定条件、输入来源" in system_prompt
    assert "新请求、替换历史主体、切换主动作、或在历史主体上追加限定条件" in system_prompt
    assert "不是封闭枚举" in system_prompt
    assert "answer_to_previous" in system_prompt
    assert "不能只输出当前短回答片段" in system_prompt


async def test_dialogue_history_does_not_inject_no_match_reason() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                status="resolved",
                resolved_query="重新搜索图片",
                relation_to_history="new_request",
            ),
            SkillRouteDecision(status="no_match", reason="no supported skill"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="蓝图片",
                result=DialogueRouteSummary(
                    status="no_match",
                    reason=(
                        "云盘搜索技能（mcloud_search_skill）和文件管理技能"
                        "均已被拒绝（rejected_skill_ids）"
                    ),
                ),
            ),
        ],
    )

    result = await router.route("重新搜图片", dialogue_history=history)

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    contextualizer_prompt = json.loads(model.calls[0][1])
    assistant_result = contextualizer_prompt["dialogue_history"][0]["assistant_result"]
    assert assistant_result == {"status": "no_match"}
    assert "rejected_skill_ids" not in model.calls[0][1]


async def test_dialogue_history_does_not_inject_loop_exhausted_internal_state() -> None:
    model = FakeStructuredClient(
        [
            ContextualizedRequest(
                status="resolved",
                resolved_query="只按关键词搜索周杰伦的歌",
                relation_to_history="answer_to_previous",
                used_history_turns=[0],
            ),
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜音频",
                code="015",
                params={"metadataList": ["周杰伦", "歌"]},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="帮我找周杰伦的歌",
                result=DialogueRouteSummary(
                    status="clarify",
                    question="您想按哪些关键词或文件类型搜索这首歌？",
                    options=[
                        {"label": "只按关键词搜索", "value": "只按关键词搜索"},
                        {"label": "指定音频格式", "value": "指定音频格式"},
                    ],
                    reason="多次卡在搜索条件是否包含文件格式",
                ),
                metadata={
                    "termination_reason": "loop_exhausted",
                    "rejections": [{"scope": "param_mismatch"}],
                },
            ),
        ],
    )

    result = await router.route("只按关键词", dialogue_history=history)

    assert result.status == "matched"
    contextualizer_prompt = json.loads(model.calls[0][1])
    assistant_result = contextualizer_prompt["dialogue_history"][0]["assistant_result"]
    assert assistant_result == {
        "status": "clarify",
        "question": "您想按哪些关键词或文件类型搜索这首歌？",
        "options": [
            {"label": "只按关键词搜索", "value": "只按关键词搜索"},
            {"label": "指定音频格式", "value": "指定音频格式"},
        ],
    }
    assert "loop_exhausted" not in model.calls[0][1]
    assert "param_mismatch" not in model.calls[0][1]


async def test_router_rejects_invalid_candidate_and_returns_dialogue_fallback() -> None:
    """ intent 输出结构不合法参数时，进入参数修正；修正失败后返回普通对话兜底，不重新误伤 skill。"""
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="mcloud_search_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="搜图片",
                code="012",
                params={"suffixList": ["jpg"]},
                confidence=0.8,
            ),
            IntentDecision(status="no_match", reason="cannot repair params"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("找猫照片")

    assert result.status == "matched"
    assert result.skill is None
    assert result.intent == "普通对话"
    assert result.code == "000"
    assert result.reason == "cannot repair params"
    assert "mcloud_search_skill" in result.visited_skills
    assert [call[2] for call in model.calls].count(SkillRouteDecision) == 1


async def test_router_returns_clarification_for_overlapping_baby_intents() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想基于哪类照片预测宝宝样子？",
                options=[
                    {"label": "已出生宝宝照片", "value": "baby_photo"},
                    {"label": "父母双方照片", "value": "parents_photo"},
                ],
                reason="宝宝时光机和宝宝长相预测依赖不同输入来源",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("我宝宝未来的样子")

    assert result.status == "clarify"
    assert result.question == "你想基于哪类照片预测宝宝样子？"

    intent_system_prompt = model.calls[1][0]
    assert "竞争意图检查" in intent_system_prompt
    assert "输入来源" in intent_system_prompt
    assert "具体操作类能力优先于入口类能力" in intent_system_prompt


async def test_dialogue_agent_uses_baby_clarification_for_time_machine() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想基于哪类照片预测宝宝样子？",
                options=[
                    {"label": "已出生宝宝照片", "value": "baby_photo"},
                    {"label": "父母双方照片", "value": "parents_photo"},
                ],
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="基于已出生宝宝照片预测宝宝未来的样子",
                relation_to_history="continuation",
                used_history_turns=[0],
                reason="当前输入补充了上一轮缺失的照片类型",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="宝宝时光机",
                code="029",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("我宝宝未来的样子")
    second = await agent.send("我有已出生宝宝照片")

    assert first.status == "clarify"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "image_skill"
    assert second.intent == "宝宝时光机"
    assert second.code == "029"
    second_context_prompt = json.loads(model.calls[2][1])
    assert second_context_prompt["dialogue_history"][0]["assistant_result"]["status"] == (
        "clarify"
    )


async def test_dialogue_agent_uses_baby_clarification_for_appearance_prediction() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想基于哪类照片预测宝宝样子？",
                options=[
                    {"label": "已出生宝宝照片", "value": "baby_photo"},
                    {"label": "父母双方照片", "value": "parents_photo"},
                ],
            ),
            ContextualizedRequest(
                status="resolved",
                resolved_query="基于父母双方照片预测宝宝未来的样子",
                relation_to_history="continuation",
                used_history_turns=[0],
                reason="当前输入补充了上一轮缺失的照片类型",
            ),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="宝宝长相预测",
                code="030",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)
    agent = IntentDialogueAgent(router)

    first = await agent.send("我宝宝未来的样子")
    second = await agent.send("用父母双方照片预测")

    assert first.status == "clarify"
    assert second.status == "matched"
    assert second.skill is not None
    assert second.skill.id == "image_skill"
    assert second.intent == "宝宝长相预测"
    assert second.code == "030"


async def test_router_clarifies_ambiguous_photo_repair() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="clarify",
                question="你想修复老照片，还是提升照片清晰度？",
                options=[
                    {"label": "老照片修复", "value": "old_photo"},
                    {"label": "提升清晰度", "value": "quality"},
                ],
                reason="修复照片未说明处理对象或目标",
            ),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我修复照片")

    assert result.status == "clarify"
    assert result.question == "你想修复老照片，还是提升照片清晰度？"


async def test_router_matches_specific_photo_repair_intents() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="老照片修复",
                code="008",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="画质修复",
                code="009",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    old_photo = await router.route("老照片修复一下")
    high_quality = await router.route("让这张照片更清晰")

    assert old_photo.status == "matched"
    assert old_photo.intent == "老照片修复"
    assert high_quality.status == "matched"
    assert high_quality.intent == "画质修复"


async def test_router_distinguishes_entry_and_specific_edit_operation() -> None:
    model = FakeStructuredClient(
        [
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI 修图",
                code="021",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
            SkillRouteDecision(
                status="route",
                skill_id="image_skill",
                confidence=0.9,
            ),
            IntentDecision(
                status="matched",
                intent="AI改图",
                code="037",
                params={},
                confidence=0.8,
            ),
            EvaluationDecision(verdict="accept", confidence=0.8),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    entry = await router.route("打开AI修图")
    edit = await router.route("把这张图背景换成海边")

    assert entry.status == "matched"
    assert entry.intent == "AI 修图"
    assert edit.status == "matched"
    assert edit.intent == "AI改图"


if __name__ == "__main__":
    test_dialogue_agent_returns_clarification_and_uses_history()
    test_router_retries_after_skill_no_match()
    test_router_retries_same_skill_after_intent_mismatch()
    test_router_rejects_invalid_candidate_and_returns_dialogue_fallback()
