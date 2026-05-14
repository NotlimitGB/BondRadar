from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BondMarketSnapshotCreate(BaseModel):
    bond_id: int = Field(..., ge=1)
    trade_date: date
    price: Decimal | None = None
    clean_price: Decimal | None = None
    dirty_price: Decimal | None = None
    nkd: Decimal | None = None
    yield_to_maturity: Decimal | None = None
    duration_years: Decimal | None = None
    volume: Decimal | None = None
    liquidity_score: int | None = Field(default=None, ge=0, le=100)
    spread_to_ofz: Decimal | None = None
    source: str = Field(default="manual", min_length=1, max_length=64)
    raw_payload: dict[str, Any] | None = None


class BondMarketSnapshotRead(BondMarketSnapshotCreate):
    id: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BondFeatureSnapshotRead(BaseModel):
    id: int
    bond_id: int
    company_id: int
    as_of_date: date
    market_snapshot_id: int | None
    bond_score_id: int | None
    company_score_id: int | None
    financial_report_id: int | None
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
    features_json: dict[str, Any] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BondReturnLabelRead(BaseModel):
    id: int
    bond_id: int
    as_of_date: date
    horizon_days: int
    start_market_snapshot_id: int | None
    end_market_snapshot_id: int | None
    start_price: Decimal | None
    end_price: Decimal | None
    future_return: Decimal | None
    benchmark_return: Decimal | None
    excess_return: Decimal | None
    label: str
    label_binary: int | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DatasetBuildRunRead(BaseModel):
    id: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    as_of_date_from: date
    as_of_date_to: date
    horizon_days: int
    features_created: int
    labels_created: int
    features_updated: int
    labels_updated: int
    errors_count: int
    errors: list[dict[str, Any]]
    params: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DatasetBuildRequest(BaseModel):
    as_of_date_from: date
    as_of_date_to: date
    horizon_days: int = 30
    bond_ids: list[int] | None = None
    rebuild_existing: bool = False


class DatasetBuildResult(BaseModel):
    run_id: int
    status: str
    features_created: int
    features_updated: int
    labels_created: int
    labels_updated: int
    errors_count: int
    errors: list[dict[str, Any]]
