import json

from intent_router.dialogue import IntentDialogueAgent
from intent_router.model_client import FakeStructuredClient
from intent_router.router import IntentRouter
from intent_router.types import (
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    EvaluationDecision,
    IntentDecision,
    SkillRouteDecision,
)


async def test_router_returns_matched_result() -> None:
    """：一级 skill、二级 intent、参数、Evaluator accept 后返回 matched；
    同时验证 evaluator prompt 带了可用 skills、schema 和 loop state。"""
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
    assert evaluator_prompt["loop_state"] == {
        "visited_skills": ["mcloud_search_skill"],
        "rejected_skill_ids": [],
        "rejected_intents": {},
    }
    assert evaluator_prompt["dialogue_history"] == []
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
    assert second_router_prompt["rejected_skill_ids"] == ["file_skill"]
    assert any(
        event.get("event") == "retry" and event.get("scope") == "skill_mismatch"
        for event in trace
    )
    assert trace[-1]["event"] == "result"


async def test_skill_reject_records_rejected_skill_even_with_low_confidence() -> None:
    """ 即使 evaluator confidence 低，只要 verdict=reject + skill_mismatch，也按明确拒绝处理，写入 rejected skill。"""
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
                skill_check="unclear",
                confidence=0.4,
                reason="可能应该搜索",
                clarity_reason="搜索和入口表达都可能成立",
            ),
            SkillRouteDecision(status="no_match", reason="no remaining skill"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("搜索合同文件")

    assert result.status == "no_match"
    second_router_prompt = json.loads(model.calls[3][1])
    assert second_router_prompt["rejected_skill_ids"] == ["file_skill"]


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
                intent_check="unclear",
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
    for call in model.calls:
        prompt = json.loads(call[1])
        assert prompt["current_user_query"] == "只找最近的"
        assert len(prompt["dialogue_history"]) == 5
        assert prompt["dialogue_history"][0]["user_query"] == "历史第1轮"
        assert prompt["dialogue_history"][-1]["user_query"] == "历史第5轮"
        assert prompt["dialogue_history"][0]["assistant_result"]["status"] == "matched"
        assert "metadata" not in prompt["dialogue_history"][0]
        assert "agent_result" not in prompt["dialogue_history"][0]
        assert "user_feedback" not in prompt["dialogue_history"][0]
        assert "result" not in prompt["dialogue_history"][0]
        assert "last_matched_turn" not in prompt
        assert "decision_query" not in prompt

    assert "assistant_result 是历史意图识别结果" in model.calls[0][0]
    assert "不要伪造 image/content/file" in model.calls[1][0]
    assert "执行载体缺失不算 param_mismatch" in model.calls[-1][0]


async def test_param_reject_records_param_rejection_even_with_low_confidence() -> None:
    """ 即使 confidence 低，param_mismatch 也会记录参数拒绝原因，并传给参数修正 prompt。"""
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
                params_check="unclear",
                confidence=0.4,
                reason="可能过度补全 mp3",
                clarity_reason="用户没有明确文件后缀",
            ),
            IntentDecision(status="no_match", reason="cannot repair params"),
        ],
    )
    router = IntentRouter.from_config("skills", model_client=model)

    result = await router.route("帮我找周杰伦的歌")

    assert result.status == "no_match"
    param_repair_prompt = json.loads(model.calls[3][1])
    assert param_repair_prompt["param_rejections"] == [
        {
            "intent": "搜音频",
            "reason": "可能过度补全 mp3",
            "scope": "param_mismatch",
        },
    ]


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


async def test_router_returns_no_match_for_unsupported_capability() -> None:
    """ 一级路由判断能力不支持时，直接返回 no_match。"""
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

    assert result.status == "no_match"
    assert result.reason == "现有 skills 不支持文生视频"


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
    second_router_prompt = json.loads(model.calls[1][1])
    assert second_router_prompt["dialogue_history"][0]["assistant_result"]["status"] == (
        "clarify"
    )
    assert second_router_prompt["dialogue_history"][0]["assistant_result"]["question"] == (
        "你想搜索资源还是打开入口？"
    )


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
    second_router_prompt = json.loads(model.calls[3][1])
    assert second_router_prompt["dialogue_history"][0]["user_query"] == (
        "帮我找蓝色天空的图片"
    )
    assert second_router_prompt["dialogue_history"][0]["assistant_result"]["intent"] == (
        "搜图片"
    )
    assert second_router_prompt["dialogue_history"][0]["assistant_result"]["params"] == {
        "metadataList": ["蓝色天空"],
    }


async def test_router_rejects_invalid_candidate_and_returns_no_match() -> None:
    """ intent 输出结构不合法参数时，进入参数修正；修正失败后返回 no_match，不重新误伤 skill。"""
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

    assert result.status == "no_match"
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
    second_router_prompt = json.loads(model.calls[2][1])
    assert second_router_prompt["dialogue_history"][0]["assistant_result"]["status"] == (
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
    test_router_rejects_invalid_candidate_and_returns_no_match()
