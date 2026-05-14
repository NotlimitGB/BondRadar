from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalysisSignal


class CompanyBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    ticker: str = Field(..., min_length=1, max_length=32)
    sector: str | None = Field(default=None, max_length=128)
    inn: str | None = Field(default=None, max_length=16)
    country: str = Field(default="RU", min_length=2, max_length=64)
    credit_rating: str | None = Field(default=None, max_length=32)
    signal: AnalysisSignal = AnalysisSignal.INSUFFICIENT_DATA
    notes: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    ticker: str | None = Field(default=None, min_length=1, max_length=32)
    sector: str | None = Field(default=None, max_length=128)
    inn: str | None = Field(default=None, max_length=16)
    country: str | None = Field(default=None, min_length=2, max_length=64)
    credit_rating: str | None = Field(default=None, max_length=32)
    signal: AnalysisSignal | None = None
    notes: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class CompanyRead(CompanyBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

