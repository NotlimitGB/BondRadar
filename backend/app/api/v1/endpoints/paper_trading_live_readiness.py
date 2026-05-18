from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.paper_trading_live_readiness import (
    LivePaperReadinessRequest,
    LivePaperReadinessResponse,
)
from app.services.paper_trading_live_readiness_service import (
    LivePaperReadinessService,
)


router = APIRouter()


@router.post("/readiness", response_model=LivePaperReadinessResponse)
def check_live_paper_readiness(
    request: LivePaperReadinessRequest,
    db: Session = Depends(get_db),
) -> LivePaperReadinessResponse:
    return LivePaperReadinessService(db).check(request)
