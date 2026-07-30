from __future__ import annotations

import re
from typing import Any

from .types import (
    IntentSchema,
    ParameterExtractionResult,
    ParameterValidationResult,
    ParamSchema,
)


OPAQUE_HANDLE_PATTERN = re.compile(
    r"(?:file|image|mail|folder|album|note|task|contact|resource)_?id$",
    re.IGNORECASE,
)


def validate_parameters(
    schema: IntentSchema,
    extraction: ParameterExtractionResult,
    *,
    current_user_query: str,
    resolved_query: str,
    trusted_params: dict[str, Any] | None = None,
) -> ParameterValidationResult:
    trusted_params = trusted_params or {}
    result = ParameterValidationResult()
    evidence_text = _compact(f"{current_user_query} {resolved_query}")

    supplied = dict(extraction.params)
    supplied.update(
        {
            name: value
            for name, value in trusted_params.items()
            if name in schema.params
        },
    )

    for name, value in supplied.items():
        param_schema = schema.params.get(name)
        if param_schema is None:
            result.rejected_fields[name] = "field is not defined in parameter schema"
            continue
        if OPAQUE_HANDLE_PATTERN.search(name) and name not in trusted_params:
            result.rejected_fields[name] = "opaque handles must come from trusted_params"
            result.uncertain_fields.append(name)
            continue
        if name not in trusted_params and not _has_grounded_evidence(
            extraction.evidence.get(name, []),
            evidence_text,
        ):
            result.rejected_fields[name] = "parameter has no grounded query evidence"
            result.uncertain_fields.append(name)
            continue
        error = _validate_value(param_schema, value)
        if error:
            result.rejected_fields[name] = error
            result.uncertain_fields.append(name)
            continue
        result.params[name] = value

    for name, param_schema in schema.params.items():
        if name not in result.params and param_schema.default is not None:
            if _validate_value(param_schema, param_schema.default) is None:
                result.params[name] = param_schema.default

    result.uncertain_fields = _dedupe(
        [*result.uncertain_fields, *extraction.uncertain_fields],
    )
    result.missing_required = [
        name
        for name, param_schema in schema.params.items()
        if param_schema.required and name not in result.params
    ]
    return result


def _validate_value(schema: ParamSchema, value: Any) -> str | None:
    if not _matches_type(schema.type, value):
        return f"expected {schema.type}"
    if schema.allowed_values and value not in schema.allowed_values:
        return f"value is not in allowed_values {schema.allowed_values!r}"
    if schema.type == "array" and schema.items is not None:
        for index, item in enumerate(value):
            if not _matches_type(schema.items.type, item):
                return f"array item {index} expected {schema.items.type}"
    return None


def _matches_type(expected: str, value: Any) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return False


def _has_grounded_evidence(evidence: list[str], compact_text: str) -> bool:
    return any(_compact(item) and _compact(item) in compact_text for item in evidence)


def _compact(value: str) -> str:
    return "".join(value.lower().split())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
