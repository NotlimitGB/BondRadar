from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ANALYSIS_SIGNAL_SQL, AnalysisSignal


class BondScore(Base):
    __tablename__ = "bond_scores"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            "as_of_date",
            "source",
            name="bond_scores_bond_snapshot_unique",
        ),
        CheckConstraint("score between 0 and 100", name="bond_score_range"),
        CheckConstraint(ANALYSIS_SIGNAL_SQL, name="bond_scores_signal_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_scores.id", ondelete="SET NULL"), index=True
    )
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    signal: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AnalysisSignal.INSUFFICIENT_DATA.value
    )
    factors: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    explanation: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    yield_score: Mapped[int | None] = mapped_column(Integer)
    duration_score: Mapped[int | None] = mapped_column(Integer)
    liquidity_score: Mapped[int | None] = mapped_column(Integer)
    spread_score: Mapped[int | None] = mapped_column(Integer)
    risk_penalty: Mapped[int | None] = mapped_column(Integer)
    final_bond_score: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str | None] = mapped_column(Text)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    bond: Mapped["Bond"] = relationship(back_populates="scores")
    company_score: Mapped["CompanyScore | None"] = relationship()
