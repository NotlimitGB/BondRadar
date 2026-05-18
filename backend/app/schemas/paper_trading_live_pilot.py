from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.paper_trading_live_monitoring import (
    LivePaperMonitoringOverviewResponse,
)
from app.schemas.paper_trading_live_readiness import LivePaperReadinessResponse
from app.schemas.paper_trading_live_schedule import LivePaperScheduleRead


LIVE_PAPER_PILOT_BOOTSTRAP_STATUSES = {"prepared", "scheduled", "blocked"}


class LivePaperPilotBootstrapRequest(BaseModel):
    name: str = "50k live paper pilot"
    description: str | None = None

    model_run_id: int
    return_method: str = "risk_adjusted"
    horizon_days: int = 30

    virtual_initial_capital: Decimal = Decimal("50000")
    planned_duration_days: int = 90

    date_from: date
    date_to: date

    next_run_at: datetime | None = None
    interval_days: int = 1
    max_runs: int | None = None

    create_schedule: bool = True
    dry_run_only: bool = False

    allow_readiness_warning: bool = False
    allow_not_ready: bool = False

    top_n: int = 5
    min_probability_positive: Decimal = Decimal("0.50")

    use_portfolio_constraints: bool = True
    max_position_weight: Decimal = Decimal("0.20")
    max_issuer_weight: Decimal = Decimal("0.30")
    max_high_risk_weight: Decimal = Decimal("0.20")

    transaction_cost_rate: Decimal = Decimal("0.001")

    include_monitoring_overview: bool = True


class LivePaperPilotBootstrapPayloads(BaseModel):
    readiness_request: dict[str, Any]
    cycle_request: dict[str, Any]
    schedule_request: dict[str, Any] | None


class LivePaperPilotBootstrapNextStep(BaseModel):
    label: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    description: str


class LivePaperPilotBootstrapResponse(BaseModel):
    status: str
    created_schedule_id: int | None
    readiness_status: str | None
    selected_model_run_id: int | None

    virtual_initial_capital: Decimal
    planned_duration_days: int
    next_run_at: datetime
    interval_days: int
    max_runs: int | None

    readiness: LivePaperReadinessResponse | None
    schedule: LivePaperScheduleRead | None
    monitoring_overview: LivePaperMonitoringOverviewResponse | None

    payloads: LivePaperPilotBootstrapPayloads
    next_steps: list[LivePaperPilotBootstrapNextStep]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
