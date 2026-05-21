from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class FinancialReportCoverageWarning(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class FinancialReportFeatureSnapshotCoverage(BaseModel):
    feature_snapshot_count: int
    feature_snapshots_with_financial_report_id: int
    feature_snapshots_with_any_financial_ratio: int
    feature_snapshots_with_core_ratios: int
    feature_snapshot_financial_report_ratio: Decimal | None
    feature_snapshot_financial_ratio_ratio: Decimal | None
    average_missing_data_count: Decimal | None
    ratio_field_counts: dict[str, int] = Field(default_factory=dict)


class FinancialReportCoverageResponse(BaseModel):
    status: str
    as_of_date: date
    active_only: bool
    stale_after_days: int

    company_count: int
    companies_with_financial_reports: int
    companies_without_financial_reports: int
    coverage_ratio: Decimal | None
    recent_report_company_count: int
    stale_report_company_count: int

    active_bond_count: int
    active_bonds_with_financial_reports: int
    active_bonds_without_financial_reports: int
    active_bond_coverage_ratio: Decimal | None

    latest_report_period_end_date: date | None
    oldest_latest_report_period_end_date: date | None
    missing_field_counts: dict[str, int] = Field(default_factory=dict)

    feature_snapshot_coverage: FinancialReportFeatureSnapshotCoverage
    warnings: list[FinancialReportCoverageWarning] = Field(default_factory=list)
