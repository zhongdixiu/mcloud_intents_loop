from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field


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


class SkillRouteDecision(BaseModel):
    status: Literal["route", "clarify", "no_match"]
    skill_id: str | None = None
    confidence: float = 0.0
    reason: str = ""
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class IntentDecision(BaseModel):
    status: Literal["matched", "clarify", "no_match"]
    intent: str | None = None
    code: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    reason: str = ""
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class EvaluationDecision(BaseModel):
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


class ContextualizedRequest(BaseModel):
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


class LoopExhaustedClarification(BaseModel):
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
