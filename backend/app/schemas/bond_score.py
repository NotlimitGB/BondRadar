from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnalysisSignal


class BondScoreBase(BaseModel):
    bond_id: int = Field(..., ge=1)
    company_score_id: int | None = Field(default=None, ge=1)
    score: Decimal = Field(..., ge=0, le=100)
    signal: AnalysisSignal = AnalysisSignal.INSUFFICIENT_DATA
    factors: dict[str, Any] = Field(default_factory=dict)
    explanation: dict[str, Any] | None = None
    yield_score: int | None = Field(default=None, ge=0, le=100)
    duration_score: int | None = Field(default=None, ge=0, le=100)
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    spread_score: int | None = Field(default=None, ge=0, le=100)
    risk_penalty: int | None = Field(default=None, ge=0)
    final_bond_score: int | None = Field(default=None, ge=0, le=100)
    summary: str | None = None
    as_of_date: date
    source: str = Field(default="manual", min_length=1, max_length=255)

    model_config = ConfigDict(use_enum_values=True)


class BondScoreCreate(BondScoreBase):
    pass


class BondScoreUpdate(BaseModel):
    bond_id: int | None = Field(default=None, ge=1)
    company_score_id: int | None = Field(default=None, ge=1)
    score: Decimal | None = Field(default=None, ge=0, le=100)
    signal: AnalysisSignal | None = None
    factors: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = None
    yield_score: int | None = Field(default=None, ge=0, le=100)
    duration_score: int | None = Field(default=None, ge=0, le=100)
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    spread_score: int | None = Field(default=None, ge=0, le=100)
    risk_penalty: int | None = Field(default=None, ge=0)
    final_bond_score: int | None = Field(default=None, ge=0, le=100)
    summary: str | None = None
    as_of_date: date | None = None
    source: str | None = Field(default=None, min_length=1, max_length=255)

    model_config = ConfigDict(use_enum_values=True)


class BondScoreRead(BondScoreBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)


class BondScoreCalculationRead(BaseModel):
    id: int
    bond_id: int
    company_score_id: int | None
    yield_score: int | None
    duration_score: int | None
    liquidity_score: int | None
    spread_score: int | None
    risk_penalty: int | None
    final_bond_score: int | None
    signal: str
    explanation: dict[str, Any] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScoreRecalculationError(BaseModel):
    bond_id: int
    error: str


class ScoreRecalculationRead(BaseModel):
    total_bonds: int
    calculated: int
    failed: int
    errors: list[ScoreRecalculationError]
