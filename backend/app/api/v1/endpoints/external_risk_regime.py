from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.external_risk_regime import (
    ExternalRiskRegimeResponse,
    ExternalRiskRegimeUpdateRequest,
)
from app.services.external_risk_regime_service import ExternalRiskRegimeService


router = APIRouter()


@router.get("", response_model=ExternalRiskRegimeResponse)
def get_external_risk_regime(
    db: Session = Depends(get_db),
) -> ExternalRiskRegimeResponse:
    return ExternalRiskRegimeService(db).current()


@router.put("", response_model=ExternalRiskRegimeResponse)
def update_external_risk_regime(
    request: ExternalRiskRegimeUpdateRequest,
    db: Session = Depends(get_db),
) -> ExternalRiskRegimeResponse:
    return ExternalRiskRegimeService(db).update(request)
