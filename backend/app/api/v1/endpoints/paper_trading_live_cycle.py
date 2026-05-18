from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.paper_trading_live_cycle import (
    LivePaperCycleRunRead,
    LivePaperCycleRunRequest,
    LivePaperCycleRunResponse,
)
from app.services.paper_trading_live_cycle_service import LivePaperCycleService


router = APIRouter()


@router.post("/run", response_model=LivePaperCycleRunResponse)
def run_live_paper_cycle(
    request: LivePaperCycleRunRequest,
    db: Session = Depends(get_db),
) -> LivePaperCycleRunResponse:
    return LivePaperCycleService(db).run(request)


@router.get("", response_model=list[LivePaperCycleRunRead])
def list_live_paper_cycles(
    limit: int = 100,
    db: Session = Depends(get_db),
) -> list[LivePaperCycleRunRead]:
    return [
        LivePaperCycleRunRead.model_validate(cycle)
        for cycle in LivePaperCycleService(db).list_runs(limit=limit)
    ]


@router.get("/{cycle_run_id}", response_model=LivePaperCycleRunRead)
def get_live_paper_cycle(
    cycle_run_id: int,
    db: Session = Depends(get_db),
) -> LivePaperCycleRunRead:
    return LivePaperCycleRunRead.model_validate(
        LivePaperCycleService(db).get_run(cycle_run_id)
    )
