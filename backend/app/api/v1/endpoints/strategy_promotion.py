from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.strategy_promotion import (
    StrategyPromotionRequest,
    StrategyPromotionResponse,
)
from app.services.strategy_promotion_service import StrategyPromotionService


router = APIRouter()


@router.post(
    "/best-experiment-to-paper-scenario",
    response_model=StrategyPromotionResponse,
)
def promote_best_experiment_to_paper_scenario(
    request: StrategyPromotionRequest,
    db: Session = Depends(get_db),
) -> StrategyPromotionResponse:
    return StrategyPromotionService(db).promote_best_experiment_to_paper_scenario(
        request
    )
