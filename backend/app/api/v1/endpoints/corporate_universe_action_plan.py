from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.corporate_universe_action_plan import (
    CorporateUniverseActionPlanResponse,
)
from app.services.corporate_universe_action_plan_service import (
    CorporateUniverseActionPlanService,
)


router = APIRouter()


@router.get(
    "/corporate-universe/action-plan",
    response_model=CorporateUniverseActionPlanResponse,
)
def get_corporate_universe_action_plan(
    board: str = "TQCB",
    minimum_corporate_bonds: int = 20,
    include_ofz: bool = False,
    active_only: bool = True,
    create_missing_companies: bool = True,
    rebuild_existing: bool = False,
    max_pages: int = 100,
    page_size: int = 100,
    sample_limit: int = 20,
    db: Session = Depends(get_db),
) -> CorporateUniverseActionPlanResponse:
    return CorporateUniverseActionPlanService(db).plan(
        board=board,
        minimum_corporate_bonds=minimum_corporate_bonds,
        include_ofz=include_ofz,
        active_only=active_only,
        create_missing_companies=create_missing_companies,
        rebuild_existing=rebuild_existing,
        max_pages=max_pages,
        page_size=page_size,
        sample_limit=sample_limit,
    )
