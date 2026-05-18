from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field


LIVE_DATA_READINESS_STATUSES = {"ready", "warning", "not_ready"}
LIVE_DATA_READINESS_CHECK_STATUSES = {"passed", "warning", "failed"}


class LiveDataReadinessCheck(BaseModel):
    name: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LiveDataReadinessWarning(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LiveDataReadinessResponse(BaseModel):
    status: str
    as_of: datetime

    corporate_bond_count: int
    ofz_bond_count: int
    total_bond_count: int
    working_bond_count: int
    company_count: int

    latest_market_snapshot_date: date | None
    market_snapshot_count: int
    bonds_with_recent_market_snapshot_count: int

    latest_cashflow_date: date | None
    cashflow_event_count: int
    bonds_with_cashflows_count: int

    latest_feature_snapshot_date: date | None
    feature_snapshot_count: int
    bonds_with_recent_features_count: int

    latest_completed_model_run_id: int | None
    latest_completed_model_run_created_at: datetime | None
    prediction_count_for_latest_run: int
    bonds_with_predictions_for_latest_run_count: int
    latest_prediction_date: date | None

    checks: list[LiveDataReadinessCheck]
    warnings: list[LiveDataReadinessWarning]
    next_steps: list[str]
