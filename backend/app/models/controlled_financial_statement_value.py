from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ControlledFinancialStatementValue(Base):
    __tablename__ = "controlled_financial_statement_values"
    __table_args__ = (
        UniqueConstraint(
            "natural_key_sha256",
            name="uq_controlled_financial_statement_values_natural_key_sha256",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[str] = mapped_column(String(32), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    report_year: Mapped[int] = mapped_column(nullable=False, index=True)
    report_standard: Mapped[str] = mapped_column(String(32), nullable=False)
    target_type: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_role: Mapped[str] = mapped_column(String(32), nullable=False)
    metric_name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    statement_page: Mapped[int] = mapped_column(nullable=False)
    page_number: Mapped[int] = mapped_column(nullable=False)
    value_2025: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    value_2024: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    raw_value_2025: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value_2024: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_line: Mapped[str] = mapped_column(Text, nullable=False)
    note_reference: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_rows_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    natural_key: Mapped[str] = mapped_column(Text, nullable=False)
    natural_key_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    row_checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
