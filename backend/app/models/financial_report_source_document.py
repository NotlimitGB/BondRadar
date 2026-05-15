from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


FINANCIAL_REPORT_SOURCE_STATUS_SQL = "status in ('imported', 'linked', 'failed', 'skipped')"


class FinancialReportSourceDocument(Base):
    __tablename__ = "financial_report_source_documents"
    __table_args__ = (
        CheckConstraint(
            FINANCIAL_REPORT_SOURCE_STATUS_SQL,
            name="financial_report_source_status_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    financial_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_reports.id", ondelete="SET NULL"), index=True
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_file_name: Mapped[str | None] = mapped_column(String(255))
    report_type: Mapped[str | None] = mapped_column(String(64))
    period_year: Mapped[int] = mapped_column(nullable=False, index=True)
    period_quarter: Mapped[int | None] = mapped_column(index=True)
    period_start_date: Mapped[date | None] = mapped_column(Date)
    period_end_date: Mapped[date | None] = mapped_column(Date)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    document_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    parse_warnings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    parse_errors: Mapped[list[dict[str, Any]]] = mapped_column(
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

    company: Mapped["Company"] = relationship()
    financial_report: Mapped["FinancialReport | None"] = relationship(
        back_populates="source_documents"
    )
