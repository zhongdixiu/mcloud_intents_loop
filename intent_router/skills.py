from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .types import IntentSchema, ParamSchema, SkillCard, SkillDefinition


FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)


class SkillLoadError(ValueError):
    pass


class SkillRegistry:
    def __init__(self, skills: dict[str, SkillDefinition]) -> None:
        self._skills = skills

    @classmethod
    def from_path(cls, path: str | Path) -> "SkillRegistry":
        root = Path(path)
        files = _discover_skill_files(root)
        if not files:
            raise SkillLoadError(f"No skill markdown files found under {root}")

        skills: dict[str, SkillDefinition] = {}
        for file_path in files:
            skill = load_skill(file_path)
            if skill.id in skills:
                raise SkillLoadError(f"Duplicate skill id: {skill.id}")
            skills[skill.id] = skill
        return cls(skills)

    def cards(self) -> list[SkillCard]:
        return [
            SkillCard(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                intents=list(skill.intents),
            )
            for skill in self._skills.values()
        ]

    def get(self, skill_id: str) -> SkillDefinition:
        return self._skills[skill_id]

    def has(self, skill_id: str) -> bool:
        return skill_id in self._skills

    def __len__(self) -> int:
        return len(self._skills)


def load_skill(path: Path) -> SkillDefinition:
    text = path.read_text(encoding="utf-8")
    metadata, content = _parse_frontmatter(text)
    schema_text = _extract_section(content, "Tools Schema")
    schema = _parse_tools_schema(schema_text)
    skill_id = path.parent.name if path.name == "SKILL.md" else path.stem

    intents: dict[str, IntentSchema] = {}
    for intent_name, raw_intent in schema.items():
        if not isinstance(raw_intent, dict):
            raise SkillLoadError(f"{path}: intent {intent_name!r} must be an object")
        raw_params = raw_intent.get("params") or {}
        params = _parse_params(raw_params)
        code = raw_intent.get("code")
        if not code:
            raise SkillLoadError(f"{path}: intent {intent_name!r} misses code")
        intents[intent_name] = IntentSchema(
            name=intent_name,
            code=str(code),
            desc=str(raw_intent.get("desc", "")),
            params=params,
        )

    return SkillDefinition(
        id=skill_id,
        name=str(metadata.get("name", skill_id)),
        description=str(metadata.get("description", "")),
        path=path,
        special_rules=_extract_section(content, "Special Rules"),
        tools_schema_text=schema_text,
        raw_markdown=text,
        intents=intents,
    )


def _discover_skill_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    standard = sorted(root.glob("*/SKILL.md"))
    flat = sorted(root.glob("*.md"))
    return standard + flat


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    metadata = yaml.safe_load(match.group("body")) or {}
    return metadata, text[match.end() :]


def _extract_section(markdown: str, section_name: str) -> str:
    pattern = re.compile(
        rf"^###\s+{re.escape(section_name)}\s*$"
        rf"(?P<body>.*?)(?=^###\s+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(markdown)
    return match.group("body").strip() if match else ""


def _parse_tools_schema(section: str) -> dict[str, Any]:
    if not section:
        raise SkillLoadError("Missing Tools Schema section")
    stripped = section.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"\A```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*\Z", "", stripped)
    json_text = _extract_json_object(stripped)
    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise SkillLoadError(f"Invalid Tools Schema JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SkillLoadError("Tools Schema must be a JSON object")
    return parsed


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    if start < 0:
        raise SkillLoadError("Tools Schema does not contain a JSON object")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise SkillLoadError("Unclosed Tools Schema JSON object")


def _parse_params(raw_params: Any) -> dict[str, ParamSchema]:
    if not isinstance(raw_params, dict):
        return {}
    params: dict[str, ParamSchema] = {}
    for name, raw_schema in raw_params.items():
        if isinstance(raw_schema, dict):
            desc = str(raw_schema.get("desc", ""))
            params[name] = ParamSchema(
                name=name,
                type=str(raw_schema.get("type")) if raw_schema.get("type") else None,
                desc=desc,
                allowed_values=_extract_allowed_values(desc),
            )
        else:
            desc = str(raw_schema)
            params[name] = ParamSchema(
                name=name,
                desc=desc,
                allowed_values=_extract_allowed_values(desc),
            )
    return params


def _extract_allowed_values(desc: str) -> list[str]:
    marker_index = desc.find("可选值")
    if marker_index < 0:
        marker_index = desc.find("限定")
    if marker_index < 0:
        return []
    tail = desc[marker_index:]
    values = re.findall(r'["“]([^"”]+)["”]', tail)
    return [value for value in values if value]
