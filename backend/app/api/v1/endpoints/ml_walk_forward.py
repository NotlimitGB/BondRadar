from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_walk_forward import (
    MLWalkForwardRunRequest,
    MLWalkForwardRunResponse,
)
from app.services.ml_walk_forward_service import MLWalkForwardService


router = APIRouter()


@router.post("/run", response_model=MLWalkForwardRunResponse)
def run_ml_walk_forward(
    request: MLWalkForwardRunRequest,
    db: Session = Depends(get_db),
) -> MLWalkForwardRunResponse:
    return MLWalkForwardService(db).run(request)
