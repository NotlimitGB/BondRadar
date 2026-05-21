from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonCandidateRequest,
    MLCandidateComparisonRequest,
)
from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessRequest,
)
from app.schemas.paper_trading import PaperPortfolioRebalanceRequest
from app.schemas.paper_trading_live_cycle import LivePaperCycleRunRequest
from app.schemas.paper_trading_live_monitoring import (
    LivePaperMonitoringOverviewResponse,
)
from app.schemas.paper_trading_live_pilot import (
    LivePaperPilotBootstrapNextStep,
    LivePaperPilotBootstrapPayloads,
    LivePaperPilotBootstrapRequest,
    LivePaperPilotBootstrapResponse,
)
from app.schemas.paper_trading_live_readiness import (
    LivePaperReadinessRequest,
    LivePaperReadinessResponse,
)
from app.schemas.paper_trading_live_schedule import (
    LivePaperScheduleCreate,
    LivePaperScheduleRead,
)
from app.schemas.strategy_experiment import (
    StrategyExperimentCompareRequest,
    StrategyExperimentVariantRequest,
)
from app.schemas.strategy_robustness import StrategyRobustnessAnalyzeRequest
from app.services.paper_trading_live_monitoring_service import (
    LivePaperMonitoringService,
)
from app.services.paper_trading_live_readiness_service import (
    LivePaperReadinessService,
)
from app.services.paper_trading_live_schedule_service import (
    LivePaperScheduleService,
)
from app.services.paper_trading_risk_policy import (
    paper_risk_policy_payload,
    risk_override_warning,
    validate_paper_risk_policy,
)


class LivePaperPilotBootstrapService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def bootstrap(
        self,
        request: LivePaperPilotBootstrapRequest,
    ) -> LivePaperPilotBootstrapResponse:
        self._validate_request(request)
        readiness_request = self._readiness_request(request)
        cycle_request = self._cycle_request(request, readiness_request)
        schedule_request = self._schedule_request(request, cycle_request)

        readiness = LivePaperReadinessService(self.db).check(readiness_request)
        warnings = self._readiness_warnings(readiness)
        warnings.extend(self._configuration_warnings(request))
        blocked_message = self._blocked_message(request, readiness)
        schedule_read: LivePaperScheduleRead | None = None

        if blocked_message is not None:
            status_value = "blocked"
            warnings.append(
                {
                    "message": blocked_message,
                    "details": {"readiness_status": readiness.readiness_status},
                }
            )
        elif request.dry_run_only or not request.create_schedule:
            status_value = "prepared"
        else:
            schedule = LivePaperScheduleService(self.db).create(schedule_request)
            schedule_read = LivePaperScheduleRead.model_validate(schedule)
            status_value = "scheduled"

        monitoring_overview = self._monitoring_overview(request)
        payloads = LivePaperPilotBootstrapPayloads(
            readiness_request=readiness_request.model_dump(mode="json"),
            cycle_request=cycle_request.model_dump(mode="json"),
            schedule_request=schedule_request.model_dump(mode="json"),
        )

        return LivePaperPilotBootstrapResponse(
            status=status_value,
            created_schedule_id=None if schedule_read is None else schedule_read.id,
            readiness_status=readiness.readiness_status,
            selected_model_run_id=(
                None
                if readiness.selected_candidate is None
                else readiness.selected_candidate.model_run_id
            ),
            virtual_initial_capital=request.virtual_initial_capital,
            planned_duration_days=request.planned_duration_days,
            next_run_at=request.next_run_at,
            interval_days=request.interval_days,
            max_runs=request.max_runs,
            readiness=readiness,
            schedule=schedule_read,
            monitoring_overview=monitoring_overview,
            payloads=payloads,
            next_steps=self._next_steps(status_value, payloads),
            warnings=warnings,
            errors=[],
        )

    @staticmethod
    def _validate_request(request: LivePaperPilotBootstrapRequest) -> None:
        if not request.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="name must not be blank",
            )
        if request.model_run_id <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_run_id must be positive",
            )
        if request.virtual_initial_capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="virtual_initial_capital must be positive",
            )
        if request.planned_duration_days < 1 or request.planned_duration_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="planned_duration_days must be between 1 and 365",
            )
        if request.date_from > request.date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_from must be before or equal to date_to",
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
        if request.top_n < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="top_n must be positive",
            )
        if not LivePaperPilotBootstrapService._between_zero_and_one(
            request.min_probability_positive
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_probability_positive must be between 0 and 1",
            )
        if not LivePaperPilotBootstrapService._between_zero_and_one(
            request.max_position_weight
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_position_weight must be between 0 and 1",
            )
        if not LivePaperPilotBootstrapService._between_zero_and_one(
            request.max_issuer_weight
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_issuer_weight must be between 0 and 1",
            )
        if not LivePaperPilotBootstrapService._between_zero_and_one(
            request.max_high_risk_weight
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_high_risk_weight must be between 0 and 1",
            )
        if request.transaction_cost_rate < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="transaction_cost_rate must be non-negative",
            )
        validate_paper_risk_policy(request)

    @staticmethod
    def _between_zero_and_one(value: Decimal) -> bool:
        return Decimal("0") <= value <= Decimal("1")

    @staticmethod
    def _readiness_request(
        request: LivePaperPilotBootstrapRequest,
    ) -> LivePaperReadinessRequest:
        candidate_name = f"pilot_model_run_{request.model_run_id}"
        comparison = MLCandidateComparisonRequest(
            candidates=[
                MLCandidateComparisonCandidateRequest(
                    name=candidate_name,
                    model_run_id=request.model_run_id,
                )
            ],
            return_method=request.return_method,
            horizon_days=request.horizon_days,
            ranking_metric="probability_separation",
            ranking_direction="desc",
            include_prediction_quality=True,
            include_failed_candidates=True,
            minimum_evaluable_predictions=2,
            minimum_positive_labels=1,
            minimum_negative_labels=1,
            maximum_missing_label_ratio=Decimal("0.50"),
            limit=100,
            offset=0,
        )
        variant = StrategyExperimentVariantRequest(
            name="pilot_top_n",
            top_n=request.top_n,
            min_probability_positive=request.min_probability_positive,
            use_portfolio_constraints=request.use_portfolio_constraints,
            max_position_weight=request.max_position_weight,
            max_issuer_weight=request.max_issuer_weight,
            max_high_risk_weight=request.max_high_risk_weight,
            min_liquidity_score=request.min_liquidity_score,
            exclude_blocked_by_risk=request.exclude_blocked_by_risk,
            exclude_insufficient_credit_data=request.exclude_insufficient_credit_data,
            allowed_risk_levels=request.allowed_risk_levels,
            allowed_decision_statuses=request.allowed_decision_statuses,
        )
        experiment = StrategyExperimentCompareRequest(
            model_run_id=request.model_run_id,
            date_from=request.date_from,
            date_to=request.date_to,
            initial_capital=request.virtual_initial_capital,
            transaction_cost_rate=request.transaction_cost_rate,
            ranking_metric="total_return",
            ranking_direction="desc",
            include_periods=False,
            include_baselines=False,
            variants=[variant],
        )
        robustness = StrategyRobustnessAnalyzeRequest(
            experiment=experiment,
            selected_variant_count=1,
            subperiod_mode="monthly",
            include_subperiod_details=False,
            include_candidate_concentration=False,
            minimum_completed_subperiods=1,
        )
        bridge_request = MLCandidateStrategyRobustnessRequest(
            candidate_comparison=comparison,
            strategy_robustness=robustness,
            require_ready_candidate=True,
            include_candidate_comparison=True,
            include_robustness_subperiod_details=False,
            include_robustness_candidate_concentration=False,
        )
        return LivePaperReadinessRequest(
            candidate_strategy_robustness=bridge_request,
            virtual_initial_capital=request.virtual_initial_capital,
            planned_duration_days=request.planned_duration_days,
            include_candidate_comparison=True,
            include_robustness_analysis=True,
            minimum_analyzed_variant_count=1,
            minimum_completed_subperiods=1,
            allow_warning_flags=True,
            maximum_warning_flag_count=None,
        )

    @staticmethod
    def _cycle_request(
        request: LivePaperPilotBootstrapRequest,
        readiness_request: LivePaperReadinessRequest,
    ) -> LivePaperCycleRunRequest:
        rebalance = PaperPortfolioRebalanceRequest(
            model_run_id=request.model_run_id,
            as_of_date=request.date_to,
            top_n=request.top_n,
            min_probability_positive=request.min_probability_positive,
            max_position_weight=request.max_position_weight,
            max_issuer_weight=request.max_issuer_weight,
            max_high_risk_weight=request.max_high_risk_weight,
            min_liquidity_score=request.min_liquidity_score,
            exclude_blocked_by_risk=request.exclude_blocked_by_risk,
            exclude_insufficient_credit_data=request.exclude_insufficient_credit_data,
            allowed_risk_levels=request.allowed_risk_levels,
            allowed_decision_statuses=request.allowed_decision_statuses,
            risk_override_enabled=request.risk_override_enabled,
            risk_override_reason=request.risk_override_reason,
            transaction_cost_rate=request.transaction_cost_rate,
            include_excluded_candidates=True,
        )
        return LivePaperCycleRunRequest(
            readiness=readiness_request,
            portfolio_id=None,
            create_portfolio_if_missing=True,
            portfolio_name=request.name,
            portfolio_description=request.description,
            as_of_date=request.date_to,
            allow_readiness_warning=request.allow_readiness_warning,
            allow_not_ready=request.allow_not_ready,
            mark_period_before_rebalance=False,
            rebalance=rebalance,
            include_readiness_report=True,
            include_rebalance_result=True,
            include_mark_period_result=True,
        )

    @staticmethod
    def _schedule_request(
        request: LivePaperPilotBootstrapRequest,
        cycle_request: LivePaperCycleRunRequest,
    ) -> LivePaperScheduleCreate:
        return LivePaperScheduleCreate(
            name=request.name,
            cycle_request=cycle_request,
            next_run_at=request.next_run_at,
            interval_days=request.interval_days,
            max_runs=request.max_runs,
            status="active",
            use_current_date_as_of_date=request.use_current_date_as_of_date,
        )

    @staticmethod
    def _readiness_warnings(
        readiness: LivePaperReadinessResponse,
    ) -> list[dict[str, Any]]:
        warnings = [
            warning.model_dump(mode="json")
            for warning in readiness.warnings
        ]
        return warnings

    @staticmethod
    def _configuration_warnings(
        request: LivePaperPilotBootstrapRequest,
    ) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = []
        override_warning = risk_override_warning(request)
        if override_warning is not None:
            warnings.append(override_warning)
        if request.use_current_date_as_of_date:
            warnings.append(
                {
                    "message": (
                        "Current-date schedule mode requires refreshed predictions "
                        "before each paper execution"
                    ),
                    "details": {
                        "use_current_date_as_of_date": True,
                        "fixed_prediction_date": request.date_to.isoformat(),
                        "risk_policy": paper_risk_policy_payload(request),
                    },
                }
            )
        return warnings

    @staticmethod
    def _blocked_message(
        request: LivePaperPilotBootstrapRequest,
        readiness: LivePaperReadinessResponse,
    ) -> str | None:
        if readiness.readiness_status == "not_ready" and not request.allow_not_ready:
            return "Readiness status blocked pilot schedule creation"
        if (
            readiness.readiness_status == "warning"
            and not request.allow_readiness_warning
        ):
            return "Readiness warning status blocked pilot schedule creation"
        return None

    def _monitoring_overview(
        self,
        request: LivePaperPilotBootstrapRequest,
    ) -> LivePaperMonitoringOverviewResponse | None:
        if not request.include_monitoring_overview:
            return None
        return LivePaperMonitoringService(self.db).overview(
            schedule_limit=20,
            portfolio_limit=20,
            cycle_limit=20,
        )

    @staticmethod
    def _next_steps(
        status_value: str,
        payloads: LivePaperPilotBootstrapPayloads,
    ) -> list[LivePaperPilotBootstrapNextStep]:
        if status_value == "scheduled":
            return [
                LivePaperPilotBootstrapNextStep(
                    label="Run scheduler dry run",
                    method="POST",
                    path="/api/paper-trading/live/schedules/run-due",
                    body={"dry_run": True},
                    description="Preview due live paper schedules without execution",
                ),
                LivePaperPilotBootstrapNextStep(
                    label="Run due schedules",
                    method="POST",
                    path="/api/paper-trading/live/schedules/run-due",
                    body={"dry_run": False},
                    description="Execute due live paper schedules when ready",
                ),
                LivePaperPilotBootstrapNextStep(
                    label="Open monitoring overview",
                    method="GET",
                    path="/api/paper-trading/live/monitoring/overview",
                    description="Review live paper system status",
                ),
                LivePaperPilotBootstrapNextStep(
                    label="Inspect live cycles",
                    method="GET",
                    path="/api/paper-trading/live/monitoring/cycles",
                    description="Review recent live paper cycle results",
                ),
            ]
        if status_value == "blocked":
            return [
                LivePaperPilotBootstrapNextStep(
                    label="Review readiness gates",
                    method="POST",
                    path="/api/paper-trading/live/readiness",
                    body=payloads.readiness_request,
                    description="Review readiness diagnostics before schedule creation",
                ),
                LivePaperPilotBootstrapNextStep(
                    label="Open monitoring overview",
                    method="GET",
                    path="/api/paper-trading/live/monitoring/overview",
                    description="Review live paper system status",
                ),
                LivePaperPilotBootstrapNextStep(
                    label="Prepare bootstrap again",
                    method="POST",
                    path="/api/paper-trading/live/pilots/bootstrap",
                    description="Run pilot preparation again after diagnostics are updated",
                ),
            ]
        return [
            LivePaperPilotBootstrapNextStep(
                label="Review readiness report",
                method="POST",
                path="/api/paper-trading/live/readiness",
                body=payloads.readiness_request,
                description="Run the returned readiness payload again if needed",
            ),
            LivePaperPilotBootstrapNextStep(
                label="Create live paper schedule",
                method="POST",
                path="/api/paper-trading/live/schedules",
                body=payloads.schedule_request,
                description="Create the schedule from the returned payload",
            ),
            LivePaperPilotBootstrapNextStep(
                label="Run scheduler dry run",
                method="POST",
                path="/api/paper-trading/live/schedules/run-due",
                body={"dry_run": True},
                description="Preview due live paper schedules without execution",
            ),
            LivePaperPilotBootstrapNextStep(
                label="Open monitoring overview",
                method="GET",
                path="/api/paper-trading/live/monitoring/overview",
                description="Review live paper system status",
            ),
        ]
