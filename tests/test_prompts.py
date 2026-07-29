import json

from intent_router.prompts import (
    CONTEXTUALIZER_SYSTEM_PROMPT,
    EVALUATOR_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    ROUTER_SYSTEM_PROMPT,
    build_contextualizer_prompt,
    build_evaluator_prompt,
    build_intent_prompt,
    build_router_prompt,
)
from intent_router.skills import SkillRegistry
from intent_router.types import (
    ContextualizedRequest,
    DialogueHistory,
    DialogueRouteSummary,
    DialogueTurn,
    IntentCandidate,
    SemanticFrame,
)


def test_all_structured_prompts_include_new_output_contracts() -> None:
    prompts = {
        "ContextualizedRequest": CONTEXTUALIZER_SYSTEM_PROMPT,
        "SkillCandidateSet": ROUTER_SYSTEM_PROMPT,
        "IntentCandidateSet": INTENT_SYSTEM_PROMPT,
        "RerankDecision": EVALUATOR_SYSTEM_PROMPT,
    }

    for prompt in prompts.values():
        assert "输出格式约束" in prompt
        assert "只输出符合该结构的对象" in prompt
        assert "禁止输出未列出的字段" in prompt

    assert "semantic_frame" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "SkillCandidateSet" in ROUTER_SYSTEM_PROMPT
    assert "IntentCandidateSet" in INTENT_SYSTEM_PROMPT
    assert "RerankDecision" in EVALUATOR_SYSTEM_PROMPT
    assert "候选集 reranker" in EVALUATOR_SYSTEM_PROMPT
    assert "参数缺失、实体缺失" in EVALUATOR_SYSTEM_PROMPT
    assert "普通对话候选用 skill_id=null" in ROUTER_SYSTEM_PROMPT
    assert "缺参数、缺实体、缺句柄" in INTENT_SYSTEM_PROMPT
    assert "纯实体名" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "默认是资源搜索语义" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "最近有效业务轮" in CONTEXTUALIZER_SYSTEM_PROMPT
    assert "工具入口类请求" in ROUTER_SYSTEM_PROMPT
    assert "具体业务 skill" in ROUTER_SYSTEM_PROMPT
    assert "label_conflict" in EVALUATOR_SYSTEM_PROMPT
    assert "search_vs_tool_entry" in EVALUATOR_SYSTEM_PROMPT


def test_candidate_prompts_do_not_inject_dialogue_history_after_contextualizer() -> None:
    registry = SkillRegistry.from_path("skills")
    search_skill = registry.get("mcloud_search_skill")
    contextualized = ContextualizedRequest(
        resolved_query="搜索猫图片",
        relation_to_history="new_request",
        semantic_frame=SemanticFrame(
            action="搜索",
            expected_result_type="resource",
            object_types=["图片"],
            subjects=["猫"],
        ),
    )
    candidate = IntentCandidate(
        candidate_id="mcloud_search_skill:搜图片:012:1",
        skill_id="mcloud_search_skill",
        skill_name="云盘搜索",
        intent="搜图片",
        code="012",
        matched_cues=["猫", "图片"],
    )

    router_prompt = json.loads(
        build_router_prompt(
            "搜索猫图片",
            registry.cards(),
            contextualized,
            "搜索猫图片",
        ),
    )
    intent_prompt = json.loads(
        build_intent_prompt(
            "搜索猫图片",
            search_skill,
            contextualized,
            "搜索猫图片",
        ),
    )
    evaluator_prompt = json.loads(
        build_evaluator_prompt(
            "搜索猫图片",
            registry.cards(),
            [candidate],
            contextualized,
            "搜索猫图片",
        ),
    )

    assert router_prompt["contextualized_request"]["semantic_frame"]["subjects"] == [
        "猫",
    ]
    assert intent_prompt["skill_id"] == "mcloud_search_skill"
    assert "intent_routing_context" in intent_prompt
    assert "skill_markdown" not in intent_prompt
    assert "execution_instructions" not in json.dumps(
        intent_prompt["intent_routing_context"],
        ensure_ascii=False,
    )
    assert "搜图片" in intent_prompt["intent_routing_context"]["intents"]
    assert "intents" not in router_prompt["available_skills"][0]
    assert "tools_schema_text" not in router_prompt["available_skills"][0]
    assert evaluator_prompt["candidate_ids"] == [
        "mcloud_search_skill:搜图片:012:1",
    ]
    assert "candidate" not in evaluator_prompt
    assert "dialogue_history" not in router_prompt
    assert "dialogue_history" not in intent_prompt
    assert "dialogue_history" not in evaluator_prompt


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
    assert "metadata" not in first
