from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.portfolio_construction import (
    PortfolioConstructionRequest,
    PortfolioConstructionResponse,
)
from app.services.portfolio_construction_service import PortfolioConstructionService


router = APIRouter()


@router.post("/construct", response_model=PortfolioConstructionResponse)
def construct_portfolio(
    request: PortfolioConstructionRequest,
    db: Session = Depends(get_db),
) -> PortfolioConstructionResponse:
    return PortfolioConstructionService(db).construct(request)
