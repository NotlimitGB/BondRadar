from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.paper_trading_live_pilot import (
    LivePaperPilotBootstrapRequest,
    LivePaperPilotBootstrapResponse,
)
from app.services.paper_trading_live_pilot_service import (
    LivePaperPilotBootstrapService,
)


router = APIRouter()


@router.post("/bootstrap", response_model=LivePaperPilotBootstrapResponse)
def bootstrap_live_paper_pilot(
    request: LivePaperPilotBootstrapRequest,
    db: Session = Depends(get_db),
) -> LivePaperPilotBootstrapResponse:
    return LivePaperPilotBootstrapService(db).bootstrap(request)
