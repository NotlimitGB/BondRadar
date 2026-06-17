from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.controlled_financial_statement_value import (
    ControlledFinancialStatementValue,
)
from app.schemas.controlled_financial_statement_value import (
    ControlledFinancialStatementValueCreate,
)


def get_by_natural_key_sha256(
    db: Session,
    natural_key_sha256: str,
) -> ControlledFinancialStatementValue | None:
    return db.execute(
        select(ControlledFinancialStatementValue).where(
            ControlledFinancialStatementValue.natural_key_sha256
            == natural_key_sha256
        )
    ).scalar_one_or_none()


def upsert_preview_payload(
    db: Session,
    value_in: ControlledFinancialStatementValueCreate,
) -> ControlledFinancialStatementValue:
    existing = get_by_natural_key_sha256(db, value_in.natural_key_sha256)
    if existing is None:
        value = ControlledFinancialStatementValue(**value_in.model_dump())
        db.add(value)
        db.flush()
        return value
    for field, value in value_in.model_dump().items():
        setattr(existing, field, value)
    db.add(existing)
    db.flush()
    return existing
