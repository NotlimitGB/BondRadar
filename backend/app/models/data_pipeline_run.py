from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


DATA_PIPELINE_RUN_STATUS_SQL = (
    "status in ('running', 'completed', 'completed_with_errors', 'failed')"
)
DATA_PIPELINE_MODE_SQL = "mode in ('manual', 'scheduled', 'demo', 'test')"


class DataPipelineRun(Base):
    __tablename__ = "data_pipeline_runs"
    __table_args__ = (
        CheckConstraint(
            DATA_PIPELINE_RUN_STATUS_SQL,
            name="data_pipeline_run_status_allowed",
        ),
        CheckConstraint(DATA_PIPELINE_MODE_SQL, name="data_pipeline_mode_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    bond_ids_json: Mapped[list[int] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    company_ids_json: Mapped[list[int] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    return_methods_json: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    params_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    steps: Mapped[list["DataPipelineStepRun"]] = relationship(
        back_populates="pipeline_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DataPipelineStepRun.id",
    )
