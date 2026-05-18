from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.paper_trading_live_cycle import (
    LivePaperCycleRunRead,
    LivePaperCycleRunRequest,
)


LIVE_SCHEDULE_STATUSES = {"active", "paused", "archived"}
LIVE_SCHEDULE_MODES = {"manual_cycle"}
LIVE_SCHEDULE_RUN_ITEM_STATUSES = {
    "due",
    "skipped",
    "completed",
    "blocked",
    "failed",
    "dry_run",
}


class LivePaperScheduleCreate(BaseModel):
    name: str | None = None
    cycle_request: LivePaperCycleRunRequest

    next_run_at: datetime | None = None
    interval_days: int = 1
    max_runs: int | None = None

    status: str = "active"
    use_current_date_as_of_date: bool = False


class LivePaperScheduleUpdate(BaseModel):
    name: str | None = None
    cycle_request: LivePaperCycleRunRequest | None = None

    next_run_at: datetime | None = None
    interval_days: int | None = None
    max_runs: int | None = None

    status: str | None = None
    use_current_date_as_of_date: bool | None = None


class LivePaperScheduleRead(BaseModel):
    id: int
    name: str
    status: str
    mode: str

    cycle_request_json: dict[str, Any]

    next_run_at: datetime
    last_run_at: datetime | None
    last_cycle_run_id: int | None

    interval_days: int
    max_runs: int | None
    run_count: int

    use_current_date_as_of_date: bool

    locked_at: datetime | None
    lock_expires_at: datetime | None
    lock_token: str | None

    summary_json: dict[str, Any]
    warnings_json: list[dict[str, Any]]
    errors_json: list[dict[str, Any]]

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LivePaperScheduleRunDueRequest(BaseModel):
    now: datetime | None = None
    limit: int = 10
    dry_run: bool = False
    lock_minutes: int = 10


class LivePaperScheduledRunItem(BaseModel):
    schedule: LivePaperScheduleRead
    status: str
    scheduled_for: datetime
    cycle: LivePaperCycleRunRead | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class LivePaperScheduleRunDueResponse(BaseModel):
    now: datetime
    dry_run: bool
    due_schedule_count: int
    executed_count: int
    skipped_count: int
    results: list[LivePaperScheduledRunItem]
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
