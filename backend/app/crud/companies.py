from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.schemas.company import CompanyCreate, CompanyUpdate


def get_company(db: Session, company_id: int) -> Company | None:
    return db.get(Company, company_id)


def list_companies(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 100,
    query: str | None = None,
    signal: AnalysisSignal | None = None,
) -> list[Company]:
    stmt = select(Company)
    if query:
        like_query = f"%{query}%"
        stmt = stmt.where(
            or_(
                Company.name.ilike(like_query),
                Company.ticker.ilike(like_query),
                Company.inn.ilike(like_query),
            )
        )
    if signal:
        stmt = stmt.where(Company.signal == signal.value)
    stmt = stmt.order_by(Company.name).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars())


def create_company(db: Session, company_in: CompanyCreate) -> Company:
    company = Company(**company_in.model_dump())
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def update_company(
    db: Session, company: Company, company_in: CompanyUpdate
) -> Company:
    for field, value in company_in.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def delete_company(db: Session, company: Company) -> None:
    db.delete(company)
    db.commit()

