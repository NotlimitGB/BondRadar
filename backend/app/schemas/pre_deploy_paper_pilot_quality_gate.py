from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


PRE_DEPLOY_QUALITY_GATE_STATUSES = {
    "ready_for_deploy",
    "warning",
    "blocked",
}
PRE_DEPLOY_GATE_STATUSES = {
    "passed",
    "warning",
    "failed",
    "skipped",
}


class PreDeployPaperPilotQualityGateRequest(BaseModel):
    recent_days: int = 7
    minimum_corporate_bonds: int = 20
    minimum_bonds_with_recent_market_snapshot: int = 20
    minimum_bonds_with_recent_features: int = 20
    minimum_bonds_with_predictions: int = 20
    include_ofz: bool = False

    model_run_id: int
    return_method: str = "risk_adjusted"
    horizon_days: int = 30
    date_from: date
    date_to: date

    positive_probability_cutoff: Decimal = Decimal("0.50")
    ranking_metric: str = "probability_separation"
    ranking_direction: str = "desc"

    top_n: int = 5
    min_probability_positive: Decimal = Decimal("0.50")
    initial_capital: Decimal = Decimal("50000")
    transaction_cost_rate: Decimal = Decimal("0.001")

    virtual_initial_capital: Decimal = Decimal("50000")
    planned_duration_days: int = 90
    next_run_at: datetime | None = None
    interval_days: int = 1
    max_runs: int | None = None

    allow_data_warning: bool = False
    allow_robustness_warning: bool = False
    allow_live_paper_warning: bool = False

    minimum_analyzed_variant_count: int = 1
    minimum_completed_subperiods: int = 2
    maximum_warning_flag_count: int | None = None

    include_detailed_payloads: bool = True
    include_scheduler_dry_run: bool = True


class PreDeployQualityGateItem(BaseModel):
    code: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PreDeployQualityGateCommand(BaseModel):
    label: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    description: str


class PreDeployPaperPilotQualityGateResponse(BaseModel):
    status: str
    as_of: datetime

    ready_for_vds_deploy: bool
    ready_for_50k_paper_pilot: bool

    model_run_id: int
    return_method: str
    horizon_days: int
    date_from: date
    date_to: date

    gates: list[PreDeployQualityGateItem]
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    next_steps: list[str]

    corporate_universe_action_plan: dict[str, Any] | None
    live_data_readiness: dict[str, Any] | None
    strategy_robustness: dict[str, Any] | None
    live_paper_readiness: dict[str, Any] | None
    pilot_bootstrap_dry_run: dict[str, Any] | None
    scheduler_dry_run: dict[str, Any] | None

    commands: list[PreDeployQualityGateCommand]
    payloads: dict[str, Any]
