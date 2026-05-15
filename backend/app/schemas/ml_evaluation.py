from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class MLPredictionEvaluationRow(BaseModel):
    prediction_id: int
    model_run_id: int
    feature_snapshot_id: int
    bond_id: int
    company_id: int
    as_of_date: date
    horizon_days: int
    return_method: str
    probability_positive: Decimal
    predicted_label: str
    actual_label: str | None
    actual_label_binary: int | None
    future_return: Decimal | None
    price_return: Decimal | None
    net_total_return: Decimal | None
    risk_adjusted_excess_return: Decimal | None
    required_risk_premium: Decimal | None
    is_correct: bool | None
    is_evaluable: bool
    created_at: datetime


class MLPredictionEvaluationRowsResponse(BaseModel):
    model_run_id: int
    total: int
    limit: int
    offset: int
    rows: list[MLPredictionEvaluationRow]


class MLClassificationMetrics(BaseModel):
    evaluable_count: int
    accuracy: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    roc_auc: float | None
    confusion_matrix: dict[str, int]


class MLProbabilityBucket(BaseModel):
    bucket_from: float
    bucket_to: float
    predictions_count: int
    evaluable_count: int
    positive_count: int
    negative_count: int
    insufficient_count: int
    missing_label_count: int
    actual_positive_rate: float | None
    avg_probability_positive: float | None
    avg_future_return: Decimal | None
    avg_price_return: Decimal | None
    avg_net_total_return: Decimal | None
    avg_risk_adjusted_excess_return: Decimal | None


class MLCalibrationReport(BaseModel):
    bucket_size: float
    buckets: list[MLProbabilityBucket]
    brier_score: float | None
    average_probability: float | None
    actual_positive_rate: float | None


class MLRunEvaluationReport(BaseModel):
    model_run_id: int
    model_type: str
    status: str
    horizon_days: int
    return_method: str
    features: list[str]
    params: dict[str, Any]
    training_metrics: dict[str, Any]
    evaluation_metrics: MLClassificationMetrics
    calibration: MLCalibrationReport
    feature_importance: list[dict[str, Any]]
    coverage: dict[str, Any]
    warnings: list[str]


class MLModelComparisonItem(BaseModel):
    model_run_id: int
    model_type: str
    horizon_days: int
    return_method: str
    features_count: int
    train_rows: int
    test_rows: int
    prediction_count: int
    evaluable_count: int
    accuracy: float | None
    precision: float | None
    recall: float | None
    f1: float | None
    roc_auc: float | None
    brier_score: float | None
    created_at: datetime


class MLModelComparisonResponse(BaseModel):
    total: int
    rows: list[MLModelComparisonItem]
