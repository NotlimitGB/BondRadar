from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.live_data_readiness import LiveDataReadinessResponse
from app.services.live_data_readiness_service import LiveDataReadinessService


router = APIRouter()


@router.get("/live", response_model=LiveDataReadinessResponse)
def get_live_data_readiness(
    recent_days: int = 7,
    minimum_corporate_bonds: int = 20,
    minimum_bonds_with_recent_market_snapshot: int = 20,
    minimum_bonds_with_recent_features: int = 20,
    minimum_bonds_with_predictions: int = 20,
    include_ofz: bool = False,
    db: Session = Depends(get_db),
) -> LiveDataReadinessResponse:
    return LiveDataReadinessService(db).check(
        recent_days=recent_days,
        minimum_corporate_bonds=minimum_corporate_bonds,
        minimum_bonds_with_recent_market_snapshot=(
            minimum_bonds_with_recent_market_snapshot
        ),
        minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
        minimum_bonds_with_predictions=minimum_bonds_with_predictions,
        include_ofz=include_ofz,
    )
