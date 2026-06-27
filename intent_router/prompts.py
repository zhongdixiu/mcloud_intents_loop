from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from .types import (
    ContextualizedRequest,
    DialogueHistory,
    EvaluationDecision,
    IntentDecision,
    SkillCard,
    SkillDefinition,
    SkillRouteDecision,
    LoopExhaustedClarification,
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
ROUTER_OUTPUT_CONTRACT = build_output_contract(SkillRouteDecision)
INTENT_OUTPUT_CONTRACT = build_output_contract(IntentDecision)
EVALUATOR_OUTPUT_CONTRACT = build_output_contract(EvaluationDecision)
LOOP_EXHAUSTED_OUTPUT_CONTRACT = build_output_contract(LoopExhaustedClarification)


CONTEXTUALIZER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的上下文语义归一节点。
你的任务是把 current_user_query 和 dialogue_history 归一成本轮完整语义请求 resolved_query。
{output_contract}
字段语义补充：
- status 只能是 resolved；上下文归一节点不得向用户追问或中断路由。
- relation_to_history 只能是 new_request、continuation、revision、answer_to_previous 或 ambiguous，表示当前输入与历史的语义关系。
- 新请求必须输出 status=resolved 且 relation_to_history=new_request，禁止把 status 写成 new_request。
- 若历史承接不确定，仍必须输出 status=resolved，可用 relation_to_history=ambiguous 和 reason 标注不确定性；真正是否澄清由后续 skill/intent 节点判断。
必须遵守：
1. 不输出 skill_id、intent、code 或 params，只输出用户本轮真实想表达的完整自然语言请求。
2. current_user_query 是最高优先级事实，但不要孤立理解；dialogue_history 只用于理解省略、延续、修正、改口、指代和对历史问题的回应。
3. 不要假设上一轮是 clarify 时本轮一定是在回答；只有当前输入与历史语义兼容且需要历史才能完整理解时，才承接历史。
4. 当前输入若包含明确的新动作、新对象或新目标，relation_to_history 应为 new_request，不要机械继承历史。
5. dialogue_history 中每轮包含 user_query、semantic_state 和 assistant_result：semantic_state.resolved_query 是该轮已经归一后的历史语义状态，user_query 是原始证据，assistant_result 是最终意图决策或澄清结果。
6. 理解历史时优先参考 semantic_state.resolved_query 表示的已归一语义，再用 user_query 和 assistant_result 校验是否可继承；不要把历史原始 user_query 逐轮重新归一后覆盖 semantic_state。
7. semantic_state.resolved_query 是历史语义证据，不是绝对事实；若它与当前输入的显式动作、对象或目标冲突，当前输入优先。
8. 判断当前输入与历史的关系时，先抽象本轮语义框架：业务主动作、问句形态、期望结果、核心主体、对象类型、限定条件、输入来源；不要只看关键词相似度。
9. 对多轮历史按时间顺序理解语义链，优先继承最近有效语义目标；若最近轮是省略表达，应先结合该轮 semantic_state.resolved_query 理解最近轮含义，再用于本轮承接，不要回退到更早已被覆盖的对象类型或主体。
10. 若当前输入缺少主动作、核心主体或对象类型，但与最近有效任务框架兼容，应继承缺失部分并补全成不依赖历史也能理解的完整请求。
11. 若当前输入只替换核心主体、对象类型、结果形态或补充限定条件，且本轮语义框架与历史兼容，应继承历史主动作和核心主体中未被替换的关键语义。
12. 若当前输入出现明确新主动作或新目标，当前动作优先；历史只可补充主体、对象类型、限定条件或输入来源，不能把历史主动作覆盖到当前输入上。
13. 生成、创作、编辑、处理、配文、识别、翻译、总结、问答等主动作与搜索/查找主动作不同；当前输入明确表达新主动作时，不应归一为历史主动作。
14. 有没有、是否、有吗、还有吗、有哪些、怎么样、如何、怎么用、是什么、介绍下、推荐下、怎么看等是问句形态或答案型表达，不是搜索、打开、生成、管理等业务主动作；不能仅因问句里出现电影、图片、歌曲、文件等资源词就改写成资源搜索。
15. 若当前输入缺少业务主动作但保留问句形态，应继承最近有效历史的交互形态：普通推荐/问答历史继续保持答案型请求，资源搜索历史继续保持资源存在性或搜索细化请求，除非当前输入显式切换到其他动作。
16. 若当前输入显式要求搜索、查找、找资源、找文件、打开、生成、创作、编辑、处理、总结等业务动作，应按当前动作归一；历史只补全主体、对象类型、限定条件或素材来源。
17. 承接历史时必须保留历史中的判别性主体，不要只继承“文件/图片/视频/音频/邮件/圈子”等泛化类型并丢弃其他具体的关键词。
18. 颜色、时间、地点、范围、格式、数量、排序、来源状态等通常是限定条件或输入来源，不应单独替换历史核心主体，也不应覆盖当前明确主动作。
19. 若当前输入只缺少主体、主题、关键词或真实资源句柄，但业务主动作、对象类型和期望结果形态已经足以支持后续 skill/intent/code 判断，应输出 status=resolved，并在 resolved_query 中保留可确定的信息；不要只因实体不完整而澄清。
20. 普通问答、开放问答、用法咨询、事实询问等答案型请求即使缺少具体主题，也应先输出 status=resolved，让后续路由归为普通对话；除非用户明确要求执行某个业务功能且缺失信息会改变业务类别。
21. 若当前输入可能是新请求、替换历史主体、切换主动作、或在历史主体上追加限定条件，且差异可能改变 skill/intent/code，也必须输出 status=resolved；选择最符合 current_user_query 和最近有效历史的 resolved_query，并用 relation_to_history=ambiguous 与 reason 说明不确定方向，禁止输出 question/options 阻塞路由。
22. 若判断为在历史主体上追加限定条件，resolved_query 必须同时包含历史核心主体和新增限定条件；若判断为替换历史主体，resolved_query 不应残留被替换的历史主体。
23. 历史 clarify 的 question/options 是理解澄清维度的语义参考，不是封闭枚举；当前输入可以选择其中方向、补充其他有效方向，或开启新请求。
24. 若 current_user_query 是对历史 clarify 的回答，relation_to_history 可为 answer_to_previous；此时 resolved_query 必须补全为可独立路由的完整自然语言请求，不能只输出当前短回答片段。
25. 若当前输入明确表达 options 之外的新方向，但仍在同一澄清维度内，应按用户新方向归一；若表达新的动作、新对象或新目标，relation_to_history 应为 new_request，不要强行贴合历史 options。
26. 历史 matched assistant_result 是历史意图决策语义，不是业务执行结果，不代表真实图片、文件、邮件、文档或内容句柄已经存在。
27. 历史 matched 且 code=000 表示普通对话或非工具执行兜底结果，可用于承接推荐、问答、解释等答案型语义，也可承接人物、作品、主题和用法咨询，但不代表云盘资源、业务对象或执行句柄。
28. 不要伪造 image/content/file/audio/video/mail_id/file_id 等执行载体参数；但可以把历史中的自然语言对象、主题或来源保留在 resolved_query 中，供后续意图识别判断。
29. 如果 current_user_query 与历史合并后仍无法确定会影响 skill/intent/code 的用户真实意图，仍输出 status=resolved、relation_to_history=ambiguous；resolved_query 采用最稳妥的自然语言解释，reason 中说明可能的歧义，不要给用户生成澄清问题。
30. 若只是缺主体、主题、关键词、联系人、时间、文件名或真实资源句柄，但不会改变 skill/code，必须输出 status=resolved，不要澄清。
"""

CONTEXTUALIZED_REQUEST_RULES = """上下文语义使用规则：
1. resolved_query 是本轮已经归一后的完整语义请求，skill/intent/code/evaluator 都必须围绕它判断。
2. current_user_query 用于校验本轮显式业务动作和问句形态是否被 resolved_query 改写；不要绕过 resolved_query 重新解释整段历史，但必须纠正与 current_user_query 明显冲突的主动作。
3. context_relation 和 context_reason 只解释 resolved_query 如何得到，不是业务执行结果。
4. 不要伪造 image/content/file/audio/video/mail_id/file_id 等执行载体参数；只有 resolved_query 或明确上下文中已有真实占位符时才抽取。
5. 意图识别阶段只判断 skill/intent/code，不评估参数完整性，也不验证外层业务是否已返回真实文件、图片、邮件或搜索结果句柄。
6. 若 skill/intent/code 已明确，不要因为执行阶段才需要的资源选择、真实句柄或唯一文件定位缺失而输出 clarify。
7. 若 resolved_query 来自历史承接，它必须保留历史判别性主体；只保留泛化文件类型而丢弃具体主体应视为错误继承历史语义。
8. 若 context_relation 为 answer_to_previous、continuation 或 revision，resolved_query 必须包含完成路由所需的主动作、对象类型、主体和关键限定条件；只输出孤立对象词、限定词或指代词属于上下文归一错误。
9. 若 resolved_query 包含生成、创作、编辑、处理、配文、识别、翻译、总结、问答等明确主动作，后续路由必须尊重该主动作，不要因历史 skill 类型、对象词或限定条件而改写主动作。
10. 有没有、是否、有吗、还有吗、有哪些、如何、是什么、介绍下、推荐下、怎么看等是问句形态；若期望结果是语言答案、推荐建议或公共信息，且不是明确要求打开、搜索、管理或执行某个工具，应归为普通对话兜底而不是澄清。
11. 搜索类 skill 只承接查找、搜索、定位、获取已有资源载体；不能仅因出现电影、图片、歌曲、近期、保存等资源词，就覆盖 current_user_query 中的推荐、问答、生成或处理形态。
12. 若用户明确表达搜索、查找、打开、入口、工具、功能、执行、生成、处理等业务动作，应优先判断是否存在可承接的 skill；只有没有可执行 skill 时才归为普通对话。
13. 缺少主体、主题、关键词、联系人、文件句柄、操作对象、参数对象或唯一资源选择时，只有这些信息会改变 skill/intent/code 才可澄清；若只影响业务执行或参数完整性，不应阻塞意图调度。
14. 同一 skill 内多个搜索类 intent 都可满足且当前系统只能输出单 intent 时，不要因为多资源类型直接澄清；按可执行单 intent 输出最贴近的一个，优先级为图片、文档、视频/影视、音频、文件夹、笔记、综合、圈子。
15. 对纯实体名、作品名、人物名或泛资源名，不要用外部常识强行推断资源类型；除非 query 或历史明确限定图片/文档/视频/音频等类型，否则优先使用综合或历史最近兼容类型。
16. 本项目只评估 skill/intent/code 调度效果；缺失或不完整的实体、资源、操作对象、参数对象不得导致 clarify 或 param_mismatch，除非缺失信息会改变 skill/intent/code。
17. clarify_scope 取值含义：route_boundary 表示补充后会改变 skill；intent_code_boundary 表示补充后会改变 code；missing_entity、execution_handle_missing、same_code_intent_boundary、context_boundary 都不是可接受的澄清理由。
18. 只有 route_boundary、intent_code_boundary 可以输出 clarify；missing_entity、execution_handle_missing、same_code_intent_boundary、context_boundary 必须改为最可能的 matched/route/resolved 结果。
"""

EVALUATOR_EXTRA_RULES = """Evaluator 额外规则：
1. 评估目标只包含 skill/intent/code；params 只是辅助观测，不得因为参数缺失、不完整、过度补全或执行载体缺失输出 reject 或 clarify。
2. 对总结、润色、翻译、配文、识别、编辑、生成等处理型意图，resolved_query 中的自然语言对象来源可作为语义证据；不要因为缺 file_id、image_id、真实文件名或唯一资源选择而 clarify。
3. 若候选结果满足的是历史搜索动作，但 current_user_query 明确表达推荐、问答、生成、创作、编辑、处理或总结等非搜索形态，应按错误层级 reject；不要接受被历史污染成搜索的结果。
4. 只要 skill/intent/code 正确，不要因为主体、关键词、联系人、文件句柄、图片句柄、操作对象或唯一资源选择缺失而 reject 或 clarify；这类问题不影响意图 code 评测。
5. verdict=clarify 时必须填写 clarify_scope；仅当缺失信息会改变 skill 或 code 才可 clarify。
6. 若判定 candidate 的 intent 名称不是最佳，但最佳 intent 与 candidate 的 code 相同，应填写 preferred_intent/preferred_code；不要因此 reject。
"""

LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的澄清问题生成节点。
当前轮意图决策多次纠错后仍未稳定收敛，你只负责生成一个能帮助用户继续补充的澄清问题。
{output_contract}
必须遵守：
1. 不重新做意图决策，不输出 skill_id、intent、code 或 params。
2. 不暴露 evaluator、reject_scope、loop、候选编号等内部实现词。
3. question 必须明确告诉用户需要补充哪类信息，不要使用“请补充更多信息”这类无方向兜底。
4. options 应是用户可理解的自然语言方向；无法提供有意义选项时可为空。
5. 若失败集中在能力大类选择，询问用户想做搜索、管理、编辑、生成、分享等哪类操作。
6. 若失败集中在具体操作选择，询问用户要执行的具体动作。
7. 不要因为参数、实体、资源句柄、主体对象或操作对象缺失生成澄清问题。
8. 若失败类型混合，只询问会影响能力大类或具体操作选择的问题。
"""


ROUTER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的一级路由节点。
只根据给定 SkillCard 选择一个最可能的一级 skill。
{output_contract}
必须遵守：
1. 先判断 resolved_query 的主动作、问句形态、目标对象和期望结果形态，再选择 skill；同时用 current_user_query 校验本轮显式动作是否被上下文改写，不要只按关键词或历史 skill 类型机械判断。
2. 若主目标是语言答案或信息服务，包括闲聊、开放问答、百科解释、推荐建议、资讯问答、政策行情、热点查询、功能用法咨询等，且没有可执行业务 skill，应输出 no_match，由系统映射为普通对话 code=000。
3. 若主动作是查找、搜索、定位、获取，且目标对象是可保存、可播放、可浏览的已有资源载体，应选择搜索类 skill；在移动云盘语境下，此类资源检索不要求用户显式说明资源位于云盘内。
4. 若主动作是生成、创作、编辑、处理、配文、识别、翻译、鉴伪、修复、总结或问答，应选择能执行该动作的处理类 skill；对象词、限定条件、素材来源或历史 skill 类型都不能覆盖当前主动作。
5. 有没有、是否、有吗、还有吗、有哪些等是问句形态，不是业务主动作；需要结合期望结果判断是语言答案、资源存在性搜索还是其他业务操作。
6. 若主动作是打开、浏览、上传、清理、管理、进入入口或查看功能位置，应选择对应的管理或入口类 skill；仅询问“如何使用/怎么用/是什么/有哪些/怎么看”属于答案型咨询，不等同于打开入口。
7. status=route 时只能返回一个 skill_id，必须是当前最优匹配 skill。
8. 若没有任何 skill 支持用户需求，输出 no_match，不要用 clarify 兜底。
9. 只有多个 skill 都可满足且用户补充会改变 skill 选择时，才输出 clarify。
10. 若用户明确查找某个移动云盘功能、AI工具或入口，应优先选择对应功能/工具 skill；不要仅因“推荐一下/有没有/如何使用”就直接归普通对话。
11. 若用户明确询问云盘中是否有某类资源，或要求把资源找出来，应按资源搜索理解，不要当作纯语言问答。
12. 不要选择未提供的 skill id，不要选择 current_loop_rejected_skill_ids。
13. current_loop_rejected_skill_ids 只表示本次 route 内已经确认不适合的 skill，用于避免重复尝试，不是跨轮历史事实。
14. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
15. status=clarify 时必须填写 clarify_scope；只有用户补充会改变一级 skill 时使用 route_boundary。
"""

INTENT_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的二级意图选择节点。
你会收到一个完整 Skill.md，请严格根据其中 Special Rules 与 Tools Schema 输出。
{output_contract}
必须遵守：
1. 先比较该 Skill.md 内所有 Tools Schema 意图，再选择最佳二级 intent。
2. intent 必须是 Tools Schema 中存在的二级意图名称，code 必须完全匹配该 intent 的 code。
3. params 只能包含 Tools Schema 声明的字段，且只能抽取用户明确出现或可直接确定的信息，禁止常识补全。
4. 具体操作类能力优先于入口类能力；入口类 intent 只在用户明确要求打开、进入、使用该入口时选择。
5. 专用 intent 优先于通用兜底 intent；兜底 intent 只在没有更专用工具覆盖时选择。
6. 必须做竞争意图检查：若同一 skill 内多个 intent 都可满足用户目标，但依赖不同输入来源、处理对象、输出形态或操作方式，且用户未给出关键区分信息，输出 clarify，不要猜测。
7. 若用户明确点名某工具，但实际诉求更符合本 skill 内其他具体 intent，应选择更合适的具体 intent；若本 skill 无法承接该诉求，输出 no_match。
8. 对处理型 intent，若 resolved_query 已包含自然语言处理对象、主题或来源，可以据此选择 intent；不要因缺外层业务资源句柄而澄清。
9. 若 rejected_intents 中已有被拒绝的 intent，除非用户澄清明确要求它，否则不要重复选择。
10. 若该 skill 不支持用户请求，输出 no_match。
11. 明确不具备的能力不要用 clarify 兜底。
12. 若多个搜索类 intent 都可满足同一资源检索请求且当前只能输出单 intent，按对象类型优先级选择最贴近的一个，不要仅因同时出现多种资源类型而澄清。
13. 缺少主体、主题、关键词、联系人、文件句柄、图片句柄、操作对象或参数对象不应导致 clarify；在 code 可确定时输出 matched，params 可为空或只填确定字段。
14. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
15. status=clarify 时必须填写 clarify_scope；只有用户补充会改变最终 code 时使用 intent_code_boundary。
16. 同一 skill 内多个 intent 名称不同但 code 相同，属于 same_code_intent_boundary；必须选择其中一个最贴近 intent 输出 matched，不要澄清。
"""

EVALUATOR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的结果评估节点。
判断候选 skill/intent/code 是否真正满足用户请求。
{output_contract}
必须按顺序检查并输出 skill_check、intent_check；params_check 可固定为 pass 或留空：
1. 先检查当前 skill 能否承接用户请求；若当前 skill 能力边界冲突，输出 reject 且 reject_scope=skill_mismatch，skill_check=fail。
2. skill 通过后，检查当前 intent 是否是该 skill 内最佳二级意图；若存在更专用或更符合用户目标的 intent，输出 reject 且 reject_scope=intent_mismatch，intent_check=fail。
3. 不检查 params 是否完整或语义最优，不要输出 reject_scope=param_mismatch。
4. 代码已负责 intent/code 字段硬约束；你只负责语义与规则复核，不要重复进行纯结构校验。
5. 若当前 intent 与其他 intent 都可满足，且缺少会改变最终 code 的区分信息，输出 clarify，并给出需用户补充的信息和可选方向。
6. 澄清只用于已支持能力中的歧义或缺少关键信息；明确不具备能力时不要澄清。
7. verdict=reject 时必须填写 reject_scope；只有 skill_check、intent_check 都通过时才允许 accept。
8. 不要输出或暗示推荐 skill_id、intent 或 code；你只负责判断当前候选是否正确以及错在哪一层。
9. 若确信当前候选正确，输出 accept；若不确定，输出 clarify；若明确错误，输出 reject。
10. verdict=reject 时，只要你能判断错误层级，就必须填写 reject_scope。
11. 若无法判断错误层级，应输出 clarify，而不是 reject。
12. 遵守以下上下文语义和评估规则。
{contextualized_request_rules}
{evaluator_extra_rules}
"""

CONTEXTUALIZER_SYSTEM_PROMPT = CONTEXTUALIZER_SYSTEM_PROMPT.format(
    output_contract=CONTEXTUALIZER_OUTPUT_CONTRACT,
)
LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT = (
    LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT.format(
        output_contract=LOOP_EXHAUSTED_OUTPUT_CONTRACT,
    )
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
    evaluator_extra_rules=EVALUATOR_EXTRA_RULES,
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
    rejected: list[str],
    contextualized_request: ContextualizedRequest | None = None,
    current_user_query: str | None = None,
) -> str:
    return json.dumps(
        {
            "resolved_query": resolved_query,
            "current_user_query": current_user_query or resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "available_skills": [card.model_dump() for card in cards],
            "current_loop_rejected_skill_ids": rejected,
        },
        ensure_ascii=False,
    )


def build_intent_prompt(
    resolved_query: str,
    skill: SkillDefinition,
    rejected_intents: list[dict] | None = None,
    contextualized_request: ContextualizedRequest | None = None,
    current_user_query: str | None = None,
) -> str:
    return json.dumps(
        {
            "resolved_query": resolved_query,
            "current_user_query": current_user_query or resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "skill_id": skill.id,
            "skill_markdown": skill.raw_markdown,
            "rejected_intents": rejected_intents or [],
        },
        ensure_ascii=False,
    )


def build_evaluator_prompt(
    resolved_query: str,
    available_skills: list[SkillCard],
    skill: SkillDefinition,
    candidate: dict,
    contextualized_request: ContextualizedRequest | None = None,
    current_user_query: str | None = None,
) -> str:
    return json.dumps(
        {
            "resolved_query": resolved_query,
            "current_user_query": current_user_query or resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "available_skills": [card.model_dump() for card in available_skills],
            "skill": {
                "id": skill.id,
                "name": skill.name,
                "description": skill.description,
                "special_rules": skill.special_rules,
                "tools_schema": {
                    intent_name: schema.model_dump()
                    for intent_name, schema in skill.intents.items()
                },
            },
            "candidate": candidate,
        },
        ensure_ascii=False,
    )


def build_loop_exhausted_clarifier_prompt(
    *,
    current_user_query: str,
    resolved_query: str,
    contextualized_request: ContextualizedRequest | None,
    available_skills: list[SkillCard],
    attempts: list[dict],
) -> str:
    return json.dumps(
        {
            "current_user_query": current_user_query,
            "resolved_query": resolved_query,
            "contextualized_request": _contextualized_request_payload(
                contextualized_request,
            ),
            "available_skills": [card.model_dump() for card in available_skills],
            "attempts": attempts,
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
            "status": "resolved",
            "relation_to_history": "new_request",
            "used_history_turns": [],
            "reason": "",
        }
    return {
        "status": contextualized_request.status,
        "relation_to_history": contextualized_request.relation_to_history,
        "used_history_turns": contextualized_request.used_history_turns,
        "reason": contextualized_request.reason,
    }
