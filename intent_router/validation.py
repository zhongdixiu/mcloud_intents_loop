from __future__ import annotations

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
        return evaluation

    raise ValidationError(f"unsupported reject_scope: {evaluation.reject_scope}")
