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


ML_PREDICTION_LABEL_SQL = (
    "predicted_label in ('predicted_positive_return', "
    "'predicted_negative_return')"
)


class MLPrediction(Base):
    __tablename__ = "ml_predictions"
    __table_args__ = (
        UniqueConstraint(
            "model_run_id",
            "feature_snapshot_id",
            name="ml_predictions_run_feature_unique",
        ),
        CheckConstraint(
            ML_PREDICTION_LABEL_SQL,
            name="ml_predictions_predicted_label_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    model_run_id: Mapped[int] = mapped_column(
        ForeignKey("ml_model_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("bond_feature_snapshots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    probability_positive: Mapped[Decimal] = mapped_column(
        Numeric(12, 10), nullable=False
    )
    predicted_label: Mapped[str] = mapped_column(String(64), nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    model_run: Mapped["MLModelRun"] = relationship(back_populates="predictions")
    feature_snapshot: Mapped["BondFeatureSnapshot"] = relationship()
    bond: Mapped["Bond"] = relationship()
    company: Mapped["Company"] = relationship()
