from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, Integer, JSON, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


ML_MODEL_RUN_STATUS_SQL = "status in ('running', 'completed', 'failed')"


class MLModelRun(Base):
    __tablename__ = "ml_model_runs"
    __table_args__ = (
        CheckConstraint(ML_MODEL_RUN_STATUS_SQL, name="ml_model_runs_status_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    model_type: Mapped[str] = mapped_column(String(64), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    features: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    target: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of_date_from: Mapped[date | None] = mapped_column(Date)
    as_of_date_to: Mapped[date | None] = mapped_column(Date)
    train_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    test_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positive_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    negative_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    feature_importance: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    artifact_path: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    predictions: Mapped[list["MLPrediction"]] = relationship(
        back_populates="model_run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
