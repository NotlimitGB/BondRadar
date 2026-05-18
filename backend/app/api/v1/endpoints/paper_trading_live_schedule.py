from datetime import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.paper_trading_live_schedule import (
    LivePaperScheduleCreate,
    LivePaperScheduleRead,
    LivePaperScheduleRunDueRequest,
    LivePaperScheduleRunDueResponse,
    LivePaperScheduleUpdate,
    LivePaperScheduledRunItem,
)
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService


router = APIRouter()


@router.post("/run-due", response_model=LivePaperScheduleRunDueResponse)
def run_due_live_paper_schedules(
    request: LivePaperScheduleRunDueRequest,
    db: Session = Depends(get_db),
) -> LivePaperScheduleRunDueResponse:
    return LivePaperScheduleService(db).run_due(request)


@router.post("", response_model=LivePaperScheduleRead)
def create_live_paper_schedule(
    request: LivePaperScheduleCreate,
    db: Session = Depends(get_db),
) -> LivePaperScheduleRead:
    return LivePaperScheduleRead.model_validate(
        LivePaperScheduleService(db).create(request)
    )


@router.get("", response_model=list[LivePaperScheduleRead])
def list_live_paper_schedules(
    limit: int = 100,
    status: str | None = None,
    db: Session = Depends(get_db),
) -> list[LivePaperScheduleRead]:
    return [
        LivePaperScheduleRead.model_validate(schedule)
        for schedule in LivePaperScheduleService(db).list_schedules(
            limit=limit,
            status_filter=status,
        )
    ]


@router.patch("/{schedule_id}", response_model=LivePaperScheduleRead)
def update_live_paper_schedule(
    schedule_id: int,
    request: LivePaperScheduleUpdate,
    db: Session = Depends(get_db),
) -> LivePaperScheduleRead:
    return LivePaperScheduleRead.model_validate(
        LivePaperScheduleService(db).update(schedule_id, request)
    )


@router.post("/{schedule_id}/run", response_model=LivePaperScheduledRunItem)
def run_live_paper_schedule_once(
    schedule_id: int,
    now: datetime | None = None,
    db: Session = Depends(get_db),
) -> LivePaperScheduledRunItem:
    return LivePaperScheduleService(db).run_schedule_once(schedule_id, now=now)


@router.get("/{schedule_id}", response_model=LivePaperScheduleRead)
def get_live_paper_schedule(
    schedule_id: int,
    db: Session = Depends(get_db),
) -> LivePaperScheduleRead:
    return LivePaperScheduleRead.model_validate(
        LivePaperScheduleService(db).get_schedule(schedule_id)
    )
