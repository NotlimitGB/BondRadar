from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


PORTFOLIO_RISK_LEVELS = {"low", "medium", "high", "critical", "unknown"}
PORTFOLIO_DECISION_STATUSES = {
    "eligible_for_analysis",
    "watchlist",
    "blocked_by_risk",
    "insufficient_data",
}
PORTFOLIO_CONSTRAINT_STATUSES = {"pass", "warning", "fail"}


class PortfolioConstructionRequest(BaseModel):
    model_run_id: int
    as_of_date: date | None = None
    capital: Decimal = Decimal("50000")
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
    include_excluded_candidates: bool = True


class PortfolioConstructionWarning(BaseModel):
    message: str
    as_of_date: date | None = None
    bond_id: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class PortfolioConstraintReport(BaseModel):
    name: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PortfolioCandidate(BaseModel):
    bond_id: int
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    as_of_date: date
    probability_positive: Decimal
    predicted_label: str
    allocation_weight: Decimal
    allocation_amount: Decimal
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


class ExcludedPortfolioCandidate(BaseModel):
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


class PortfolioConstructionSummary(BaseModel):
    candidate_count: int
    selected_count: int
    excluded_count: int
    allocated_weight: Decimal
    unallocated_weight: Decimal
    allocated_capital: Decimal
    unallocated_capital: Decimal
    average_probability_positive: Decimal | None
    weighted_probability_positive: Decimal | None
    average_yield_to_maturity: Decimal | None
    weighted_yield_to_maturity: Decimal | None
    max_issuer_weight: Decimal
    high_risk_weight: Decimal
    exclusion_reason_counts: dict[str, int] = Field(default_factory=dict)


class PortfolioConstructionResponse(BaseModel):
    model_run_id: int
    as_of_date: date
    return_method: str
    horizon_days: int
    capital: Decimal
    summary: PortfolioConstructionSummary
    selected_candidates: list[PortfolioCandidate]
    excluded_candidates: list[ExcludedPortfolioCandidate]
    constraints: list[PortfolioConstraintReport]
    warnings: list[PortfolioConstructionWarning]
