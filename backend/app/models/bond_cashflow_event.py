from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


CASHFLOW_EVENT_TYPE_SQL = (
    "event_type in ('coupon', 'amortization', 'redemption', "
    "'offer_redemption', 'other')"
)


class BondCashflowEvent(Base):
    __tablename__ = "bond_cashflow_events"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            "event_date",
            "event_type",
            "source",
            name="bond_cashflow_events_bond_date_type_source_unique",
        ),
        CheckConstraint(
            CASHFLOW_EVENT_TYPE_SQL,
            name="bond_cashflow_events_type_allowed",
        ),
        CheckConstraint(
            "amount is null or amount >= 0",
            name="bond_cashflow_events_amount_non_negative",
        ),
        CheckConstraint(
            "amount_percent is null or amount_percent >= 0",
            name="bond_cashflow_events_amount_percent_non_negative",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    amount_percent: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="manual", server_default="manual"
    )
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    bond: Mapped["Bond"] = relationship()
