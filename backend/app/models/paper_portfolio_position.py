from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, String, UniqueConstraint, func, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class PaperPortfolioPosition(Base):
    __tablename__ = "paper_portfolio_positions"
    __table_args__ = (
        UniqueConstraint("portfolio_id", "bond_id", name="paper_position_bond_unique"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("paper_portfolios.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int | None] = mapped_column(
        ForeignKey("companies.id", ondelete="SET NULL"), index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    allocation_weight: Mapped[Decimal] = mapped_column(Numeric(12, 10), nullable=False)
    allocation_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    probability_positive: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    predicted_label: Mapped[str | None] = mapped_column(String(64))
    yield_to_maturity: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    liquidity_score: Mapped[int | None] = mapped_column(Integer)
    decision_status: Mapped[str | None] = mapped_column(String(32))
    risk_level: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
        index=True,
    )
    source_model_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ml_model_runs.id", ondelete="SET NULL"), index=True
    )
    source_prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ml_predictions.id", ondelete="SET NULL"), index=True
    )
    source_details_json: Mapped[dict[str, Any]] = mapped_column(
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

    portfolio: Mapped["PaperPortfolio"] = relationship(back_populates="positions")
    bond: Mapped["Bond"] = relationship()
    company: Mapped["Company | None"] = relationship()
    source_model_run: Mapped["MLModelRun | None"] = relationship()
    source_prediction: Mapped["MLPrediction | None"] = relationship()
