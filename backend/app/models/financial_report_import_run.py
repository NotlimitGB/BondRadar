from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


FINANCIAL_REPORT_IMPORT_STATUS_SQL = (
    "status in ('running', 'completed', 'completed_with_errors', 'failed')"
)
FINANCIAL_REPORT_IMPORT_INPUT_TYPE_SQL = "input_type in ('json', 'csv', 'manual')"


class FinancialReportImportRun(Base):
    __tablename__ = "financial_report_import_runs"
    __table_args__ = (
        CheckConstraint(
            FINANCIAL_REPORT_IMPORT_STATUS_SQL,
            name="financial_report_import_status_allowed",
        ),
        CheckConstraint(
            FINANCIAL_REPORT_IMPORT_INPUT_TYPE_SQL,
            name="financial_report_import_input_type_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    input_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
