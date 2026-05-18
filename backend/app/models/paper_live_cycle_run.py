from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, JSON, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


LIVE_CYCLE_STATUSES = {"running", "completed", "blocked", "failed"}
LIVE_CYCLE_MODES = {"manual"}

LIVE_CYCLE_STATUS_SQL = "status in ('running', 'completed', 'blocked', 'failed')"
LIVE_CYCLE_MODE_SQL = "mode in ('manual')"


class PaperLiveCycleRun(Base):
    __tablename__ = "paper_live_cycle_runs"
    __table_args__ = (
        CheckConstraint(
            LIVE_CYCLE_STATUS_SQL,
            name="paper_live_cycle_status_allowed",
        ),
        CheckConstraint(
            LIVE_CYCLE_MODE_SQL,
            name="paper_live_cycle_mode_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",
        server_default="manual",
    )
    portfolio_id: Mapped[int | None] = mapped_column(
        ForeignKey("paper_portfolios.id", ondelete="SET NULL"),
        index=True,
    )
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey(
            "paper_live_schedules.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_paper_live_cycle_schedule",
        ),
        index=True,
    )
    client_cycle_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )
    as_of_date: Mapped[date | None] = mapped_column(Date, index=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )

    readiness_status: Mapped[str | None] = mapped_column(String(32))
    selected_model_run_id: Mapped[int | None] = mapped_column(index=True)
    selected_model_run_ids_json: Mapped[list[int] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )

    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    readiness_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    mark_period_result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    rebalance_result_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    warnings_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )
    errors_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    portfolio: Mapped["PaperPortfolio | None"] = relationship()
    schedule: Mapped["PaperLiveSchedule | None"] = relationship(
        foreign_keys=[schedule_id]
    )
