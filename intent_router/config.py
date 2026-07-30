from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CandidateConfig(BaseModel):
    max_skill_candidates: int = Field(default=5, ge=1)
    default_skill_candidates: int = Field(default=3, ge=1)
    high_confidence_skill_candidates: int = Field(default=2, ge=1)
    high_confidence_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    high_margin_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    low_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    low_margin_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    max_intent_candidates_per_skill: int = Field(default=2, ge=1)
    max_expansion_rounds: int = Field(default=1, ge=0)

    @model_validator(mode="after")
    def validate_candidate_counts(self) -> "CandidateConfig":
        if self.default_skill_candidates > self.max_skill_candidates:
            raise ValueError(
                "default_skill_candidates must not exceed max_skill_candidates",
            )
        if self.high_confidence_skill_candidates > self.max_skill_candidates:
            raise ValueError(
                "high_confidence_skill_candidates must not exceed "
                "max_skill_candidates",
            )
        return self


class DecisionConfig(BaseModel):
    direct_select_min_score: float = Field(default=0.82, ge=0.0, le=1.0)
    direct_select_min_margin: float = Field(default=0.20, ge=0.0, le=1.0)
    evaluator_min_confidence: float = Field(default=0.70, ge=0.0, le=1.0)
    evaluator_switch_margin: float = Field(default=0.15, ge=0.0, le=1.0)
    evaluator_mode: Literal["always", "conditional", "disabled"] = "conditional"


class ConcurrencyConfig(BaseModel):
    intent_generation_per_request: int = Field(default=3, ge=1)
    global_model_calls: int = Field(default=20, ge=1)


class TimeoutConfig(BaseModel):
    route_deadline_ms: int = Field(default=8000, ge=1)
    contextualizer_ms: int = Field(default=1500, ge=1)
    skill_selector_ms: int = Field(default=1500, ge=1)
    intent_selector_ms: int = Field(default=2500, ge=1)
    evaluator_ms: int = Field(default=1500, ge=1)
    parameter_extractor_ms: int = Field(default=1500, ge=1)


class RetryConfig(BaseModel):
    max_retries: int = Field(default=2, ge=0)
    base_backoff_ms: int = Field(default=200, ge=0)
    jitter_ratio: float = Field(default=0.25, ge=0.0, le=1.0)


class SessionConfig(BaseModel):
    history_limit: int = Field(default=5, ge=0)


class ConfidenceConfig(BaseModel):
    direct_selector_weight: float = 0.80
    direct_margin_weight: float = 0.20
    evaluated_selector_weight: float = 0.45
    evaluated_confidence_weight: float = 0.45
    evaluated_margin_weight: float = 0.10
    margin_scale: float = Field(default=0.25, gt=0.0)
    non_blocking_risk_penalty: float = 0.05
    context_ambiguity_penalty: float = 0.10
    context_conflict_penalty: float = 0.05
    max_context_conflict_penalty: float = 0.20


class RouterConfig(BaseModel):
    candidates: CandidateConfig = Field(default_factory=CandidateConfig)
    decision: DecisionConfig = Field(default_factory=DecisionConfig)
    concurrency: ConcurrencyConfig = Field(default_factory=ConcurrencyConfig)
    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    confidence: ConfidenceConfig = Field(default_factory=ConfidenceConfig)
