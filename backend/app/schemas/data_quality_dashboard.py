from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class DataQualityWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DataQualityDateRange(BaseModel):
    min_date: date | None
    max_date: date | None
    row_count: int


class DataQualityCounts(BaseModel):
    companies_total: int
    companies_with_bonds: int
    companies_with_financial_reports: int
    companies_with_credit_health: int
    bonds_total: int
    bonds_with_secid: int
    bonds_with_isin: int
    bonds_with_market_snapshots: int
    bonds_with_cashflows: int
    bonds_with_features: int
    bonds_with_price_labels: int
    bonds_with_total_return_labels: int
    bonds_with_risk_adjusted_labels: int
    bonds_with_risk_assessment: int
    market_snapshots_total: int
    cashflow_events_total: int
    financial_reports_total: int
    company_credit_health_total: int
    bond_risk_assessments_total: int
    feature_snapshots_total: int
    labels_total: int
    ml_model_runs_total: int
    ml_predictions_total: int


class DataQualityCoverage(BaseModel):
    company_report_coverage: Decimal | None
    company_credit_health_coverage: Decimal | None
    bond_secid_coverage: Decimal | None
    bond_isin_coverage: Decimal | None
    bond_market_snapshot_coverage: Decimal | None
    bond_cashflow_coverage: Decimal | None
    bond_feature_coverage: Decimal | None
    bond_price_label_coverage: Decimal | None
    bond_total_return_label_coverage: Decimal | None
    bond_risk_adjusted_label_coverage: Decimal | None
    bond_risk_assessment_coverage: Decimal | None


class DataQualitySourceBreakdown(BaseModel):
    source: str
    rows: int


class DataQualityLabelBreakdown(BaseModel):
    return_method: str
    label: str
    horizon_days: int
    rows: int


class DataQualityReturnMethodBreakdown(BaseModel):
    return_method: str
    rows: int
    bonds: int
    positive_rows: int
    negative_rows: int
    insufficient_rows: int
    min_as_of_date: date | None
    max_as_of_date: date | None


class DataQualityIssueSummary(BaseModel):
    bonds_missing_secid: int
    bonds_missing_isin: int
    bonds_without_market_snapshots: int
    bonds_without_cashflows: int
    bonds_without_features: int
    bonds_without_any_labels: int
    bonds_without_risk_assessment: int
    companies_without_financial_reports: int
    companies_without_credit_health: int
    companies_without_bonds: int
    labels_with_insufficient_data: int
    labels_without_label_binary: int
    ml_runs_without_predictions: int


class DataQualityOverviewResponse(BaseModel):
    date_from: date | None
    date_to: date | None
    include_demo: bool
    counts: DataQualityCounts
    coverage: DataQualityCoverage
    date_ranges: dict[str, DataQualityDateRange]
    source_breakdowns: dict[str, list[DataQualitySourceBreakdown]]
    label_breakdowns: list[DataQualityLabelBreakdown]
    return_method_breakdowns: list[DataQualityReturnMethodBreakdown]
    issue_summary: DataQualityIssueSummary
    warnings: list[DataQualityWarning]


class DataQualityBondRow(BaseModel):
    bond_id: int
    company_id: int
    company_name: str | None
    name: str
    isin: str | None
    secid: str | None
    currency: str
    is_demo: bool
    market_snapshot_count: int
    market_snapshot_min_date: date | None
    market_snapshot_max_date: date | None
    cashflow_count: int
    cashflow_min_date: date | None
    cashflow_max_date: date | None
    feature_count: int
    feature_min_date: date | None
    feature_max_date: date | None
    price_label_count: int
    total_return_label_count: int
    risk_adjusted_label_count: int
    label_min_date: date | None
    label_max_date: date | None
    risk_assessment_count: int
    risk_assessment_min_date: date | None
    risk_assessment_max_date: date | None
    latest_liquidity_score: int | None
    latest_yield_to_maturity: Decimal | None
    latest_decision_status: str | None
    latest_risk_level: str | None
    issue_flags: list[str]


class DataQualityBondRowsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DataQualityBondRow]
    warnings: list[DataQualityWarning]


class DataQualityCompanyRow(BaseModel):
    company_id: int
    name: str
    ticker: str
    inn: str | None
    country: str
    is_demo: bool
    bond_count: int
    bonds_with_market_snapshots: int
    bonds_with_cashflows: int
    bonds_with_features: int
    bonds_with_labels: int
    bonds_with_risk_assessment: int
    financial_report_count: int
    financial_report_min_period_year: int | None
    financial_report_max_period_year: int | None
    financial_report_latest_published_at: date | None
    credit_health_count: int
    credit_health_min_date: date | None
    credit_health_max_date: date | None
    latest_credit_status: str | None
    latest_credit_health_score: int | None
    latest_data_quality_level: str | None
    issue_flags: list[str]


class DataQualityCompanyRowsResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DataQualityCompanyRow]
    warnings: list[DataQualityWarning]
