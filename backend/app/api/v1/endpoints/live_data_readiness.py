from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.live_data_action_plan import LiveDataActionPlanResponse
from app.schemas.live_data_readiness import LiveDataReadinessResponse
from app.services.live_data_action_plan_service import LiveDataActionPlanService
from app.services.live_data_readiness_service import LiveDataReadinessService


router = APIRouter()


@router.get("/live/action-plan", response_model=LiveDataActionPlanResponse)
def get_live_data_action_plan(
    recent_days: int = 7,
    minimum_corporate_bonds: int = 20,
    minimum_bonds_with_recent_market_snapshot: int = 20,
    minimum_bonds_with_recent_features: int = 20,
    minimum_bonds_with_predictions: int = 20,
    include_ofz: bool = False,
    date_from: date | None = None,
    date_to: date | None = None,
    horizon_days: int = 30,
    mode: str = "manual",
    moex_board: str = "TQCB",
    return_method: str = "risk_adjusted",
    allow_readiness_warning: bool = True,
    fail_on_not_ready: bool = True,
    include_ml_training: bool = True,
    include_predictions: bool = True,
    include_evaluation: bool = True,
    rebuild_existing: bool = False,
    transaction_cost_rate: Decimal = Decimal("0.001"),
    db: Session = Depends(get_db),
) -> LiveDataActionPlanResponse:
    return LiveDataActionPlanService(db).plan(
        recent_days=recent_days,
        minimum_corporate_bonds=minimum_corporate_bonds,
        minimum_bonds_with_recent_market_snapshot=(
            minimum_bonds_with_recent_market_snapshot
        ),
        minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
        minimum_bonds_with_predictions=minimum_bonds_with_predictions,
        include_ofz=include_ofz,
        date_from=date_from,
        date_to=date_to,
        horizon_days=horizon_days,
        mode=mode,
        moex_board=moex_board,
        return_method=return_method,
        allow_readiness_warning=allow_readiness_warning,
        fail_on_not_ready=fail_on_not_ready,
        include_ml_training=include_ml_training,
        include_predictions=include_predictions,
        include_evaluation=include_evaluation,
        rebuild_existing=rebuild_existing,
        transaction_cost_rate=transaction_cost_rate,
    )


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
