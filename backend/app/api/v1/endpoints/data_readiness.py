from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.data_readiness import (
    DataReadinessCheckRequest,
    DataReadinessResponse,
)
from app.services.data_readiness_service import DataReadinessService


router = APIRouter()


@router.post("/check", response_model=DataReadinessResponse)
def check_data_readiness(
    request: DataReadinessCheckRequest,
    db: Session = Depends(get_db),
) -> DataReadinessResponse:
    return DataReadinessService(db).check(request)
