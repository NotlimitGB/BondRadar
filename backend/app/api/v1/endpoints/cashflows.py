from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.cashflow import (
    BondCashflowEventCreate,
    BondCashflowEventRead,
    BondTotalReturnLabelBuildRequest,
    BondTotalReturnLabelBuildResult,
)
from app.services.bond_cashflow_service import BondCashflowService
from app.services.total_return_label_service import TotalReturnLabelService


router = APIRouter()


@router.post("/events", response_model=BondCashflowEventRead)
def create_cashflow_event(
    event_in: BondCashflowEventCreate,
    db: Session = Depends(get_db),
) -> BondCashflowEventRead:
    return BondCashflowService(db).create_or_update_event(event_in)


@router.get("/events", response_model=list[BondCashflowEventRead])
def list_cashflow_events(
    bond_id: int | None = Query(default=None, ge=1),
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=10000),
    db: Session = Depends(get_db),
) -> list[BondCashflowEventRead]:
    return BondCashflowService(db).list_events(
        bond_id=bond_id,
        date_from=date_from,
        date_to=date_to,
        event_type=event_type,
        source=source,
        limit=limit,
    )


@router.post("/labels/build", response_model=BondTotalReturnLabelBuildResult)
def build_total_return_labels(
    request: BondTotalReturnLabelBuildRequest,
    db: Session = Depends(get_db),
) -> BondTotalReturnLabelBuildResult:
    return TotalReturnLabelService(db).build_labels(request)
