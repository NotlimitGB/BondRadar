from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.label_quality import (
    LabelQualityReportRequest,
    LabelQualityReportResponse,
)
from app.services.label_quality_service import LabelQualityService


router = APIRouter()


@router.post("/report", response_model=LabelQualityReportResponse)
def report_label_quality(
    request: LabelQualityReportRequest,
    db: Session = Depends(get_db),
) -> LabelQualityReportResponse:
    return LabelQualityService(db).report(request)
