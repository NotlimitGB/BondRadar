from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.market_history_quality import (
    MarketHistoryQualityAuditRequest,
    MarketHistoryQualityAuditResponse,
)
from app.services.market_history_quality_service import MarketHistoryQualityService


router = APIRouter()


@router.post("/audit", response_model=MarketHistoryQualityAuditResponse)
def audit_market_history_quality(
    request: MarketHistoryQualityAuditRequest,
    db: Session = Depends(get_db),
) -> MarketHistoryQualityAuditResponse:
    return MarketHistoryQualityService(db).audit(request)
