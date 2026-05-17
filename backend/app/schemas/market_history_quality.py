from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class MarketHistoryQualityAuditRequest(BaseModel):
    date_from: date
    date_to: date
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    secids: list[str] | None = None
    source: str | None = "moex"
    expected_date_mode: str = "business_days"
    minimum_snapshot_count: int = 20
    minimum_coverage_ratio: Decimal = Decimal("0.70")
    maximum_gap_days: int = 14
    require_price: bool = True
    require_yield: bool = False
    require_volume: bool = False
    include_bond_rows: bool = True
    include_gap_details: bool = True
    limit: int = 100
    offset: int = 0


class MarketHistoryQualityWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class MarketHistoryQualityGap(BaseModel):
    gap_start: date
    gap_end: date
    gap_days: int
    missing_expected_dates: int


class MarketHistoryQualityOverview(BaseModel):
    selected_bond_count: int
    selected_company_count: int
    expected_date_count: int
    bonds_with_snapshots: int
    bonds_without_snapshots: int
    total_snapshot_count: int
    average_coverage_ratio: Decimal | None
    median_coverage_ratio: Decimal | None
    ready_bond_count: int
    warning_bond_count: int
    not_ready_bond_count: int
    price_available_ratio: Decimal | None
    yield_available_ratio: Decimal | None
    volume_available_ratio: Decimal | None


class MarketHistoryQualityIssueSummary(BaseModel):
    missing_secid_count: int
    no_snapshots_count: int
    low_snapshot_count: int
    low_coverage_count: int
    long_gap_count: int
    missing_price_count: int
    missing_yield_count: int
    missing_volume_count: int
    non_positive_price_count: int
    negative_yield_count: int
    negative_volume_count: int
    stale_history_count: int


class MarketHistoryQualityBondRow(BaseModel):
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
    snapshot_count: int
    expected_date_count: int
    coverage_ratio: Decimal | None
    first_trade_date: date | None
    last_trade_date: date | None
    missing_expected_date_count: int
    longest_gap_days: int | None
    gap_count: int
    gaps: list[MarketHistoryQualityGap]
    price_count: int
    yield_count: int
    volume_count: int
    missing_price_count: int
    missing_yield_count: int
    missing_volume_count: int
    non_positive_price_count: int
    negative_yield_count: int
    negative_volume_count: int
    latest_price: Decimal | None
    latest_yield_to_maturity: Decimal | None
    latest_volume: Decimal | None
    latest_trade_date: date | None


class MarketHistoryQualityAuditResponse(BaseModel):
    date_from: date
    date_to: date
    source: str | None
    expected_date_mode: str
    overview: MarketHistoryQualityOverview
    issue_summary: MarketHistoryQualityIssueSummary
    total_bond_rows: int
    limit: int
    offset: int
    bond_rows: list[MarketHistoryQualityBondRow]
    warnings: list[MarketHistoryQualityWarning]
