from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ControlledFinancialStatementValueBase(BaseModel):
    company_id: str = Field(..., min_length=1, max_length=32)
    company_name: str = Field(..., min_length=1, max_length=255)
    report_year: int = Field(..., ge=1900, le=2100)
    report_standard: str = Field(..., min_length=1, max_length=32)
    target_type: str = Field(..., min_length=1, max_length=128)
    metric_key: str = Field(..., min_length=1, max_length=128)
    metric_role: str = Field(..., min_length=1, max_length=32)
    metric_name_ru: str = Field(..., max_length=255)
    metric_name_en: str = Field(..., max_length=255)
    statement_page: int = Field(..., ge=1)
    page_number: int = Field(..., ge=1)
    value_2025: Decimal
    value_2024: Decimal
    raw_value_2025: str = Field(..., min_length=1, max_length=64)
    raw_value_2024: str = Field(..., min_length=1, max_length=64)
    raw_line: str = Field(..., min_length=1)
    note_reference: str = Field(default="", max_length=64)
    source_pdf_sha256: str = Field(..., min_length=64, max_length=64)
    plan_checksum_sha256: str = Field(..., min_length=64, max_length=64)
    plan_rows_checksum_sha256: str = Field(..., min_length=64, max_length=64)
    natural_key: str = Field(..., min_length=1)
    natural_key_sha256: str = Field(..., min_length=64, max_length=64)
    row_checksum_sha256: str = Field(..., min_length=64, max_length=64)


class ControlledFinancialStatementValueCreate(ControlledFinancialStatementValueBase):
    pass


class ControlledFinancialStatementValueImportPreview(ControlledFinancialStatementValueBase):
    import_plan_row_id: str | None = None
    target_logical_entity: str = "financial_statement_value"
    target_operation: str = "upsert_preview"
    duplicate_policy: str = "natural_key_update_existing_preview"


class ControlledFinancialStatementValueRead(ControlledFinancialStatementValueBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
