from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.strategy_robustness import (
    StrategyRobustnessAnalyzeRequest,
    StrategyRobustnessAnalyzeResponse,
)
from app.services.strategy_robustness_service import StrategyRobustnessService


router = APIRouter()


@router.post("/analyze", response_model=StrategyRobustnessAnalyzeResponse)
def analyze_strategy_robustness(
    request: StrategyRobustnessAnalyzeRequest,
    db: Session = Depends(get_db),
) -> StrategyRobustnessAnalyzeResponse:
    return StrategyRobustnessService(db).analyze(request)
