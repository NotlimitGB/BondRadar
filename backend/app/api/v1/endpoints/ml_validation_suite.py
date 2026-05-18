from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_validation_suite import (
    MLValidationSuiteRequest,
    MLValidationSuiteResponse,
)
from app.services.ml_validation_suite_service import MLValidationSuiteService


router = APIRouter()


@router.post("/run", response_model=MLValidationSuiteResponse)
def run_ml_validation_suite(
    request: MLValidationSuiteRequest,
    db: Session = Depends(get_db),
) -> MLValidationSuiteResponse:
    return MLValidationSuiteService(db).run(request)
