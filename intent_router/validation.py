from __future__ import annotations

from typing import Any

from .types import EvaluationDecision, IntentDecision, SkillDefinition


class ValidationError(ValueError):
    pass


def validate_intent_decision(
    skill: SkillDefinition,
    decision: IntentDecision,
) -> IntentDecision:
    if decision.status != "matched":
        return decision
    if not decision.intent:
        raise ValidationError("matched decision misses intent")
    if decision.intent not in skill.intents:
        raise ValidationError(
            f"intent {decision.intent!r} is not defined in skill {skill.id!r}",
        )

    schema = skill.intents[decision.intent]
    if decision.code != schema.code:
        raise ValidationError(
            f"intent {decision.intent!r} code must be {schema.code!r}, got {decision.code!r}",
        )

    if not isinstance(decision.params, dict):
        raise ValidationError("params must be an object")

    allowed_param_names = set(schema.params)
    extra_params = set(decision.params) - allowed_param_names
    if extra_params:
        raise ValidationError(f"unknown params: {sorted(extra_params)}")

    for param_name, value in decision.params.items():
        param_schema = schema.params[param_name]
        _validate_allowed_values(param_name, value, param_schema.allowed_values)

    return decision


def validate_evaluation_decision(
    evaluation: EvaluationDecision,
) -> EvaluationDecision:
    if evaluation.verdict == "clarify":
        return evaluation

    if evaluation.verdict == "accept":
        if evaluation.reject_scope:
            raise ValidationError("accepted evaluation must not include reject_scope")
        if "fail" in {
            evaluation.skill_check,
            evaluation.intent_check,
            evaluation.params_check,
        }:
            raise ValidationError("accepted evaluation must not include failed checks")
        return evaluation

    if not evaluation.reject_scope:
        raise ValidationError("rejected evaluation misses reject_scope")

    if evaluation.reject_scope == "skill_mismatch":
        if evaluation.skill_check != "fail":
            raise ValidationError(
                "skill_mismatch requires skill_check='fail'",
            )
        return evaluation

    if evaluation.reject_scope == "intent_mismatch":
        if evaluation.skill_check != "pass" or evaluation.intent_check != "fail":
            raise ValidationError(
                "intent_mismatch requires skill_check='pass' and intent_check='fail'",
            )
        return evaluation

    if evaluation.reject_scope == "param_mismatch":
        if (
            evaluation.skill_check != "pass"
            or evaluation.intent_check != "pass"
            or evaluation.params_check != "fail"
        ):
            raise ValidationError(
                "param_mismatch requires skill_check='pass', "
                "intent_check='pass', and params_check='fail'",
            )
        return evaluation

    raise ValidationError(f"unsupported reject_scope: {evaluation.reject_scope}")


def _validate_allowed_values(
    param_name: str,
    value: Any,
    allowed_values: list[str],
) -> None:
    if not allowed_values:
        return
    values = value if isinstance(value, list) else [value]
    invalid = [item for item in values if item not in allowed_values]
    if invalid:
        raise ValidationError(
            f"param {param_name!r} contains values outside allowed enum: {invalid}",
        )
