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
1. 不澄清、不追问、不输出 skill_id、intent、code 或 params。这些属于下游 Router/Intent 节点的职责，你只输出语义层信息（resolved_query、semantic_frame）。
2. current_user_query 是最高优先级事实；历史只用于补全省略、延续、修正、改口、指代和对历史问题的回应。当前轮显式表达的动作、对象、目标始终优先于历史中的对应内容（即当前轮可以替换历史，历史不能替换当前轮）。
3. 【继承 vs 替换的判别】当前轮需要先判断是"继承式追加"还是"替换式改口"，再决定如何处理历史维度：
   - 继承式追加：当前轮出现"再/还/也/继续/又/另外/顺便"等追加词，或当前轮本身是历史动作的延续（如历史在搜歌，当前轮只补充新的限定词），此时历史的 object_types、action 保持继承，仅在当前轮明确提到的维度上新增内容。
     例：历史="搜周杰伦的歌"，当前="再加上春节歌曲" → 继承 action=搜索、object_types=[歌曲]，subjects 追加为 [周杰伦, 春节]
   - 替换式改口：当前轮出现"不是/我是说/应该是/改成/搞错了/不对"等否定/修正词，此时当前轮明确提到的维度整体替换历史对应维度，不做追加。
     例：历史="搜周杰伦的歌"，当前="不是，我是要找周杰伦的电影" → 替换 object_types=[歌曲]→[电影]
   - 无法判断属于继承还是替换时（没有追加词也没有修正词，单纯一个短语），默认采用继承式追加，并在 uncertainty_notes 中说明"采用默认继承策略"。
4. 替换只作用于当前轮明确提到的维度：只提主体（人名/作品名等）就只替换 subjects；只提格式/时间/后缀等限定词就只替换 qualifiers；当前轮未提及 object_types 时，默认保持继承，不主动替换。
5. 生成、创作、编辑、处理、配文、识别、翻译、总结、问答等"处理型"主动作与"搜索/查找"主动作语义不同；不要因为历史是搜索类请求，就把当前轮的处理型请求污染为搜索语义（也不要反过来污染）。
6. semantic_frame.expected_result_type 是下游 Evaluator 的权威诉求类型，必须谨慎裁决并在 reason/uncertainty_notes 中写明关键证据。不要把同一轮同时标成互相冲突的语义；若确有冲突，在 uncertainty_notes 说明。
7. 问答/咨询诉求不能只靠短词表判断，要结合三类证据：
   - 强问答形态：是什么、为什么、怎么、如何、吗、有没有、有哪些、区别、参数、介绍、推荐、评价、展开说说、讲讲等。
   - 语义结构：主体 + 属性/观点/解释诉求，如"手机参数""人物介绍""这部剧立意""两者区别"。
   - 历史延续：最近有效轮是普通问答、推荐、解释或泛写作，当前轮是短实体、短补充、代词追问时，默认延续 ordinary_answer。
   泛写作/泛改写/泛翻译/读后感/影评/作文/人物传记等，如果没有明确要求进入产品工具或处理一个真实已选文件/笔记/图片对象，也按 ordinary_answer 处理。
8. 当前轮只有一个实体名词或纯实体名（人名、作品名、IP名等），且没有问答/咨询证据，最近有效轮也不是 ordinary_answer 链路时，semantic_frame.expected_result_type 默认设为 resource（默认是资源搜索语义）；不要仅因实体类型像书、歌、人就改判为 ordinary_answer。具体应该搜索哪个细分类型（如综合搜索/分类搜索）由下游 Intent 节点决定，你只需要标注 expected_result_type=resource，不需要给出更具体的二级判断。
9. 承接历史时优先继承最近有效业务轮（即上一个有 matched 结果的轮次），并在 semantic_frame.inherited_turns 中记录来源轮次编号；如果跳过了最近有效业务轮（转而继承更早的轮次），必须在 uncertainty_notes 说明跳过原因。
10. 不伪造 image_id、file_id、mail_id、真实文件句柄、图片句柄或任何外层业务执行结果；这些不存在就不要编造。
11. 缺主体、缺主题、缺关键词、缺联系人、缺时间、缺真实资源句柄、缺唯一对象选择，都不影响你输出完整 semantic_frame；这些缺失信息不是阻塞输出的理由，也不是 intent/code 判断的阻塞条件（那是下游节点的职责）。
12. 如果当前轮与历史的关系（继承/替换/无关）影响 skill/intent/code 判断且确实无法判断，relation_to_history 输出 ambiguous，并在 uncertainty_notes/reason 中记录原因；不得借此向用户追问或要求澄清。
"""

# ==================== 语义判断规则（给Router/Intent使用）====================
 
ROUTING_SEMANTIC_RULES = """语义判断规则（用于 Router / Intent 候选生成）：
1. resolved_query 和 semantic_frame 是本轮路由的核心依据；current_user_query 仅用于核对本轮显式动作是否被上游错误改写。
2. 候选生成只判断 skill / intent / code 是否匹配语义，不需要评估参数是否完整。
3. 参数缺失、实体缺失、主体对象缺失、真实文件/图片/邮件句柄缺失、唯一对象选择缺失，都不是不生成候选或降低候选优先级的理由。
4. 当前轮明确表达搜索、查找、打开、入口、工具、发送、整理、筛选、生成、编辑、处理等业务动作时，必须给出对应的业务候选，不能仅给普通对话候选。
5. 答案型、咨询型、推荐型、解释型、泛写作型请求应保留普通对话高优先级；不能仅因 query 中出现电影、图片、歌曲、文件等资源词，就强行判定为云盘搜索语义——要以 contextualizer 的 expected_result_type 为准。
6. 搜索类 skill 只承接查找、搜索、定位、获取已有资源载体类需求；处理型 skill 承接生成、创作、编辑、识别、翻译、总结等主动作类需求；两者不要混淆。
7. 当前轮只有一个实体名词、纯作品名、人名或 IP 名，且没有明确问答类证据时，属于资源搜索语义；在云盘搜索 skill 内，如果没有更具体的类型词（图片/视频/文档等），优先给出综合搜索 intent 候选，同时也可以给出该实体最可能对应的细分类型 intent 候选（如人名更可能搜素材/视频，可同时给综合搜索和该细分类型两个候选，由候选集决定最终排序）。
8. 比较或去重同 code 的不同 intent 时，不能只比较 code 是否相同，必须比较完整 route key（skill_id + intent + code）三者组合。
 
【入口类请求的判断指引】（对应"动作词+入口类名词"的组合表达）
- 当 query 包含"搜索/找/查"等搜索动作词，但搭配的对象是"入口/制作/创作/工具"等功能性名词时（例如"搜索影集制作入口""帮我找PPT生成工具"），实际语义通常是用户想要使用/进入该功能，而不是在云盘里搜索一个叫"入口"的资源文件。这种情况下应优先给出对应的功能入口 intent 候选，普通的资源搜索候选可以同时保留作为备选，但不应作为唯一或首位候选。
- 判断标准：如果"搜索/找"后面跟的对象本身就是某个功能/工具/能力的名称（而非具体的文件、图片、歌曲等资源载体），倾向功能入口；如果跟的是具体资源描述（如"找猫的照片""搜周杰伦的歌"），才是真正的资源搜索语义。
"""


ROUTER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的一级候选召回节点。
你只负责输出 Top-N skill candidates，不做最终裁判。
{output_contract}
 
必须遵守：
1. 输出 candidates，按最可能到最不可能排序，默认给出 Top 3，按真实置信度排列，不要为了凑数而强行拉低质量候选的排名。
2. 普通对话候选用 skill_id=null、intent_domain="普通对话"；它是候选集中的一个正常成员，不是兜底的 terminal no_match。
3. 强业务动作（搜索、查找、打开、入口、工具、发送、整理、筛选、生成、编辑、处理等）下，必须确保候选集中至少包含一个业务 skill 候选，但不需要为此牺牲候选集的置信度排序——业务候选可以排在第二或第三位，只要它存在于候选集中。
4. 工具入口类请求（参见 routing_semantic_rules 的入口类判断指引）需要确保具体业务 skill、function_skill 通用入口、普通对话三者中至少两类有机会进入候选集，但同样按真实置信度排序，不强制三者都必须出现在 Top-N 中。
5. 答案型问句（是什么、哪里、为什么、怎么看、介绍下、推荐下、如何理解等）可以把普通对话排在第一位。
6. 不要输出 available_skills 之外未提供的 skill_id。
7. matched_cues 只写当前 query 或 semantic_frame 中真实出现的证据词，不要编造；risk_flags 可记录 label_conflict（候选标签存在矛盾证据）、search_vs_tool_entry（搜索语义与工具入口语义混淆风险）、answer_vs_resource（问答语义与资源语义混淆风险）、context_unclear（上下文关系不确定）等风险，多个风险可同时标注。
8. 若 expansion_scope=skill_recall_gap，围绕 expansion_hint 补充上一轮未覆盖的能力方向，不要重复已有 route key（skill_id 维度）。
9. 遵守以下语义判断规则。
{contextualized_request_rules}
"""


INTENT_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的二级 intent 候选召回节点。
你会收到一个 Skill.md，请在该 skill 内输出 Top-M intent candidates。
{output_contract}
 
必须遵守：
1. candidates 按最贴近语义到较弱排序，默认给出 Top 2。
2. intent 必须存在于该 Skill.md 的 Tools Schema 中，code 必须与该 intent 在 Schema 中定义的 code 完全一致。
3. candidate_id 必须稳定且唯一，建议格式为 `{{skill_id}}:{{intent}}:{{rank}}`。
4. params 只抽取确定信息，缺参数、缺实体、缺句柄、缺唯一对象选择都不影响候选生成（不因此减少候选数量或降低候选置信度）。
5. 当同一个 code 对应多个 intent 名称时（即该 code 在 Schema 中有多个同义 intent），只保留语义最贴近当前请求的一个作为候选，其余同 code 的 intent 名称写入该候选的 alternatives 字段；这是同一 skill 内部的去重，与跨 skill 的同 code 场景无关。
6. 专用 intent 优先于通用兜底 intent；入口类 intent（打开、进入、使用、工具、功能、入口等明确表达）优先于普通业务 intent；具体判断参考 routing_semantic_rules 中"入口类请求的判断指引"，尤其注意"搜索类动作词+功能性名词"的组合表达。
7. 若该 skill 确实不支持用户请求，可以输出空 candidates 列表；不要因此输出澄清问题。
8. 若 expansion_scope=intent_recall_gap，围绕 expansion_hint 补充当前 skill 内上一轮漏召回的 intent 候选。
9. 遵守以下语义判断规则。
{contextualized_request_rules}
"""

# ==================== Evaluator专用语义规则（不重新做语义判断，仅辅助排序）====================
 
EVALUATOR_SEMANTIC_RULES = """语义辅助规则（用于 Evaluator 排序，不用于重新判断语义）：
1. semantic_frame.expected_result_type 是本轮诉求类型的权威裁决。你不允许推翻它，也不需要重新判断"这个 query 到底是问答、资源搜索还是工具入口"；只能判断候选与该诉求类型是否一致。
2. 你的任务是比较候选集内已有的 candidate 之间谁更贴近语义，而不是评估某个 candidate 的参数是否完整——参数缺失、实体缺失、主体对象缺失、文件/图片/邮件句柄缺失、唯一对象选择缺失，都不能成为你降低某个 candidate 排名或拒绝整个候选集的理由。
3. 比较或去重候选时，如果发现不同 skill_id 但 code 数值相同的情况（跨 skill 的 code 碰撞），必须按完整 route key（skill_id + intent + code）区分，不能仅凭 code 数值判断为同一候选。
"""


EVALUATOR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的候选集 reranker。
你只能在给定 candidate_id 中选择，或要求定向扩充候选集；不能自由生成新的 skill、intent 或 code。
{output_contract}
 
必须遵守：
1. 只评估候选集中各 candidate 的 skill/intent/code 是否匹配用户语义，不评估参数是否完整。
2. 不因为缺参数、缺实体、缺主体对象、缺文件句柄、缺图片句柄、缺邮件句柄、缺唯一对象选择而降低某个 candidate 的排名，也不因此整体拒绝候选集。
3. ranking 中的每个元素必须是候选集内已存在的 candidate_id；selected_candidate_id 也必须来自候选集，不能引用不存在的 ID。
4. verdict 的判断顺序如下（按此顺序依次检查，命中即停止）：
   a. 候选集内是否存在一个语义明确匹配的 candidate？如果存在，verdict=select，选择该 candidate 作为 selected_candidate_id，即使它带有风险标志（label_conflict 等）也应优先 select 并在 reason 中说明风险已被纳入考虑。
   b. 如果候选集内所有 candidate 都明显不匹配用户语义（即没有一个是合理答案），才输出 verdict=expand，并指明 expand_scope。
   c. risk_flags（label_conflict / search_vs_tool_entry / answer_vs_resource / context_unclear）始终只作为排序参考和 reason 中的风险说明，不单独触发 expand，也不单独阻止 select——只有"候选集内没有语义匹配的候选"才能触发 expand。
5. expand_scope=skill_recall_gap 表示一级 skill 方向存在明显漏召回（候选集中所有 skill 候选都不对）；expand_scope=intent_recall_gap 表示某个 skill 方向是对的，但该 skill 下的 intent 候选都不够贴切；expand_scope=context_unclear 仅用于在 reason/diagnostics 中记录上下文关系不确定，不能单独作为触发 expand 的理由（必须同时满足条件4b）。
6. 不允许输出候选结构之外的任何字段，也不允许输出候选集之外的 code。
7. 普通对话候选与业务候选冲突时，必须优先按 semantic_frame.expected_result_type 排序：ordinary_answer 下普通对话优先；resource 下搜索候选优先；function_entry/content_processing 下业务候选优先。
8. 工具入口类请求中，如果 expected_result_type=function_entry 且候选集内同时存在具体业务工具 intent 和通用入口/普通对话/通用搜索 intent，优先选择具体业务工具 intent。不要在 ordinary_answer 下仅因存在工具候选就选择工具。
9. 遵守以下语义辅助规则。
{contextualized_request_rules}
"""


CONTEXTUALIZER_SYSTEM_PROMPT = CONTEXTUALIZER_SYSTEM_PROMPT.format(
    output_contract=CONTEXTUALIZER_OUTPUT_CONTRACT,
)
ROUTER_SYSTEM_PROMPT = ROUTER_SYSTEM_PROMPT.format(
    output_contract=ROUTER_OUTPUT_CONTRACT,
    contextualized_request_rules=ROUTING_SEMANTIC_RULES ,
)
INTENT_SYSTEM_PROMPT = INTENT_SYSTEM_PROMPT.format(
    output_contract=INTENT_OUTPUT_CONTRACT,
    contextualized_request_rules=ROUTING_SEMANTIC_RULES ,
)
EVALUATOR_SYSTEM_PROMPT = EVALUATOR_SYSTEM_PROMPT.format(
    output_contract=EVALUATOR_OUTPUT_CONTRACT,
    contextualized_request_rules=EVALUATOR_SEMANTIC_RULES,
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
