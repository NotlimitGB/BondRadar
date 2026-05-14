from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


DATASET_BUILD_STATUS_SQL = (
    "status in ('running', 'completed', 'failed', 'completed_with_errors')"
)


class DatasetBuildRun(Base):
    __tablename__ = "dataset_build_runs"
    __table_args__ = (
        CheckConstraint(DATASET_BUILD_STATUS_SQL, name="dataset_build_runs_status_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    as_of_date_from: Mapped[date] = mapped_column(Date, nullable=False)
    as_of_date_to: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    features_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    labels_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    labels_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
