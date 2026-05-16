from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


BACKTEST_REBALANCE_FREQUENCIES = {"label_dates", "monthly", "weekly"}


class StrategyBacktestRequest(BaseModel):
    model_run_id: int | None = None
    model_run_ids: list[int] | None = None
    date_from: date | None = None
    date_to: date | None = None
    initial_capital: Decimal = Decimal("50000")
    top_n: int = 5
    min_probability_positive: Decimal = Decimal("0.50")
    rebalance_frequency: str = "label_dates"
    rebalance_gap_days: int | None = None
    max_position_weight: Decimal = Decimal("0.25")
    transaction_cost_rate: Decimal = Decimal("0.001")
    min_liquidity_score: int | None = None
    exclude_blocked_by_risk: bool = True
    exclude_insufficient_credit_data: bool = False
    use_portfolio_constraints: bool = True
    max_issuer_weight: Decimal = Decimal("0.30")
    max_high_risk_weight: Decimal = Decimal("0.20")
    allowed_risk_levels: list[str] | None = None
    allowed_decision_statuses: list[str] | None = None
    include_excluded_candidates: bool = False
    include_baselines: bool = True


class StrategyBacktestWarning(BaseModel):
    message: str
    as_of_date: date | None = None
    bond_id: int | None = None
    details: dict[str, Any] = {}


class StrategyBacktestMetricSet(BaseModel):
    period_count: int
    selected_period_count: int
    total_return: Decimal
    annualized_return: Decimal | None
    max_drawdown: Decimal
    volatility: Decimal | None
    hit_rate: Decimal | None
    average_period_return: Decimal | None
    negative_periods_count: int
    turnover: Decimal
    average_selected_candidates: Decimal | None
    average_allocated_weight: Decimal | None
    average_unallocated_weight: Decimal | None
    average_high_risk_weight: Decimal | None
    average_max_issuer_weight: Decimal | None


class StrategyBacktestSelectedCandidate(BaseModel):
    bond_id: int
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    as_of_date: date
    probability_positive: Decimal
    predicted_label: str
    realized_label: str | None
    realized_return: Decimal | None
    weight: Decimal
    allocation_amount: Decimal | None
    yield_to_maturity: Decimal | None
    duration_years: Decimal | None
    liquidity_score: int | None
    volume: Decimal | None
    decision_status: str | None
    risk_level: str | None
    assessment_score: int | None
    required_risk_premium: Decimal | None
    selection_reasons: list[str]
    risk_notes: list[str]


class StrategyBacktestExcludedCandidate(BaseModel):
    bond_id: int
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    as_of_date: date
    probability_positive: Decimal
    predicted_label: str
    yield_to_maturity: Decimal | None
    liquidity_score: int | None
    decision_status: str | None
    risk_level: str | None
    exclusion_reasons: list[str]


class StrategyBacktestPeriodResult(BaseModel):
    as_of_date: date
    portfolio_value_start: Decimal
    portfolio_value_end: Decimal
    period_return: Decimal
    gross_period_return: Decimal
    estimated_costs_return: Decimal
    allocated_weight: Decimal
    unallocated_weight: Decimal
    allocated_capital: Decimal
    unallocated_capital: Decimal
    high_risk_weight: Decimal
    max_issuer_weight: Decimal
    excluded_candidates_count: int
    constraints: list[dict[str, Any]]
    selected_candidates_count: int
    selected_candidates: list[StrategyBacktestSelectedCandidate]
    excluded_candidates: list[StrategyBacktestExcludedCandidate] = []


class StrategyBacktestBaselineResult(BaseModel):
    name: str
    final_portfolio_value: Decimal
    metrics: StrategyBacktestMetricSet
    warnings: list[StrategyBacktestWarning]


class StrategyBacktestResponse(BaseModel):
    model_run_id: int | None
    model_run_ids: list[int]
    model_run_count: int
    prediction_source_mode: str
    return_method: str
    horizon_days: int
    date_from: date | None
    date_to: date | None
    initial_capital: Decimal
    final_portfolio_value: Decimal
    metrics: StrategyBacktestMetricSet
    periods: list[StrategyBacktestPeriodResult]
    baselines: list[StrategyBacktestBaselineResult]
    warnings: list[StrategyBacktestWarning]
