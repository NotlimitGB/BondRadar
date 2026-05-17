from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.cashflow_quality import (
    CashflowQualityAuditRequest,
    CashflowQualityAuditResponse,
)
from app.services.cashflow_quality_service import CashflowQualityService


router = APIRouter()


@router.post("/audit", response_model=CashflowQualityAuditResponse)
def audit_cashflow_quality(
    request: CashflowQualityAuditRequest,
    db: Session = Depends(get_db),
) -> CashflowQualityAuditResponse:
    return CashflowQualityService(db).audit(request)
