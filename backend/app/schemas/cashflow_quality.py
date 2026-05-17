from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class CashflowQualityAuditRequest(BaseModel):
    date_from: date
    date_to: date
    horizon_days: int = 365
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    secids: list[str] | None = None
    source: str | None = "moex"
    require_future_cashflows: bool = True
    require_coupon_events: bool = False
    require_redemption_or_maturity: bool = False
    max_duplicate_events_per_bond: int = 0
    maximum_days_without_future_event: int = 180
    include_bond_rows: bool = True
    include_event_type_breakdown: bool = True
    include_issue_details: bool = True
    limit: int = 100
    offset: int = 0


class CashflowQualityWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CashflowQualityEventTypeSummary(BaseModel):
    event_type: str
    count: int
    first_event_date: date | None
    last_event_date: date | None
    total_amount: Decimal | None


class CashflowQualityIssueDetail(BaseModel):
    code: str
    message: str
    event_date: date | None = None
    event_type: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class CashflowQualityOverview(BaseModel):
    selected_bond_count: int
    selected_company_count: int
    bonds_with_cashflows: int
    bonds_without_cashflows: int
    total_event_count: int
    future_event_count: int
    ready_bond_count: int
    warning_bond_count: int
    not_ready_bond_count: int
    coupon_event_count: int
    amortization_event_count: int
    offer_event_count: int
    redemption_event_count: int
    other_event_count: int
    average_future_event_count: Decimal | None
    median_future_event_count: Decimal | None


class CashflowQualityIssueSummary(BaseModel):
    missing_secid_count: int
    no_cashflows_count: int
    no_future_cashflows_count: int
    no_coupon_events_count: int
    no_redemption_or_maturity_count: int
    invalid_event_date_count: int
    invalid_event_type_count: int
    missing_amount_count: int
    non_positive_amount_count: int
    currency_mismatch_count: int
    duplicate_event_count: int
    stale_future_schedule_count: int


class CashflowQualityBondRow(BaseModel):
    bond_id: int
    secid: str | None
    isin: str | None
    bond_name: str | None
    company_id: int | None
    company_name: str | None
    company_ticker: str | None
    status: str
    issue_count: int
    issues: list[str]
    event_count: int
    future_event_count: int
    first_event_date: date | None
    last_event_date: date | None
    next_event_date: date | None
    days_to_next_event: int | None
    coupon_event_count: int
    amortization_event_count: int
    offer_event_count: int
    redemption_event_count: int
    other_event_count: int
    missing_amount_count: int
    non_positive_amount_count: int
    currency_mismatch_count: int
    duplicate_event_count: int
    event_type_breakdown: list[CashflowQualityEventTypeSummary]
    issue_details: list[CashflowQualityIssueDetail]


class CashflowQualityAuditResponse(BaseModel):
    date_from: date
    date_to: date
    audit_end_date: date
    horizon_days: int
    source: str | None
    overview: CashflowQualityOverview
    issue_summary: CashflowQualityIssueSummary
    total_bond_rows: int
    limit: int
    offset: int
    bond_rows: list[CashflowQualityBondRow]
    warnings: list[CashflowQualityWarning]
