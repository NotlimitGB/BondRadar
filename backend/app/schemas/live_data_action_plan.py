from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.live_data_readiness import LiveDataReadinessResponse


LIVE_DATA_ACTION_PLAN_STATUSES = {"ready_to_run", "needs_attention", "blocked"}
LIVE_DATA_ACTION_STATUSES = {"recommended", "optional", "blocked", "not_needed"}


class LiveDataActionPlanStep(BaseModel):
    name: str
    status: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class LiveDataActionPlanCommand(BaseModel):
    label: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    description: str


class LiveDataPipelinePayloadPreview(BaseModel):
    mode: str
    date_from: date
    date_to: date
    horizon_days: int
    steps: list[str]
    return_methods: list[str]
    rebuild_existing: bool
    moex_board: str
    run_ml: bool
    run_predictions: bool
    run_evaluation: bool
    ml_return_method: str
    allow_readiness_warning: bool
    fail_on_not_ready: bool
    transaction_cost_rate: Decimal


class LiveDataActionPlanResponse(BaseModel):
    status: str
    as_of: datetime

    readiness_status: str
    readiness: LiveDataReadinessResponse

    date_from: date
    date_to: date
    horizon_days: int
    include_ofz: bool

    recommended_steps: list[str]
    blocked_steps: list[str]
    optional_steps: list[str]

    actions: list[LiveDataActionPlanStep]
    commands: list[LiveDataActionPlanCommand]

    pipeline_payload: dict[str, Any]
    curl_example: str

    can_run_pipeline: bool
    can_run_ml_training: bool
    can_generate_predictions: bool
    can_bootstrap_paper_pilot: bool

    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    next_steps: list[str]
