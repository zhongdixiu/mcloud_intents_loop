from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ParamType = Literal["string", "integer", "number", "boolean", "array", "object"]
SkillStatus = Literal["active", "deprecated", "disabled"]


class ParamItemsSchema(BaseModel):
    type: Literal["string", "integer", "number", "boolean", "object"]


class ParamSchema(BaseModel):
    name: str
    type: ParamType
    required: bool
    desc: str
    allowed_values: list[Any] = Field(default_factory=list)
    items: ParamItemsSchema | None = None
    default: Any | None = None
    normalization: str = ""


class IntentSchema(BaseModel):
    name: str
    code: str
    desc: str
    params: dict[str, ParamSchema] = Field(default_factory=dict)
    status: SkillStatus = "active"


class SkillDefinition(BaseModel):
    id: str
    name: str
    description: str
    version: str
    scope: list[str]
    out_of_scope: list[str]
    aliases: list[str] = Field(default_factory=list)
    owner: str | None = None
    status: SkillStatus = "active"
    path: Path

    skill_scope: str
    routing_principles: str
    contrast_rules: str
    tools_schema_text: str = ""
    intent_specific_rules: str
    positive_examples: str
    negative_examples: str
    execution_instructions: str
    raw_markdown: str
    intents: dict[str, IntentSchema]


class SkillCard(BaseModel):
    id: str
    name: str
    description: str
    version: str
    scope: list[str]
    out_of_scope: list[str]
    aliases: list[str] = Field(default_factory=list)


class IntentRoutingContext(BaseModel):
    skill_id: str
    skill_name: str
    skill_scope: str
    routing_principles: str
    contrast_rules: str
    intents: dict[str, IntentSchema]
    intent_specific_rules: str
    positive_examples: str
    negative_examples: str


class ExecutionContext(BaseModel):
    skill_id: str
    skill_name: str
    execution_instructions: str


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


KNOWN_STRUCTURED_EXTRA_FIELDS = {
    "risk_flags_detail",
    "risk_flags_note",
    "analysis",
    "notes",
    "debug",
}


def _normalize_structured_dict(
    data: Any,
    *,
    json_list_fields: tuple[str, ...] = (),
) -> Any:
    if not isinstance(data, dict):
        return data

    normalized = dict(data)
    for field in KNOWN_STRUCTURED_EXTRA_FIELDS:
        normalized.pop(field, None)

    for field in json_list_fields:
        value = normalized.get(field)
        if not isinstance(value, str):
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        normalized[field] = parsed

    return normalized


CONTEXT_RELATION_VALUES = {
    "new_request",
    "continuation",
    "revision",
    "answer_to_previous",
    "ambiguous",
}


class SemanticFrame(StrictOutputModel):
    action: str | None = None
    expected_result_type: Literal[
        "resource",
        "function_entry",
        "content_generation",
        "content_processing",
        "mail_action",
        "social_share",
        "ordinary_answer",
        "unknown",
    ] = "unknown"
    object_types: list[str] = Field(default_factory=list)
    subjects: list[str] = Field(default_factory=list)
    qualifiers: list[str] = Field(default_factory=list)
    inherited_turns: list[int] = Field(default_factory=list)
    explicit_overrides: list[str] = Field(default_factory=list)
    uncertainty_notes: list[str] = Field(default_factory=list)


class ContextualizedRequest(StrictOutputModel):
    resolved_query: str
    relation_to_history: Literal[
        "new_request",
        "continuation",
        "revision",
        "answer_to_previous",
        "ambiguous",
    ] = "new_request"
    semantic_frame: SemanticFrame = Field(default_factory=SemanticFrame)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    conflict_flags: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_contextualizer_output(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        normalized = dict(data)
        status = normalized.pop("status", None)
        if status in CONTEXT_RELATION_VALUES:
            normalized.setdefault("relation_to_history", status)
        elif status == "clarify":
            normalized.setdefault("relation_to_history", "ambiguous")

        used_history_turns = normalized.pop("used_history_turns", None)
        semantic_frame = normalized.get("semantic_frame")
        if semantic_frame is None:
            semantic_frame = {}
        if isinstance(semantic_frame, dict) and used_history_turns:
            semantic_frame = dict(semantic_frame)
            semantic_frame.setdefault("inherited_turns", used_history_turns)
        normalized["semantic_frame"] = semantic_frame

        normalized.pop("question", None)
        normalized.pop("options", None)
        normalized.pop("clarify_scope", None)
        return normalized


class SkillCandidate(StrictOutputModel):
    candidate_id: str
    skill_id: str | None = None
    intent_domain: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_evidence: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        normalized = _normalize_structured_dict(data)
        if isinstance(normalized, dict) and "matched_evidence" not in normalized:
            normalized["matched_evidence"] = normalized.pop("matched_cues", [])
        return normalized


class SkillCandidateSet(StrictOutputModel):
    candidates: list[SkillCandidate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        return _normalize_structured_dict(data, json_list_fields=("candidates",))


class IntentCandidate(StrictOutputModel):
    candidate_id: str
    skill_id: str | None = None
    skill_name: str | None = None
    intent: str
    code: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    matched_evidence: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    reason: str = ""
    alternatives: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        normalized = _normalize_structured_dict(data)
        if isinstance(normalized, dict):
            normalized.pop("params", None)
            if "matched_evidence" not in normalized:
                normalized["matched_evidence"] = normalized.pop("matched_cues", [])
        return normalized


class IntentCandidateSet(StrictOutputModel):
    candidates: list[IntentCandidate] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        return _normalize_structured_dict(data, json_list_fields=("candidates",))


class CandidateScore(StrictOutputModel):
    candidate_id: str
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        return _normalize_structured_dict(data)


class RerankDecision(StrictOutputModel):
    verdict: Literal["select", "expand", "abstain", "unsupported"]
    selected_candidate_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    ranking: list[CandidateScore] = Field(default_factory=list)
    reason: str = ""
    expand_scope: Literal[
        "skill_recall_gap",
        "intent_recall_gap",
        "context_unclear",
    ] | None = None
    expansion_hint: str | None = None
    risk_flags: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_model_output(cls, data: Any) -> Any:
        return _normalize_structured_dict(data, json_list_fields=("ranking",))


class ModelCallRecord(BaseModel):
    stage: str
    duration_ms: float = 0.0
    attempts: int = 1
    status: Literal["ok", "timeout", "error", "skipped"] = "ok"
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class ParameterExtractionResult(StrictOutputModel):
    params: dict[str, Any] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    evidence: dict[str, list[str]] = Field(default_factory=dict)


class ParameterValidationResult(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    missing_required: list[str] = Field(default_factory=list)
    uncertain_fields: list[str] = Field(default_factory=list)
    rejected_fields: dict[str, str] = Field(default_factory=dict)


class DecisionGateResult(BaseModel):
    action: Literal["direct", "evaluate", "abstain", "error"]
    selected_candidate_id: str | None = None
    reason: str = ""
    top1_score: float = 0.0
    top2_score: float = 0.0
    margin: float = 0.0
    risk_flags: list[str] = Field(default_factory=list)


class RouteDiagnostics(BaseModel):
    first_candidate_id: str | None = None
    final_candidate_id: str | None = None
    evaluator_action: str = "not_called"
    evaluator_called: bool = False
    expansion_rounds: int = 0
    risk_flags: list[str] = Field(default_factory=list)
    rerank_reason: str = ""
    fallback_reason: str | None = None
    gate_reason: str = ""
    gate_margin: float = 0.0
    selector_score: float = 0.0
    evaluator_score: float | None = None
    evaluator_margin: float | None = None
    final_confidence_features: dict[str, float] = Field(default_factory=dict)
    failed_skills: dict[str, str] = Field(default_factory=dict)
    expansion_scopes: list[str] = Field(default_factory=list)
    discarded_candidates: list[dict[str, str]] = Field(default_factory=list)
    parameter_validation: ParameterValidationResult | None = None
    model_calls: list[ModelCallRecord] = Field(default_factory=list)


class SkillRef(BaseModel):
    id: str
    name: str


class DialogueRouteSummary(BaseModel):
    status: Literal["matched", "clarify", "abstain", "unsupported", "error"]
    skill_id: str | None = None
    skill_name: str | None = None
    intent: str | None = None
    code: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    reason: str | None = None
    resolved_query: str | None = None
    context_relation: str | None = None


class DialogueTurn(BaseModel):
    user_query: str
    result: DialogueRouteSummary
    metadata: dict[str, Any] = Field(default_factory=dict)


class DialogueHistory(BaseModel):
    turns: list[DialogueTurn] = Field(default_factory=list)


class RouteResult(BaseModel):
    status: Literal["matched", "clarify", "abstain", "unsupported", "error"] = "matched"
    skill: SkillRef | None = None
    intent: str | None = None
    code: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    reason: str | None = None
    visited_skills: list[str] = Field(default_factory=list)
    loop_count: int = 1
    correction_scopes: list[str] = Field(default_factory=list)
    resolved_query: str | None = None
    context_relation: str | None = None
    alternatives: list[IntentCandidate] = Field(default_factory=list)
    diagnostics: RouteDiagnostics | None = None
    termination_reason: str | None = None
