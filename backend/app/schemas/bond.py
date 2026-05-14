from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalysisSignal


class BondBase(BaseModel):
    company_id: int = Field(..., ge=1)
    isin: str = Field(..., min_length=12, max_length=12)
    name: str = Field(..., min_length=1, max_length=255)
    currency: str = Field(default="RUB", min_length=3, max_length=3)
    nominal_value: Decimal | None = Field(default=None, ge=0)
    current_price: Decimal | None = Field(default=None, ge=0)
    coupon_rate: Decimal | None = Field(default=None, ge=0)
    yield_to_maturity: Decimal | None = None
    duration_years: Decimal | None = Field(default=None, ge=0)
    volume: Decimal | None = Field(default=None, ge=0)
    maturity_date: date | None = None
    offer_date: date | None = None
    is_floating_coupon: bool = False
    is_subordinated: bool = False
    is_perpetual: bool = False
    amortization: bool | None = None
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    signal: AnalysisSignal = AnalysisSignal.INSUFFICIENT_DATA
    risk_notes: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class BondCreate(BondBase):
    pass


class BondUpdate(BaseModel):
    company_id: int | None = Field(default=None, ge=1)
    isin: str | None = Field(default=None, min_length=12, max_length=12)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    nominal_value: Decimal | None = Field(default=None, ge=0)
    current_price: Decimal | None = Field(default=None, ge=0)
    coupon_rate: Decimal | None = Field(default=None, ge=0)
    yield_to_maturity: Decimal | None = None
    duration_years: Decimal | None = Field(default=None, ge=0)
    volume: Decimal | None = Field(default=None, ge=0)
    maturity_date: date | None = None
    offer_date: date | None = None
    is_floating_coupon: bool | None = None
    is_subordinated: bool | None = None
    is_perpetual: bool | None = None
    amortization: bool | None = None
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    signal: AnalysisSignal | None = None
    risk_notes: str | None = None

    model_config = ConfigDict(use_enum_values=True)


class BondRead(BondBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)
