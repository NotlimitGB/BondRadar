from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.schemas.company_score import CompanyScoreCreate, CompanyScoreUpdate


def get_company_score(db: Session, score_id: int) -> CompanyScore | None:
    return db.get(CompanyScore, score_id)


def list_company_scores(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 100,
    company_id: int | None = None,
    as_of_date: date | None = None,
    signal: AnalysisSignal | None = None,
) -> list[CompanyScore]:
    stmt = select(CompanyScore)
    if company_id is not None:
        stmt = stmt.where(CompanyScore.company_id == company_id)
    if as_of_date is not None:
        stmt = stmt.where(CompanyScore.as_of_date == as_of_date)
    if signal:
        stmt = stmt.where(CompanyScore.signal == signal.value)
    stmt = (
        stmt.order_by(CompanyScore.as_of_date.desc(), CompanyScore.id.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def create_company_score(
    db: Session, score_in: CompanyScoreCreate
) -> CompanyScore:
    score = CompanyScore(**score_in.model_dump())
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def update_company_score(
    db: Session, score: CompanyScore, score_in: CompanyScoreUpdate
) -> CompanyScore:
    for field, value in score_in.model_dump(exclude_unset=True).items():
        setattr(score, field, value)
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def delete_company_score(db: Session, score: CompanyScore) -> None:
    db.delete(score)
    db.commit()

