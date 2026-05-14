from datetime import date

from pydantic import BaseModel, Field


class MoexMarketDataSyncRequest(BaseModel):
    bond_ids: list[int] | None = None
    date_from: date
    date_to: date
    board: str = Field(default="TQCB", min_length=1, max_length=16)
    rebuild_existing: bool = False


class MoexMarketDataSyncError(BaseModel):
    bond_id: int | None = None
    secid: str | None = None
    message: str


class MoexMarketDataSyncResult(BaseModel):
    total_bonds: int
    processed_bonds: int
    skipped_bonds: int
    created: int
    updated: int
    skipped: int
    errors: list[MoexMarketDataSyncError]
    warnings: list[str]
