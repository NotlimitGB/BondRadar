from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


DATA_PIPELINE_STEP_STATUS_SQL = (
    "status in ('pending', 'running', 'completed', 'skipped', 'failed')"
)


class DataPipelineStepRun(Base):
    __tablename__ = "data_pipeline_step_runs"
    __table_args__ = (
        CheckConstraint(
            DATA_PIPELINE_STEP_STATUS_SQL,
            name="data_pipeline_step_status_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        ForeignKey("data_pipeline_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    pipeline_run: Mapped["DataPipelineRun"] = relationship(back_populates="steps")
