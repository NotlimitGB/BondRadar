from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class CompanyCreditHealthCalculateRequest(BaseModel):
    as_of_date: date | None = None


class BondRiskAssessmentCalculateRequest(BaseModel):
    as_of_date: date | None = None
    recalculate_company_health: bool = True


class RiskGateResult(BaseModel):
    gate: str
    result: str
    reason: str | None = None


class RiskAssessmentSummary(BaseModel):
    summary: str
    decision_reason: str


class CompanyCreditHealthRead(BaseModel):
    id: int
    company_id: int
    as_of_date: date
    financial_report_id: int | None
    company_score_id: int | None
    credit_health_score: int
    credit_status: str
    risk_level: str
    data_quality_level: str
    debt_to_ebitda: Decimal | None
    interest_coverage: Decimal | None
    cash_to_short_term_debt: Decimal | None
    ocf_to_total_debt: Decimal | None
    debt_to_equity: Decimal | None
    net_profit_margin: Decimal | None
    revenue: Decimal | None
    ebitda: Decimal | None
    net_debt: Decimal | None
    total_debt: Decimal | None
    cash: Decimal | None
    equity: Decimal | None
    short_term_debt: Decimal | None
    operating_cash_flow: Decimal | None
    net_profit: Decimal | None
    interest_expense: Decimal | None
    risk_factors: list[str]
    positive_factors: list[str]
    missing_data: list[str]
    explanation: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BondRiskAssessmentRead(BaseModel):
    id: int
    bond_id: int
    company_id: int
    as_of_date: date
    company_credit_health_id: int | None
    bond_score_id: int | None
    market_snapshot_id: int | None
    assessment_score: int
    decision_status: str
    risk_level: str
    required_risk_premium: Decimal
    yield_to_maturity: Decimal | None
    coupon_rate: Decimal | None
    duration_years: Decimal | None
    liquidity_score: int | None
    volume: Decimal | None
    company_credit_status: str | None
    company_credit_health_score: int | None
    company_score: Decimal | None
    bond_score: Decimal | None
    gates: dict[str, str]
    warnings: list[str]
    blocking_reasons: list[str]
    positive_factors: list[str]
    negative_factors: list[str]
    missing_data: list[str]
    explanation: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CreditRiskErrorItem(BaseModel):
    entity_type: str
    entity_id: int | None
    message: str


class RecalculateCreditRiskResult(BaseModel):
    total: int
    calculated: int
    failed: int
    errors: list[CreditRiskErrorItem]
