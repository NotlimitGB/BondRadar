from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonRequest,
    MLCandidateComparisonResponse,
)
from app.services.ml_candidate_comparison_service import (
    MLCandidateComparisonService,
)


router = APIRouter()


@router.post("/compare", response_model=MLCandidateComparisonResponse)
def compare_ml_candidates(
    request: MLCandidateComparisonRequest,
    db: Session = Depends(get_db),
) -> MLCandidateComparisonResponse:
    return MLCandidateComparisonService(db).compare(request)
