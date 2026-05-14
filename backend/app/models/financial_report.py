from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ANALYSIS_SIGNAL_SQL, AnalysisSignal


class FinancialReport(Base):
    __tablename__ = "financial_reports"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "period_year",
            "period_quarter",
            name="financial_reports_company_period_unique",
        ),
        CheckConstraint("period_quarter between 0 and 4", name="period_quarter_range"),
        CheckConstraint(ANALYSIS_SIGNAL_SQL, name="financial_reports_signal_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_year: Mapped[int] = mapped_column(nullable=False, index=True)
    period_quarter: Mapped[int] = mapped_column(nullable=False, default=0)
    revenue: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    ebitda: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    net_debt: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    total_debt: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    cash: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    equity: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    short_term_debt: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    operating_cash_flow: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    net_profit: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    interest_expense: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    debt_to_ebitda: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    interest_coverage: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    source: Mapped[str | None] = mapped_column(String(255))
    signal: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AnalysisSignal.INSUFFICIENT_DATA.value
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

    company: Mapped["Company"] = relationship(back_populates="financial_reports")
    company_scores: Mapped[list["CompanyScore"]] = relationship(back_populates="report")
