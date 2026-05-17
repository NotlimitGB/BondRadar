from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_prediction_quality import (
    MLPredictionQualityReportRequest,
    MLPredictionQualityReportResponse,
)
from app.services.ml_prediction_quality_service import MLPredictionQualityService


router = APIRouter()


@router.post("/report", response_model=MLPredictionQualityReportResponse)
def report_ml_prediction_quality(
    request: MLPredictionQualityReportRequest,
    db: Session = Depends(get_db),
) -> MLPredictionQualityReportResponse:
    return MLPredictionQualityService(db).report(request)
