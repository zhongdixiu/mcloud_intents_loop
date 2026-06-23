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

EVALUATOR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的结果评估节点。
判断候选 skill/intent/code/params 是否真正满足用户请求。
若 skill 能力边界冲突，输出 reject 且 reject_scope=skill_mismatch。
若 skill 正确但二级意图不合适、参数幻觉或违反规则，输出 reject 且 reject_scope=intent_mismatch。
必须检查同 skill 的全部 tools_schema：若存在更专用或更符合用户目标的 intent，输出 reject 且 reject_scope=intent_mismatch。
若当前 intent 与其他 intent 都可满足，且缺少会改变最终 intent 的关键区分信息，输出 clarify，并给出可选方向。
澄清只用于已支持能力中的歧义或缺少关键信息；明确不具备能力时不要澄清。
否则输出 accept。
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
) -> str:
    return json.dumps(
        {
            "query": query,
            "skill_id": skill.id,
            "skill_markdown": skill.raw_markdown,
            "rejected_intents": rejected_intents or [],
        },
        ensure_ascii=False,
    )


def build_evaluator_prompt(
    query: str,
    skill: SkillDefinition,
    candidate: dict,
) -> str:
    return json.dumps(
        {
            "query": query,
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
