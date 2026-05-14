from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalysisSignal


class FinancialReportBase(BaseModel):
    period_year: int = Field(..., ge=1900, le=2100)
    period_quarter: int = Field(default=0, ge=0, le=4)
    revenue: Decimal | None = None
    ebitda: Decimal | None = None
    net_debt: Decimal | None = None
    total_debt: Decimal | None = None
    cash: Decimal | None = None
    equity: Decimal | None = None
    short_term_debt: Decimal | None = None
    operating_cash_flow: Decimal | None = None
    net_profit: Decimal | None = None
    interest_expense: Decimal | None = None
    debt_to_ebitda: Decimal | None = None
    interest_coverage: Decimal | None = None
    source: str | None = Field(default=None, max_length=255)
    signal: AnalysisSignal = AnalysisSignal.INSUFFICIENT_DATA

    model_config = ConfigDict(use_enum_values=True)


class FinancialReportCreate(FinancialReportBase):
    pass


class FinancialReportUpdate(BaseModel):
    period_year: int | None = Field(default=None, ge=1900, le=2100)
    period_quarter: int | None = Field(default=None, ge=0, le=4)
    revenue: Decimal | None = None
    ebitda: Decimal | None = None
    net_debt: Decimal | None = None
    total_debt: Decimal | None = None
    cash: Decimal | None = None
    equity: Decimal | None = None
    short_term_debt: Decimal | None = None
    operating_cash_flow: Decimal | None = None
    net_profit: Decimal | None = None
    interest_expense: Decimal | None = None
    debt_to_ebitda: Decimal | None = None
    interest_coverage: Decimal | None = None
    source: str | None = Field(default=None, max_length=255)
    signal: AnalysisSignal | None = None

    model_config = ConfigDict(use_enum_values=True)


class FinancialReportRead(FinancialReportBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
