from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class MLTrainRequest(BaseModel):
    horizon_days: int = 30
    return_method: str = "price"
    include_credit_risk_features: bool = True
    as_of_date_from: date | None = None
    as_of_date_to: date | None = None
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    model_type: str = "logistic_regression"
    test_size: float = 0.2
    min_rows: int = 30
    random_state: int = 42
    max_rows: int | None = None


class MLTrainResult(BaseModel):
    run_id: int
    status: str
    model_type: str
    horizon_days: int
    train_rows: int
    test_rows: int
    positive_rows: int
    negative_rows: int
    metrics: dict[str, Any]
    feature_importance: list[dict[str, Any]]
    artifact_path: str | None
    started_at: datetime
    finished_at: datetime | None


class MLModelRunRead(BaseModel):
    id: int
    status: str
    model_type: str
    horizon_days: int
    features: list[str]
    target: str
    as_of_date_from: date | None
    as_of_date_to: date | None
    train_rows: int
    test_rows: int
    positive_rows: int
    negative_rows: int
    metrics: dict[str, Any]
    feature_importance: list[dict[str, Any]]
    params: dict[str, Any]
    artifact_path: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MLPredictionRequest(BaseModel):
    model_run_id: int
    bond_id: int | None = None
    company_id: int | None = None
    as_of_date_from: date | None = None
    as_of_date_to: date | None = None
    limit: int = 100
    offset: int = 0
    save_predictions: bool = True


class MLPredictionRead(BaseModel):
    id: int | None
    model_run_id: int
    feature_snapshot_id: int
    bond_id: int
    company_id: int
    as_of_date: date
    horizon_days: int
    probability_positive: Decimal
    predicted_label: str
    features: dict[str, Any]
    created_at: datetime | None

    model_config = {"from_attributes": True}


class MLPredictionResponse(BaseModel):
    model_run_id: int | None
    total: int
    limit: int
    offset: int
    predictions: list[MLPredictionRead]
