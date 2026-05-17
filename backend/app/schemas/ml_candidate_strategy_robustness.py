from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.ml_candidate_comparison import MLCandidateComparisonRequest
from app.schemas.strategy_robustness import StrategyRobustnessAnalyzeRequest


class MLCandidateStrategyRobustnessRequest(BaseModel):
    candidate_comparison: MLCandidateComparisonRequest
    strategy_robustness: StrategyRobustnessAnalyzeRequest
    promote_ranking_metric: str | None = None
    promote_ranking_direction: str | None = None
    require_ready_candidate: bool = True
    include_candidate_comparison: bool = True
    include_robustness_subperiod_details: bool | None = None
    include_robustness_candidate_concentration: bool | None = None


class MLCandidateStrategyRobustnessWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class MLCandidateStrategyRobustnessSelectedCandidate(BaseModel):
    name: str
    rank: int | None
    ranking_metric: str
    ranking_direction: str
    ranking_value: Decimal | int | bool | None
    model_run_id: int | None
    model_run_ids: list[int]
    model_run_count: int
    prediction_source_mode: str
    ready_for_strategy_research: bool
    issues: list[str]


class MLCandidateStrategyRobustnessResponse(BaseModel):
    selected_candidate: MLCandidateStrategyRobustnessSelectedCandidate | None
    candidate_comparison: dict[str, Any] | None
    robustness_analysis: dict[str, Any] | None
    warnings: list[MLCandidateStrategyRobustnessWarning]
