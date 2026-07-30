from __future__ import annotations

from .skills import SkillRegistry
from .types import IntentCandidate, RerankDecision


class ValidationError(ValueError):
    pass


def validate_intent_candidate(
    registry: SkillRegistry,
    candidate: IntentCandidate,
) -> IntentCandidate:
    if candidate.skill_id is None:
        if candidate.intent != "普通对话" or candidate.code != "000":
            raise ValidationError("ordinary dialogue candidate must use intent='普通对话' and code='000'")
        return candidate

    if not registry.has(candidate.skill_id, active_only=True):
        raise ValidationError(f"skill {candidate.skill_id!r} is not defined")

    skill = registry.get(candidate.skill_id)
    if candidate.intent not in skill.intents:
        raise ValidationError(
            f"intent {candidate.intent!r} is not defined in skill {skill.id!r}",
        )

    schema = skill.intents[candidate.intent]
    if schema.status != "active":
        raise ValidationError(
            f"intent {candidate.intent!r} in skill {skill.id!r} is not active",
        )
    if candidate.code != schema.code:
        raise ValidationError(
            f"intent {candidate.intent!r} code must be {schema.code!r}, got {candidate.code!r}",
        )

    return candidate


def validate_rerank_decision(
    decision: RerankDecision,
    candidate_ids: set[str],
) -> RerankDecision:
    ranking_ids: set[str] = set()
    for score in decision.ranking:
        if score.candidate_id not in candidate_ids:
            raise ValidationError(
                f"rerank ranking contains unknown candidate_id {score.candidate_id!r}",
            )
        if score.candidate_id in ranking_ids:
            raise ValidationError(
                f"rerank ranking duplicates candidate_id {score.candidate_id!r}",
            )
        ranking_ids.add(score.candidate_id)

    if decision.verdict == "select":
        if not decision.selected_candidate_id:
            raise ValidationError("select verdict must include selected_candidate_id")
        if decision.selected_candidate_id not in candidate_ids:
            raise ValidationError(
                f"selected_candidate_id {decision.selected_candidate_id!r} is not in candidate set",
            )
        return decision

    if decision.verdict == "expand" and not decision.expand_scope:
        raise ValidationError("expand verdict must include expand_scope")
    if decision.verdict in {"abstain", "unsupported"}:
        if decision.selected_candidate_id is not None:
            raise ValidationError(
                f"{decision.verdict} verdict must not select a candidate",
            )
        if decision.expand_scope is not None:
            raise ValidationError(
                f"{decision.verdict} verdict must not include expand_scope",
            )
        return decision
    if decision.selected_candidate_id and decision.selected_candidate_id not in candidate_ids:
        raise ValidationError(
            f"selected_candidate_id {decision.selected_candidate_id!r} is not in candidate set",
        )
    return decision
