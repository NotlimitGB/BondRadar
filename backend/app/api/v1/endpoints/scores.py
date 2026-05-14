from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.bond_score import ScoreRecalculationRead
from app.services.bond_score_service import BondScoreService


router = APIRouter()


@router.post("/recalculate-all", response_model=ScoreRecalculationRead)
def recalculate_all_scores(
    db: Session = Depends(get_db),
) -> ScoreRecalculationRead:
    return BondScoreService(db).recalculate_all()

