from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class DatasetExportRow(BaseModel):
    bond_id: int
    company_id: int
    as_of_date: date
    horizon_days: int
    return_method: str
    bond_name: str
    isin: str | None
    secid: str | None
    company_name: str
    company_ticker: str
    bond_score: Decimal | None
    company_score: Decimal | None
    yield_to_maturity: Decimal | None
    duration_years: Decimal | None
    liquidity_score: int | None
    volume: Decimal | None
    spread_to_ofz: Decimal | None
    net_debt_to_ebitda: Decimal | None
    debt_to_equity: Decimal | None
    interest_coverage: Decimal | None
    cash_to_short_term_debt: Decimal | None
    ocf_to_total_debt: Decimal | None
    net_profit_margin: Decimal | None
    days_to_maturity: int | None
    has_offer: bool | None
    has_amortization: bool | None
    missing_data_count: int
    future_return: Decimal | None
    benchmark_return: Decimal | None
    excess_return: Decimal | None
    price_return: Decimal | None
    coupon_return: Decimal | None
    amortization_return: Decimal | None
    redemption_return: Decimal | None
    gross_total_return: Decimal | None
    estimated_costs_return: Decimal | None
    net_total_return: Decimal | None
    risk_adjusted_excess_return: Decimal | None
    required_risk_premium: Decimal | None
    label: str
    label_binary: int | None
    market_snapshot_id: int | None
    bond_score_id: int | None
    company_score_id: int | None
    financial_report_id: int | None
    label_id: int
    feature_snapshot_id: int
    feature_created_at: datetime
    label_created_at: datetime


class DatasetExportResponse(BaseModel):
    total: int
    limit: int
    offset: int
    rows: list[DatasetExportRow]


class DatasetQualityLabelDistribution(BaseModel):
    positive_return: int
    negative_return: int
    insufficient_data: int
    label_binary_1: int
    label_binary_0: int
    label_binary_null: int


class DatasetQualityMissingFeature(BaseModel):
    feature: str
    missing_count: int
    missing_ratio: float


class DatasetQualityNumericFeatureStats(BaseModel):
    feature: str
    count: int
    missing_count: int
    min: Decimal | None
    max: Decimal | None
    avg: Decimal | None


class DatasetQualityFinancialRatioCoverage(BaseModel):
    feature_snapshot_count: int
    snapshots_with_financial_report_id: int
    snapshots_with_any_financial_ratio: int
    snapshots_with_core_ratios: int
    financial_report_id_ratio: float | None
    any_financial_ratio_ratio: float | None
    ratio_field_counts: dict[str, int]
    average_missing_data_count: float | None


class DatasetQualityBondCoverage(BaseModel):
    bond_id: int
    bond_name: str
    secid: str | None
    rows_count: int
    as_of_date_min: date | None
    as_of_date_max: date | None
    positive_return_count: int
    negative_return_count: int
    insufficient_data_count: int


class DatasetQualityCompanyCoverage(BaseModel):
    company_id: int
    company_name: str
    company_ticker: str
    rows_count: int
    bond_count: int
    positive_return_count: int
    negative_return_count: int
    insufficient_data_count: int


class DatasetQualityReport(BaseModel):
    total_rows: int
    horizon_days: int
    as_of_date_min: date | None
    as_of_date_max: date | None
    bond_count: int
    company_count: int
    label_distribution: DatasetQualityLabelDistribution
    missing_features: list[DatasetQualityMissingFeature]
    numeric_feature_stats: list[DatasetQualityNumericFeatureStats]
    financial_ratio_coverage: DatasetQualityFinancialRatioCoverage
    coverage_by_bond: list[DatasetQualityBondCoverage]
    coverage_by_company: list[DatasetQualityCompanyCoverage]
