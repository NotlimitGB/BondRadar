from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalysisSignal


class CompanyScoreBase(BaseModel):
    company_id: int = Field(..., ge=1)
    report_id: int | None = Field(default=None, ge=1)
    score: Decimal = Field(..., ge=0, le=100)
    signal: AnalysisSignal = AnalysisSignal.INSUFFICIENT_DATA
    factors: dict[str, Any] = Field(default_factory=dict)
    explanation: dict[str, Any] | None = None
    debt_score: int | None = Field(default=None, ge=0, le=100)
    profitability_score: int | None = Field(default=None, ge=0, le=100)
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    cashflow_score: int | None = Field(default=None, ge=0, le=100)
    stability_score: int | None = Field(default=None, ge=0, le=100)
    final_company_score: int | None = Field(default=None, ge=0, le=100)
    risk_level: str | None = Field(default=None, max_length=32)
    summary: str | None = None
    as_of_date: date
    source: str = Field(default="manual", min_length=1, max_length=255)

    model_config = ConfigDict(use_enum_values=True)


class CompanyScoreCreate(CompanyScoreBase):
    pass


class CompanyScoreUpdate(BaseModel):
    company_id: int | None = Field(default=None, ge=1)
    report_id: int | None = Field(default=None, ge=1)
    score: Decimal | None = Field(default=None, ge=0, le=100)
    signal: AnalysisSignal | None = None
    factors: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    debt_score: int | None = Field(default=None, ge=0, le=100)
    profitability_score: int | None = Field(default=None, ge=0, le=100)
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    cashflow_score: int | None = Field(default=None, ge=0, le=100)
    stability_score: int | None = Field(default=None, ge=0, le=100)
    final_company_score: int | None = Field(default=None, ge=0, le=100)
    risk_level: str | None = Field(default=None, max_length=32)
    summary: str | None = None
    as_of_date: date | None = None
    source: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(use_enum_values=True)


class CompanyScoreRead(CompanyScoreBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class CompanyScoreCalculationRead(BaseModel):
    id: int
    company_id: int
    report_id: int
    debt_score: int
    profitability_score: int
    liquidity_score: int
    cashflow_score: int
    stability_score: int
    final_company_score: int
    risk_level: str
    explanation: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
