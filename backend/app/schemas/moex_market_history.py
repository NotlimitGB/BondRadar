from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class MoexBondMarketHistoryBackfillRequest(BaseModel):
    date_from: date
    date_to: date
    bond_ids: list[int] | None = None
    secids: list[str] | None = None
    board: str = "TQCB"
    page_size: int = 100
    max_pages_per_bond: int = 100
    rebuild_existing: bool = False
    skip_bonds_without_secid: bool = True
    source: str = "moex"


class MoexBondMarketHistoryBackfillWarning(BaseModel):
    message: str
    bond_id: int | None = None
    secid: str | None = None
    trade_date: date | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MoexBondMarketHistoryBackfillError(BaseModel):
    message: str
    bond_id: int | None = None
    secid: str | None = None
    trade_date: date | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class MoexBondMarketHistoryBackfillBondResult(BaseModel):
    bond_id: int | None = None
    secid: str | None = None
    status: str
    rows_fetched: int
    snapshots_created: int
    snapshots_updated: int
    snapshots_skipped: int
    warnings: list[MoexBondMarketHistoryBackfillWarning]
    errors: list[MoexBondMarketHistoryBackfillError]


class MoexBondMarketHistoryBackfillResult(BaseModel):
    date_from: date
    date_to: date
    board: str
    source: str
    bonds_requested: int
    bonds_processed: int
    bonds_skipped: int
    bonds_failed: int
    rows_fetched: int
    snapshots_created: int
    snapshots_updated: int
    snapshots_skipped: int
    bond_results: list[MoexBondMarketHistoryBackfillBondResult]
    warnings: list[MoexBondMarketHistoryBackfillWarning]
    errors: list[MoexBondMarketHistoryBackfillError]
