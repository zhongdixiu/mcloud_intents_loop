import hashlib
from pathlib import Path

import pytest

from intent_router.skills import SkillLoadError, SkillRegistry, load_skill


EXPECTED_ROUTE_KEY_SHA256 = (
    "1b6af0cacc11f5d085729b2c66062822cb4e3740846629423fb5840cc9c25578"
)
STANDARD_SECTIONS = (
    "Skill Scope",
    "Intent Routing Principles",
    "Intent Contrast Rules",
    "Tools Schema",
    "Intent-Specific Rules",
    "Positive Examples",
    "Negative Examples",
    "Execution Instructions",
)


def test_load_current_flat_skills() -> None:
    registry = SkillRegistry.from_path("skills")

    assert len(registry) == 15
    cards = {card.id: card for card in registry.cards()}
    assert set(cards) >= {
        "mcloud_search_skill",
        "mail_skill",
        "mcloud_person_skill",
        "todo_skill",
    }
    assert all(card.version == "1.0" for card in cards.values())
    assert all(card.scope and card.out_of_scope for card in cards.values())
    assert not hasattr(cards["mcloud_search_skill"], "intents")

    search_skill = registry.get("mcloud_search_skill")
    assert search_skill.name == "云盘搜索"
    assert search_skill.intents["搜图片"].code == "012"
    assert "metadataList" in search_skill.intents["搜图片"].params
    assert "搜图片" in registry.routing_context("mcloud_search_skill").intents
    assert registry.execution_context("file_skill").execution_instructions


def test_all_current_skills_use_standard_sections_and_parameter_types() -> None:
    registry = SkillRegistry.from_path("skills")
    valid_types = {"string", "integer", "number", "boolean", "array", "object"}

    for card in registry.cards():
        skill = registry.get(card.id)
        text = skill.path.read_text(encoding="utf-8")
        for section in STANDARD_SECTIONS:
            assert text.count(f"## {section}") == 1
        for intent in skill.intents.values():
            assert intent.code
            assert intent.desc
            for param in intent.params.values():
                assert param.type in valid_types
                assert isinstance(param.required, bool)
                if param.type == "array":
                    assert param.items is not None


def test_route_keys_are_unchanged_by_skill_migration() -> None:
    registry = SkillRegistry.from_path("skills")
    route_keys = sorted(
        f"{skill.id}\t{intent.name}\t{intent.code}"
        for card in registry.cards()
        for skill in [registry.get(card.id)]
        for intent in skill.intents.values()
    )

    assert len(route_keys) == 120
    digest = hashlib.sha256("\n".join(route_keys).encode()).hexdigest()
    assert digest == EXPECTED_ROUTE_KEY_SHA256


def test_activity_allowed_values_are_structured() -> None:
    registry = SkillRegistry.from_path("skills")
    activity = registry.get("activity_search_skill")
    metadata = activity.intents["搜活动"].params["metadataList"]

    assert metadata.type == "array"
    assert metadata.items is not None
    assert metadata.items.type == "string"
    assert "云朵中心 签到领会员" in metadata.allowed_values
    assert "立省29！1元购会员" in metadata.allowed_values


def test_load_directory_style_skill(tmp_path: Path) -> None:
    skill_dir = tmp_path / "demo_skill"
    skill_dir.mkdir()
    path = skill_dir / "SKILL.md"
    path.write_text(_valid_skill_markdown("demo_skill"), encoding="utf-8")

    registry = SkillRegistry.from_path(tmp_path)

    assert registry.get("demo_skill").intents["演示"].code == "001"


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ("id: demo_skill\n", "missing required frontmatter fields"),
        ('"code": 1', "code must be a non-empty string"),
        ('"type": "list[str]"', "type must be one of"),
        ('"required": "false"', "required must be boolean"),
        ('"type": "array", "required": false, "desc": "值"', "array items.type"),
    ],
)
def test_strict_skill_validation_rejects_invalid_contracts(
    tmp_path: Path,
    replacement: str,
    message: str,
) -> None:
    text = _valid_skill_markdown("demo_skill")
    if replacement == "id: demo_skill\n":
        text = text.replace("name: 演示\n", "")
    elif replacement == '"code": 1':
        text = text.replace('"code": "001"', replacement)
    elif replacement == '"required": "false"':
        text = text.replace('"required": false', replacement)
    else:
        text = text.replace(
            '"type": "string", "required": false, "desc": "值"',
            replacement,
        )
    path = tmp_path / "demo_skill.md"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SkillLoadError, match=message):
        load_skill(path)


def test_validation_rejects_duplicate_json_key(tmp_path: Path) -> None:
    text = _valid_skill_markdown("demo_skill").replace(
        '"演示": {',
        '"演示": {"code": "001", "desc": "重复", "params": {}},\n  "演示": {',
    )
    path = tmp_path / "demo_skill.md"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SkillLoadError, match="duplicate JSON key"):
        load_skill(path)


def test_validation_rejects_unknown_intent_rule_heading(tmp_path: Path) -> None:
    text = _valid_skill_markdown("demo_skill").replace(
        "## Intent-Specific Rules\n\n- 无。",
        "## Intent-Specific Rules\n\n### 不存在\n\n- 无。",
    )
    path = tmp_path / "demo_skill.md"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SkillLoadError, match="unknown intent"):
        load_skill(path)


def test_validation_rejects_path_id_mismatch_and_missing_section(
    tmp_path: Path,
) -> None:
    mismatch = tmp_path / "wrong_skill.md"
    mismatch.write_text(_valid_skill_markdown("demo_skill"), encoding="utf-8")
    with pytest.raises(SkillLoadError, match="must match path id"):
        load_skill(mismatch)

    missing = tmp_path / "demo_skill.md"
    missing.write_text(
        _valid_skill_markdown("demo_skill").replace(
            "## Negative Examples\n\n- “别的” → 不属于本 Skill\n\n",
            "",
        ),
        encoding="utf-8",
    )
    with pytest.raises(SkillLoadError, match="missing sections: Negative Examples"):
        load_skill(missing)


def test_validation_rejects_duplicate_skill_id(tmp_path: Path) -> None:
    flat = tmp_path / "demo_skill.md"
    flat.write_text(_valid_skill_markdown("demo_skill"), encoding="utf-8")
    directory = tmp_path / "demo_skill"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        _valid_skill_markdown("demo_skill"),
        encoding="utf-8",
    )

    with pytest.raises(SkillLoadError, match="Duplicate skill id"):
        SkillRegistry.from_path(tmp_path)


def test_validation_rejects_allowed_value_type_mismatch(tmp_path: Path) -> None:
    text = _valid_skill_markdown("demo_skill").replace(
        '"type": "string", "required": false, "desc": "值"',
        (
            '"type": "string", "required": false, "desc": "值", '
            '"allowed_values": [1]'
        ),
    )
    path = tmp_path / "demo_skill.md"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(SkillLoadError, match="does not match type string"):
        load_skill(path)


def test_disabled_skill_is_not_exposed_as_card(tmp_path: Path) -> None:
    path = tmp_path / "demo_skill.md"
    path.write_text(
        _valid_skill_markdown("demo_skill").replace(
            "status: active",
            "status: disabled",
        ),
        encoding="utf-8",
    )
    registry = SkillRegistry.from_path(tmp_path)

    assert len(registry) == 1
    assert registry.cards() == []
    assert registry.has("demo_skill")
    assert not registry.has("demo_skill", active_only=True)


def _valid_skill_markdown(skill_id: str) -> str:
    return f"""---
id: {skill_id}
name: 演示
description: 演示规范 Skill。
version: "1.0"
scope:
  - 演示能力
out_of_scope:
  - 非演示能力
status: active
---

# 演示 Skill

## Skill Scope

演示能力。

## Intent Routing Principles

选择最匹配的 Intent。

## Intent Contrast Rules

只有一个 Intent。

## Tools Schema

```json
{{
  "演示": {{
    "code": "001",
    "desc": "执行演示",
    "params": {{
      "value": {{"type": "string", "required": false, "desc": "值"}}
    }}
  }}
}}
```

## Intent-Specific Rules

- 无。

## Positive Examples

- “演示” → 演示

## Negative Examples

- “别的” → 不属于本 Skill

## Execution Instructions

- 无。
"""
