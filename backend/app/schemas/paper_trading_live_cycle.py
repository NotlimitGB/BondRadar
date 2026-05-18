from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.paper_trading import (
    PaperPortfolioMarkPeriodRequest,
    PaperPortfolioMarkPeriodResult,
    PaperPortfolioRead,
    PaperPortfolioRebalanceRequest,
    PaperPortfolioRebalanceResult,
)
from app.schemas.paper_trading_live_readiness import (
    LivePaperReadinessRequest,
    LivePaperReadinessResponse,
)


class LivePaperCycleRunRequest(BaseModel):
    readiness: LivePaperReadinessRequest

    portfolio_id: int | None = None
    create_portfolio_if_missing: bool = True
    portfolio_name: str | None = None
    portfolio_description: str | None = None

    as_of_date: date | None = None
    client_cycle_key: str | None = None

    allow_readiness_warning: bool = False
    allow_not_ready: bool = False

    mark_period_before_rebalance: bool = False
    mark_period: PaperPortfolioMarkPeriodRequest | None = None

    rebalance: PaperPortfolioRebalanceRequest = Field(
        default_factory=PaperPortfolioRebalanceRequest
    )

    include_readiness_report: bool = True
    include_rebalance_result: bool = True
    include_mark_period_result: bool = True


class LivePaperCycleRunRead(BaseModel):
    id: int
    status: str
    mode: str
    portfolio_id: int | None
    client_cycle_key: str | None
    as_of_date: date | None

    readiness_status: str | None
    selected_model_run_id: int | None
    selected_model_run_ids_json: list[int] | None

    request_json: dict[str, Any]
    readiness_json: dict[str, Any]
    mark_period_result_json: dict[str, Any] | None
    rebalance_result_json: dict[str, Any] | None
    summary_json: dict[str, Any]
    warnings_json: list[dict[str, Any]]
    errors_json: list[dict[str, Any]]

    started_at: datetime
    finished_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LivePaperCycleRunResponse(BaseModel):
    cycle: LivePaperCycleRunRead
    readiness: LivePaperReadinessResponse | None
    portfolio: PaperPortfolioRead | None
    mark_period_result: PaperPortfolioMarkPeriodResult | None
    rebalance_result: PaperPortfolioRebalanceResult | None
    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
