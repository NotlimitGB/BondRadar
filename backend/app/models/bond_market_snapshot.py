from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BondMarketSnapshot(Base):
    __tablename__ = "bond_market_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            "trade_date",
            "source",
            name="bond_market_snapshots_bond_date_source_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    clean_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    dirty_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    nkd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    yield_to_maturity: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    duration_years: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    liquidity_score: Mapped[int | None] = mapped_column(Integer)
    spread_to_ofz: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
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
