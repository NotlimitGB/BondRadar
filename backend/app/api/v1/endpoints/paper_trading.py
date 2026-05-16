from datetime import date

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
from app.schemas.paper_trading_report import (
    PaperTradingContributionsResponse,
    PaperTradingEquityPoint,
    PaperTradingPerformanceResponse,
)
from app.schemas.paper_trading_scenario import (
    PaperTradingScenarioRunRequest,
    PaperTradingScenarioRunResponse,
)
from app.services.paper_trading_report_service import PaperTradingReportService
from app.services.paper_trading_scenario_service import PaperTradingScenarioService
from app.services.paper_trading_service import PaperTradingService


router = APIRouter()


@router.post("/scenarios/run", response_model=PaperTradingScenarioRunResponse)
def run_paper_trading_scenario(
    request: PaperTradingScenarioRunRequest,
    db: Session = Depends(get_db),
) -> PaperTradingScenarioRunResponse:
    return PaperTradingScenarioService(db).run(request)


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


@router.get(
    "/portfolios/{portfolio_id}/performance",
    response_model=PaperTradingPerformanceResponse,
)
def get_paper_performance(
    portfolio_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    include_equity_curve: bool = True,
    db: Session = Depends(get_db),
) -> PaperTradingPerformanceResponse:
    return PaperTradingReportService(db).performance(
        portfolio_id,
        date_from=date_from,
        date_to=date_to,
        include_equity_curve=include_equity_curve,
    )


@router.get(
    "/portfolios/{portfolio_id}/equity-curve",
    response_model=list[PaperTradingEquityPoint],
)
def get_paper_equity_curve(
    portfolio_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
) -> list[PaperTradingEquityPoint]:
    return PaperTradingReportService(db).equity_curve(
        portfolio_id,
        date_from=date_from,
        date_to=date_to,
    )


@router.get(
    "/portfolios/{portfolio_id}/contributions",
    response_model=PaperTradingContributionsResponse,
)
def get_paper_contributions(
    portfolio_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 100,
    include_inactive: bool = True,
    db: Session = Depends(get_db),
) -> PaperTradingContributionsResponse:
    return PaperTradingReportService(db).contributions(
        portfolio_id,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        include_inactive=include_inactive,
    )


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
