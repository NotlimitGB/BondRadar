from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_candidate_strategy_promotion import (
    MLCandidateStrategyPromotionRequest,
    MLCandidateStrategyPromotionResponse,
)
from app.services.ml_candidate_strategy_promotion_service import (
    MLCandidateStrategyPromotionService,
)


router = APIRouter()


@router.post("/promote-to-strategy-experiment", response_model=MLCandidateStrategyPromotionResponse)
def promote_ml_candidate_to_strategy_experiment(
    request: MLCandidateStrategyPromotionRequest,
    db: Session = Depends(get_db),
) -> MLCandidateStrategyPromotionResponse:
    return MLCandidateStrategyPromotionService(db).promote(request)
