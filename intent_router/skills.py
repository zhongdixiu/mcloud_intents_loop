from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from .types import (
    ExecutionContext,
    IntentRoutingContext,
    IntentSchema,
    ParamItemsSchema,
    ParamSchema,
    SkillCard,
    SkillDefinition,
)


FRONTMATTER_RE = re.compile(r"\A---\s*\n(?P<body>.*?)\n---\s*\n", re.DOTALL)
SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SECTION_HEADING_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$", re.MULTILINE)
SUBSECTION_HEADING_RE = re.compile(r"^###\s+(?P<name>.+?)\s*$", re.MULTILINE)

REQUIRED_METADATA = ("id", "name", "description", "version", "scope", "out_of_scope")
REQUIRED_SECTIONS = (
    "Skill Scope",
    "Intent Routing Principles",
    "Intent Contrast Rules",
    "Tools Schema",
    "Intent-Specific Rules",
    "Positive Examples",
    "Negative Examples",
    "Execution Instructions",
)
PARAM_TYPES = {"string", "integer", "number", "boolean", "array", "object"}
ITEM_TYPES = PARAM_TYPES - {"array"}
SKILL_STATUSES = {"active", "deprecated", "disabled"}
INTENT_FIELDS = {"code", "desc", "params", "status"}
PARAM_FIELDS = {
    "type",
    "required",
    "desc",
    "allowed_values",
    "items",
    "default",
    "normalization",
}


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
                version=skill.version,
                scope=skill.scope,
                out_of_scope=skill.out_of_scope,
                aliases=skill.aliases,
            )
            for skill in self._skills.values()
            if skill.status == "active"
        ]

    def get(self, skill_id: str) -> SkillDefinition:
        return self._skills[skill_id]

    def routing_context(self, skill_id: str) -> IntentRoutingContext:
        skill = self.get(skill_id)
        return IntentRoutingContext(
            skill_id=skill.id,
            skill_name=skill.name,
            skill_scope=skill.skill_scope,
            routing_principles=skill.routing_principles,
            contrast_rules=skill.contrast_rules,
            intents={
                name: intent
                for name, intent in skill.intents.items()
                if intent.status == "active"
            },
            intent_specific_rules=skill.intent_specific_rules,
            positive_examples=skill.positive_examples,
            negative_examples=skill.negative_examples,
        )

    def execution_context(self, skill_id: str) -> ExecutionContext:
        skill = self.get(skill_id)
        return ExecutionContext(
            skill_id=skill.id,
            skill_name=skill.name,
            execution_instructions=skill.execution_instructions,
        )

    def has(self, skill_id: str, *, active_only: bool = False) -> bool:
        skill = self._skills.get(skill_id)
        return skill is not None and (not active_only or skill.status == "active")

    def __len__(self) -> int:
        return len(self._skills)


def load_skill(path: Path) -> SkillDefinition:
    text = path.read_text(encoding="utf-8")
    metadata, content = _parse_frontmatter(text, path=path)
    sections = _parse_sections(content, path=path)
    schema_text = sections["Tools Schema"]
    schema = _parse_tools_schema(schema_text, path=path)

    expected_id = path.parent.name if path.name == "SKILL.md" else path.stem
    skill_id = _required_string(metadata, "id", path=path)
    if skill_id != expected_id:
        raise SkillLoadError(
            f"{path}: frontmatter id {skill_id!r} must match path id {expected_id!r}",
        )
    if not SKILL_ID_RE.fullmatch(skill_id):
        raise SkillLoadError(f"{path}: invalid skill id {skill_id!r}")

    intents: dict[str, IntentSchema] = {}
    for intent_name, raw_intent in schema.items():
        intents[intent_name] = _parse_intent(path, intent_name, raw_intent)

    _validate_intent_specific_rules(
        path,
        sections["Intent-Specific Rules"],
        set(intents),
    )

    status = metadata.get("status", "active")
    if status not in SKILL_STATUSES:
        raise SkillLoadError(
            f"{path}: status must be one of {sorted(SKILL_STATUSES)}, got {status!r}",
        )

    return SkillDefinition(
        id=skill_id,
        name=_required_string(metadata, "name", path=path),
        description=_required_string(metadata, "description", path=path),
        version=_required_string(metadata, "version", path=path),
        scope=_required_string_list(metadata, "scope", path=path),
        out_of_scope=_required_string_list(metadata, "out_of_scope", path=path),
        aliases=_optional_string_list(metadata, "aliases", path=path),
        owner=_optional_string(metadata, "owner", path=path),
        status=status,
        path=path,
        skill_scope=sections["Skill Scope"],
        routing_principles=sections["Intent Routing Principles"],
        contrast_rules=sections["Intent Contrast Rules"],
        tools_schema_text=schema_text,
        intent_specific_rules=sections["Intent-Specific Rules"],
        positive_examples=sections["Positive Examples"],
        negative_examples=sections["Negative Examples"],
        execution_instructions=sections["Execution Instructions"],
        raw_markdown=text,
        intents=intents,
    )


def _discover_skill_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    standard = sorted(root.glob("*/SKILL.md"))
    flat = sorted(root.glob("*.md"))
    return standard + flat


def _parse_frontmatter(
    text: str,
    *,
    path: Path,
) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise SkillLoadError(f"{path}: missing YAML frontmatter")
    try:
        metadata = yaml.safe_load(match.group("body")) or {}
    except yaml.YAMLError as exc:
        raise SkillLoadError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SkillLoadError(f"{path}: frontmatter must be a YAML object")
    missing = [field for field in REQUIRED_METADATA if field not in metadata]
    if missing:
        raise SkillLoadError(
            f"{path}: missing required frontmatter fields: {', '.join(missing)}",
        )
    return metadata, text[match.end() :]


def _parse_sections(markdown: str, *, path: Path) -> dict[str, str]:
    matches = list(SECTION_HEADING_RE.finditer(markdown))
    names = [match.group("name") for match in matches]
    duplicate_names = sorted({name for name in names if names.count(name) > 1})
    if duplicate_names:
        raise SkillLoadError(
            f"{path}: duplicate sections: {', '.join(duplicate_names)}",
        )
    missing = [name for name in REQUIRED_SECTIONS if name not in names]
    if missing:
        raise SkillLoadError(f"{path}: missing sections: {', '.join(missing)}")
    if names != list(REQUIRED_SECTIONS):
        raise SkillLoadError(
            f"{path}: sections must contain exactly the standard sections in order",
        )

    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group("name")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        sections[name] = markdown[start:end].strip()
    normalized = {name: sections[name] for name in REQUIRED_SECTIONS}
    empty = [name for name, body in normalized.items() if not body]
    if empty:
        raise SkillLoadError(f"{path}: empty sections: {', '.join(empty)}")
    return normalized


def _parse_tools_schema(section: str, *, path: Path) -> dict[str, Any]:
    if not section:
        raise SkillLoadError(f"{path}: empty Tools Schema section")
    stripped = section.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"\A```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```\s*\Z", "", stripped)
    json_text = _extract_json_object(stripped, path=path)
    try:
        parsed = json.loads(
            json_text,
            object_pairs_hook=lambda pairs: _object_without_duplicates(pairs, path),
        )
    except json.JSONDecodeError as exc:
        raise SkillLoadError(f"{path}: invalid Tools Schema JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SkillLoadError(f"{path}: Tools Schema must be a JSON object")
    if not parsed:
        raise SkillLoadError(f"{path}: Tools Schema must define at least one intent")
    return parsed


def _object_without_duplicates(
    pairs: list[tuple[str, Any]],
    path: Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SkillLoadError(f"{path}: duplicate JSON key {key!r}")
        result[key] = value
    return result


def _extract_json_object(text: str, *, path: Path) -> str:
    start = text.find("{")
    if start < 0:
        raise SkillLoadError(f"{path}: Tools Schema does not contain a JSON object")

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
    raise SkillLoadError(f"{path}: unclosed Tools Schema JSON object")


def _parse_intent(path: Path, intent_name: str, raw_intent: Any) -> IntentSchema:
    if not isinstance(intent_name, str) or not intent_name.strip():
        raise SkillLoadError(f"{path}: intent name must be a non-empty string")
    if not isinstance(raw_intent, dict):
        raise SkillLoadError(f"{path}: intent {intent_name!r} must be an object")
    extra_fields = sorted(set(raw_intent) - INTENT_FIELDS)
    if extra_fields:
        raise SkillLoadError(
            f"{path}: intent {intent_name!r} has unknown fields: "
            f"{', '.join(extra_fields)}",
        )

    code = raw_intent.get("code")
    if not isinstance(code, str) or not code:
        raise SkillLoadError(
            f"{path}: intent {intent_name!r} code must be a non-empty string",
        )
    desc = raw_intent.get("desc")
    if not isinstance(desc, str) or not desc.strip():
        raise SkillLoadError(
            f"{path}: intent {intent_name!r} desc must be a non-empty string",
        )
    if "params" not in raw_intent or not isinstance(raw_intent["params"], dict):
        raise SkillLoadError(f"{path}: intent {intent_name!r} params must be an object")

    intent_status = raw_intent.get("status", "active")
    if intent_status not in SKILL_STATUSES:
        raise SkillLoadError(
            f"{path}: intent {intent_name!r} has invalid status {intent_status!r}",
        )

    params = {
        name: _parse_param(path, intent_name, name, raw_schema)
        for name, raw_schema in raw_intent["params"].items()
    }
    return IntentSchema(
        name=intent_name,
        code=code,
        desc=desc.strip(),
        params=params,
        status=intent_status,
    )


def _parse_param(
    path: Path,
    intent_name: str,
    name: str,
    raw_schema: Any,
) -> ParamSchema:
    prefix = f"{path}: intent {intent_name!r} param {name!r}"
    if not isinstance(name, str) or not name:
        raise SkillLoadError(f"{prefix} must have a non-empty name")
    if not isinstance(raw_schema, dict):
        raise SkillLoadError(f"{prefix} must be an object")
    extra_fields = sorted(set(raw_schema) - PARAM_FIELDS)
    if extra_fields:
        raise SkillLoadError(f"{prefix} has unknown fields: {', '.join(extra_fields)}")

    param_type = raw_schema.get("type")
    if param_type not in PARAM_TYPES:
        raise SkillLoadError(
            f"{prefix} type must be one of {sorted(PARAM_TYPES)}, got {param_type!r}",
        )
    required = raw_schema.get("required")
    if not isinstance(required, bool):
        raise SkillLoadError(f"{prefix} required must be boolean")
    desc = raw_schema.get("desc")
    if not isinstance(desc, str) or not desc.strip():
        raise SkillLoadError(f"{prefix} desc must be a non-empty string")

    items: ParamItemsSchema | None = None
    raw_items = raw_schema.get("items")
    if param_type == "array":
        if not isinstance(raw_items, dict) or raw_items.get("type") not in ITEM_TYPES:
            raise SkillLoadError(
                f"{prefix} array items.type must be one of {sorted(ITEM_TYPES)}",
            )
        if set(raw_items) != {"type"}:
            raise SkillLoadError(f"{prefix} items only supports the type field")
        items = ParamItemsSchema(type=raw_items["type"])
    elif raw_items is not None:
        raise SkillLoadError(f"{prefix} items is only valid for array parameters")

    allowed_values = raw_schema.get("allowed_values", [])
    if not isinstance(allowed_values, list):
        raise SkillLoadError(f"{prefix} allowed_values must be an array")
    value_type = items.type if items is not None else param_type
    for value in allowed_values:
        if not _value_matches_type(value, value_type):
            raise SkillLoadError(
                f"{prefix} allowed value {value!r} does not match type {value_type}",
            )

    default = raw_schema.get("default")
    if "default" in raw_schema and not _value_matches_param(default, param_type, items):
        raise SkillLoadError(f"{prefix} default does not match type {param_type}")

    normalization = raw_schema.get("normalization", "")
    if not isinstance(normalization, str):
        raise SkillLoadError(f"{prefix} normalization must be a string")

    return ParamSchema(
        name=name,
        type=param_type,
        required=required,
        desc=desc.strip(),
        allowed_values=allowed_values,
        items=items,
        default=default,
        normalization=normalization,
    )


def _value_matches_param(
    value: Any,
    param_type: str,
    items: ParamItemsSchema | None,
) -> bool:
    if not _value_matches_type(value, param_type):
        return False
    if param_type == "array" and items is not None:
        return all(_value_matches_type(item, items.type) for item in value)
    return True


def _value_matches_type(value: Any, expected_type: str) -> bool:
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    if expected_type == "array":
        return isinstance(value, list)
    if expected_type == "object":
        return isinstance(value, dict)
    return False


def _validate_intent_specific_rules(
    path: Path,
    section: str,
    intents: set[str],
) -> None:
    for match in SUBSECTION_HEADING_RE.finditer(section):
        heading = match.group("name").strip()
        if heading not in intents:
            raise SkillLoadError(
                f"{path}: Intent-Specific Rules references unknown intent {heading!r}",
            )


def _required_string(metadata: dict[str, Any], name: str, *, path: Path) -> str:
    value = metadata.get(name)
    if not isinstance(value, str) or not value.strip():
        raise SkillLoadError(f"{path}: frontmatter {name} must be a non-empty string")
    return value.strip()


def _required_string_list(
    metadata: dict[str, Any],
    name: str,
    *,
    path: Path,
) -> list[str]:
    value = metadata.get(name)
    if not isinstance(value, list) or not value:
        raise SkillLoadError(f"{path}: frontmatter {name} must be a non-empty list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise SkillLoadError(f"{path}: frontmatter {name} must contain strings")
    return [item.strip() for item in value]


def _optional_string_list(
    metadata: dict[str, Any],
    name: str,
    *,
    path: Path,
) -> list[str]:
    if name not in metadata:
        return []
    value = metadata[name]
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise SkillLoadError(f"{path}: frontmatter {name} must be a string list")
    return [item.strip() for item in value]


def _optional_string(
    metadata: dict[str, Any],
    name: str,
    *,
    path: Path,
) -> str | None:
    if name not in metadata:
        return None
    value = metadata[name]
    if not isinstance(value, str) or not value.strip():
        raise SkillLoadError(f"{path}: frontmatter {name} must be a non-empty string")
    return value.strip()
