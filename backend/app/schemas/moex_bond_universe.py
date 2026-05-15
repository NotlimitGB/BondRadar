from __future__ import annotations

from datetime import date as Date

from pydantic import BaseModel


class MoexBondUniverseSyncRequest(BaseModel):
    secids: list[str] | None = None
    board: str = "TQCB"
    date: Date | None = None
    active_only: bool = True
    create_missing_companies: bool = True
    rebuild_existing: bool = False
    max_pages: int = 100
    page_size: int = 100


class MoexBondUniverseSyncError(BaseModel):
    secid: str | None = None
    isin: str | None = None
    message: str


class MoexBondUniverseSyncWarning(BaseModel):
    secid: str | None = None
    isin: str | None = None
    message: str


class MoexBondUniverseSyncResult(BaseModel):
    requested_securities: int | None
    processed_securities: int
    companies_created: int
    companies_updated: int
    companies_skipped: int
    bonds_created: int
    bonds_updated: int
    bonds_skipped: int
    errors: list[MoexBondUniverseSyncError]
    warnings: list[MoexBondUniverseSyncWarning]
