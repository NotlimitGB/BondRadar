from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


PAPER_PORTFOLIO_STATUS_SQL = "status in ('active', 'archived')"


class PaperPortfolio(Base):
    __tablename__ = "paper_portfolios"
    __table_args__ = (
        CheckConstraint(
            PAPER_PORTFOLIO_STATUS_SQL,
            name="paper_portfolio_status_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="active",
        server_default="active",
        index=True,
    )
    base_currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="RUB",
        server_default="RUB",
    )
    initial_capital: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    cash_balance: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    current_value: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    model_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ml_model_runs.id", ondelete="SET NULL"), index=True
    )
    return_method: Mapped[str | None] = mapped_column(String(32))
    horizon_days: Mapped[int | None] = mapped_column(Integer)
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    last_rebalanced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_rebalance_as_of_date: Mapped[date | None] = mapped_column(Date)
    last_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model_run: Mapped["MLModelRun | None"] = relationship()
    positions: Mapped[list["PaperPortfolioPosition"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    transactions: Mapped[list["PaperPortfolioTransaction"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    snapshots: Mapped[list["PaperPortfolioSnapshot"]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
