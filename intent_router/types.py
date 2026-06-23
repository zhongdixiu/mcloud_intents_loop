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
    candidate_skill_ids: list[str] = Field(default_factory=list)
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
    confidence: float = 0.0
    reason: str = ""
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)


class SkillRef(BaseModel):
    id: str
    name: str


class RouteResult(BaseModel):
    status: Literal["matched", "clarify", "no_match"]
    skill: SkillRef | None = None
    intent: str | None = None
    code: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.0
    question: str | None = None
    options: list[dict[str, str]] = Field(default_factory=list)
    resume_token: str | None = None
    reason: str | None = None
    visited_skills: list[str] = Field(default_factory=list)
