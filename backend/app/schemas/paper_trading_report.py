from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class PaperTradingReportWarning(BaseModel):
    message: str
    as_of_date: date | None = None
    bond_id: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PaperTradingEquityPoint(BaseModel):
    as_of_date: date
    portfolio_value: Decimal
    cash_balance: Decimal
    allocated_value: Decimal
    allocated_weight: Decimal
    unallocated_weight: Decimal
    cumulative_return: Decimal
    period_return: Decimal | None
    drawdown: Decimal
    active_positions_count: int


class PaperTradingPerformanceMetrics(BaseModel):
    snapshot_count: int
    transaction_count: int
    initial_capital: Decimal
    current_value: Decimal
    cash_balance: Decimal
    allocated_value: Decimal
    cumulative_return: Decimal
    annualized_return: Decimal | None
    max_drawdown: Decimal
    volatility: Decimal | None
    average_period_return: Decimal | None
    positive_period_ratio: Decimal | None
    negative_period_count: int
    total_fee_amount: Decimal
    total_period_return_amount: Decimal
    total_allocation_increase_amount: Decimal
    total_allocation_decrease_amount: Decimal
    total_removed_amount: Decimal
    current_allocated_weight: Decimal
    current_unallocated_weight: Decimal
    active_positions_count: int
    inactive_positions_count: int


class PaperTradingPerformanceResponse(BaseModel):
    portfolio_id: int
    name: str
    status: str
    base_currency: str
    model_run_id: int | None
    return_method: str | None
    horizon_days: int | None
    date_from: date | None
    date_to: date | None
    metrics: PaperTradingPerformanceMetrics
    equity_curve: list[PaperTradingEquityPoint]
    warnings: list[PaperTradingReportWarning]


class PaperTradingContributionItem(BaseModel):
    bond_id: int | None
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    period_return_amount: Decimal
    allocation_increase_amount: Decimal
    allocation_decrease_amount: Decimal
    removed_amount: Decimal
    fee_amount: Decimal
    net_amount_delta: Decimal
    transaction_count: int
    current_amount: Decimal | None
    current_weight: Decimal | None
    is_active: bool | None


class PaperTradingContributionsResponse(BaseModel):
    portfolio_id: int
    date_from: date | None
    date_to: date | None
    items: list[PaperTradingContributionItem]
    warnings: list[PaperTradingReportWarning]
