from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.paper_live_cycle_run import LIVE_CYCLE_STATUSES, PaperLiveCycleRun
from app.models.paper_live_schedule import PaperLiveSchedule
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.schemas.paper_trading import PaperPortfolioPositionRead
from app.schemas.paper_trading_live_monitoring import (
    LivePaperCycleMonitoringListResponse,
    LivePaperCycleMonitoringSummary,
    LivePaperMonitoringAlert,
    LivePaperMonitoringOverviewResponse,
    LivePaperPortfolioMonitoringResponse,
    LivePaperPortfolioMonitoringSummary,
    LivePaperScheduleMonitoringResponse,
    LivePaperScheduleMonitoringSummary,
)
from app.services.external_risk_regime_service import ExternalRiskRegimeService
from app.services.paper_trading_report_service import PaperTradingReportService


STALE_RUNNING_AGE = timedelta(hours=2)


class LivePaperMonitoringService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def overview(
        self,
        *,
        include_schedules: bool = True,
        include_portfolios: bool = True,
        include_recent_cycles: bool = True,
        include_alerts: bool = True,
        schedule_limit: int = 20,
        portfolio_limit: int = 20,
        cycle_limit: int = 20,
        now: datetime | None = None,
    ) -> LivePaperMonitoringOverviewResponse:
        self._validate_limit(
            schedule_limit,
            lower=1,
            upper=100,
            detail="schedule_limit must be between 1 and 100",
        )
        self._validate_limit(
            portfolio_limit,
            lower=1,
            upper=100,
            detail="portfolio_limit must be between 1 and 100",
        )
        self._validate_cycle_limit(cycle_limit)
        as_of_now = self._utc_now(now)

        schedules = self._all_schedules()
        portfolios = self._all_portfolios()
        recent_cycles = self._recent_cycles(limit=cycle_limit)
        schedule_summaries = [
            self._schedule_summary(schedule, as_of_now, include_alerts=include_alerts)
            for schedule in schedules[:schedule_limit]
        ]
        portfolio_summaries = [
            self._portfolio_summary(portfolio)
            for portfolio in portfolios[:portfolio_limit]
        ]
        cycle_summaries = [
            self._cycle_summary(cycle) for cycle in recent_cycles
        ]

        active_schedule_count = sum(schedule.status == "active" for schedule in schedules)
        due_schedule_count = sum(
            self._schedule_is_due(schedule, as_of_now) for schedule in schedules
        )
        locked_schedule_count = sum(
            self._schedule_is_locked(schedule, as_of_now) for schedule in schedules
        )
        active_portfolio_count = sum(
            portfolio.status == "active" for portfolio in portfolios
        )
        external_risk_regime = ExternalRiskRegimeService(self.db).current(now=as_of_now)
        alerts = (
            self._overview_alerts(
                schedules=schedules,
                recent_cycles=recent_cycles,
                external_risk_regime=external_risk_regime,
                now=as_of_now,
            )
            if include_alerts
            else []
        )

        return LivePaperMonitoringOverviewResponse(
            health_status=self._overview_health(
                schedules=schedules,
                portfolios=portfolios,
                recent_cycles=recent_cycles,
                alerts=alerts,
                now=as_of_now,
            ),
            now=as_of_now,
            schedule_count=len(schedules),
            active_schedule_count=active_schedule_count,
            due_schedule_count=due_schedule_count,
            locked_schedule_count=locked_schedule_count,
            portfolio_count=len(portfolios),
            active_portfolio_count=active_portfolio_count,
            recent_cycle_count=len(recent_cycles),
            completed_cycle_count=sum(
                cycle.status == "completed" for cycle in recent_cycles
            ),
            blocked_cycle_count=sum(cycle.status == "blocked" for cycle in recent_cycles),
            failed_cycle_count=sum(cycle.status == "failed" for cycle in recent_cycles),
            running_cycle_count=sum(cycle.status == "running" for cycle in recent_cycles),
            schedules=schedule_summaries if include_schedules else [],
            portfolios=portfolio_summaries if include_portfolios else [],
            recent_cycles=cycle_summaries if include_recent_cycles else [],
            external_risk_regime=external_risk_regime,
            alerts=alerts,
        )

    def schedule_detail(
        self,
        schedule_id: int,
        *,
        include_recent_cycles: bool = True,
        include_alerts: bool = True,
        cycle_limit: int = 20,
        now: datetime | None = None,
    ) -> LivePaperScheduleMonitoringResponse:
        self._validate_cycle_limit(cycle_limit)
        as_of_now = self._utc_now(now)
        schedule = self._get_schedule(schedule_id)
        summary = self._schedule_summary(
            schedule,
            as_of_now,
            include_alerts=include_alerts,
        )
        recent_cycles = [
            self._cycle_summary(cycle)
            for cycle in self._recent_cycles(
                schedule_id=schedule_id,
                limit=cycle_limit,
            )
        ]
        return LivePaperScheduleMonitoringResponse(
            schedule=summary,
            recent_cycles=recent_cycles if include_recent_cycles else [],
            alerts=summary.alerts if include_alerts else [],
        )

    def portfolio_detail(
        self,
        portfolio_id: int,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        include_performance: bool = True,
        include_equity_curve: bool = True,
        include_contributions: bool = True,
        include_positions: bool = True,
        include_recent_cycles: bool = True,
        cycle_limit: int = 20,
        contribution_limit: int = 50,
    ) -> LivePaperPortfolioMonitoringResponse:
        self._validate_date_range(date_from, date_to)
        self._validate_cycle_limit(cycle_limit)
        self._validate_limit(
            contribution_limit,
            lower=1,
            upper=500,
            detail="contribution_limit must be between 1 and 500",
        )
        portfolio = self._get_portfolio(portfolio_id)
        summary = self._portfolio_summary(portfolio)
        report_service = PaperTradingReportService(self.db)
        performance = (
            report_service.performance(
                portfolio_id,
                date_from=date_from,
                date_to=date_to,
                include_equity_curve=include_equity_curve,
            ).model_dump(mode="json")
            if include_performance
            else None
        )
        equity_curve = (
            [
                point.model_dump(mode="json")
                for point in report_service.equity_curve(
                    portfolio_id,
                    date_from=date_from,
                    date_to=date_to,
                )
            ]
            if include_equity_curve
            else []
        )
        contributions = (
            report_service.contributions(
                portfolio_id,
                date_from=date_from,
                date_to=date_to,
                limit=contribution_limit,
            ).model_dump(mode="json")
            if include_contributions
            else None
        )
        positions = (
            [
                PaperPortfolioPositionRead.model_validate(position).model_dump(
                    mode="json"
                )
                for position in self._positions(portfolio_id)
            ]
            if include_positions
            else []
        )
        recent_cycles = [
            self._cycle_summary(cycle)
            for cycle in self._recent_cycles(
                portfolio_id=portfolio_id,
                limit=cycle_limit,
            )
        ]
        return LivePaperPortfolioMonitoringResponse(
            portfolio=summary,
            performance=performance,
            equity_curve=equity_curve,
            contributions=contributions,
            positions=positions,
            recent_cycles=recent_cycles if include_recent_cycles else [],
            alerts=summary.alerts,
        )

    def cycle_list(
        self,
        *,
        schedule_id: int | None = None,
        portfolio_id: int | None = None,
        status_filter: str | None = None,
        limit: int = 50,
    ) -> LivePaperCycleMonitoringListResponse:
        self._validate_cycle_limit(limit)
        if status_filter is not None and status_filter not in LIVE_CYCLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="invalid cycle status",
            )
        cycles = self._recent_cycles(
            schedule_id=schedule_id,
            portfolio_id=portfolio_id,
            status_filter=status_filter,
            limit=limit,
        )
        return LivePaperCycleMonitoringListResponse(
            total_returned=len(cycles),
            cycles=[self._cycle_summary(cycle) for cycle in cycles],
            alerts=[],
        )

    def _all_schedules(self) -> list[PaperLiveSchedule]:
        return list(
            self.db.execute(
                select(PaperLiveSchedule).order_by(
                    PaperLiveSchedule.next_run_at.asc(),
                    PaperLiveSchedule.id.asc(),
                )
            ).scalars()
        )

    def _all_portfolios(self) -> list[PaperPortfolio]:
        return list(
            self.db.execute(
                select(PaperPortfolio).order_by(PaperPortfolio.id.desc())
            ).scalars()
        )

    def _recent_cycles(
        self,
        *,
        schedule_id: int | None = None,
        portfolio_id: int | None = None,
        status_filter: str | None = None,
        limit: int,
    ) -> list[PaperLiveCycleRun]:
        query = select(PaperLiveCycleRun)
        if schedule_id is not None:
            query = query.where(PaperLiveCycleRun.schedule_id == schedule_id)
        if portfolio_id is not None:
            query = query.where(PaperLiveCycleRun.portfolio_id == portfolio_id)
        if status_filter is not None:
            query = query.where(PaperLiveCycleRun.status == status_filter)
        query = query.order_by(
            PaperLiveCycleRun.started_at.desc(),
            PaperLiveCycleRun.id.desc(),
        ).limit(limit)
        return list(self.db.execute(query).scalars())

    def _schedule_summary(
        self,
        schedule: PaperLiveSchedule,
        now: datetime,
        *,
        include_alerts: bool,
    ) -> LivePaperScheduleMonitoringSummary:
        alerts = self._schedule_alerts(schedule, now) if include_alerts else []
        return LivePaperScheduleMonitoringSummary(
            id=schedule.id,
            name=schedule.name,
            status=schedule.status,
            next_run_at=schedule.next_run_at,
            last_run_at=schedule.last_run_at,
            last_cycle_run_id=schedule.last_cycle_run_id,
            run_count=schedule.run_count,
            max_runs=schedule.max_runs,
            interval_days=schedule.interval_days,
            is_due=self._schedule_is_due(schedule, now),
            is_locked=self._schedule_is_locked(schedule, now),
            lock_expires_at=schedule.lock_expires_at,
            health_status=self._schedule_health(alerts),
            alerts=alerts,
        )

    def _portfolio_summary(
        self,
        portfolio: PaperPortfolio,
    ) -> LivePaperPortfolioMonitoringSummary:
        positions = self._positions(portfolio.id)
        snapshots = self._snapshots(portfolio.id)
        latest_snapshot = snapshots[-1] if snapshots else None
        performance = PaperTradingReportService(self.db).performance(
            portfolio.id,
            include_equity_curve=False,
        )
        active_positions_count = sum(position.is_active for position in positions)
        alerts = self._portfolio_alerts(
            portfolio,
            active_positions_count=active_positions_count,
            latest_snapshot=latest_snapshot,
            snapshot_count=len(snapshots),
        )
        return LivePaperPortfolioMonitoringSummary(
            id=portfolio.id,
            name=portfolio.name,
            status=portfolio.status,
            base_currency=portfolio.base_currency,
            initial_capital=portfolio.initial_capital,
            current_value=portfolio.current_value,
            cash_balance=portfolio.cash_balance,
            model_run_id=portfolio.model_run_id,
            return_method=portfolio.return_method,
            horizon_days=portfolio.horizon_days,
            last_rebalance_as_of_date=portfolio.last_rebalance_as_of_date,
            last_rebalanced_at=portfolio.last_rebalanced_at,
            last_marked_at=portfolio.last_marked_at,
            active_positions_count=active_positions_count,
            snapshot_count=len(snapshots),
            latest_snapshot_date=None if latest_snapshot is None else latest_snapshot.as_of_date,
            cumulative_return=(
                None if latest_snapshot is None else latest_snapshot.cumulative_return
            ),
            max_drawdown=performance.metrics.max_drawdown,
            health_status=self._portfolio_health(alerts),
            alerts=alerts,
        )

    @staticmethod
    def _cycle_summary(cycle: PaperLiveCycleRun) -> LivePaperCycleMonitoringSummary:
        return LivePaperCycleMonitoringSummary(
            id=cycle.id,
            status=cycle.status,
            mode=cycle.mode,
            portfolio_id=cycle.portfolio_id,
            schedule_id=cycle.schedule_id,
            client_cycle_key=cycle.client_cycle_key,
            as_of_date=cycle.as_of_date,
            scheduled_for=cycle.scheduled_for,
            readiness_status=cycle.readiness_status,
            selected_model_run_id=cycle.selected_model_run_id,
            started_at=cycle.started_at,
            finished_at=cycle.finished_at,
            warning_count=len(cycle.warnings_json or []),
            error_count=len(cycle.errors_json or []),
            summary=cycle.summary_json or {},
        )

    def _schedule_alerts(
        self,
        schedule: PaperLiveSchedule,
        now: datetime,
    ) -> list[LivePaperMonitoringAlert]:
        alerts: list[LivePaperMonitoringAlert] = []
        if self._schedule_lock_stale(schedule, now):
            alerts.append(
                self._alert(
                    "critical",
                    "schedule_lock_stale",
                    "Live paper schedule lock appears stale",
                    schedule_id=schedule.id,
                )
            )
        elif self._schedule_is_locked(schedule, now):
            alerts.append(
                self._alert(
                    "info",
                    "schedule_locked",
                    "Live paper schedule has an active lock",
                    schedule_id=schedule.id,
                )
            )
        if self._schedule_is_due(schedule, now):
            alerts.append(
                self._alert(
                    "warning",
                    "schedule_due",
                    "Live paper schedule is due for execution",
                    schedule_id=schedule.id,
                )
            )
        if schedule.max_runs is not None and schedule.run_count >= schedule.max_runs:
            alerts.append(
                self._alert(
                    "warning",
                    "schedule_max_runs_reached",
                    "Live paper schedule reached configured maximum run count",
                    schedule_id=schedule.id,
                )
            )
        last_cycle = (
            None
            if schedule.last_cycle_run_id is None
            else self.db.get(PaperLiveCycleRun, schedule.last_cycle_run_id)
        )
        risk_override = self._risk_override_from_cycle_request(
            schedule.cycle_request_json
        )
        if risk_override.get("risk_override_enabled") is True:
            alerts.append(
                self._alert(
                    "warning",
                    "risk_override_enabled",
                    "Paper risk override is enabled for this schedule",
                    schedule_id=schedule.id,
                    risk_override=risk_override,
                    risk_policy=self._risk_policy_from_cycle_request(
                        schedule.cycle_request_json
                    ),
                )
            )
        if last_cycle is not None and last_cycle.status == "failed":
            alerts.append(
                self._alert(
                    "warning",
                    "last_cycle_failed",
                    "Last live paper cycle failed",
                    schedule_id=schedule.id,
                    cycle_id=last_cycle.id,
                )
            )
        if last_cycle is not None and last_cycle.status == "blocked":
            alerts.append(
                self._alert(
                    "warning",
                    "last_cycle_blocked",
                    "Last live paper cycle was blocked",
                    schedule_id=schedule.id,
                    cycle_id=last_cycle.id,
                )
            )
        return alerts

    @staticmethod
    def _portfolio_alerts(
        portfolio: PaperPortfolio,
        *,
        active_positions_count: int,
        latest_snapshot: PaperPortfolioSnapshot | None,
        snapshot_count: int,
    ) -> list[LivePaperMonitoringAlert]:
        alerts: list[LivePaperMonitoringAlert] = []
        if portfolio.status == "archived":
            alerts.append(
                LivePaperMonitoringService._alert(
                    "critical",
                    "portfolio_archived",
                    "Paper portfolio is archived",
                    portfolio_id=portfolio.id,
                )
            )
        if snapshot_count == 0:
            alerts.append(
                LivePaperMonitoringService._alert(
                    "warning",
                    "portfolio_no_snapshots",
                    "Paper portfolio has no snapshots",
                    portfolio_id=portfolio.id,
                )
            )
        if active_positions_count == 0:
            construction_summary = (
                portfolio.summary_json or {}
            ).get("construction_summary") or {}
            alerts.append(
                LivePaperMonitoringService._alert(
                    "warning",
                    "portfolio_no_active_positions",
                    "Paper portfolio has no active positions",
                    portfolio_id=portfolio.id,
                    construction_summary=construction_summary,
                    exclusion_reason_counts=construction_summary.get(
                        "exclusion_reason_counts",
                        {},
                    ),
                )
            )
        if (
            latest_snapshot is not None
            and portfolio.last_rebalance_as_of_date is not None
            and latest_snapshot.as_of_date < portfolio.last_rebalance_as_of_date
        ):
            alerts.append(
                LivePaperMonitoringService._alert(
                    "warning",
                    "portfolio_snapshot_stale",
                    "Paper portfolio snapshot is older than last rebalance date",
                    portfolio_id=portfolio.id,
                )
            )
        return alerts

    def _overview_alerts(
        self,
        *,
        schedules: list[PaperLiveSchedule],
        recent_cycles: list[PaperLiveCycleRun],
        external_risk_regime: Any,
        now: datetime,
    ) -> list[LivePaperMonitoringAlert]:
        alerts: list[LivePaperMonitoringAlert] = []
        if external_risk_regime.mode == "elevated":
            alerts.append(
                self._alert(
                    "warning",
                    "external_risk_elevated",
                    "External risk regime requires manual review",
                    mode=external_risk_regime.mode,
                    source=external_risk_regime.source,
                    expires_at=external_risk_regime.expires_at,
                )
            )
        if external_risk_regime.mode == "severe":
            alerts.append(
                self._alert(
                    "critical",
                    "external_risk_severe",
                    "External risk regime blocks confirmed paper execution by default",
                    mode=external_risk_regime.mode,
                    source=external_risk_regime.source,
                    expires_at=external_risk_regime.expires_at,
                )
            )
        if not any(schedule.status == "active" for schedule in schedules):
            alerts.append(
                self._alert(
                    "warning",
                    "no_active_schedules",
                    "No active live paper schedules are configured",
                )
            )
        if any(cycle.status == "failed" for cycle in recent_cycles):
            alerts.append(
                self._alert(
                    "critical",
                    "recent_failed_cycles",
                    "Recent live paper cycles contain failures",
                )
            )
        if any(cycle.status == "blocked" for cycle in recent_cycles):
            alerts.append(
                self._alert(
                    "warning",
                    "recent_blocked_cycles",
                    "Recent live paper cycles contain blocked results",
                )
            )
        zero_position_cycles = [
            cycle for cycle in recent_cycles if self._cycle_selected_zero_positions(cycle)
        ]
        if zero_position_cycles:
            alerts.append(
                self._alert(
                    "warning",
                    "recent_zero_position_cycles",
                    "Recent live paper cycles selected zero positions",
                    cycle_ids=[cycle.id for cycle in zero_position_cycles],
                    exclusion_reason_counts=self._merged_exclusion_reason_counts(
                        zero_position_cycles
                    ),
                )
            )
        risk_override_schedules = [
            schedule.id
            for schedule in schedules
            if self._risk_override_from_cycle_request(
                schedule.cycle_request_json
            ).get("risk_override_enabled")
            is True
        ]
        if risk_override_schedules:
            alerts.append(
                self._alert(
                    "warning",
                    "risk_override_enabled",
                    "Paper risk override is enabled for one or more schedules",
                    schedule_ids=risk_override_schedules,
                )
            )
        stale_cycles = [
            cycle for cycle in recent_cycles if self._cycle_is_stale(cycle, now)
        ]
        if stale_cycles:
            alerts.append(
                self._alert(
                    "critical",
                    "stale_running_cycle",
                    "Live paper cycle appears stale",
                    cycle_ids=[cycle.id for cycle in stale_cycles],
                )
            )
        for schedule in schedules:
            if self._schedule_is_due(schedule, now):
                alerts.append(
                    self._alert(
                        "warning",
                        "schedule_due",
                        "Live paper schedule is due for execution",
                        schedule_id=schedule.id,
                    )
                )
        return alerts

    @staticmethod
    def _overview_health(
        *,
        schedules: list[PaperLiveSchedule],
        portfolios: list[PaperPortfolio],
        recent_cycles: list[PaperLiveCycleRun],
        alerts: list[LivePaperMonitoringAlert],
        now: datetime,
    ) -> str:
        if any(alert.level == "critical" for alert in alerts):
            return "critical"
        if not schedules and not portfolios and not recent_cycles:
            return "unknown"
        if any(cycle.status == "failed" for cycle in recent_cycles):
            return "critical"
        if any(
            cycle.status == "running"
            and LivePaperMonitoringService._cycle_is_stale(cycle, now)
            for cycle in recent_cycles
        ):
            return "critical"
        if any(alert.level == "warning" for alert in alerts):
            return "warning"
        if any(cycle.status == "blocked" for cycle in recent_cycles):
            return "warning"
        if not any(schedule.status == "active" for schedule in schedules):
            return "warning"
        return "healthy"

    @staticmethod
    def _schedule_health(alerts: list[LivePaperMonitoringAlert]) -> str:
        if any(alert.level == "critical" for alert in alerts):
            return "critical"
        if any(alert.level == "warning" for alert in alerts):
            return "warning"
        return "healthy"

    @staticmethod
    def _portfolio_health(alerts: list[LivePaperMonitoringAlert]) -> str:
        if any(alert.level == "critical" for alert in alerts):
            return "critical"
        if any(alert.level == "warning" for alert in alerts):
            return "warning"
        return "healthy"

    @staticmethod
    def _risk_policy_from_cycle_request(payload: dict[str, Any]) -> dict[str, Any]:
        rebalance = (payload or {}).get("rebalance") or {}
        return {
            key: rebalance.get(key)
            for key in (
                "top_n",
                "min_probability_positive",
                "max_position_weight",
                "max_issuer_weight",
                "max_high_risk_weight",
                "min_liquidity_score",
                "exclude_blocked_by_risk",
                "exclude_insufficient_credit_data",
                "allowed_risk_levels",
                "allowed_decision_statuses",
            )
            if key in rebalance
        }

    @staticmethod
    def _risk_override_from_cycle_request(payload: dict[str, Any]) -> dict[str, Any]:
        rebalance = (payload or {}).get("rebalance") or {}
        return {
            "risk_override_enabled": rebalance.get("risk_override_enabled", False),
            "risk_override_reason": rebalance.get("risk_override_reason"),
        }

    @staticmethod
    def _cycle_selected_zero_positions(cycle: PaperLiveCycleRun) -> bool:
        summary = cycle.summary_json or {}
        if summary.get("selected_position_count") == 0:
            return True
        construction_summary = summary.get("construction_summary") or {}
        return construction_summary.get("selected_count") == 0

    @staticmethod
    def _merged_exclusion_reason_counts(
        cycles: list[PaperLiveCycleRun],
    ) -> dict[str, int]:
        counts: dict[str, int] = {}
        for cycle in cycles:
            summary = cycle.summary_json or {}
            reason_counts = summary.get("exclusion_reason_counts") or (
                summary.get("construction_summary") or {}
            ).get("exclusion_reason_counts") or {}
            for reason, count in reason_counts.items():
                counts[str(reason)] = counts.get(str(reason), 0) + int(count)
        return dict(
            sorted(
                counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        )

    @staticmethod
    def _schedule_is_due(schedule: PaperLiveSchedule, now: datetime) -> bool:
        if schedule.status != "active":
            return False
        if schedule.max_runs is not None and schedule.run_count >= schedule.max_runs:
            return False
        return LivePaperMonitoringService._as_utc(schedule.next_run_at) <= now

    @staticmethod
    def _schedule_is_locked(schedule: PaperLiveSchedule, now: datetime) -> bool:
        if schedule.lock_expires_at is None:
            return False
        return LivePaperMonitoringService._as_utc(schedule.lock_expires_at) >= now

    @staticmethod
    def _schedule_lock_stale(schedule: PaperLiveSchedule, now: datetime) -> bool:
        if schedule.lock_expires_at is None:
            return False
        has_lock_fields = schedule.locked_at is not None or schedule.lock_token is not None
        return has_lock_fields and LivePaperMonitoringService._as_utc(schedule.lock_expires_at) < now

    @staticmethod
    def _cycle_is_stale(cycle: PaperLiveCycleRun, now: datetime) -> bool:
        if cycle.status != "running" or cycle.finished_at is not None:
            return False
        return LivePaperMonitoringService._as_utc(cycle.started_at) <= now - STALE_RUNNING_AGE

    def _positions(self, portfolio_id: int) -> list[PaperPortfolioPosition]:
        return list(
            self.db.execute(
                select(PaperPortfolioPosition).where(
                    PaperPortfolioPosition.portfolio_id == portfolio_id
                )
            ).scalars()
        )

    def _snapshots(self, portfolio_id: int) -> list[PaperPortfolioSnapshot]:
        return list(
            self.db.execute(
                select(PaperPortfolioSnapshot)
                .where(PaperPortfolioSnapshot.portfolio_id == portfolio_id)
                .order_by(PaperPortfolioSnapshot.as_of_date.asc())
            ).scalars()
        )

    def _get_schedule(self, schedule_id: int) -> PaperLiveSchedule:
        schedule = self.db.get(PaperLiveSchedule, schedule_id)
        if schedule is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Live paper schedule not found",
            )
        return schedule

    def _get_portfolio(self, portfolio_id: int) -> PaperPortfolio:
        portfolio = self.db.get(PaperPortfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Paper portfolio not found",
            )
        return portfolio

    @staticmethod
    def _validate_date_range(date_from: date | None, date_to: date | None) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )

    @staticmethod
    def _validate_cycle_limit(value: int) -> None:
        LivePaperMonitoringService._validate_limit(
            value,
            lower=1,
            upper=200,
            detail="cycle_limit must be between 1 and 200",
        )

    @staticmethod
    def _validate_limit(value: int, *, lower: int, upper: int, detail: str) -> None:
        if value < lower or value > upper:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=detail,
            )

    @staticmethod
    def _utc_now(value: datetime | None = None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        return LivePaperMonitoringService._as_utc(value)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _alert(
        level: str,
        code: str,
        message: str,
        **details: Any,
    ) -> LivePaperMonitoringAlert:
        return LivePaperMonitoringAlert(
            level=level,
            code=code,
            message=message,
            details=details,
        )
