from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, JSON, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


PAPER_TRANSACTION_TYPE_SQL = (
    "transaction_type in ('portfolio_created', 'allocation_increase', "
    "'allocation_decrease', 'allocation_removed', 'rebalance_fee', "
    "'period_return', 'cash_adjustment', 'snapshot')"
)


class PaperPortfolioTransaction(Base):
    __tablename__ = "paper_portfolio_transactions"
    __table_args__ = (
        CheckConstraint(
            PAPER_TRANSACTION_TYPE_SQL,
            name="paper_transaction_type_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("paper_portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="SET NULL"), index=True
    )
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    amount_delta: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    weight_delta: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    portfolio_value_before: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    portfolio_value_after: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    details_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    portfolio: Mapped["PaperPortfolio"] = relationship(back_populates="transactions")
    bond: Mapped["Bond | None"] = relationship()
