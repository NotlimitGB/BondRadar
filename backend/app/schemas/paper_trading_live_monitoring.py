from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


LIVE_MONITORING_HEALTH_STATUSES = {"healthy", "warning", "critical", "unknown"}
LIVE_MONITORING_ALERT_LEVELS = {"info", "warning", "critical"}


class LivePaperMonitoringAlert(BaseModel):
    level: str
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LivePaperScheduleMonitoringSummary(BaseModel):
    id: int
    name: str
    status: str
    next_run_at: datetime
    last_run_at: datetime | None
    last_cycle_run_id: int | None
    run_count: int
    max_runs: int | None
    interval_days: int
    is_due: bool
    is_locked: bool
    lock_expires_at: datetime | None
    health_status: str
    alerts: list[LivePaperMonitoringAlert]


class LivePaperCycleMonitoringSummary(BaseModel):
    id: int
    status: str
    mode: str
    portfolio_id: int | None
    schedule_id: int | None
    client_cycle_key: str | None
    as_of_date: date | None
    scheduled_for: datetime | None
    readiness_status: str | None
    selected_model_run_id: int | None
    started_at: datetime
    finished_at: datetime | None
    warning_count: int
    error_count: int
    summary: dict[str, Any] = Field(default_factory=dict)


class LivePaperPortfolioMonitoringSummary(BaseModel):
    id: int
    name: str
    status: str
    base_currency: str
    initial_capital: Decimal
    current_value: Decimal
    cash_balance: Decimal
    model_run_id: int | None
    return_method: str | None
    horizon_days: int | None
    last_rebalance_as_of_date: date | None
    last_rebalanced_at: datetime | None
    last_marked_at: datetime | None
    active_positions_count: int
    snapshot_count: int
    latest_snapshot_date: date | None
    cumulative_return: Decimal | None
    max_drawdown: Decimal | None
    health_status: str
    alerts: list[LivePaperMonitoringAlert]


class LivePaperMonitoringOverviewResponse(BaseModel):
    health_status: str
    now: datetime

    schedule_count: int
    active_schedule_count: int
    due_schedule_count: int
    locked_schedule_count: int

    portfolio_count: int
    active_portfolio_count: int

    recent_cycle_count: int
    completed_cycle_count: int
    blocked_cycle_count: int
    failed_cycle_count: int
    running_cycle_count: int

    schedules: list[LivePaperScheduleMonitoringSummary]
    portfolios: list[LivePaperPortfolioMonitoringSummary]
    recent_cycles: list[LivePaperCycleMonitoringSummary]

    alerts: list[LivePaperMonitoringAlert]


class LivePaperScheduleMonitoringResponse(BaseModel):
    schedule: LivePaperScheduleMonitoringSummary
    recent_cycles: list[LivePaperCycleMonitoringSummary]
    alerts: list[LivePaperMonitoringAlert]


class LivePaperPortfolioMonitoringResponse(BaseModel):
    portfolio: LivePaperPortfolioMonitoringSummary
    performance: dict[str, Any] | None
    equity_curve: list[dict[str, Any]]
    contributions: dict[str, Any] | None
    positions: list[dict[str, Any]]
    recent_cycles: list[LivePaperCycleMonitoringSummary]
    alerts: list[LivePaperMonitoringAlert]


class LivePaperCycleMonitoringListResponse(BaseModel):
    total_returned: int
    cycles: list[LivePaperCycleMonitoringSummary]
    alerts: list[LivePaperMonitoringAlert]
