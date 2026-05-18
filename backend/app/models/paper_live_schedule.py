from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, JSON, String, func, false
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


LIVE_SCHEDULE_STATUSES = {"active", "paused", "archived"}
LIVE_SCHEDULE_MODES = {"manual_cycle"}

LIVE_SCHEDULE_STATUS_SQL = "status in ('active', 'paused', 'archived')"
LIVE_SCHEDULE_MODE_SQL = "mode in ('manual_cycle')"
LIVE_SCHEDULE_INTERVAL_SQL = "interval_days >= 1"
LIVE_SCHEDULE_RUN_COUNT_SQL = "run_count >= 0"
LIVE_SCHEDULE_MAX_RUNS_SQL = "max_runs is null or max_runs >= 1"


class PaperLiveSchedule(Base):
    __tablename__ = "paper_live_schedules"
    __table_args__ = (
        CheckConstraint(
            LIVE_SCHEDULE_STATUS_SQL,
            name="paper_live_schedule_status_allowed",
        ),
        CheckConstraint(
            LIVE_SCHEDULE_MODE_SQL,
            name="paper_live_schedule_mode_allowed",
        ),
        CheckConstraint(
            LIVE_SCHEDULE_INTERVAL_SQL,
            name="paper_live_schedule_interval_positive",
        ),
        CheckConstraint(
            LIVE_SCHEDULE_RUN_COUNT_SQL,
            name="paper_live_schedule_run_count_non_negative",
        ),
        CheckConstraint(
            LIVE_SCHEDULE_MAX_RUNS_SQL,
            name="paper_live_schedule_max_runs_positive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual_cycle",
        server_default="manual_cycle",
    )

    cycle_request_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
    )

    next_run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_cycle_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("paper_live_cycle_runs.id", ondelete="SET NULL"),
        index=True,
    )

    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    max_runs: Mapped[int | None] = mapped_column(Integer)
    run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")

    use_current_date_as_of_date: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default=false(),
    )

    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
    )
    lock_token: Mapped[str | None] = mapped_column(String(64))

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

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    last_cycle_run: Mapped["PaperLiveCycleRun | None"] = relationship(
        foreign_keys=[last_cycle_run_id]
    )
