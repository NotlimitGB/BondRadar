from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessRequest,
    MLCandidateStrategyRobustnessSelectedCandidate,
)


LIVE_PAPER_READINESS_STATUSES = {"ready", "warning", "not_ready"}
LIVE_PAPER_GATE_STATUSES = {"passed", "warning", "failed"}


class LivePaperReadinessRequest(BaseModel):
    candidate_strategy_robustness: MLCandidateStrategyRobustnessRequest

    virtual_initial_capital: Decimal = Decimal("50000")
    planned_duration_days: int = 90

    include_candidate_comparison: bool | None = None
    include_robustness_analysis: bool = True

    minimum_analyzed_variant_count: int = 1
    minimum_completed_subperiods: int = 2
    allow_warning_flags: bool = True
    maximum_warning_flag_count: int | None = None


class LivePaperReadinessGate(BaseModel):
    code: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LivePaperReadinessWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LivePaperReadinessResponse(BaseModel):
    readiness_status: str
    virtual_initial_capital: Decimal
    planned_duration_days: int

    selected_candidate: MLCandidateStrategyRobustnessSelectedCandidate | None
    candidate_comparison: dict[str, Any] | None
    robustness_analysis: dict[str, Any] | None

    gates: list[LivePaperReadinessGate]
    warnings: list[LivePaperReadinessWarning]
