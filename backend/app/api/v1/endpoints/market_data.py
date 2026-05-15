from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.moex import (
    MoexCashflowSyncRequest,
    MoexCashflowSyncResult,
    MoexMarketDataSyncRequest,
    MoexMarketDataSyncResult,
)
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
    "/market-data/moex/cashflows/sync",
    response_model=MoexCashflowSyncResult,
)
def sync_moex_cashflows(
    request: MoexCashflowSyncRequest,
    db: Session = Depends(get_db),
) -> MoexCashflowSyncResult:
    return MoexCashflowService(db).sync(request)
