from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.data_readiness import (
    DataReadinessCheckRequest,
    DataReadinessResponse,
)
from app.schemas.financial_report_coverage import FinancialReportCoverageResponse
from app.services.data_readiness_service import DataReadinessService
from app.services.financial_report_coverage_service import FinancialReportCoverageService


router = APIRouter()


@router.get(
    "/financial-reports/coverage",
    response_model=FinancialReportCoverageResponse,
)
def get_financial_report_coverage(
    as_of_date: date | None = None,
    active_only: bool = True,
    stale_after_days: int = Query(default=540, ge=1, le=3650),
    db: Session = Depends(get_db),
) -> FinancialReportCoverageResponse:
    return FinancialReportCoverageService(db).coverage(
        as_of_date=as_of_date,
        active_only=active_only,
        stale_after_days=stale_after_days,
    )


@router.post("/check", response_model=DataReadinessResponse)
def check_data_readiness(
    request: DataReadinessCheckRequest,
    db: Session = Depends(get_db),
) -> DataReadinessResponse:
    return DataReadinessService(db).check(request)
