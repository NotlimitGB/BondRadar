from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


MetricInput = Decimal | int | float | str | None


class FinancialReportStructuredInput(BaseModel):
    company_id: int | None = Field(default=None, ge=1)
    company_ticker: str | None = Field(default=None, max_length=32)
    company_inn: str | None = Field(default=None, max_length=16)
    period_year: int
    period_quarter: int | None = None
    period_start_date: date | None = None
    period_end_date: date | None = None
    published_at: datetime | None = None
    document_date: date | None = None
    currency: str | None = Field(default="RUB", max_length=3)
    source: str = Field(default="manual", min_length=1, max_length=64)
    source_url: str | None = None
    source_file_name: str | None = Field(default=None, max_length=255)
    report_type: str | None = Field(default=None, max_length=64)
    revenue: MetricInput = None
    ebitda: MetricInput = None
    net_debt: MetricInput = None
    total_debt: MetricInput = None
    cash: MetricInput = None
    equity: MetricInput = None
    short_term_debt: MetricInput = None
    operating_cash_flow: MetricInput = None
    net_profit: MetricInput = None
    interest_expense: MetricInput = None
    debt_to_ebitda: MetricInput = None
    interest_coverage: MetricInput = None


class FinancialReportIngestRequest(BaseModel):
    source: str = Field(default="manual", min_length=1, max_length=64)
    rows: list[FinancialReportStructuredInput]
    rebuild_existing: bool = False


class FinancialReportIngestError(BaseModel):
    row_index: int | None = None
    company_id: int | None = None
    company_ticker: str | None = None
    company_inn: str | None = None
    period_year: int | None = None
    period_quarter: int | None = None
    message: str


class FinancialReportIngestWarning(FinancialReportIngestError):
    pass


class FinancialReportPreviewRow(BaseModel):
    row_index: int
    company_id: int | None = None
    company_ticker: str | None = None
    company_inn: str | None = None
    matched_company_id: int | None = None
    matched_company_name: str | None = None
    identifier_used: str | None = None
    period_year: int | None = None
    period_quarter: int | None = None
    would_action: str
    errors: list[FinancialReportIngestError] = Field(default_factory=list)
    warnings: list[FinancialReportIngestWarning] = Field(default_factory=list)


class FinancialReportPreviewResult(BaseModel):
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    would_create: int
    would_update: int
    would_skip: int
    rows: list[FinancialReportPreviewRow]
    errors: list[FinancialReportIngestError] = Field(default_factory=list)
    warnings: list[FinancialReportIngestWarning] = Field(default_factory=list)


class FinancialReportIngestResult(BaseModel):
    run_id: int
    status: str
    total_rows: int
    created: int
    updated: int
    skipped: int
    failed: int
    errors: list[FinancialReportIngestError]
    warnings: list[FinancialReportIngestWarning]


class FinancialReportStatsRead(BaseModel):
    financial_reports_count: int
    financial_report_source_documents_count: int
    financial_report_import_runs_count: int


class FinancialScoringPreviewBatchRequest(BaseModel):
    company_ids: list[int] = Field(..., min_length=1)
    include_diagnostics: bool = True
    include_bond_context: bool = True


class FinancialCollectionPriorityBatchRequest(BaseModel):
    company_ids: list[int] = Field(..., min_length=1)
    source_presence: dict[str, list[str]] | None = None
    include_covered: bool = True
    exclude_government_like: bool = True


class IdentityFirstCollectionBatchRequest(BaseModel):
    company_ids: list[int] = Field(..., min_length=1)
    source_presence: dict[str, list[str]] | None = None
    include_covered: bool = True
    exclude_government_like: bool = True


class FinancialReportImportRunRead(BaseModel):
    id: int
    source: str
    input_type: str
    status: str
    params_json: dict[str, Any]
    errors_json: list[dict[str, Any]]
    warnings_json: list[dict[str, Any]]
    total_rows: int
    created: int
    updated: int
    skipped: int
    failed: int
    started_at: datetime
    finished_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FinancialReportSourceDocumentRead(BaseModel):
    id: int
    company_id: int
    financial_report_id: int | None
    source: str
    source_url: str | None
    source_file_name: str | None
    report_type: str | None
    period_year: int
    period_quarter: int | None
    period_start_date: date | None
    period_end_date: date | None
    published_at: datetime | None
    document_date: date | None
    currency: str | None
    status: str
    raw_payload: dict[str, Any] | None
    parse_warnings: list[dict[str, Any]]
    parse_errors: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
