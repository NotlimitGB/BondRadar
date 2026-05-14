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


class CompanyScore(Base):
    __tablename__ = "company_scores"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "as_of_date",
            "source",
            name="company_scores_company_snapshot_unique",
        ),
        CheckConstraint("score between 0 and 100", name="company_score_range"),
        CheckConstraint(ANALYSIS_SIGNAL_SQL, name="company_scores_signal_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    report_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_reports.id", ondelete="SET NULL"), index=True
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
    debt_score: Mapped[int | None] = mapped_column(Integer)
    profitability_score: Mapped[int | None] = mapped_column(Integer)
    liquidity_score: Mapped[int | None] = mapped_column(Integer)
    cashflow_score: Mapped[int | None] = mapped_column(Integer)
    stability_score: Mapped[int | None] = mapped_column(Integer)
    final_company_score: Mapped[int | None] = mapped_column(Integer)
    risk_level: Mapped[str | None] = mapped_column(String(32))
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

    company: Mapped["Company"] = relationship(back_populates="scores")
    report: Mapped["FinancialReport | None"] = relationship(
        back_populates="company_scores"
    )
