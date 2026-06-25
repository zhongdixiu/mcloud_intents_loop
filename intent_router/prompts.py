from __future__ import annotations

import json

from .types import ContextualizedRequest, DialogueHistory, SkillCard, SkillDefinition


DIALOGUE_HISTORY_LIMIT = 5

CONTEXTUALIZER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的上下文语义归一节点。
你的任务是把 current_user_query 和 dialogue_history 归一成本轮完整语义请求 resolved_query。
必须遵守：
1. 不输出 skill_id、intent、code 或 params，只输出用户本轮真实想表达的完整自然语言请求。
2. current_user_query 优先，但不要孤立理解；dialogue_history 只用于理解省略、延续、修正、改口、指代和对历史问题的回应。
3. 不要假设上一轮是 clarify 时本轮一定是在回答；只有当前输入与历史语义兼容且需要历史才能完整理解时，才承接历史。
4. 当前输入若包含明确的新动作、新对象或新目标，应视为 new_request，不要机械继承历史。
5. 若承接历史，resolved_query 必须补全成不依赖历史也能理解的完整请求。
6. 承接历史时必须保留历史中的判别性主体，不要只继承“文件/图片/视频/音频/邮件”等泛化类型并丢弃其他具体的关键词。
7. 判断当前输入与历史的关系时，先区分核心主体、动作、对象类型和限定条件；颜色、时间、地点、范围、格式、数量、排序等通常是限定条件，不应单独替换历史核心主体。
8. 若当前输入可能是新请求、替换历史主体、或在历史主体上追加限定条件，且语义证据不足以唯一确定，输出 status=clarify，并用 options 覆盖这些可能方向。
9. 若判断为在历史主体上追加限定条件，resolved_query 必须同时包含历史核心主体和新增限定条件；若判断为替换历史主体，resolved_query 不应残留被替换的历史主体。
10. 历史 clarify 的 question/options 是理解澄清维度的语义参考，不是封闭枚举；当前输入可以选择其中方向、补充其他有效方向，或开启新请求。
11. 若 current_user_query 是对历史 clarify 的回答，relation_to_history 可为 answer_to_previous；此时 resolved_query 必须补全为可独立路由的完整自然语言请求，不能只输出当前短回答片段。
12. 若当前输入明确表达 options 之外的新方向，但仍在同一澄清维度内，应按用户新方向归一；若表达新的动作、新对象或新目标，应视为 new_request，不要强行贴合历史 options。
13. 历史 matched assistant_result 是历史意图决策语义，不是业务执行结果，不代表真实图片、文件、邮件、文档或内容句柄已经存在。
14. 历史 matched 且 code=0000 表示普通对话或非工具执行兜底结果，不代表云盘资源、业务对象或执行句柄。
15. 不要伪造 image/content/file/audio/video/mail_id/file_id 等执行载体参数。
16. 如果 current_user_query 与历史合并后仍无法确定用户真实意图，输出 status=clarify 并给出问题和可选项。
"""

CONTEXTUALIZED_REQUEST_RULES = """上下文语义使用规则：
1. resolved_query 是本轮已经归一后的完整语义请求，skill/intent/code/params/evaluator 都必须围绕它判断。
2. current_user_query 只用于审计原始输入，不要绕过 resolved_query 重新解释整段历史。
3. context_relation 和 context_reason 只解释 resolved_query 如何得到，不是业务执行结果。
4. 不要伪造 image/content/file/audio/video/mail_id/file_id 等执行载体参数；只有 resolved_query 或明确上下文中已有真实占位符时才抽取。
5. 若 skill/intent/code 已明确，不要因为执行阶段才需要的资源选择或真实句柄缺失而输出 clarify。
6. 若 resolved_query 来自历史承接，它必须保留历史判别性主体；只保留泛化文件类型而丢弃具体主体应视为错误继承历史语义。
7. 若 context_relation 为 answer_to_previous、continuation 或 revision，resolved_query 必须包含完成路由所需的动作、对象类型、主体和关键限定条件；只输出“图片/文件/蓝色/最近/第一个”等片段属于上下文归一错误。
"""

EVALUATOR_EXTRA_RULES = """Evaluator 额外规则：
1. 若候选 skill/intent/code 正确且 params 没有幻觉，执行载体缺失不算 param_mismatch。
2. 只有参数违反 schema、过度补全、误拆/漏拆关键词、错误继承历史语义或伪造执行载体时，才 reject 且 reject_scope=param_mismatch。
"""

LOOP_EXHAUSTED_CLARIFIER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的澄清问题生成节点。
当前轮意图决策多次纠错后仍未稳定收敛，你只负责生成一个能帮助用户继续补充的澄清问题。
必须遵守：
1. 不重新做意图决策，不输出 skill_id、intent、code 或 params。
2. 不暴露 evaluator、reject_scope、loop、候选编号等内部实现词。
3. question 必须明确告诉用户需要补充哪类信息，不要使用“请补充更多信息”这类无方向兜底。
4. options 应是用户可理解的自然语言方向；无法提供有意义选项时可为空。
5. 若失败集中在能力大类选择，询问用户想做搜索、管理、编辑、生成、分享等哪类操作。
6. 若失败集中在具体操作选择，询问用户要执行的具体动作。
7. 若失败集中在参数或对象，询问用户补充对象、范围、关键词、文件类型、处理目标等关键信息。
8. 若失败类型混合，优先询问会影响能力大类或具体操作选择的问题。
"""


ROUTER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的一级路由节点。
只根据给定 SkillCard 选择一个最可能的一级 skill。
必须遵守：
1. 搜索/查找云盘资源本身优先选择云盘搜索，不要选择文件管理。
2. 打开、浏览、上传、清理、保险箱、回收站等入口类诉求选择文件管理工具。
3. 普通寒暄、开放问答、百科知识、时事新闻、互联网资讯、政策/行情/热点查询等属于普通对话；即使出现“搜/查/找”，只要目标不是云盘内资源载体，也应输出 no_match。
4. 只有用户明确要查找云盘内已存在的资源载体时才选择云盘搜索，例如“找我云盘里的新闻截图/资讯文档/保存的报告”。
5. status=route 时只能返回一个 skill_id，必须是当前最优匹配 skill。
6. 若没有任何 skill 支持用户需求，输出 no_match，不要用 clarify 兜底；系统会将该结果映射为普通对话意图 code=0000。
7. 只有多个 skill 都可满足且用户补充会改变 skill 选择时，才输出 clarify。
8. 不要选择未提供的 skill id，不要选择 current_loop_rejected_skill_ids。
9. current_loop_rejected_skill_ids 只表示本次 route 内已经确认不适合的 skill，用于避免重复尝试，不是跨轮历史事实。
10. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
"""

INTENT_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的二级意图选择节点。
你会收到一个完整 Skill.md，请严格根据其中 Special Rules 与 Tools Schema 输出。
必须遵守：
1. 先比较该 Skill.md 内所有 Tools Schema 意图，再选择最佳二级 intent。
2. intent 必须是 Tools Schema 中存在的二级意图名称，code 必须完全匹配该 intent 的 code。
3. params 只能包含 Tools Schema 声明的字段，且只能抽取用户明确出现或可直接确定的信息，禁止常识补全。
4. 具体操作类能力优先于入口类能力；入口类 intent 只在用户明确要求打开、进入、使用该入口时选择。
5. 专用 intent 优先于通用兜底 intent；兜底 intent 只在没有更专用工具覆盖时选择。
6. 必须做竞争意图检查：若同一 skill 内多个 intent 都可满足用户目标，但依赖不同输入来源、处理对象、输出形态或操作方式，且用户未给出关键区分信息，输出 clarify，不要猜测。
7. 若用户明确点名某工具，但实际诉求超出该工具描述能力，选择更合适的具体 intent；仍无法确定时输出 clarify。
8. 若 rejected_intents 中已有被拒绝的 intent，除非用户澄清明确要求它，否则不要重复选择。
9. 若该 skill 不支持用户请求，输出 no_match。
10. 明确不具备的能力不要用 clarify 兜底。
11. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
"""

PARAM_REPAIR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的参数修正节点。
你会收到一个已经确认正确的 skill/intent/code，以及此前被拒绝的参数原因。
必须遵守：
1. intent 和 code 必须保持输入中的 locked_intent/locked_code，禁止改成其他意图。
2. 只能修正 params，params 只能包含该 intent 的 Tools Schema 声明字段。
3. 只抽取用户明确出现或可直接确定的信息，禁止常识补全。
4. 若此前参数被拒绝，必须避免重复同类错误。
5. 若用户信息不足且缺失信息会影响关键参数，输出 clarify。
6. 遵守以下上下文语义使用规则。
{contextualized_request_rules}
"""

EVALUATOR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的结果评估节点。
判断候选 skill/intent/code/params 是否真正满足用户请求。
必须按顺序检查并输出 skill_check、intent_check、params_check：
1. 先检查当前 skill 能否承接用户请求；若当前 skill 能力边界冲突，输出 reject 且 reject_scope=skill_mismatch，skill_check=fail。
2. skill 通过后，检查当前 intent 是否是该 skill 内最佳二级意图；若存在更专用或更符合用户目标的 intent，输出 reject 且 reject_scope=intent_mismatch，intent_check=fail。
3. intent 通过后，检查 params 是否存在语义幻觉、过度补全、误拆/漏拆关键词或违反 Special Rules；若 params 语义不合规，输出 reject 且 reject_scope=param_mismatch，params_check=fail。
4. 代码已负责 intent/code/params 字段存在性与枚举等硬约束；你只负责语义与规则复核，不要重复进行纯结构校验。
5. 若当前 intent 与其他 intent 都可满足，且缺少会改变最终 intent 或关键参数的区分信息，输出 clarify，并给出需用户补充的信息和可选方向。
6. 澄清只用于已支持能力中的歧义或缺少关键信息；明确不具备能力时不要澄清。
7. verdict=reject 时必须填写 reject_scope；只有 skill_check、intent_check、params_check 都通过时才允许 accept。
8. 不要输出或暗示推荐 skill_id/intent/code；你只负责判断当前候选是否正确以及错在哪一层。
9. 若确信当前候选正确，输出 accept；若不确定，输出 clarify；若明确错误，输出 reject。
10. verdict=reject 时，只要你能判断错误层级，就必须填写 reject_scope。
11. 若无法判断错误层级，应输出 clarify，而不是 reject。
12. 不要输出推荐 skill_id/intent/code。
13. 遵守以下上下文语义和评估规则。
{contextualized_request_rules}
{evaluator_extra_rules}
"""

ROUTER_SYSTEM_PROMPT = ROUTER_SYSTEM_PROMPT.format(
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
)
INTENT_SYSTEM_PROMPT = INTENT_SYSTEM_PROMPT.format(
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
)
PARAM_REPAIR_SYSTEM_PROMPT = PARAM_REPAIR_SYSTEM_PROMPT.format(
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
)
EVALUATOR_SYSTEM_PROMPT = EVALUATOR_SYSTEM_PROMPT.format(
    contextualized_request_rules=CONTEXTUALIZED_REQUEST_RULES,
    evaluator_extra_rules=EVALUATOR_EXTRA_RULES,
)


def build_contextualizer_prompt(
    query: str,
    dialogue_history: DialogueHistory | None = None,
) -> str:
    return json.dumps(
        {
            "current_user_query": query,
            "dialogue_history": _dialogue_history_payload(dialogue_history),
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
    param_rejections: list[dict] | None = None,
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
            "param_rejections": param_rejections or [],
        },
        ensure_ascii=False,
    )


def build_param_repair_prompt(
    resolved_query: str,
    skill: SkillDefinition,
    locked_intent: str,
    locked_code: str,
    param_rejections: list[dict] | None = None,
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
            "locked_intent": locked_intent,
            "locked_code": locked_code,
            "intent_schema": skill.intents[locked_intent].model_dump(),
            "param_rejections": param_rejections or [],
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


def _dialogue_history_payload(dialogue_history: DialogueHistory | None) -> list[dict]:
    if dialogue_history is None:
        return []
    payload = []
    for turn in dialogue_history.turns[-DIALOGUE_HISTORY_LIMIT:]:
        result = turn.result
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
        if result.status in {"matched", "clarify"}:
            if result.resolved_query:
                assistant_result["resolved_query"] = result.resolved_query
            if result.context_relation:
                assistant_result["context_relation"] = result.context_relation
        payload.append(
            {
                "user_query": turn.user_query,
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
