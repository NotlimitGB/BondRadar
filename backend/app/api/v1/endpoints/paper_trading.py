from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.paper_trading import (
    PaperPortfolioCreate,
    PaperPortfolioMarkPeriodRequest,
    PaperPortfolioMarkPeriodResult,
    PaperPortfolioPositionRead,
    PaperPortfolioRead,
    PaperPortfolioRebalanceRequest,
    PaperPortfolioRebalanceResult,
    PaperPortfolioSnapshotRead,
    PaperPortfolioTransactionRead,
)
from app.services.paper_trading_service import PaperTradingService


router = APIRouter()


@router.post("/portfolios", response_model=PaperPortfolioRead)
def create_paper_portfolio(
    request: PaperPortfolioCreate,
    db: Session = Depends(get_db),
) -> PaperPortfolioRead:
    return PaperPortfolioRead.model_validate(
        PaperTradingService(db).create_portfolio(request)
    )


@router.get("/portfolios", response_model=list[PaperPortfolioRead])
def list_paper_portfolios(
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[PaperPortfolioRead]:
    return [
        PaperPortfolioRead.model_validate(portfolio)
        for portfolio in PaperTradingService(db).list_portfolios(limit=limit)
    ]


@router.get("/portfolios/{portfolio_id}", response_model=PaperPortfolioRead)
def get_paper_portfolio(
    portfolio_id: int,
    db: Session = Depends(get_db),
) -> PaperPortfolioRead:
    return PaperPortfolioRead.model_validate(
        PaperTradingService(db).get_portfolio(portfolio_id)
    )


@router.get(
    "/portfolios/{portfolio_id}/positions",
    response_model=list[PaperPortfolioPositionRead],
)
def list_paper_positions(
    portfolio_id: int,
    db: Session = Depends(get_db),
) -> list[PaperPortfolioPositionRead]:
    return [
        PaperPortfolioPositionRead.model_validate(position)
        for position in PaperTradingService(db).list_positions(portfolio_id)
    ]


@router.get(
    "/portfolios/{portfolio_id}/transactions",
    response_model=list[PaperPortfolioTransactionRead],
)
def list_paper_transactions(
    portfolio_id: int,
    db: Session = Depends(get_db),
) -> list[PaperPortfolioTransactionRead]:
    return [
        PaperPortfolioTransactionRead.model_validate(transaction)
        for transaction in PaperTradingService(db).list_transactions(portfolio_id)
    ]


@router.get(
    "/portfolios/{portfolio_id}/snapshots",
    response_model=list[PaperPortfolioSnapshotRead],
)
def list_paper_snapshots(
    portfolio_id: int,
    db: Session = Depends(get_db),
) -> list[PaperPortfolioSnapshotRead]:
    return [
        PaperPortfolioSnapshotRead.model_validate(snapshot)
        for snapshot in PaperTradingService(db).list_snapshots(portfolio_id)
    ]


@router.post(
    "/portfolios/{portfolio_id}/rebalance",
    response_model=PaperPortfolioRebalanceResult,
)
def rebalance_paper_portfolio(
    portfolio_id: int,
    request: PaperPortfolioRebalanceRequest,
    db: Session = Depends(get_db),
) -> PaperPortfolioRebalanceResult:
    return PaperTradingService(db).rebalance(portfolio_id, request)


@router.post(
    "/portfolios/{portfolio_id}/mark-period",
    response_model=PaperPortfolioMarkPeriodResult,
)
def mark_paper_portfolio_period(
    portfolio_id: int,
    request: PaperPortfolioMarkPeriodRequest,
    db: Session = Depends(get_db),
) -> PaperPortfolioMarkPeriodResult:
    return PaperTradingService(db).mark_period(portfolio_id, request)
