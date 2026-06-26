import json

from intent_router.prompts import (
    CONTEXTUALIZER_SYSTEM_PROMPT,
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT,
    PARAM_REPAIR_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_contextualizer_prompt,
)
from intent_router.types import DialogueHistory, DialogueRouteSummary, DialogueTurn


def test_all_structured_prompts_include_output_contracts() -> None:
    prompts = {
        "ContextualizedRequest": CONTEXTUALIZER_SYSTEM_PROMPT,
        "SkillRouteDecision": ROUTER_SYSTEM_PROMPT,
        "IntentDecision": INTENT_SYSTEM_PROMPT,
        "ParamRepairIntentDecision": PARAM_REPAIR_SYSTEM_PROMPT,
        "EvaluationDecision": EVALUATOR_SYSTEM_PROMPT,
        "LoopExhaustedClarification": LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT,
    }

    for prompt in prompts.values():
        assert "输出格式约束" in prompt
        assert "只输出符合该结构的对象" in prompt
        assert "禁止输出未列出的字段" in prompt

    assert "status: 必填，枚举值只能是 resolved / clarify" in (
        CONTEXTUALIZER_SYSTEM_PROMPT
    )
    assert "relation_to_history: 可选，枚举值只能是 new_request" in (
        CONTEXTUALIZER_SYSTEM_PROMPT
    )
    assert "status: 必填，枚举值只能是 route / clarify / no_match" in (
        ROUTER_SYSTEM_PROMPT
    )
    assert "status: 必填，枚举值只能是 matched / clarify / no_match" in (
        INTENT_SYSTEM_PROMPT
    )
    assert "status: 必填，枚举值只能是 matched / clarify / no_match" in (
        PARAM_REPAIR_SYSTEM_PROMPT
    )
    assert "verdict: 必填，枚举值只能是 accept / reject / clarify" in (
        EVALUATOR_SYSTEM_PROMPT
    )
    assert "question: 必填，类型为 string" in (
        LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT
    )
    assert "semantic_state.resolved_query 是该轮已经归一后的历史语义状态" in (
        CONTEXTUALIZER_SYSTEM_PROMPT
    )
    assert "有没有、是否、有吗、还有吗" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "问句形态或答案型表达，不是搜索、打开、生成、管理等业务主动作" in (
        CONTEXTUALIZER_SYSTEM_PROMPT
    )
    assert "current_user_query 用于校验本轮显式业务动作和问句形态" in (
        ROUTER_SYSTEM_PROMPT
    )
    assert "不能仅因出现电影、图片、歌曲、近期、保存等资源词" in (
        EVALUATOR_SYSTEM_PROMPT
    )


def test_contextualizer_history_payload_splits_semantic_state() -> None:
    history = DialogueHistory(
        turns=[
            DialogueTurn(
                user_query="帮我找蓝色",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜图片",
                    code="012",
                    params={"metadataList": ["蓝色"]},
                    resolved_query="搜索蓝色图片",
                    context_relation="new_request",
                ),
            ),
            DialogueTurn(
                user_query="只要ppt格式",
                result=DialogueRouteSummary(
                    status="matched",
                    skill_id="mcloud_search_skill",
                    intent="搜文档",
                    code="013",
                    params={"suffixList": ["ppt"]},
                ),
            ),
        ],
    )

    prompt = json.loads(build_contextualizer_prompt("最近的", history))
    first, second = prompt["dialogue_history"]

    assert first["semantic_state"] == {
        "status": "matched",
        "resolved_query": "搜索蓝色图片",
        "relation_to_previous": "new_request",
    }
    assert second["semantic_state"] == {
        "status": "matched",
        "resolved_query": "只要ppt格式",
        "relation_to_previous": None,
    }
    assert "resolved_query" not in first["assistant_result"]
    assert "context_relation" not in first["assistant_result"]
    assert "task_type" not in first["semantic_state"]
    assert "task_type" not in first["assistant_result"]
