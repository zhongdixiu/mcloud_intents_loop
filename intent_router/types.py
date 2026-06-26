from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ParamSchema(BaseModel):
    name: str
    type: str | None = None
    desc: str = ""
    allowed_values: list[str] = Field(default_factory=list)


class IntentSchema(BaseModel):
    name: str
    code: str
    desc: str = ""
    params: dict[str, ParamSchema] = Field(default_factory=dict)


class SkillDefinition(BaseModel):
    id: str
    name: str
    description: str
    path: Path
    special_rules: str = ""
    
    tools_schema_text: str = ""
    raw_markdown: str
    intents: dict[str, IntentSchema]


class SkillCard(BaseModel):
    id: str
    name: str
    description: str
    intents: list[str]


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillRouteDecision(StrictOutputModel):
    status: Literal["route", "clarify", "no_match"]
    skill_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class IntentDecision(StrictOutputModel):
    status: Literal["matched", "clarify", "no_match"]
    intent: str | None = None
    code: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class EvaluationDecision(StrictOutputModel):
    verdict: Literal["accept", "reject", "clarify"]
    reject_scope: (
        Literal["skill_mismatch", "intent_mismatch", "param_mismatch"] | None
    ) = None
    skill_check: Literal["pass", "fail", "unclear"] | None = None
    intent_check: Literal["pass", "fail", "unclear"] | None = None
    params_check: Literal["pass", "fail", "unclear"] | None = None
    confidence: float = 0.0
    reason: str = ""
    clarity_reason: str | None = None
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class ContextualizedRequest(StrictOutputModel):
    status: Literal["resolved", "clarify"]
    resolved_query: str | None = None
    relation_to_history: Literal[
        "new_request",
        "continuation",
        "revision",
        "answer_to_previous",
        "ambiguous",
    ] = "new_request"
    used_history_turns: list[int] = Field(default_factory=list)
    reason: str = ""
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_status_relation_mixup(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        status = data.get("status")
        relation_values = {
            "new_request",
            "continuation",
            "revision",
            "answer_to_previous",
            "ambiguous",
        }
        if status not in relation_values:
            return data

        normalized = dict(data)
        normalized.setdefault("relation_to_history", status)
        if status == "ambiguous":
            normalized["status"] = (
                "clarify"
                if normalized.get("question") or normalized.get("options")
                else "resolved"
            )
        else:
            normalized["status"] = "resolved"
        return normalized


class LoopExhaustedClarification(StrictOutputModel):
    question: str
    options: list[dict[str, str]] = Field(default_factory=list)
    reason: str = ""


class SkillRef(BaseModel):
    id: str
    name: str


class DialogueRouteSummary(BaseModel):
    status: Literal["matched", "clarify", "no_match"]
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
    status: Literal["matched", "clarify", "no_match"]
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
    termination_reason: str | None = None
