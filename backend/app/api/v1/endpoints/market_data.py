from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.moex import (
    MoexCashflowSyncRequest,
    MoexCashflowSyncResult,
    MoexMarketDataSyncRequest,
    MoexMarketDataSyncResult,
)
from app.schemas.moex_market_history import (
    MoexBondMarketHistoryBackfillRequest,
    MoexBondMarketHistoryBackfillResult,
)
from app.schemas.moex_bond_universe import (
    MoexBondUniverseSyncRequest,
    MoexBondUniverseSyncResult,
)
from app.services.moex_bond_universe_service import MoexBondUniverseService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService


router = APIRouter()


@router.post("/market-data/moex/sync", response_model=MoexMarketDataSyncResult)
def sync_moex_market_data(
    request: MoexMarketDataSyncRequest,
    db: Session = Depends(get_db),
) -> MoexMarketDataSyncResult:
    return MoexMarketDataService(db).sync(request)


@router.post(
    "/market-data/moex/bonds/history/backfill",
    response_model=MoexBondMarketHistoryBackfillResult,
)
def backfill_moex_bond_market_history(
    request: MoexBondMarketHistoryBackfillRequest,
    db: Session = Depends(get_db),
) -> MoexBondMarketHistoryBackfillResult:
    return MoexMarketDataService(db).backfill_history(request)


@router.post(
    "/market-data/moex/bonds/sync",
    response_model=MoexBondUniverseSyncResult,
)
def sync_moex_bond_universe(
    request: MoexBondUniverseSyncRequest,
    db: Session = Depends(get_db),
) -> MoexBondUniverseSyncResult:
    return MoexBondUniverseService(db).sync(request)


@router.post(
    "/market-data/moex/cashflows/sync",
    response_model=MoexCashflowSyncResult,
)
def sync_moex_cashflows(
    request: MoexCashflowSyncRequest,
    db: Session = Depends(get_db),
) -> MoexCashflowSyncResult:
    return MoexCashflowService(db).sync(request)
