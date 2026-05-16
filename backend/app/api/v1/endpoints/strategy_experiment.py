from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.strategy_experiment import (
    StrategyExperimentCompareRequest,
    StrategyExperimentCompareResponse,
)
from app.services.strategy_experiment_service import StrategyExperimentService


router = APIRouter()


@router.post("/compare", response_model=StrategyExperimentCompareResponse)
def compare_strategy_experiment(
    request: StrategyExperimentCompareRequest,
    db: Session = Depends(get_db),
) -> StrategyExperimentCompareResponse:
    return StrategyExperimentService(db).compare(request)
