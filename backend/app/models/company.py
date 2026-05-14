from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ANALYSIS_SIGNAL_SQL, AnalysisSignal


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(ANALYSIS_SIGNAL_SQL, name="companies_signal_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    sector: Mapped[str | None] = mapped_column(String(128))
    inn: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    country: Mapped[str] = mapped_column(String(64), nullable=False, default="RU")
    credit_rating: Mapped[str | None] = mapped_column(String(32))
    signal: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AnalysisSignal.INSUFFICIENT_DATA.value
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    bonds: Mapped[list["Bond"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    financial_reports: Mapped[list["FinancialReport"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    scores: Mapped[list["CompanyScore"]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
