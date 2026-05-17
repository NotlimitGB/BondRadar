from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class LabelQualityReportRequest(BaseModel):
    date_from: date
    date_to: date
    horizon_days: int | None = None
    return_methods: list[str] | None = None
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    secids: list[str] | None = None
    include_bond_rows: bool = True
    include_company_rows: bool = True
    include_warning_breakdown: bool = True
    include_component_summary: bool = True
    include_return_distribution: bool = True
    extreme_return_abs_limit: Decimal = Decimal("0.50")
    minimum_evaluable_rows: int = 100
    minimum_positive_rows: int = 20
    minimum_negative_rows: int = 20
    maximum_insufficient_ratio: Decimal = Decimal("0.30")
    limit: int = 100
    offset: int = 0


class LabelQualityWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class LabelQualityOverview(BaseModel):
    selected_bond_count: int
    selected_company_count: int
    label_row_count: int
    evaluable_label_count: int
    positive_label_count: int
    negative_label_count: int
    insufficient_label_count: int
    insufficient_ratio: Decimal | None
    positive_ratio: Decimal | None
    negative_ratio: Decimal | None
    ready_for_ml_dataset: bool
    labels_with_start_snapshot_count: int
    labels_with_end_snapshot_count: int
    labels_with_warnings_count: int
    labels_with_details_count: int
    extreme_return_count: int
    null_future_return_count: int


class LabelQualityIssueSummary(BaseModel):
    no_labels_count: int
    insufficient_labels_count: int
    null_future_return_count: int
    missing_start_snapshot_count: int
    missing_end_snapshot_count: int
    warning_label_count: int
    extreme_return_count: int
    low_evaluable_rows: int
    low_positive_rows: int
    low_negative_rows: int
    high_insufficient_ratio: int


class LabelQualityMethodSummary(BaseModel):
    return_method: str
    horizon_days: int | None
    label_row_count: int
    evaluable_label_count: int
    positive_label_count: int
    negative_label_count: int
    insufficient_label_count: int
    insufficient_ratio: Decimal | None
    positive_ratio: Decimal | None
    negative_ratio: Decimal | None
    average_future_return: Decimal | None
    median_future_return: Decimal | None
    min_future_return: Decimal | None
    max_future_return: Decimal | None
    labels_with_warnings_count: int
    extreme_return_count: int


class LabelQualityWarningItem(BaseModel):
    message: str
    count: int
    first_seen_label_id: int | None
    example_bond_id: int | None
    example_as_of_date: date | None


class LabelQualityReturnDistribution(BaseModel):
    count: int
    average: Decimal | None
    median: Decimal | None
    minimum: Decimal | None
    maximum: Decimal | None
    p10: Decimal | None
    p25: Decimal | None
    p75: Decimal | None
    p90: Decimal | None


class LabelQualityComponentSummary(BaseModel):
    price_return_average: Decimal | None
    coupon_return_average: Decimal | None
    amortization_return_average: Decimal | None
    redemption_return_average: Decimal | None
    gross_total_return_average: Decimal | None
    estimated_costs_return_average: Decimal | None
    net_total_return_average: Decimal | None
    risk_adjusted_excess_return_average: Decimal | None
    required_risk_premium_average: Decimal | None
    cashflow_included_count: int
    cashflow_disabled_count: int
    benchmark_missing_count: int
    risk_premium_missing_count: int


class LabelQualityBondRow(BaseModel):
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
    label_row_count: int
    evaluable_label_count: int
    positive_label_count: int
    negative_label_count: int
    insufficient_label_count: int
    insufficient_ratio: Decimal | None
    positive_ratio: Decimal | None
    negative_ratio: Decimal | None
    first_label_date: date | None
    last_label_date: date | None
    average_future_return: Decimal | None
    min_future_return: Decimal | None
    max_future_return: Decimal | None
    labels_with_warnings_count: int
    extreme_return_count: int
    missing_start_snapshot_count: int
    missing_end_snapshot_count: int


class LabelQualityCompanyRow(BaseModel):
    company_id: int
    company_name: str | None
    company_ticker: str | None
    bond_count: int
    status: str
    issue_count: int
    issues: list[str]
    label_row_count: int
    evaluable_label_count: int
    positive_label_count: int
    negative_label_count: int
    insufficient_label_count: int
    insufficient_ratio: Decimal | None
    labels_with_warnings_count: int
    extreme_return_count: int
    ready_bond_count: int
    warning_bond_count: int
    not_ready_bond_count: int


class LabelQualityReportResponse(BaseModel):
    date_from: date
    date_to: date
    horizon_days: int | None
    return_methods: list[str]
    overview: LabelQualityOverview
    issue_summary: LabelQualityIssueSummary
    method_summaries: list[LabelQualityMethodSummary]
    return_distribution: LabelQualityReturnDistribution | None
    component_summary: LabelQualityComponentSummary | None
    warning_breakdown: list[LabelQualityWarningItem]
    total_bond_rows: int
    bond_rows: list[LabelQualityBondRow]
    total_company_rows: int
    company_rows: list[LabelQualityCompanyRow]
    limit: int
    offset: int
    warnings: list[LabelQualityWarning]
