from __future__ import annotations

from dataclasses import dataclass, field

from .skills import SkillRegistry
from .types import (
    ContextualizedRequest,
    IntentCandidate,
    SkillCandidate,
)
from .validation import ValidationError, validate_intent_candidate


ORDINARY_CANDIDATE_ID = "ordinary_dialogue:普通对话"


@dataclass
class CandidateAggregation:
    candidates: list[IntentCandidate] = field(default_factory=list)
    discarded: list[dict[str, str]] = field(default_factory=list)


def normalize_skill_candidates(
    registry: SkillRegistry,
    candidates: list[SkillCandidate],
    *,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
    max_business_candidates: int,
) -> tuple[list[SkillCandidate], list[dict[str, str]]]:
    normalized: list[SkillCandidate] = []
    discarded: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_skills: set[str | None] = set()
    evidence_text = semantic_evidence_text(contextualized_request, current_user_query)

    for candidate in candidates:
        if candidate.candidate_id in seen_ids:
            discarded.append(_discard(candidate.candidate_id, "duplicate candidate_id"))
            continue
        if candidate.skill_id in seen_skills:
            discarded.append(_discard(candidate.candidate_id, "duplicate skill candidate"))
            continue
        if not 0.0 <= candidate.confidence <= 1.0:
            discarded.append(_discard(candidate.candidate_id, "confidence outside [0,1]"))
            continue
        if candidate.skill_id is not None and not registry.has(
            candidate.skill_id,
            active_only=True,
        ):
            discarded.append(_discard(candidate.candidate_id, "unknown or inactive skill"))
            continue
        candidate.matched_evidence = valid_evidence(
            candidate.matched_evidence,
            evidence_text,
        )
        candidate.candidate_id = (
            f"skill:{candidate.skill_id}"
            if candidate.skill_id is not None
            else "skill:ordinary_dialogue"
        )
        seen_ids.add(candidate.candidate_id)
        seen_skills.add(candidate.skill_id)
        normalized.append(candidate)

    business = [candidate for candidate in normalized if candidate.skill_id is not None]
    ordinary = [candidate for candidate in normalized if candidate.skill_id is None]
    business = business[:max_business_candidates]
    if not ordinary:
        ordinary = [
            SkillCandidate(
                candidate_id="skill:ordinary_dialogue",
                skill_id=None,
                intent_domain="普通对话",
                confidence=0.15,
                reason="ordinary dialogue candidate",
            ),
        ]
    return [*business, ordinary[0]], discarded


def dynamic_business_candidate_count(
    candidates: list[SkillCandidate],
    *,
    high_confidence_count: int,
    default_count: int,
    max_count: int,
    high_confidence_threshold: float,
    high_margin_threshold: float,
    low_confidence_threshold: float,
    low_margin_threshold: float,
) -> int:
    business = [candidate for candidate in candidates if candidate.skill_id is not None]
    if not business:
        return 0
    top1 = business[0].confidence
    top2 = business[1].confidence if len(business) > 1 else 0.0
    margin = top1 - top2
    if top1 >= high_confidence_threshold and margin >= high_margin_threshold:
        return min(high_confidence_count, len(business))
    if top1 < low_confidence_threshold or margin < low_margin_threshold:
        return min(max_count, len(business))
    return min(default_count, len(business))


def validate_and_merge_intent_candidates(
    registry: SkillRegistry,
    candidates: list[IntentCandidate],
    *,
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> CandidateAggregation:
    result = CandidateAggregation()
    positions: dict[tuple[str | None, str, str], int] = {}
    evidence_text = semantic_evidence_text(contextualized_request, current_user_query)

    for candidate in candidates:
        if not 0.0 <= candidate.confidence <= 1.0:
            result.discarded.append(
                _discard(candidate.candidate_id, "confidence outside [0,1]"),
            )
            continue
        try:
            validate_intent_candidate(registry, candidate)
        except ValidationError as exc:
            result.discarded.append(_discard(candidate.candidate_id, str(exc)))
            continue

        source_id = candidate.candidate_id
        candidate.matched_evidence = valid_evidence(
            candidate.matched_evidence,
            evidence_text,
        )
        candidate.candidate_id = canonical_candidate_id(candidate)
        candidate.source_ids = _dedupe(
            [*candidate.source_ids, source_id],
        )
        key = route_key(candidate)
        if key not in positions:
            positions[key] = len(result.candidates)
            result.candidates.append(candidate)
            continue

        index = positions[key]
        existing = result.candidates[index]
        primary, secondary = (
            (candidate, existing)
            if candidate.confidence > existing.confidence
            else (existing, candidate)
        )
        primary.candidate_id = canonical_candidate_id(primary)
        primary.matched_evidence = _dedupe(
            [*existing.matched_evidence, *candidate.matched_evidence],
        )
        primary.risk_flags = _dedupe(
            [*existing.risk_flags, *candidate.risk_flags],
        )
        primary.alternatives = _dedupe(
            [*existing.alternatives, *candidate.alternatives],
        )
        primary.source_ids = _dedupe(
            [*existing.source_ids, *candidate.source_ids],
        )
        if not primary.reason:
            primary.reason = secondary.reason
        result.candidates[index] = primary

    return result


def canonical_candidate_id(candidate: IntentCandidate) -> str:
    if candidate.skill_id is None:
        return ORDINARY_CANDIDATE_ID
    return f"{candidate.skill_id}:{candidate.intent}"


def route_key(candidate: IntentCandidate) -> tuple[str | None, str, str]:
    return candidate.skill_id, candidate.intent, candidate.code


def semantic_evidence_text(
    contextualized_request: ContextualizedRequest,
    current_user_query: str,
) -> str:
    frame = contextualized_request.semantic_frame
    return " ".join(
        [
            current_user_query,
            contextualized_request.resolved_query,
            frame.action or "",
            frame.expected_result_type,
            *frame.object_types,
            *frame.subjects,
            *frame.qualifiers,
            *frame.explicit_overrides,
        ],
    ).lower()


def valid_evidence(values: list[str], evidence_text: str) -> list[str]:
    compact_text = _compact(evidence_text)
    return _dedupe(
        [
            value
            for value in values
            if value and _compact(value) and _compact(value) in compact_text
        ],
    )


def _compact(value: str) -> str:
    return "".join(value.lower().split())


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _discard(candidate_id: str, reason: str) -> dict[str, str]:
    return {"candidate_id": candidate_id, "reason": reason}
