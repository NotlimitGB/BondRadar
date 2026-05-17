from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class MLCandidateComparisonCandidateRequest(BaseModel):
    name: str | None = None
    model_run_id: int | None = None
    model_run_ids: list[int] | None = None
    date_from: date | None = None
    date_to: date | None = None
    return_method: str | None = None
    horizon_days: int | None = None


class MLCandidateComparisonRequest(BaseModel):
    candidates: list[MLCandidateComparisonCandidateRequest]
    date_from: date | None = None
    date_to: date | None = None
    return_method: str | None = None
    horizon_days: int | None = None
    positive_probability_cutoff: Decimal = Decimal("0.50")
    ranking_metric: str = "probability_separation"
    ranking_direction: str = "desc"
    include_prediction_quality: bool = False
    include_failed_candidates: bool = True
    bucket_count: int = 10
    minimum_evaluable_predictions: int = 30
    minimum_positive_labels: int = 5
    minimum_negative_labels: int = 5
    maximum_missing_label_ratio: Decimal = Decimal("0.30")
    max_candidates: int = 20
    limit: int = 100
    offset: int = 0


class MLCandidateComparisonWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class MLCandidateComparisonCandidateResult(BaseModel):
    name: str
    status: str
    model_run_id: int | None
    model_run_ids: list[int]
    model_run_count: int
    prediction_source_mode: str | None
    date_from: date | None
    date_to: date | None
    horizon_days: int | None
    return_method: str | None
    ranking_value: Decimal | int | bool | None
    prediction_count: int | None
    evaluable_prediction_count: int | None
    missing_label_count: int | None
    positive_label_count: int | None
    negative_label_count: int | None
    ready_for_strategy_research: bool | None
    accuracy: Decimal | None
    precision: Decimal | None
    recall: Decimal | None
    f1_score: Decimal | None
    probability_separation: Decimal | None
    average_realized_return: Decimal | None
    average_realized_return_for_predicted_positive: Decimal | None
    missing_label_ratio: Decimal | None
    issues: list[str]
    warnings: list[str]
    error: str | None
    prediction_quality: dict[str, Any] | None


class MLCandidateComparisonLeaderboardItem(BaseModel):
    rank: int
    name: str
    status: str
    ranking_value: Decimal | int | bool | None
    model_run_id: int | None
    model_run_ids: list[int]
    prediction_source_mode: str | None
    ready_for_strategy_research: bool | None
    issues: list[str]
    error: str | None


class MLCandidateComparisonSelectedCandidate(BaseModel):
    name: str
    rank: int
    ranking_metric: str
    ranking_value: Decimal | int | bool | None
    model_run_id: int | None
    model_run_ids: list[int]
    prediction_source_mode: str
    ready_for_strategy_research: bool
    issues: list[str]


class MLCandidateComparisonResponse(BaseModel):
    ranking_metric: str
    ranking_direction: str
    candidate_count: int
    completed_candidate_count: int
    failed_candidate_count: int
    selected_candidate: MLCandidateComparisonSelectedCandidate | None
    leaderboard: list[MLCandidateComparisonLeaderboardItem]
    candidates: list[MLCandidateComparisonCandidateResult]
    limit: int
    offset: int
    warnings: list[MLCandidateComparisonWarning]
