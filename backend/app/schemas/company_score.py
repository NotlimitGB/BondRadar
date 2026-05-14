from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalysisSignal


class CompanyScoreBase(BaseModel):
    company_id: int = Field(..., ge=1)
    score: Decimal = Field(..., ge=0, le=100)
    signal: AnalysisSignal = AnalysisSignal.INSUFFICIENT_DATA
    factors: dict[str, Any] = Field(default_factory=dict)
    summary: str | None = None
    as_of_date: date
    source: str = Field(default="manual", min_length=1, max_length=255)

    model_config = ConfigDict(use_enum_values=True)


class CompanyScoreCreate(CompanyScoreBase):
    pass


class CompanyScoreUpdate(BaseModel):
    company_id: int | None = Field(default=None, ge=1)
    score: Decimal | None = Field(default=None, ge=0, le=100)
    signal: AnalysisSignal | None = None
    factors: dict[str, Any] | None = None
    summary: str | None = None
    as_of_date: date | None = None
    source: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(use_enum_values=True)


class CompanyScoreRead(CompanyScoreBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

