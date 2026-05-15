from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.strategy_backtest import (
    StrategyBacktestRequest,
    StrategyBacktestResponse,
)
from app.services.strategy_backtest_service import StrategyBacktestService


router = APIRouter()


@router.post("/run", response_model=StrategyBacktestResponse)
def run_strategy_backtest(
    request: StrategyBacktestRequest,
    db: Session = Depends(get_db),
) -> StrategyBacktestResponse:
    return StrategyBacktestService(db).run(request)
