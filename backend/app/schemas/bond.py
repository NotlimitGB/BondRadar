from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import AnalysisSignal


class BondBase(BaseModel):
    company_id: int = Field(..., ge=1)
    isin: str | None = Field(default=None, min_length=12, max_length=12)
    secid: str | None = Field(default=None, min_length=1, max_length=32)
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
    @model_validator(mode="after")
    def require_identifier(self) -> "BondCreate":
        if not self.isin and not self.secid:
            raise ValueError("Either isin or secid is required")
        return self


class BondUpdate(BaseModel):
    company_id: int | None = Field(default=None, ge=1)
    isin: str | None = Field(default=None, min_length=12, max_length=12)
    secid: str | None = Field(default=None, min_length=1, max_length=32)
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


class BondIssuerRead(BaseModel):
    id: int
    name: str
    ticker: str
    sector: str | None
    inn: str | None
    country: str
    credit_rating: str | None
    signal: AnalysisSignal

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class BondMarketRead(BaseModel):
    id: int
    trade_date: date
    price: Decimal | None
    clean_price: Decimal | None
    dirty_price: Decimal | None
    nkd: Decimal | None
    yield_to_maturity: Decimal | None
    duration_years: Decimal | None
    volume: Decimal | None
    liquidity_score: int | None
    spread_to_ofz: Decimal | None
    source: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BondRiskRead(BaseModel):
    id: int
    as_of_date: date
    assessment_score: int
    decision_status: str
    risk_level: str
    required_risk_premium: Decimal
    yield_to_maturity: Decimal | None
    coupon_rate: Decimal | None
    duration_years: Decimal | None
    liquidity_score: int | None
    volume: Decimal | None
    company_credit_status: str | None
    company_credit_health_score: int | None
    company_score: Decimal | None
    bond_score: Decimal | None
    gates: dict[str, str]
    warnings: list[str]
    blocking_reasons: list[str]
    positive_factors: list[str]
    negative_factors: list[str]
    missing_data: list[str]
    explanation: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BondProductRead(BondRead):
    issuer: BondIssuerRead
    latest_market: BondMarketRead | None
    latest_risk: BondRiskRead | None
