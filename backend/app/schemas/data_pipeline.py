from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


PIPELINE_MODES = {"manual", "scheduled", "demo", "test"}
PIPELINE_STATUSES = {"running", "completed", "completed_with_errors", "failed"}
PIPELINE_STEP_STATUSES = {"pending", "running", "completed", "skipped", "failed"}
PIPELINE_RETURN_METHODS = {"price", "total_return", "risk_adjusted"}
PIPELINE_STEPS = {
    "moex_market_sync",
    "moex_cashflow_sync",
    "credit_health",
    "bond_risk_assessment",
    "dataset_build_price",
    "labels_total_return",
    "labels_risk_adjusted",
    "ml_train",
    "ml_predict",
    "ml_evaluate",
}


class DataPipelineRunRequest(BaseModel):
    mode: str = "manual"
    date_from: date
    date_to: date
    horizon_days: int = 30
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    steps: list[str] | None = None
    return_methods: list[str] = ["price", "total_return", "risk_adjusted"]
    rebuild_existing: bool = False
    moex_board: str = "TQCB"
    run_ml: bool = False
    run_predictions: bool = False
    run_evaluation: bool = False
    ml_return_method: str = "risk_adjusted"
    ml_min_rows: int = 30
    ml_test_size: float = 0.2
    ml_include_credit_risk_features: bool = True
    benchmark_return: Decimal | None = None
    transaction_cost_rate: Decimal = Decimal("0.001")


class DataPipelineStepRunRead(BaseModel):
    id: int
    pipeline_run_id: int
    step_name: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    input_json: dict[str, Any]
    result_json: dict[str, Any]
    errors_json: list[dict[str, Any]]
    warnings_json: list[dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DataPipelineRunRead(BaseModel):
    id: int
    status: str
    mode: str
    date_from: date
    date_to: date
    horizon_days: int
    bond_ids_json: list[int] | None
    company_ids_json: list[int] | None
    return_methods_json: list[str]
    params_json: dict[str, Any]
    summary_json: dict[str, Any]
    errors_json: list[dict[str, Any]]
    warnings_json: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime
    steps: list[DataPipelineStepRunRead] = []

    model_config = ConfigDict(from_attributes=True)


class DataPipelineRunResult(BaseModel):
    run: DataPipelineRunRead
    status: str
    summary: dict[str, Any]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
