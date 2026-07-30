from __future__ import annotations

from .config import ConfidenceConfig, DecisionConfig
from .types import (
    ContextualizedRequest,
    DecisionGateResult,
    IntentCandidate,
)


BLOCKING_RISK_FLAGS = {
    "illegal_route",
    "label_conflict",
    "semantic_frame_severe_conflict",
}
EVALUATOR_RISK_FLAGS = {
    "answer_vs_resource",
    "context_unclear",
    "label_conflict",
    "search_vs_tool_entry",
    "semantic_frame_conflict",
}


def decide(
    candidates: list[IntentCandidate],
    contextualized_request: ContextualizedRequest,
    config: DecisionConfig,
) -> DecisionGateResult:
    if not candidates:
        return DecisionGateResult(
            action="error",
            reason="no valid intent candidates",
        )

    top1 = candidates[0]
    top2 = candidates[1] if len(candidates) > 1 else None
    top2_score = top2.confidence if top2 else 0.0
    margin = top1.confidence - top2_score
    risks = _dedupe(
        [
            *contextualized_request.conflict_flags,
            *top1.risk_flags,
            *(top2.risk_flags if top2 else []),
        ],
    )
    cross_route_domain = bool(
        top2 is not None
        and top1.skill_id is not None
        and top2.skill_id is not None
        and top1.skill_id != top2.skill_id,
    )
    uncertain_context = (
        contextualized_request.relation_to_history == "ambiguous"
        or bool(contextualized_request.semantic_frame.uncertainty_notes)
        or bool(contextualized_request.conflict_flags)
    )
    direct = (
        top1.confidence >= config.direct_select_min_score
        and margin >= config.direct_select_min_margin
        and bool(top1.matched_evidence)
        and not cross_route_domain
        and not uncertain_context
        and not (set(risks) & EVALUATOR_RISK_FLAGS)
    )

    if config.evaluator_mode == "always":
        action = "evaluate"
        reason = "evaluator mode is always"
    elif direct:
        action = "direct"
        reason = "high-confidence candidate passed the direct decision gate"
    elif config.evaluator_mode == "disabled":
        action = "abstain"
        reason = "candidate did not pass direct gate and evaluator is disabled"
    else:
        action = "evaluate"
        reason = _evaluation_reason(
            top1=top1,
            margin=margin,
            cross_route_domain=cross_route_domain,
            uncertain_context=uncertain_context,
            risks=risks,
            config=config,
        )

    return DecisionGateResult(
        action=action,
        selected_candidate_id=top1.candidate_id if action == "direct" else None,
        reason=reason,
        top1_score=top1.confidence,
        top2_score=top2_score,
        margin=margin,
        risk_flags=risks,
    )


def evaluator_switch_allowed(
    *,
    first_candidate: IntentCandidate,
    selected_candidate: IntentCandidate,
    evaluator_confidence: float,
    score_by_id: dict[str, float],
    config: DecisionConfig,
) -> tuple[bool, float, str]:
    if selected_candidate.candidate_id == first_candidate.candidate_id:
        return True, 0.0, "evaluator kept the first candidate"

    selected_score = score_by_id.get(selected_candidate.candidate_id, 0.0)
    first_score = score_by_id.get(first_candidate.candidate_id, 0.0)
    margin = selected_score - first_score
    blocking = set(selected_candidate.risk_flags) & BLOCKING_RISK_FLAGS
    allowed = (
        evaluator_confidence >= config.evaluator_min_confidence
        and margin >= config.evaluator_switch_margin
        and not blocking
    )
    reason = (
        f"evaluator_confidence={evaluator_confidence:.3f}, "
        f"switch_margin={margin:.3f}, blocking_risks={sorted(blocking)}"
    )
    return allowed, margin, reason


def final_confidence(
    *,
    selector_score: float,
    selector_margin: float,
    contextualized_request: ContextualizedRequest,
    risk_flags: list[str],
    config: ConfidenceConfig,
    evaluator_confidence: float | None = None,
    evaluator_margin: float | None = None,
) -> tuple[float, dict[str, float]]:
    normalized_selector_margin = min(1.0, max(0.0, selector_margin) / config.margin_scale)
    if evaluator_confidence is None:
        base = (
            config.direct_selector_weight * selector_score
            + config.direct_margin_weight * normalized_selector_margin
        )
        normalized_evaluator_margin = 0.0
    else:
        normalized_evaluator_margin = min(
            1.0,
            max(0.0, evaluator_margin or 0.0) / config.margin_scale,
        )
        base = (
            config.evaluated_selector_weight * selector_score
            + config.evaluated_confidence_weight * evaluator_confidence
            + config.evaluated_margin_weight * normalized_evaluator_margin
        )

    risk_count = len(set(risk_flags) - BLOCKING_RISK_FLAGS)
    risk_penalty = min(0.20, risk_count * config.non_blocking_risk_penalty)
    ambiguity_penalty = (
        config.context_ambiguity_penalty
        if contextualized_request.relation_to_history == "ambiguous"
        else 0.0
    )
    conflict_penalty = min(
        config.max_context_conflict_penalty,
        len(contextualized_request.conflict_flags) * config.context_conflict_penalty,
    )
    score = max(0.0, min(1.0, base - risk_penalty - ambiguity_penalty - conflict_penalty))
    return score, {
        "selector_score": selector_score,
        "selector_margin": selector_margin,
        "normalized_selector_margin": normalized_selector_margin,
        "evaluator_confidence": evaluator_confidence or 0.0,
        "evaluator_margin": evaluator_margin or 0.0,
        "normalized_evaluator_margin": normalized_evaluator_margin,
        "risk_penalty": risk_penalty,
        "ambiguity_penalty": ambiguity_penalty,
        "conflict_penalty": conflict_penalty,
        "final_confidence": score,
    }


def _evaluation_reason(
    *,
    top1: IntentCandidate,
    margin: float,
    cross_route_domain: bool,
    uncertain_context: bool,
    risks: list[str],
    config: DecisionConfig,
) -> str:
    reasons: list[str] = []
    if top1.confidence < config.direct_select_min_score:
        reasons.append("top1 score below direct threshold")
    if margin < config.direct_select_min_margin:
        reasons.append("candidate margin below direct threshold")
    if not top1.matched_evidence:
        reasons.append("top1 has no validated evidence")
    if cross_route_domain:
        reasons.append("top candidates cross skill domains")
    if uncertain_context:
        reasons.append("context is uncertain or conflicting")
    if set(risks) & EVALUATOR_RISK_FLAGS:
        reasons.append("candidate set contains evaluator risk flags")
    return "; ".join(reasons) or "candidate requires evaluator review"


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))
