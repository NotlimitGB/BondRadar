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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


CREDIT_STATUS_SQL = (
    "credit_status in ('credit_stable', 'credit_watchlist', "
    "'credit_stressed', 'credit_distressed', 'insufficient_data')"
)
RISK_LEVEL_SQL = "risk_level in ('low', 'medium', 'high', 'critical', 'unknown')"
DATA_QUALITY_LEVEL_SQL = (
    "data_quality_level in ('high', 'medium', 'low', 'insufficient')"
)


class CompanyCreditHealthSnapshot(Base):
    __tablename__ = "company_credit_health_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "as_of_date",
            name="company_credit_health_company_as_of_unique",
        ),
        CheckConstraint(CREDIT_STATUS_SQL, name="company_credit_health_status_allowed"),
        CheckConstraint(RISK_LEVEL_SQL, name="company_credit_health_risk_allowed"),
        CheckConstraint(
            DATA_QUALITY_LEVEL_SQL,
            name="company_credit_health_data_quality_allowed",
        ),
        CheckConstraint(
            "credit_health_score between 0 and 100",
            name="company_credit_health_score_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    financial_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_reports.id", ondelete="SET NULL"), index=True
    )
    company_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_scores.id", ondelete="SET NULL"), index=True
    )
    credit_health_score: Mapped[int] = mapped_column(Integer, nullable=False)
    credit_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    data_quality_level: Mapped[str] = mapped_column(String(32), nullable=False)
    debt_to_ebitda: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    interest_coverage: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cash_to_short_term_debt: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    ocf_to_total_debt: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    net_profit_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
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
    risk_factors: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    positive_factors: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    missing_data: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    company: Mapped["Company"] = relationship()
    financial_report: Mapped["FinancialReport | None"] = relationship()
    company_score: Mapped["CompanyScore | None"] = relationship()
