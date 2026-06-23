from __future__ import annotations

import json

from .types import SkillCard, SkillDefinition


ROUTER_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的一级路由节点。
只根据给定 SkillCard 选择最可能的一级 skill。
必须遵守：
1. 搜索/查找云盘资源本身优先选择云盘搜索，不要选择文件管理。
2. 打开、浏览、上传、清理、保险箱、回收站等入口类诉求选择文件管理工具。
3. 若用户输入不足以区分一级 skill，输出 clarify。
4. 不要选择未提供的 skill id。
"""

INTENT_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的二级意图选择节点。
你会收到一个完整 Skill.md，请严格根据其中 Special Rules 与 Tools Schema 输出。
必须遵守：
1. intent 必须是 Tools Schema 中存在的二级意图名称。
2. code 必须完全匹配该 intent 的 code。
3. params 只能包含 Tools Schema 声明的字段。
4. 参数只能抽取用户明确出现或可直接确定的信息，禁止常识补全。
5. 若该 skill 不满足用户请求，输出 no_match；若必须用户补充才能稳定选择，输出 clarify。
"""

EVALUATOR_SYSTEM_PROMPT = """你是移动云盘意图路由 Agent 的结果评估节点。
判断候选 skill/intent/code/params 是否真正满足用户请求。
若 skill 能力边界冲突、二级意图不合适、参数幻觉或违反规则，输出 reject。
若信息不足以判断且会改变最终意图，输出 clarify。
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


def build_intent_prompt(query: str, skill: SkillDefinition) -> str:
    return json.dumps(
        {
            "query": query,
            "skill_id": skill.id,
            "skill_markdown": skill.raw_markdown,
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
            },
            "candidate": candidate,
        },
        ensure_ascii=False,
    )
