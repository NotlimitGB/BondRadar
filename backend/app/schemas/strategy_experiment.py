from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


EXPERIMENT_RANKING_METRICS = {
    "total_return",
    "annualized_return",
    "max_drawdown",
    "volatility",
    "hit_rate",
    "final_portfolio_value",
    "average_unallocated_weight",
}
EXPERIMENT_RANKING_DIRECTIONS = {"asc", "desc"}
EXPERIMENT_RESULT_STATUSES = {"completed", "failed"}


class StrategyExperimentVariantRequest(BaseModel):
    name: str | None = None
    top_n: int = 10
    min_probability_positive: Decimal = Decimal("0.55")
    rebalance_frequency: str = "label_dates"
    rebalance_gap_days: int | None = None
    use_portfolio_constraints: bool = True
    max_position_weight: Decimal = Decimal("0.20")
    max_issuer_weight: Decimal = Decimal("0.30")
    max_high_risk_weight: Decimal = Decimal("0.20")
    min_liquidity_score: int | None = None
    exclude_blocked_by_risk: bool = True
    exclude_insufficient_credit_data: bool = False
    allowed_risk_levels: list[str] | None = None
    allowed_decision_statuses: list[str] | None = None


class StrategyExperimentCompareRequest(BaseModel):
    model_run_id: int
    date_from: date | None = None
    date_to: date | None = None
    initial_capital: Decimal = Decimal("50000")
    transaction_cost_rate: Decimal = Decimal("0.001")
    variants: list[StrategyExperimentVariantRequest]
    ranking_metric: str = "total_return"
    ranking_direction: str = "desc"
    include_periods: bool = False
    include_baselines: bool = True
    max_variants: int = 50


class StrategyExperimentWarning(BaseModel):
    message: str
    variant_index: int | None = None
    variant_name: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class StrategyExperimentLeaderboardItem(BaseModel):
    rank: int
    variant_name: str
    variant_index: int
    status: str
    ranking_value: Decimal | None
    final_portfolio_value: Decimal | None
    total_return: Decimal | None
    annualized_return: Decimal | None
    max_drawdown: Decimal | None
    volatility: Decimal | None
    hit_rate: Decimal | None
    average_unallocated_weight: Decimal | None
    negative_periods_count: int | None
    selected_period_count: int | None


class StrategyExperimentVariantResult(BaseModel):
    variant_index: int
    variant_name: str
    status: str
    request: dict[str, Any]
    metrics: dict[str, Any] | None
    final_portfolio_value: Decimal | None
    period_count: int | None
    baseline_summaries: list[dict[str, Any]]
    periods: list[dict[str, Any]]
    warnings: list[StrategyExperimentWarning]
    error: str | None


class StrategyExperimentCompareResponse(BaseModel):
    model_run_id: int
    return_method: str
    horizon_days: int
    date_from: date | None
    date_to: date | None
    initial_capital: Decimal
    transaction_cost_rate: Decimal
    ranking_metric: str
    ranking_direction: str
    variant_count: int
    successful_variant_count: int
    failed_variant_count: int
    leaderboard: list[StrategyExperimentLeaderboardItem]
    results: list[StrategyExperimentVariantResult]
    warnings: list[StrategyExperimentWarning]
