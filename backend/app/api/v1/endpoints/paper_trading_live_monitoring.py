from datetime import date, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.paper_trading_live_monitoring import (
    LivePaperCycleMonitoringListResponse,
    LivePaperMonitoringOverviewResponse,
    LivePaperPortfolioMonitoringResponse,
    LivePaperScheduleMonitoringResponse,
)
from app.services.paper_trading_live_monitoring_service import (
    LivePaperMonitoringService,
)


router = APIRouter()


@router.get("/overview", response_model=LivePaperMonitoringOverviewResponse)
def get_live_paper_monitoring_overview(
    include_schedules: bool = True,
    include_portfolios: bool = True,
    include_recent_cycles: bool = True,
    include_alerts: bool = True,
    schedule_limit: int = 20,
    portfolio_limit: int = 20,
    cycle_limit: int = 20,
    now: datetime | None = None,
    db: Session = Depends(get_db),
) -> LivePaperMonitoringOverviewResponse:
    return LivePaperMonitoringService(db).overview(
        include_schedules=include_schedules,
        include_portfolios=include_portfolios,
        include_recent_cycles=include_recent_cycles,
        include_alerts=include_alerts,
        schedule_limit=schedule_limit,
        portfolio_limit=portfolio_limit,
        cycle_limit=cycle_limit,
        now=now,
    )


@router.get(
    "/schedules/{schedule_id}",
    response_model=LivePaperScheduleMonitoringResponse,
)
def get_live_paper_schedule_monitoring(
    schedule_id: int,
    include_recent_cycles: bool = True,
    include_alerts: bool = True,
    cycle_limit: int = 20,
    now: datetime | None = None,
    db: Session = Depends(get_db),
) -> LivePaperScheduleMonitoringResponse:
    return LivePaperMonitoringService(db).schedule_detail(
        schedule_id,
        include_recent_cycles=include_recent_cycles,
        include_alerts=include_alerts,
        cycle_limit=cycle_limit,
        now=now,
    )


@router.get(
    "/portfolios/{portfolio_id}",
    response_model=LivePaperPortfolioMonitoringResponse,
)
def get_live_paper_portfolio_monitoring(
    portfolio_id: int,
    date_from: date | None = None,
    date_to: date | None = None,
    include_performance: bool = True,
    include_equity_curve: bool = True,
    include_contributions: bool = True,
    include_positions: bool = True,
    include_recent_cycles: bool = True,
    cycle_limit: int = 20,
    contribution_limit: int = 50,
    db: Session = Depends(get_db),
) -> LivePaperPortfolioMonitoringResponse:
    return LivePaperMonitoringService(db).portfolio_detail(
        portfolio_id,
        date_from=date_from,
        date_to=date_to,
        include_performance=include_performance,
        include_equity_curve=include_equity_curve,
        include_contributions=include_contributions,
        include_positions=include_positions,
        include_recent_cycles=include_recent_cycles,
        cycle_limit=cycle_limit,
        contribution_limit=contribution_limit,
    )


@router.get("/cycles", response_model=LivePaperCycleMonitoringListResponse)
def list_live_paper_cycle_monitoring(
    schedule_id: int | None = None,
    portfolio_id: int | None = None,
    status: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> LivePaperCycleMonitoringListResponse:
    return LivePaperMonitoringService(db).cycle_list(
        schedule_id=schedule_id,
        portfolio_id=portfolio_id,
        status_filter=status,
        limit=limit,
    )
