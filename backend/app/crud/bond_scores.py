from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_score import BondScore
from app.models.enums import AnalysisSignal
from app.schemas.bond_score import BondScoreCreate, BondScoreUpdate


def get_bond_score(db: Session, score_id: int) -> BondScore | None:
    return db.get(BondScore, score_id)


def list_bond_scores(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 100,
    bond_id: int | None = None,
    as_of_date: date | None = None,
    signal: AnalysisSignal | None = None,
) -> list[BondScore]:
    stmt = select(BondScore)
    if bond_id is not None:
        stmt = stmt.where(BondScore.bond_id == bond_id)
    if as_of_date is not None:
        stmt = stmt.where(BondScore.as_of_date == as_of_date)
    if signal:
        stmt = stmt.where(BondScore.signal == signal.value)
    stmt = (
        stmt.order_by(BondScore.as_of_date.desc(), BondScore.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def create_bond_score(db: Session, score_in: BondScoreCreate) -> BondScore:
    score = BondScore(**score_in.model_dump())
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def update_bond_score(
    db: Session, score: BondScore, score_in: BondScoreUpdate
) -> BondScore:
    for field, value in score_in.model_dump(exclude_unset=True).items():
        setattr(score, field, value)
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def delete_bond_score(db: Session, score: BondScore) -> None:
    db.delete(score)
    db.commit()

