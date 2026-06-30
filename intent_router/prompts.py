from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from .types import (
    ContextualizedRequest,
    DialogueHistory,
    IntentCandidate,
    IntentCandidateSet,
    RerankDecision,
    SkillCandidate,
    SkillCandidateSet,
    SkillCard,
    SkillDefinition,
)


DIALOGUE_HISTORY_LIMIT = 5


def build_output_contract(model: type[BaseModel]) -> str:
    schema = model.model_json_schema()
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    lines = [
        f"输出格式约束（{model.__name__}）：",
        "- 只输出符合该结构的对象，不输出 Markdown、解释文字或代码块。",
        "- 只允许下列字段，禁止输出未列出的字段。",
    ]
    for field_name, field_info in model.model_fields.items():
        property_schema = properties.get(field_name, {})
        requirement = "必填" if field_name in required else "可选"
        type_text = _schema_type_text(property_schema)
        default = ""
        if not field_info.is_required():
            default = f"，默认值={field_info.default!r}"
        lines.append(f"- {field_name}: {requirement}，{type_text}{default}")
    return "\n".join(lines)


def _schema_type_text(schema: dict[str, Any]) -> str:
    enum_values = _collect_schema_values(schema, "enum")
    if enum_values:
        return "枚举值只能是 " + " / ".join(str(item) for item in enum_values)
    const_values = _collect_schema_values(schema, "const")
    if const_values:
        return "固定值 " + " / ".join(str(item) for item in const_values)
    type_values = _collect_schema_types(schema)
    if type_values:
        return "类型为 " + " 或 ".join(type_values)
    return "类型需符合 schema"


def _collect_schema_values(schema: dict[str, Any], key: str) -> list[Any]:
    values: list[Any] = []

    def visit(item: Any) -> None:
        if not isinstance(item, dict):
            return
        if key in item:
            raw_value = item[key]
            if isinstance(raw_value, list):
                for value in raw_value:
                    if value is not None and value not in values:
                        values.append(value)
            elif raw_value is not None and raw_value not in values:
                values.append(raw_value)
        for child_key in ("anyOf", "oneOf", "allOf"):
            for child in item.get(child_key, []):
                visit(child)
        if "items" in item:
            visit(item["items"])
        if "additionalProperties" in item:
            visit(item["additionalProperties"])

    visit(schema)
    return values


def _collect_schema_types(schema: dict[str, Any]) -> list[str]:
    values: list[str] = []

    def add(value: str) -> None:
        if value != "null" and value not in values:
            values.append(value)

    def visit(item: Any) -> None:
        if not isinstance(item, dict):
            return
        raw_type = item.get("type")
        if isinstance(raw_type, list):
            for value in raw_type:
                add(value)
        elif isinstance(raw_type, str):
            add(raw_type)
        for child_key in ("anyOf", "oneOf", "allOf"):
            for child in item.get(child_key, []):
                visit(child)
        if "items" in item and "array" in values:
            item_types = _collect_schema_types(item["items"])
            if item_types:
                values[values.index("array")] = f"array[{', '.join(item_types)}]"
        if "additionalProperties" in item and "object" in values:
            value_types = _collect_schema_types(item["additionalProperties"])
            if value_types:
                values[values.index("object")] = f"object[str, {', '.join(value_types)}]"

    visit(schema)
    return values


CONTEXTUALIZER_OUTPUT_CONTRACT = build_output_contract(ContextualizedRequest)
ROUTER_OUTPUT_CONTRACT = build_output_contract(SkillCandidateSet)
INTENT_OUTPUT_CONTRACT = build_output_contract(IntentCandidateSet)
EVALUATOR_OUTPUT_CONTRACT = build_output_contract(RerankDecision)


CONTEXTUALIZER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的上下文语义归一节点。
你的任务是把 current_user_query 和 dialogue_history 归一成本轮完整语义请求，并输出结构化 semantic_frame。
{output_contract}
必须遵守：
1. 不澄清、不追问、不输出 skill_id、intent、code 或 params。
2. current_user_query 是最高优先级事实；历史只补全省略、延续、修正、改口、指代和对历史问题的回应。
3. 当前轮显式动作、对象或目标优先于历史；历史不得覆盖当前轮显式动作。
4. 生成、创作、编辑、处理、配文、识别、翻译、总结、问答等主动作与搜索/查找不同；不要因历史搜索污染当前处理型请求。
5. 有没有、是否、有吗、还有吗、有哪些、如何、是什么、介绍下、推荐下、怎么看等是问句形态；期望语言答案时 semantic_frame.expected_result_type 应为 ordinary_answer。
6. 纯实体名、纯作品名、人名、IP 名或只有一个实体名词，且没有普通问答诉求时，默认是资源搜索语义；不要因实体像书、歌、人就改成 ordinary_answer。
7. 承接历史时优先继承最近有效业务轮，并在 semantic_frame.inherited_turns 中记录来源轮次；若跳过最近有效业务轮，必须在 uncertainty_notes 说明原因。
8. 改口或覆盖历史时，只替换当前轮明确提到的维度；只提主体就替换 subjects，只提格式/时间/后缀就替换 qualifiers，默认不替换最近 object_types。
9. 不伪造 image_id、file_id、mail_id、真实文件句柄、图片句柄或外层业务执行结果。
10. 若只缺主体、主题、关键词、联系人、时间、真实资源句柄或唯一对象选择，仍输出最稳妥 semantic_frame；这些缺失不是 intent code 阻塞原因。
11. 若会影响 skill/intent/code 的关系不确定，relation_to_history 用 ambiguous，并在 uncertainty_notes/reason 记录，不得向用户追问。
"""


CONTEXTUALIZED_REQUEST_RULES = """上下文语义使用规则：
1. resolved_query 和 semantic_frame 是本轮路由依据；current_user_query 用于校验本轮显式动作是否被错误改写。
2. 只判断 skill/intent/code，不评估参数完整性。
3. 参数缺失、实体缺失、主体对象缺失、真实文件/图片/邮件句柄缺失、唯一对象选择缺失，都不是阻塞 intent code 的理由。
4. 若当前轮明确表达搜索、查找、打开、入口、工具、发送、整理、筛选、生成、编辑、处理等业务动作，必须优先保留业务候选。
5. 答案型问句可优先普通对话；不能仅因出现电影、图片、歌曲、文件等资源词就强行走云盘搜索。
6. 搜索类 skill 只承接查找、搜索、定位、获取已有资源载体；处理型 skill 承接生成、创作、编辑、识别、翻译、总结等动作。
7. 只有一个实体名词、纯作品名、人名或 IP 名，且无明确答案型问句证据时，属于资源搜索语义；云盘搜索内按纯实体名/无明确类型词默认搜综合。
8. 同一 code 的不同 intent 不能只按 code 判断，内部排序必须比较完整 route key: skill_id、intent、code。
"""


ROUTER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的一级候选召回节点。
你只负责输出 Top-N skill candidates，不做最终裁判。
{output_contract}
必须遵守：
1. 输出 candidates，默认按最可能到最不可能排序，通常给出 Top 3。
2. 普通对话候选用 skill_id=null、intent_domain="普通对话"，它是候选而不是 terminal no_match。
3. 强业务动作下，例如搜索、查找、打开、入口、工具、发送、整理、筛选、生成、编辑、处理，必须至少给出一个业务 skill 候选。
4. 工具入口类请求必须保留具体业务 skill、function_skill 通用入口、普通对话的选择空间；不能只给搜索类候选。
5. 答案型问句，例如是什么、哪里、为什么、怎么看、介绍下、推荐下、如何理解，可把普通对话排在第一。
6. 不要输出未提供的 skill_id。
7. matched_cues 只写当前 query 或 semantic_frame 中真实出现的证据词；risk_flags 可记录 label_conflict、search_vs_tool_entry、answer_vs_resource、context_unclear 等风险。
8. 若 expansion_scope=skill_recall_gap，补充上一轮未覆盖的能力方向，不要重复已有 route key。
9. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
"""


INTENT_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的二级 intent 候选召回节点。
你会收到一个 Skill.md，请在该 skill 内输出 Top-M intent candidates。
{output_contract}
必须遵守：
1. candidates 默认按最贴近语义到较弱排序，通常给出 Top 2。
2. intent 必须存在于该 Skill.md 的 Tools Schema，code 必须完全匹配该 intent 的 code。
3. candidate_id 必须稳定且唯一，建议格式为 skill_id:intent 或 skill_id:intent:rank。
4. params 只抽取确定信息；缺参数、缺实体、缺句柄、缺唯一对象选择不影响候选生成。
5. 如果多个 intent 同 code，只保留最贴近语义的一个，并把同 code intent 名写入 alternatives。
6. 专用 intent 优先于通用兜底 intent；入口类 intent 只在用户明确要求打开、进入、使用、工具、功能或入口时选择。
7. 若该 skill 不支持用户请求，可输出空 candidates；不要澄清。
8. 若 expansion_scope=intent_recall_gap，围绕 expansion_hint 补充当前 skill 内上一轮漏召回的 intent。
9. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
"""


EVALUATOR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的候选集 reranker。
你只能在给定 candidate_id 中选择或要求定向扩充候选集，不能自由生成 skill、intent 或 code。
{output_contract}
必须遵守：
1. 只评估 skill/intent/code 是否匹配用户语义，不评估参数完整性。
2. 不因为缺参数、缺实体、缺主体对象、缺文件句柄、缺图片句柄、缺邮件句柄、缺唯一对象选择而降低候选。
3. ranking 只包含候选集内 candidate_id；selected_candidate_id 也必须来自候选集。
4. verdict=select 表示候选集已有足够好答案；verdict=expand 只能用于候选集明显缺少正确方向。
5. expand_scope=skill_recall_gap 表示一级 skill 方向漏召回；intent_recall_gap 表示 skill 对但 intent 候选漏召回；context_unclear 只记录上下文风险，不能向用户澄清。
6. 若存在 label_conflict，不要强行覆盖 selector 高置信候选；search_vs_tool_entry、answer_vs_resource、context_unclear 只作为风险解释，不是一票否决。
7. 不允许输出候选结构外的任何字段，也不允许输出候选集外 code。
8. 普通对话与业务候选冲突时，按 current_user_query 显式动作和 semantic_frame.expected_result_type 判断。
9. 工具入口类请求中，具体业务工具可优先于通用入口、普通对话或通用搜索；reason 要说明具体业务证据。
10. 重复 code 必须比较完整 route key: skill_id、intent、code。
11. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
"""


CONTEXTUALIZER_SYSTEM_PROMPT = CONTEXTUALIZER_SYSTEM_PROMPT.format(
    output_contract=CONTEXTUALIZER_OUTPUT_CONTRACT,
)
ROUTER_SYSTEM_PROMPT = ROUTER_SYSTEM_PROMPT.format(
    output_contract=ROUTER_OUTPUT_CONTRACT,
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
)
INTENT_SYSTEM_PROMPT = INTENT_SYSTEM_PROMPT.format(
    output_contract=INTENT_OUTPUT_CONTRACT,
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
)
EVALUATOR_SYSTEM_PROMPT = EVALUATOR_SYSTEM_PROMPT.format(
    output_contract=EVALUATOR_OUTPUT_CONTRACT,
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
)


def build_contextualizer_prompt(
    query: str,
    dialogue_history: DialogueHistory | None = None,
    history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
) -> str:
    return json.dumps(
        {
            "current_user_query": query,
            "dialogue_history": _dialogue_history_payload(
                dialogue_history,
                history_limit=history_limit,
            ),
        },
        ensure_ascii=False,
    )


def build_router_prompt(
    resolved_query: str,
    cards: list[SkillCard],
    contextualized_request: ContextualizedRequest | None = None,
    current_user_query: str | None = None,
    *,
    top_n: int = 3,
    existing_candidates: list[SkillCandidate] | None = None,
    expansion_scope: str | None = None,
    expansion_hint: str | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    return json.dumps(
        {
            "resolved_query": resolved_query,
            "current_user_query": current_user_query or resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "available_skills": [card.model_dump() for card in cards],
            "top_n": top_n,
            "existing_candidates": [
                candidate.model_dump(mode="json")
                for candidate in (existing_candidates or [])
            ],
            "expansion_scope": expansion_scope,
            "expansion_hint": expansion_hint,
            "context": context or {},
        },
        ensure_ascii=False,
    )


def build_intent_prompt(
    resolved_query: str,
    skill: SkillDefinition,
    contextualized_request: ContextualizedRequest | None = None,
    current_user_query: str | None = None,
    *,
    skill_candidate: SkillCandidate | None = None,
    top_m: int = 2,
    existing_candidates: list[IntentCandidate] | None = None,
    expansion_scope: str | None = None,
    expansion_hint: str | None = None,
) -> str:
    return json.dumps(
        {
            "resolved_query": resolved_query,
            "current_user_query": current_user_query or resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "skill_candidate": (
                skill_candidate.model_dump(mode="json") if skill_candidate else None
            ),
            "skill_id": skill.id,
            "skill_markdown": skill.raw_markdown,
            "top_m": top_m,
            "existing_candidates": [
                candidate.model_dump(mode="json")
                for candidate in (existing_candidates or [])
            ],
            "expansion_scope": expansion_scope,
            "expansion_hint": expansion_hint,
        },
        ensure_ascii=False,
    )


def build_evaluator_prompt(
    resolved_query: str,
    available_skills: list[SkillCard],
    candidates: list[IntentCandidate],
    contextualized_request: ContextualizedRequest | None = None,
    current_user_query: str | None = None,
    *,
    expansion_round: int = 0,
) -> str:
    return json.dumps(
        {
            "resolved_query": resolved_query,
            "current_user_query": current_user_query or resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "available_skills": [card.model_dump() for card in available_skills],
            "candidates": [
                candidate.model_dump(mode="json") for candidate in candidates
            ],
            "candidate_ids": [candidate.candidate_id for candidate in candidates],
            "expansion_round": expansion_round,
        },
        ensure_ascii=False,
    )


def _dialogue_history_payload(
    dialogue_history: DialogueHistory | None,
    *,
    history_limit: int | None = DIALOGUE_HISTORY_LIMIT,
) -> list[dict]:
    if dialogue_history is None:
        return []
    turns = dialogue_history.turns
    if history_limit is None:
        selected_turns = turns
    elif history_limit <= 0:
        selected_turns = []
    else:
        selected_turns = turns[-history_limit:]
    payload = []
    for index, turn in enumerate(selected_turns):
        result = turn.result
        semantic_state = {
            "status": result.status,
            "resolved_query": result.resolved_query or turn.user_query,
            "relation_to_previous": result.context_relation,
        }
        if result.status == "matched":
            assistant_result = {
                "status": result.status,
                "skill_id": result.skill_id,
                "skill_name": result.skill_name,
                "intent": result.intent,
                "code": result.code,
                "params": result.params,
            }
        elif result.status == "clarify":
            assistant_result = {
                "status": result.status,
                "question": result.question,
                "options": result.options,
            }
        else:
            assistant_result = {"status": result.status}
        payload.append(
            {
                "turn_index": index,
                "user_query": turn.user_query,
                "semantic_state": semantic_state,
                "assistant_result": assistant_result,
            },
        )
    return payload


def _contextualized_request_payload(
    contextualized_request: ContextualizedRequest | None,
) -> dict:
    if contextualized_request is None:
        return {
            "resolved_query": "",
            "relation_to_history": "new_request",
            "semantic_frame": {},
            "reason": "",
        }
    return contextualized_request.model_dump(mode="json")
