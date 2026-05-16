from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class MLWalkForwardRunRequest(BaseModel):
    date_from: date
    date_to: date
    train_window_days: int | None = None
    min_train_date: date | None = None
    test_window_days: int = 30
    step_days: int = 30
    horizon_days: int = 30
    return_method: str = "risk_adjusted"
    model_type: str = "logistic_regression"
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    include_credit_risk_features: bool = True
    min_rows: int = 100
    min_positive_rows: int = 20
    min_negative_rows: int = 20
    test_size: Decimal = Decimal("0.2")
    save_predictions: bool = True
    skip_not_ready_folds: bool = True
    run_readiness_check: bool = True
    readiness_min_rows: int | None = None
    readiness_min_positive_rows: int | None = None
    readiness_min_negative_rows: int | None = None
    readiness_max_insufficient_ratio: Decimal | None = None
    max_folds: int = 50


class MLWalkForwardWarning(BaseModel):
    message: str
    fold_index: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MLWalkForwardFoldResult(BaseModel):
    fold_index: int
    status: str
    train_date_from: date | None
    train_date_to: date
    predict_date_from: date
    predict_date_to: date
    readiness_status: str | None = None
    readiness_evaluable_rows: int | None = None
    readiness_positive_rows: int | None = None
    readiness_negative_rows: int | None = None
    readiness_insufficient_ratio: Decimal | None = None
    model_run_id: int | None = None
    train_rows: int | None = None
    test_rows: int | None = None
    positive_rows: int | None = None
    negative_rows: int | None = None
    prediction_count: int = 0
    saved_prediction_count: int = 0
    metrics: dict[str, Any] | None = None
    warnings: list[MLWalkForwardWarning] = Field(default_factory=list)
    error: str | None = None


class MLWalkForwardSummary(BaseModel):
    model_run_ids: list[int] = Field(default_factory=list)
    total_predictions: int = 0
    total_saved_predictions: int = 0
    average_train_rows: Decimal | None = None
    average_test_rows: Decimal | None = None
    average_positive_rows: Decimal | None = None
    average_negative_rows: Decimal | None = None
    average_accuracy: Decimal | None = None
    average_auc: Decimal | None = None


class MLWalkForwardRunResponse(BaseModel):
    date_from: date
    date_to: date
    return_method: str
    horizon_days: int
    model_type: str
    fold_count: int
    completed_fold_count: int
    skipped_fold_count: int
    failed_fold_count: int
    summary: MLWalkForwardSummary
    folds: list[MLWalkForwardFoldResult]
    warnings: list[MLWalkForwardWarning] = Field(default_factory=list)
