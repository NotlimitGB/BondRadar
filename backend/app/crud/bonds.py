from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.enums import AnalysisSignal
from app.schemas.bond import BondCreate, BondUpdate


def get_bond(db: Session, bond_id: int) -> Bond | None:
    return db.get(Bond, bond_id)


def list_bonds(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 100,
    company_id: int | None = None,
    signal: AnalysisSignal | None = None,
) -> list[Bond]:
    stmt = select(Bond)
    if company_id is not None:
        stmt = stmt.where(Bond.company_id == company_id)
    if signal:
        stmt = stmt.where(Bond.signal == signal.value)
    stmt = stmt.order_by(Bond.name).offset(skip).limit(limit)
    return list(db.execute(stmt).scalars())


def create_bond(db: Session, bond_in: BondCreate) -> Bond:
    bond = Bond(**bond_in.model_dump())
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def update_bond(db: Session, bond: Bond, bond_in: BondUpdate) -> Bond:
    for field, value in bond_in.model_dump(exclude_unset=True).items():
        setattr(bond, field, value)
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def delete_bond(db: Session, bond: Bond) -> None:
    db.delete(bond)
    db.commit()

