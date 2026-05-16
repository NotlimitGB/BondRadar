from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PaperPortfolioSnapshot(Base):
    __tablename__ = "paper_portfolio_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "portfolio_id",
            "as_of_date",
            name="paper_snapshot_date_unique",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("paper_portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    portfolio_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    allocated_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    allocated_weight: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    unallocated_weight: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    positions_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active_positions_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cumulative_return: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    period_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    portfolio: Mapped["PaperPortfolio"] = relationship(back_populates="snapshots")
