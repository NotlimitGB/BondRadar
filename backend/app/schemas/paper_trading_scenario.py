from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.paper_trading_report import PaperTradingPerformanceResponse


SCENARIO_REBALANCE_FREQUENCIES = {"label_dates", "weekly", "monthly"}
SCENARIO_STEP_STATUSES = {"completed", "skipped", "failed"}


class PaperTradingScenarioWarning(BaseModel):
    message: str
    as_of_date: date | None = None
    cycle_index: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PaperTradingScenarioCycleResult(BaseModel):
    cycle_index: int
    as_of_date: date
    mark_snapshot_date: date | None
    rebalance_status: str
    mark_status: str
    portfolio_value_before: Decimal | None
    portfolio_value_after_rebalance: Decimal | None
    portfolio_value_after_mark: Decimal | None
    selected_positions_count: int
    turnover: Decimal | None
    fee_amount: Decimal | None
    warnings: list[PaperTradingScenarioWarning]


class PaperTradingScenarioSummary(BaseModel):
    initial_capital: Decimal
    final_value: Decimal
    cumulative_return: Decimal
    max_drawdown: Decimal | None
    total_fee_amount: Decimal | None
    snapshot_count: int
    transaction_count: int
    active_positions_count: int
    last_cycle_as_of_date: date | None


class PaperTradingScenarioRunRequest(BaseModel):
    portfolio_id: int | None = None
    name: str | None = None
    description: str | None = None
    initial_capital: Decimal = Decimal("50000")
    base_currency: str = "RUB"
    model_run_id: int
    date_from: date | None = None
    date_to: date | None = None
    rebalance_frequency: str = "label_dates"
    rebalance_gap_days: int | None = None
    max_cycles: int = 100
    top_n: int = 10
    min_probability_positive: Decimal = Decimal("0.55")
    max_position_weight: Decimal = Decimal("0.20")
    max_issuer_weight: Decimal = Decimal("0.30")
    max_high_risk_weight: Decimal = Decimal("0.20")
    min_liquidity_score: int | None = None
    exclude_blocked_by_risk: bool = True
    exclude_insufficient_credit_data: bool = True
    allowed_risk_levels: list[str] | None = None
    allowed_decision_statuses: list[str] | None = None
    transaction_cost_rate: Decimal = Decimal("0.001")
    allow_partial_marking: bool = True
    stop_on_rebalance_error: bool = False
    stop_on_mark_error: bool = False
    include_performance_report: bool = True
    include_cycle_details: bool = True


class PaperTradingScenarioRunResponse(BaseModel):
    portfolio_id: int
    model_run_id: int
    return_method: str
    horizon_days: int
    date_from: date | None
    date_to: date | None
    cycles_requested: int
    cycles_completed: int
    rebalance_success_count: int
    mark_success_count: int
    rebalance_failed_count: int
    mark_failed_count: int
    final_portfolio_value: Decimal
    final_cash_balance: Decimal
    summary: PaperTradingScenarioSummary
    cycles: list[PaperTradingScenarioCycleResult]
    performance_report: PaperTradingPerformanceResponse | None
    warnings: list[PaperTradingScenarioWarning]
