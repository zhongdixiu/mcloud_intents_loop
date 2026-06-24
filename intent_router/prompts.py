from __future__ import annotations

import json

from .types import SkillCard, SkillDefinition


ROUTER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的一级路由节点。
只根据给定 SkillCard 选择一个最可能的一级 skill。
必须遵守：
1. 搜索/查找云盘资源本身优先选择云盘搜索，不要选择文件管理。
2. 打开、浏览、上传、清理、保险箱、回收站等入口类诉求选择文件管理工具。
3. status=route 时只能返回一个 skill_id，必须是当前最优匹配 skill。
4. 若没有任何 skill 支持用户需求，输出 no_match，不要用 clarify 兜底。
5. 只有多个 skill 都可满足且用户补充会改变 skill 选择时，才输出 clarify。
6. 不要选择未提供的 skill id，不要选择 rejected_skill_ids。
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
"""

PARAM_REPAIR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的参数修正节点。
你会收到一个已经确认正确的 skill/intent/code，以及此前被拒绝的参数原因。
必须遵守：
1. intent 和 code 必须保持输入中的 locked_intent/locked_code，禁止改成其他意图。
2. 只能修正 params，params 只能包含该 intent 的 Tools Schema 声明字段。
3. 只抽取用户明确出现或可直接确定的信息，禁止常识补全。
4. 若此前参数被拒绝，必须避免重复同类错误。
5. 若用户信息不足且缺失信息会影响关键参数，输出 clarify。
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
"""


def build_router_prompt(
    query: str,
    cards: list[SkillCard],
    rejected: list[str],
) -> str:
    return json.dumps(
        {
            "query": query,
            "available_skills": [card.model_dump() for card in cards],
            "rejected_skill_ids": rejected,
        },
        ensure_ascii=False,
    )


def build_intent_prompt(
    query: str,
    skill: SkillDefinition,
    rejected_intents: list[dict] | None = None,
    param_rejections: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "query": query,
            "skill_id": skill.id,
            "skill_markdown": skill.raw_markdown,
            "rejected_intents": rejected_intents or [],
            "param_rejections": param_rejections or [],
        },
        ensure_ascii=False,
    )


def build_param_repair_prompt(
    query: str,
    skill: SkillDefinition,
    locked_intent: str,
    locked_code: str,
    param_rejections: list[dict] | None = None,
) -> str:
    return json.dumps(
        {
            "query": query,
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
    query: str,
    available_skills: list[SkillCard],
    skill: SkillDefinition,
    candidate: dict,
    rejected_skill_ids: list[str] | None = None,
    rejected_intents: dict[str, list[dict[str, str]]] | None = None,
    visited_skills: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "query": query,
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
            "loop_state": {
                "visited_skills": visited_skills or [],
                "rejected_skill_ids": rejected_skill_ids or [],
                "rejected_intents": rejected_intents or {},
            },
        },
        ensure_ascii=False,
    )
