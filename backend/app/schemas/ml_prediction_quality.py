from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class MLPredictionQualityReportRequest(BaseModel):
    model_run_id: int | None = None
    model_run_ids: list[int] | None = None
    date_from: date | None = None
    date_to: date | None = None
    return_method: str | None = None
    horizon_days: int | None = None
    positive_probability_cutoff: Decimal = Decimal("0.50")
    include_run_rows: bool = True
    include_date_rows: bool = True
    include_probability_buckets: bool = True
    include_missing_label_examples: bool = True
    bucket_count: int = 10
    minimum_evaluable_predictions: int = 30
    minimum_positive_labels: int = 5
    minimum_negative_labels: int = 5
    maximum_missing_label_ratio: Decimal = Decimal("0.30")
    limit: int = 100
    offset: int = 0


class MLPredictionQualityWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class MLPredictionQualityOverview(BaseModel):
    prediction_count: int
    evaluable_prediction_count: int
    missing_label_count: int
    positive_label_count: int
    negative_label_count: int
    predicted_positive_count: int
    predicted_negative_count: int
    missing_label_ratio: Decimal | None
    positive_label_ratio: Decimal | None
    predicted_positive_ratio: Decimal | None
    ready_for_strategy_research: bool


class MLPredictionQualityMetricSet(BaseModel):
    accuracy: Decimal | None
    precision: Decimal | None
    recall: Decimal | None
    f1_score: Decimal | None
    true_positive_count: int
    true_negative_count: int
    false_positive_count: int
    false_negative_count: int
    average_probability_positive: Decimal | None
    median_probability_positive: Decimal | None
    average_probability_for_positive_labels: Decimal | None
    average_probability_for_negative_labels: Decimal | None
    probability_separation: Decimal | None
    average_realized_return: Decimal | None
    average_realized_return_for_predicted_positive: Decimal | None
    average_realized_return_for_predicted_negative: Decimal | None


class MLPredictionQualityIssueSummary(BaseModel):
    missing_model_run_count: int
    non_completed_model_run_count: int
    incompatible_model_run_count: int
    missing_label_count: int
    high_missing_label_ratio: int
    low_evaluable_predictions: int
    low_positive_labels: int
    low_negative_labels: int
    zero_predicted_positive_count: int
    zero_predicted_negative_count: int
    weak_probability_separation: int


class MLPredictionQualityRunRow(BaseModel):
    model_run_id: int
    status: str
    horizon_days: int
    return_method: str
    prediction_count: int
    evaluable_prediction_count: int
    missing_label_count: int
    positive_label_count: int
    negative_label_count: int
    predicted_positive_count: int
    predicted_negative_count: int
    missing_label_ratio: Decimal | None
    accuracy: Decimal | None
    precision: Decimal | None
    recall: Decimal | None
    f1_score: Decimal | None
    average_probability_positive: Decimal | None
    probability_separation: Decimal | None
    first_prediction_date: date | None
    last_prediction_date: date | None
    issues: list[str]


class MLPredictionQualityDateRow(BaseModel):
    as_of_date: date
    prediction_count: int
    evaluable_prediction_count: int
    missing_label_count: int
    positive_label_count: int
    negative_label_count: int
    predicted_positive_count: int
    predicted_negative_count: int
    missing_label_ratio: Decimal | None
    accuracy: Decimal | None
    average_probability_positive: Decimal | None
    average_realized_return: Decimal | None
    issues: list[str]


class MLPredictionQualityProbabilityBucket(BaseModel):
    bucket_index: int
    bucket_start: Decimal
    bucket_end: Decimal
    prediction_count: int
    evaluable_prediction_count: int
    positive_label_count: int
    negative_label_count: int
    positive_label_ratio: Decimal | None
    average_realized_return: Decimal | None


class MLPredictionQualityMissingLabelExample(BaseModel):
    model_run_id: int
    bond_id: int
    as_of_date: date
    horizon_days: int
    return_method: str
    probability_positive: Decimal | None
    reason: str


class MLPredictionQualityReportResponse(BaseModel):
    model_run_id: int | None
    model_run_ids: list[int]
    model_run_count: int
    prediction_source_mode: str
    date_from: date | None
    date_to: date | None
    horizon_days: int
    return_method: str
    overview: MLPredictionQualityOverview
    metrics: MLPredictionQualityMetricSet
    issue_summary: MLPredictionQualityIssueSummary
    run_rows: list[MLPredictionQualityRunRow]
    date_rows: list[MLPredictionQualityDateRow]
    probability_buckets: list[MLPredictionQualityProbabilityBucket]
    missing_label_examples: list[MLPredictionQualityMissingLabelExample]
    limit: int
    offset: int
    warnings: list[MLPredictionQualityWarning]
