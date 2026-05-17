from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


READINESS_RETURN_METHODS = {"price", "total_return", "risk_adjusted"}
READINESS_STATUSES = {"ready", "warning", "not_ready"}
READINESS_GATE_STATUSES = {"pass", "warning", "fail"}


class DataReadinessCheckRequest(BaseModel):
    date_from: date
    date_to: date
    horizon_days: int = 30
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    return_method: str = "risk_adjusted"
    min_rows: int = 100
    min_positive_rows: int = 20
    min_negative_rows: int = 20
    max_insufficient_ratio: Decimal = Decimal("0.30")
    require_credit_risk: bool = True
    require_financial_reports: bool = True
    require_cashflows: bool = False
    require_moex_secid: bool = True
    max_bond_issues: int = 50
    include_market_history_quality: bool = False
    include_cashflow_quality: bool = False
    market_quality_source: str | None = "moex"
    market_expected_date_mode: str = "business_days"
    market_minimum_snapshot_count: int | None = None
    market_minimum_coverage_ratio: Decimal | None = None
    market_maximum_gap_days: int | None = None
    market_require_price: bool = True
    market_require_yield: bool = False
    market_require_volume: bool = False
    cashflow_quality_source: str | None = "moex"
    cashflow_require_future_cashflows: bool = True
    cashflow_require_coupon_events: bool = False
    cashflow_require_redemption_or_maturity: bool = False
    cashflow_max_duplicate_events_per_bond: int | None = None
    cashflow_maximum_days_without_future_event: int | None = None


class DataReadinessGate(BaseModel):
    name: str
    status: str
    message: str
    details: dict[str, Any]


class DataReadinessQualityGateSummary(BaseModel):
    enabled: bool
    status: str
    ready_bond_count: int
    warning_bond_count: int
    not_ready_bond_count: int
    total_bond_count: int
    issue_summary: dict[str, int]
    warnings: list[str]


class DataReadinessClassDistribution(BaseModel):
    evaluable_label_count: int
    positive_label_count: int
    negative_label_count: int
    insufficient_label_count: int


class DataReadinessCoverage(BaseModel):
    bonds_with_market_snapshots_count: int
    bonds_with_cashflows_count: int
    companies_with_financial_reports_count: int
    companies_with_credit_health_count: int
    bonds_with_risk_assessments_count: int
    bonds_with_features_count: int
    bonds_with_labels_count: int


class DataReadinessSummary(BaseModel):
    date_from: date
    date_to: date
    horizon_days: int
    return_method: str
    selected_bonds_count: int
    selected_companies_count: int
    bonds_with_secid_count: int
    bonds_without_secid_count: int
    market_snapshot_count: int
    cashflow_event_count: int
    financial_report_count: int
    credit_health_snapshot_count: int
    bond_risk_assessment_count: int
    feature_row_count: int
    label_row_count: int
    joined_feature_label_row_count: int
    evaluable_label_count: int
    positive_label_count: int
    negative_label_count: int
    insufficient_label_count: int
    insufficient_ratio: Decimal
    ready_for_ml_training: bool
    class_distribution: DataReadinessClassDistribution
    coverage: DataReadinessCoverage


class DataReadinessBondIssue(BaseModel):
    bond_id: int
    bond_name: str | None
    isin: str | None
    secid: str | None
    company_id: int | None
    company_name: str | None
    issues: list[str]
    details: dict[str, Any]


class DataReadinessResponse(BaseModel):
    status: str
    summary: DataReadinessSummary
    gates: list[DataReadinessGate]
    bond_issues: list[DataReadinessBondIssue]
    warnings: list[str]
    recommended_next_actions: list[str]
    market_history_quality: DataReadinessQualityGateSummary | None = None
    cashflow_quality: DataReadinessQualityGateSummary | None = None
