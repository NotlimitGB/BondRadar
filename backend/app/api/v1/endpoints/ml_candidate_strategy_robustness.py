from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessRequest,
    MLCandidateStrategyRobustnessResponse,
)
from app.services.ml_candidate_strategy_robustness_service import (
    MLCandidateStrategyRobustnessService,
)


router = APIRouter()


@router.post(
    "/promote-to-strategy-robustness",
    response_model=MLCandidateStrategyRobustnessResponse,
)
def promote_ml_candidate_to_strategy_robustness(
    request: MLCandidateStrategyRobustnessRequest,
    db: Session = Depends(get_db),
) -> MLCandidateStrategyRobustnessResponse:
    return MLCandidateStrategyRobustnessService(db).analyze(request)
