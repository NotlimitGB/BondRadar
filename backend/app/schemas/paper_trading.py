from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PaperTradingWarning(BaseModel):
    message: str
    bond_id: int | None = None
    as_of_date: date | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PaperPortfolioCreate(BaseModel):
    name: str
    description: str | None = None
    initial_capital: Decimal
    base_currency: str = "RUB"
    model_run_id: int | None = None


class PaperPortfolioRead(BaseModel):
    id: int
    name: str
    description: str | None
    status: str
    base_currency: str
    initial_capital: Decimal
    cash_balance: Decimal
    current_value: Decimal
    model_run_id: int | None
    return_method: str | None
    horizon_days: int | None
    params_json: dict[str, Any]
    summary_json: dict[str, Any]
    warnings_json: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    last_rebalanced_at: datetime | None
    last_rebalance_as_of_date: date | None
    last_marked_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class PaperPortfolioPositionRead(BaseModel):
    id: int
    portfolio_id: int
    bond_id: int
    company_id: int | None
    as_of_date: date
    allocation_weight: Decimal
    allocation_amount: Decimal
    current_amount: Decimal
    probability_positive: Decimal | None
    predicted_label: str | None
    yield_to_maturity: Decimal | None
    liquidity_score: int | None
    decision_status: str | None
    risk_level: str | None
    is_active: bool
    source_model_run_id: int | None
    source_prediction_id: int | None
    source_details_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaperPortfolioTransactionRead(BaseModel):
    id: int
    portfolio_id: int
    bond_id: int | None
    transaction_type: str
    as_of_date: date
    amount_delta: Decimal
    weight_delta: Decimal | None
    fee_amount: Decimal | None
    portfolio_value_before: Decimal | None
    portfolio_value_after: Decimal | None
    details_json: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaperPortfolioSnapshotRead(BaseModel):
    id: int
    portfolio_id: int
    as_of_date: date
    portfolio_value: Decimal
    cash_balance: Decimal
    allocated_value: Decimal
    allocated_weight: Decimal
    unallocated_weight: Decimal
    positions_count: int
    active_positions_count: int
    cumulative_return: Decimal
    period_return: Decimal | None
    metrics_json: dict[str, Any]
    warnings_json: list[dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaperPortfolioRebalanceRequest(BaseModel):
    model_run_id: int | None = None
    as_of_date: date | None = None
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
    risk_override_enabled: bool = False
    risk_override_reason: str | None = None
    transaction_cost_rate: Decimal = Decimal("0.001")
    include_excluded_candidates: bool = True


class PaperPortfolioRebalanceResult(BaseModel):
    portfolio: PaperPortfolioRead
    snapshot: PaperPortfolioSnapshotRead
    selected_positions: list[PaperPortfolioPositionRead]
    excluded_candidates: list[dict[str, Any]]
    turnover: Decimal
    fee_amount: Decimal
    construction_summary: dict[str, Any]
    warnings: list[PaperTradingWarning]


class PaperPortfolioMarkPeriodRequest(BaseModel):
    as_of_date: date | None = None
    allow_partial: bool = True


class PaperPortfolioMarkPeriodResult(BaseModel):
    portfolio: PaperPortfolioRead
    snapshot: PaperPortfolioSnapshotRead
    updated_positions: list[PaperPortfolioPositionRead]
    transactions: list[PaperPortfolioTransactionRead]
    warnings: list[PaperTradingWarning]
