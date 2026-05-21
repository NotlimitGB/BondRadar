from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.paper_live_cycle_run import PaperLiveCycleRun
from app.models.paper_live_schedule import PaperLiveSchedule
from app.schemas.paper_trading_live_cycle import (
    LivePaperCycleRunRead,
    LivePaperCycleRunRequest,
    LivePaperCycleRunResponse,
)
from app.schemas.paper_trading_live_schedule import (
    LIVE_SCHEDULE_STATUSES,
    LivePaperScheduleCreate,
    LivePaperScheduleRead,
    LivePaperScheduleRunDueRequest,
    LivePaperScheduleRunDueResponse,
    LivePaperScheduleUpdate,
    LivePaperScheduledRunItem,
)
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_risk_policy import validate_paper_risk_policy


CURRENT_DATE_PREDICTION_ERROR = (
    "No predictions found for current execution date. Run data refresh/predictions "
    "first or disable use_current_date_as_of_date."
)
GENERIC_AS_OF_DATE_PREDICTION_ERROR = (
    "No predictions found for selected model run and as_of_date"
)


class LivePaperScheduleService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, request: LivePaperScheduleCreate) -> PaperLiveSchedule:
        self._validate_create(request)
        schedule = PaperLiveSchedule(
            name=request.name.strip(),
            status=request.status,
            mode="manual_cycle",
            cycle_request_json=request.cycle_request.model_dump(mode="json"),
            next_run_at=request.next_run_at,
            interval_days=request.interval_days,
            max_runs=request.max_runs,
            run_count=0,
            use_current_date_as_of_date=request.use_current_date_as_of_date,
            summary_json={},
            warnings_json=[],
            errors_json=[],
        )
        self.db.add(schedule)
        self.db.commit()
        self.db.refresh(schedule)
        return schedule

    def update(
        self,
        schedule_id: int,
        request: LivePaperScheduleUpdate,
    ) -> PaperLiveSchedule:
        schedule = self.get_schedule(schedule_id)
        self._validate_update(request)
        if request.name is not None:
            schedule.name = request.name.strip()
        if request.cycle_request is not None:
            schedule.cycle_request_json = request.cycle_request.model_dump(mode="json")
        if request.next_run_at is not None:
            schedule.next_run_at = request.next_run_at
        if request.interval_days is not None:
            schedule.interval_days = request.interval_days
        if "max_runs" in request.model_fields_set:
            schedule.max_runs = request.max_runs
        if request.status is not None:
            schedule.status = request.status
        if request.use_current_date_as_of_date is not None:
            schedule.use_current_date_as_of_date = request.use_current_date_as_of_date
        self.db.commit()
        self.db.refresh(schedule)
        return schedule

    def list_schedules(
        self,
        *,
        limit: int = 100,
        status_filter: str | None = None,
    ) -> list[PaperLiveSchedule]:
        stmt = select(PaperLiveSchedule).order_by(PaperLiveSchedule.id.desc())
        if status_filter is not None:
            if status_filter not in LIVE_SCHEDULE_STATUSES:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid schedule status",
                )
            stmt = stmt.where(PaperLiveSchedule.status == status_filter)
        return list(
            self.db.execute(stmt.limit(max(1, min(limit, 500)))).scalars()
        )

    def get_schedule(self, schedule_id: int) -> PaperLiveSchedule:
        schedule = self.db.get(PaperLiveSchedule, schedule_id)
        if schedule is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Live paper schedule not found",
            )
        return schedule

    def run_schedule_once(
        self,
        schedule_id: int,
        *,
        now: datetime | None = None,
    ) -> LivePaperScheduledRunItem:
        schedule = self.get_schedule(schedule_id)
        self._ensure_executable(schedule)
        run_now = self._utc_now(now)
        if self._max_runs_reached(schedule):
            return self._skipped_item(
                schedule,
                scheduled_for=run_now,
                message="Configured maximum run count was reached",
            )
        return self._run_schedule(
            schedule,
            now=run_now,
            scheduled_for=run_now,
            lock_minutes=10,
        )

    def run_due(
        self,
        request: LivePaperScheduleRunDueRequest,
    ) -> LivePaperScheduleRunDueResponse:
        self._validate_run_due(request)
        now = self._utc_now(request.now)
        schedules = self._due_schedules(now, request.limit)
        results: list[LivePaperScheduledRunItem] = []
        if request.dry_run:
            results = [
                LivePaperScheduledRunItem(
                    schedule=LivePaperScheduleRead.model_validate(schedule),
                    status="dry_run",
                    scheduled_for=schedule.next_run_at,
                    warnings=[],
                    errors=[],
                )
                for schedule in schedules
            ]
        else:
            for schedule in schedules:
                results.append(
                    self._run_schedule(
                        schedule,
                        now=now,
                        scheduled_for=schedule.next_run_at,
                        lock_minutes=request.lock_minutes,
                    )
                )

        executed_count = sum(
            1 for item in results if item.status in {"completed", "blocked", "failed"}
        )
        skipped_count = sum(1 for item in results if item.status == "skipped")
        return LivePaperScheduleRunDueResponse(
            now=now,
            dry_run=request.dry_run,
            due_schedule_count=len(schedules),
            executed_count=executed_count,
            skipped_count=skipped_count,
            results=results,
            warnings=[],
            errors=[],
        )

    def _run_schedule(
        self,
        schedule: PaperLiveSchedule,
        *,
        now: datetime,
        scheduled_for: datetime,
        lock_minutes: int,
    ) -> LivePaperScheduledRunItem:
        if self._is_locked(schedule, now):
            return self._skipped_item(
                schedule,
                scheduled_for=scheduled_for,
                message="Schedule lock could not be acquired",
            )
        locked_schedule = self._acquire_lock(schedule.id, now, lock_minutes)
        if locked_schedule is None:
            return self._skipped_item(
                schedule,
                scheduled_for=scheduled_for,
                message="Schedule lock could not be acquired",
            )

        try:
            cycle_request = self._cycle_request(locked_schedule, scheduled_for, now)
            cycle_response = LivePaperCycleService(self.db).run(cycle_request)
            cycle_response = self._enrich_current_date_prediction_failure(
                locked_schedule,
                cycle_response,
            )
            linked_cycle = self._link_cycle(
                cycle_response,
                schedule_id=locked_schedule.id,
                scheduled_for=scheduled_for,
            )
            schedule = self.get_schedule(locked_schedule.id)
            self._record_attempt(schedule, linked_cycle, now)
            return LivePaperScheduledRunItem(
                schedule=LivePaperScheduleRead.model_validate(schedule),
                status=linked_cycle.status,
                scheduled_for=scheduled_for,
                cycle=cycle_response.cycle.model_copy(
                    update={
                        "schedule_id": linked_cycle.schedule_id,
                        "scheduled_for": linked_cycle.scheduled_for,
                    }
                ),
                warnings=cycle_response.warnings,
                errors=cycle_response.errors,
            )
        except Exception as exc:  # pragma: no cover - defensive scheduler logging
            schedule = self.get_schedule(locked_schedule.id)
            error = {
                "type": exc.__class__.__name__,
                "detail": str(exc),
            }
            schedule.errors_json = [*schedule.errors_json, error]
            self._release_lock(schedule)
            self.db.commit()
            self.db.refresh(schedule)
            return LivePaperScheduledRunItem(
                schedule=LivePaperScheduleRead.model_validate(schedule),
                status="failed",
                scheduled_for=scheduled_for,
                warnings=[],
                errors=[error],
            )

    def _cycle_request(
        self,
        schedule: PaperLiveSchedule,
        scheduled_for: datetime,
        now: datetime,
    ) -> LivePaperCycleRunRequest:
        payload = dict(schedule.cycle_request_json)
        payload["client_cycle_key"] = (
            f"scheduled-cycle:{schedule.id}:{scheduled_for.isoformat()}"
        )
        if schedule.use_current_date_as_of_date:
            execution_date = now.date().isoformat()
            payload["as_of_date"] = execution_date
            rebalance = dict(payload.get("rebalance") or {})
            rebalance["as_of_date"] = execution_date
            payload["rebalance"] = rebalance
        return LivePaperCycleRunRequest.model_validate(payload)

    def _enrich_current_date_prediction_failure(
        self,
        schedule: PaperLiveSchedule,
        cycle_response: LivePaperCycleRunResponse,
    ) -> LivePaperCycleRunResponse:
        if not schedule.use_current_date_as_of_date:
            return cycle_response
        if cycle_response.cycle.status != "failed":
            return cycle_response
        if not self._has_generic_prediction_error(cycle_response.errors):
            return cycle_response

        diagnostic = {
            "detail": CURRENT_DATE_PREDICTION_ERROR,
            "source": "use_current_date_as_of_date",
        }
        cycle = self.db.get(PaperLiveCycleRun, cycle_response.cycle.id)
        if cycle is None:
            return cycle_response
        cycle.errors_json = [*cycle.errors_json, diagnostic]
        cycle.summary_json = {
            **(cycle.summary_json or {}),
            "failure_detail": CURRENT_DATE_PREDICTION_ERROR,
            "use_current_date_as_of_date": True,
        }
        self.db.commit()
        self.db.refresh(cycle)
        return cycle_response.model_copy(
            update={
                "cycle": LivePaperCycleRunRead.model_validate(cycle),
                "errors": [*cycle_response.errors, diagnostic],
            }
        )

    @staticmethod
    def _has_generic_prediction_error(errors: list[dict[str, Any]]) -> bool:
        return any(
            error.get("detail") == GENERIC_AS_OF_DATE_PREDICTION_ERROR
            for error in errors
        )

    def _link_cycle(
        self,
        cycle_response: LivePaperCycleRunResponse,
        *,
        schedule_id: int,
        scheduled_for: datetime,
    ) -> PaperLiveCycleRun:
        cycle = self.db.get(PaperLiveCycleRun, cycle_response.cycle.id)
        if cycle is None:
            raise RuntimeError("Scheduled live paper cycle was not found")
        cycle.schedule_id = schedule_id
        cycle.scheduled_for = scheduled_for
        self.db.commit()
        self.db.refresh(cycle)
        return cycle

    def _record_attempt(
        self,
        schedule: PaperLiveSchedule,
        cycle: PaperLiveCycleRun,
        now: datetime,
    ) -> None:
        schedule.last_run_at = now
        schedule.last_cycle_run_id = cycle.id
        schedule.run_count += 1
        schedule.next_run_at = self._advanced_next_run_at(
            schedule.next_run_at,
            schedule.interval_days,
            now,
        )
        schedule.summary_json = {
            "last_cycle_run_id": cycle.id,
            "last_cycle_status": cycle.status,
            "last_scheduled_for": cycle.scheduled_for.isoformat()
            if cycle.scheduled_for is not None
            else None,
        }
        self._release_lock(schedule)
        self.db.commit()
        self.db.refresh(schedule)

    def _due_schedules(
        self,
        now: datetime,
        limit: int,
    ) -> list[PaperLiveSchedule]:
        return list(
            self.db.execute(
                select(PaperLiveSchedule)
                .where(
                    PaperLiveSchedule.status == "active",
                    PaperLiveSchedule.next_run_at <= now,
                    or_(
                        PaperLiveSchedule.max_runs.is_(None),
                        PaperLiveSchedule.run_count < PaperLiveSchedule.max_runs,
                    ),
                )
                .order_by(PaperLiveSchedule.next_run_at.asc(), PaperLiveSchedule.id.asc())
                .limit(limit)
            ).scalars()
        )

    def _acquire_lock(
        self,
        schedule_id: int,
        now: datetime,
        lock_minutes: int,
    ) -> PaperLiveSchedule | None:
        token = uuid4().hex
        result = self.db.execute(
            update(PaperLiveSchedule)
            .where(
                PaperLiveSchedule.id == schedule_id,
                or_(
                    PaperLiveSchedule.lock_expires_at.is_(None),
                    PaperLiveSchedule.lock_expires_at < now,
                ),
            )
            .values(
                locked_at=now,
                lock_expires_at=now + timedelta(minutes=lock_minutes),
                lock_token=token,
            )
        )
        self.db.commit()
        if result.rowcount != 1:
            return None
        schedule = self.get_schedule(schedule_id)
        return schedule if schedule.lock_token == token else None

    @staticmethod
    def _release_lock(schedule: PaperLiveSchedule) -> None:
        schedule.locked_at = None
        schedule.lock_expires_at = None
        schedule.lock_token = None

    def _skipped_item(
        self,
        schedule: PaperLiveSchedule,
        *,
        scheduled_for: datetime,
        message: str,
    ) -> LivePaperScheduledRunItem:
        self.db.refresh(schedule)
        return LivePaperScheduledRunItem(
            schedule=LivePaperScheduleRead.model_validate(schedule),
            status="skipped",
            scheduled_for=scheduled_for,
            cycle=None,
            warnings=[{"message": message, "details": {}}],
            errors=[],
        )

    @staticmethod
    def _is_locked(schedule: PaperLiveSchedule, now: datetime) -> bool:
        if schedule.lock_expires_at is None:
            return False
        return LivePaperScheduleService._as_utc(schedule.lock_expires_at) >= now

    @staticmethod
    def _max_runs_reached(schedule: PaperLiveSchedule) -> bool:
        return schedule.max_runs is not None and schedule.run_count >= schedule.max_runs

    @staticmethod
    def _advanced_next_run_at(
        previous_next_run_at: datetime,
        interval_days: int,
        now: datetime,
    ) -> datetime:
        next_run_at = LivePaperScheduleService._as_utc(previous_next_run_at) + timedelta(
            days=interval_days
        )
        while next_run_at <= now:
            next_run_at += timedelta(days=interval_days)
        return next_run_at

    @staticmethod
    def _utc_now(value: datetime | None = None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        return LivePaperScheduleService._as_utc(value)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _ensure_executable(schedule: PaperLiveSchedule) -> None:
        if schedule.status == "paused":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paused live paper schedule cannot be executed",
            )
        if schedule.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Archived live paper schedule cannot be executed",
            )

    @staticmethod
    def _validate_create(request: LivePaperScheduleCreate) -> None:
        if request.name is None or not request.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="schedule name is required",
            )
        if request.status not in LIVE_SCHEDULE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid schedule status",
            )
        if request.next_run_at is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="next_run_at is required",
            )
        if request.interval_days < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="interval_days must be positive",
            )
        if request.max_runs is not None and request.max_runs < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_runs must be positive when provided",
            )
        validate_paper_risk_policy(request.cycle_request.rebalance)

    @staticmethod
    def _validate_update(request: LivePaperScheduleUpdate) -> None:
        if request.name is not None and not request.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="schedule name is required",
            )
        if request.status is not None and request.status not in LIVE_SCHEDULE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid schedule status",
            )
        if request.interval_days is not None and request.interval_days < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="interval_days must be positive",
            )
        if request.max_runs is not None and request.max_runs < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_runs must be positive when provided",
            )
        if request.cycle_request is not None:
            validate_paper_risk_policy(request.cycle_request.rebalance)

    @staticmethod
    def _validate_run_due(request: LivePaperScheduleRunDueRequest) -> None:
        if request.limit < 1 or request.limit > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 100",
            )
        if request.lock_minutes < 1 or request.lock_minutes > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="lock_minutes must be between 1 and 120",
            )
