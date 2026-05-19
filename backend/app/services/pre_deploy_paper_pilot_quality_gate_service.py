from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.ml_model_run import MLModelRun
from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonCandidateRequest,
    MLCandidateComparisonRequest,
)
from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessRequest,
    MLCandidateStrategyRobustnessResponse,
)
from app.schemas.paper_trading_live_pilot import LivePaperPilotBootstrapRequest
from app.schemas.paper_trading_live_readiness import LivePaperReadinessRequest
from app.schemas.paper_trading_live_schedule import LivePaperScheduleRunDueRequest
from app.schemas.pre_deploy_paper_pilot_quality_gate import (
    PreDeployPaperPilotQualityGateRequest,
    PreDeployPaperPilotQualityGateResponse,
    PreDeployQualityGateCommand,
    PreDeployQualityGateItem,
)
from app.schemas.strategy_experiment import (
    StrategyExperimentCompareRequest,
    StrategyExperimentVariantRequest,
)
from app.schemas.strategy_robustness import StrategyRobustnessAnalyzeRequest
from app.services.corporate_universe_action_plan_service import (
    CorporateUniverseActionPlanService,
)
from app.services.external_risk_regime_service import ExternalRiskRegimeService
from app.services.live_data_readiness_service import LiveDataReadinessService
from app.services.ml_candidate_comparison_service import RANKING_DIRECTIONS
from app.services.ml_candidate_strategy_robustness_service import (
    MLCandidateStrategyRobustnessService,
)
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.paper_trading_live_pilot_service import (
    LivePaperPilotBootstrapService,
)
from app.services.paper_trading_live_readiness_service import LivePaperReadinessService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService


CORE_GATE_CODES = {
    "corporate_universe_ready",
    "live_data_ready",
    "model_run_available",
    "strategy_robustness_ready",
    "live_paper_readiness_ready",
    "external_risk_regime_ready",
    "pilot_bootstrap_dry_run_ready",
}


class PreDeployPaperPilotQualityGateService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def check(
        self,
        request: PreDeployPaperPilotQualityGateRequest,
    ) -> PreDeployPaperPilotQualityGateResponse:
        self._validate_request(request)
        as_of = datetime.now(timezone.utc)
        bootstrap_next_run_at = request.next_run_at or as_of + timedelta(days=1)
        scheduler_now = request.next_run_at or as_of

        gates: list[PreDeployQualityGateItem] = []
        warnings: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        next_steps: list[str] = []

        corporate_plan = CorporateUniverseActionPlanService(self.db).plan(
            minimum_corporate_bonds=request.minimum_corporate_bonds,
            include_ofz=request.include_ofz,
        )
        gates.append(self._corporate_universe_gate(request, corporate_plan))

        live_data = LiveDataReadinessService(self.db).check(
            recent_days=request.recent_days,
            minimum_corporate_bonds=request.minimum_corporate_bonds,
            minimum_bonds_with_recent_market_snapshot=(
                request.minimum_bonds_with_recent_market_snapshot
            ),
            minimum_bonds_with_recent_features=(
                request.minimum_bonds_with_recent_features
            ),
            minimum_bonds_with_predictions=request.minimum_bonds_with_predictions,
            include_ofz=request.include_ofz,
        )
        gates.append(self._live_data_gate(request, live_data))

        model_run = self.db.get(MLModelRun, request.model_run_id)
        gates.append(self._model_run_gate(request, model_run))

        robustness_result: MLCandidateStrategyRobustnessResponse | None = None
        live_readiness = None
        bootstrap_result = None
        scheduler_result = None
        external_risk_regime = None
        bridge_request = self._candidate_strategy_request(request)
        live_readiness_request = self._live_paper_readiness_request(
            request,
            bridge_request,
        )
        pilot_bootstrap_request = self._pilot_bootstrap_request(
            request,
            bootstrap_next_run_at,
        )
        scheduler_request = LivePaperScheduleRunDueRequest(
            now=scheduler_now,
            limit=10,
            dry_run=True,
            lock_minutes=10,
        )

        if self._can_continue_core(gates):
            try:
                robustness_result = MLCandidateStrategyRobustnessService(
                    self.db
                ).analyze(bridge_request)
                gates.append(self._strategy_robustness_gate(request, robustness_result))
            except HTTPException as exc:
                gates.append(
                    self._gate(
                        "strategy_robustness_ready",
                        "failed",
                        "Strategy robustness check failed",
                        {"error": exc.detail},
                    )
                )
            except Exception as exc:
                gates.append(
                    self._gate(
                        "strategy_robustness_ready",
                        "failed",
                        "Strategy robustness check failed",
                        {"error": str(exc)},
                    )
                )
        else:
            gates.append(
                self._gate(
                    "strategy_robustness_ready",
                    "skipped",
                    "Strategy robustness check was skipped because earlier core gates failed",
                )
            )

        if self._can_continue_core(gates):
            try:
                live_readiness = LivePaperReadinessService(self.db).check(
                    live_readiness_request
                )
                gates.append(self._live_paper_readiness_gate(request, live_readiness))
            except HTTPException as exc:
                gates.append(
                    self._gate(
                        "live_paper_readiness_ready",
                        "failed",
                        "Live paper readiness check failed",
                        {"error": exc.detail},
                    )
                )
            except Exception as exc:
                gates.append(
                    self._gate(
                        "live_paper_readiness_ready",
                        "failed",
                        "Live paper readiness check failed",
                        {"error": str(exc)},
                    )
                )
        else:
            gates.append(
                self._gate(
                    "live_paper_readiness_ready",
                    "skipped",
                    "Live paper readiness check was skipped because earlier core gates failed",
                )
            )

        external_risk_regime = ExternalRiskRegimeService(self.db).current()
        gates.append(self._external_risk_gate(request, external_risk_regime))

        if self._can_continue_core(gates):
            try:
                bootstrap_result = LivePaperPilotBootstrapService(self.db).bootstrap(
                    pilot_bootstrap_request
                )
                gates.append(self._pilot_bootstrap_gate(bootstrap_result))
            except HTTPException as exc:
                gates.append(
                    self._gate(
                        "pilot_bootstrap_dry_run_ready",
                        "failed",
                        "Pilot bootstrap dry-run failed",
                        {"error": exc.detail},
                    )
                )
            except Exception as exc:
                gates.append(
                    self._gate(
                        "pilot_bootstrap_dry_run_ready",
                        "failed",
                        "Pilot bootstrap dry-run failed",
                        {"error": str(exc)},
                    )
                )
        else:
            gates.append(
                self._gate(
                    "pilot_bootstrap_dry_run_ready",
                    "skipped",
                    "Pilot bootstrap dry-run was skipped because earlier core gates failed",
                )
            )

        if request.include_scheduler_dry_run:
            try:
                scheduler_result = LivePaperScheduleService(self.db).run_due(
                    scheduler_request
                )
                gates.append(self._scheduler_dry_run_gate(scheduler_result))
            except HTTPException as exc:
                gates.append(
                    self._gate(
                        "scheduler_dry_run_ready",
                        "failed",
                        "Scheduler dry-run failed",
                        {"error": exc.detail},
                    )
                )
            except Exception as exc:
                gates.append(
                    self._gate(
                        "scheduler_dry_run_ready",
                        "failed",
                        "Scheduler dry-run failed",
                        {"error": str(exc)},
                    )
                )
        else:
            gates.append(
                self._gate(
                    "scheduler_dry_run_ready",
                    "skipped",
                    "Scheduler dry-run was skipped by request",
                )
            )

        gates.extend(self._manual_gates())
        warnings.extend(self._warnings_from_gates(gates))
        errors.extend(self._errors_from_gates(gates))
        next_steps.extend(self._next_steps(gates))

        ready_for_50k = self._ready_for_50k(gates)
        ready_for_vds = all(gate.status == "passed" for gate in gates)
        status_value = self._status(gates)
        payloads = (
            {
                "candidate_strategy_robustness_request": bridge_request.model_dump(
                    mode="json"
                ),
                "live_paper_readiness_request": live_readiness_request.model_dump(
                    mode="json"
                ),
                "pilot_bootstrap_dry_run_request": pilot_bootstrap_request.model_dump(
                    mode="json"
                ),
                "scheduler_dry_run_request": scheduler_request.model_dump(mode="json"),
            }
            if request.include_detailed_payloads
            else {}
        )

        return PreDeployPaperPilotQualityGateResponse(
            status=status_value,
            as_of=as_of,
            ready_for_vds_deploy=ready_for_vds,
            ready_for_50k_paper_pilot=ready_for_50k,
            model_run_id=request.model_run_id,
            return_method=request.return_method,
            horizon_days=request.horizon_days,
            date_from=request.date_from,
            date_to=request.date_to,
            gates=gates,
            warnings=warnings,
            errors=errors,
            next_steps=next_steps,
            corporate_universe_action_plan=corporate_plan.model_dump(mode="json"),
            live_data_readiness=live_data.model_dump(mode="json"),
            external_risk_regime=(
                external_risk_regime.model_dump(mode="json")
                if external_risk_regime is not None
                else None
            ),
            strategy_robustness=(
                robustness_result.model_dump(mode="json")
                if robustness_result is not None
                else None
            ),
            live_paper_readiness=(
                live_readiness.model_dump(mode="json")
                if live_readiness is not None
                else None
            ),
            pilot_bootstrap_dry_run=(
                bootstrap_result.model_dump(mode="json")
                if bootstrap_result is not None
                else None
            ),
            scheduler_dry_run=(
                scheduler_result.model_dump(mode="json")
                if scheduler_result is not None
                else None
            ),
            commands=self._commands(request, payloads),
            payloads=payloads,
        )

    @staticmethod
    def _validate_request(request: PreDeployPaperPilotQualityGateRequest) -> None:
        checks: list[tuple[bool, str]] = [
            (request.model_run_id > 0, "model_run_id must be positive"),
            (1 <= request.recent_days <= 365, "recent_days must be between 1 and 365"),
            (
                request.minimum_corporate_bonds >= 0,
                "minimum_corporate_bonds must be non-negative",
            ),
            (
                request.minimum_bonds_with_recent_market_snapshot >= 0,
                "minimum_bonds_with_recent_market_snapshot must be non-negative",
            ),
            (
                request.minimum_bonds_with_recent_features >= 0,
                "minimum_bonds_with_recent_features must be non-negative",
            ),
            (
                request.minimum_bonds_with_predictions >= 0,
                "minimum_bonds_with_predictions must be non-negative",
            ),
            (
                request.date_from <= request.date_to,
                "date_from must be before or equal to date_to",
            ),
            (
                1 <= request.horizon_days <= 365,
                "horizon_days must be between 1 and 365",
            ),
            (
                request.return_method in RETURN_METHODS,
                "return_method must be supported",
            ),
            (
                request.ranking_direction in RANKING_DIRECTIONS,
                "ranking_direction must be asc or desc",
            ),
            (request.top_n > 0, "top_n must be positive"),
            (
                Decimal("0") <= request.min_probability_positive <= Decimal("1"),
                "min_probability_positive must be between 0 and 1",
            ),
            (
                Decimal("0") <= request.positive_probability_cutoff <= Decimal("1"),
                "positive_probability_cutoff must be between 0 and 1",
            ),
            (request.initial_capital > 0, "initial_capital must be positive"),
            (
                request.virtual_initial_capital > 0,
                "virtual_initial_capital must be positive",
            ),
            (
                1 <= request.planned_duration_days <= 365,
                "planned_duration_days must be between 1 and 365",
            ),
            (request.interval_days > 0, "interval_days must be positive"),
            (
                request.max_runs is None or request.max_runs > 0,
                "max_runs must be positive when provided",
            ),
            (
                request.transaction_cost_rate >= 0,
                "transaction_cost_rate must be non-negative",
            ),
            (
                request.minimum_analyzed_variant_count > 0,
                "minimum_analyzed_variant_count must be positive",
            ),
            (
                request.minimum_completed_subperiods > 0,
                "minimum_completed_subperiods must be positive",
            ),
            (
                request.maximum_warning_flag_count is None
                or request.maximum_warning_flag_count >= 0,
                "maximum_warning_flag_count must be non-negative when provided",
            ),
        ]
        for is_valid, detail in checks:
            if not is_valid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=detail,
                )

    @staticmethod
    def _candidate_strategy_request(
        request: PreDeployPaperPilotQualityGateRequest,
    ) -> MLCandidateStrategyRobustnessRequest:
        candidate_name = f"pre_deploy_model_run_{request.model_run_id}"
        comparison = MLCandidateComparisonRequest(
            candidates=[
                MLCandidateComparisonCandidateRequest(
                    name=candidate_name,
                    model_run_id=request.model_run_id,
                )
            ],
            date_from=request.date_from,
            date_to=request.date_to,
            return_method=request.return_method,
            horizon_days=request.horizon_days,
            positive_probability_cutoff=request.positive_probability_cutoff,
            ranking_metric=request.ranking_metric,
            ranking_direction=request.ranking_direction,
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
            name="pre_deploy_top_n",
            top_n=request.top_n,
            min_probability_positive=request.min_probability_positive,
            use_portfolio_constraints=True,
            max_position_weight=Decimal("0.20"),
            max_issuer_weight=Decimal("0.30"),
            max_high_risk_weight=Decimal("0.20"),
        )
        experiment = StrategyExperimentCompareRequest(
            model_run_id=request.model_run_id,
            date_from=request.date_from,
            date_to=request.date_to,
            initial_capital=request.initial_capital,
            transaction_cost_rate=request.transaction_cost_rate,
            variants=[variant],
            ranking_metric="total_return",
            ranking_direction="desc",
            include_periods=False,
            include_baselines=False,
        )
        robustness = StrategyRobustnessAnalyzeRequest(
            experiment=experiment,
            selected_variant_count=1,
            subperiod_mode="monthly",
            include_subperiod_details=False,
            include_candidate_concentration=False,
            minimum_completed_subperiods=request.minimum_completed_subperiods,
        )
        return MLCandidateStrategyRobustnessRequest(
            candidate_comparison=comparison,
            strategy_robustness=robustness,
            require_ready_candidate=True,
            include_candidate_comparison=True,
            include_robustness_subperiod_details=False,
            include_robustness_candidate_concentration=False,
        )

    @staticmethod
    def _live_paper_readiness_request(
        request: PreDeployPaperPilotQualityGateRequest,
        bridge_request: MLCandidateStrategyRobustnessRequest,
    ) -> LivePaperReadinessRequest:
        return LivePaperReadinessRequest(
            candidate_strategy_robustness=bridge_request,
            virtual_initial_capital=request.virtual_initial_capital,
            planned_duration_days=request.planned_duration_days,
            include_candidate_comparison=True,
            include_robustness_analysis=True,
            minimum_analyzed_variant_count=request.minimum_analyzed_variant_count,
            minimum_completed_subperiods=request.minimum_completed_subperiods,
            allow_warning_flags=request.allow_live_paper_warning,
            maximum_warning_flag_count=request.maximum_warning_flag_count,
        )

    @staticmethod
    def _pilot_bootstrap_request(
        request: PreDeployPaperPilotQualityGateRequest,
        next_run_at: datetime,
    ) -> LivePaperPilotBootstrapRequest:
        return LivePaperPilotBootstrapRequest(
            name="50k pre-deploy paper pilot",
            description="Dry-run preparation for virtual paper pilot",
            model_run_id=request.model_run_id,
            return_method=request.return_method,
            horizon_days=request.horizon_days,
            virtual_initial_capital=request.virtual_initial_capital,
            planned_duration_days=request.planned_duration_days,
            date_from=request.date_from,
            date_to=request.date_to,
            next_run_at=next_run_at,
            interval_days=request.interval_days,
            max_runs=request.max_runs,
            create_schedule=True,
            dry_run_only=True,
            allow_readiness_warning=request.allow_live_paper_warning,
            allow_not_ready=False,
            top_n=request.top_n,
            min_probability_positive=request.min_probability_positive,
            use_portfolio_constraints=True,
            max_position_weight=Decimal("0.20"),
            max_issuer_weight=Decimal("0.30"),
            max_high_risk_weight=Decimal("0.20"),
            transaction_cost_rate=request.transaction_cost_rate,
            include_monitoring_overview=True,
        )

    @staticmethod
    def _corporate_universe_gate(
        request: PreDeployPaperPilotQualityGateRequest,
        plan: Any,
    ) -> PreDeployQualityGateItem:
        details = {
            "status": plan.status,
            "can_continue_to_data_pipeline": plan.can_continue_to_data_pipeline,
            "local_corporate_bond_count": plan.local_corporate_bond_count,
            "configured_minimum": request.minimum_corporate_bonds,
        }
        if plan.status == "ready" and plan.can_continue_to_data_pipeline:
            return PreDeployPaperPilotQualityGateService._gate(
                "corporate_universe_ready",
                "passed",
                "Corporate bond universe is ready",
                details,
            )
        if plan.status == "needs_sync" and request.allow_data_warning:
            return PreDeployPaperPilotQualityGateService._gate(
                "corporate_universe_ready",
                "warning",
                "Corporate bond universe has warnings accepted by request",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "corporate_universe_ready",
            "failed",
            "Corporate bond universe is not ready",
            details,
        )

    @staticmethod
    def _live_data_gate(
        request: PreDeployPaperPilotQualityGateRequest,
        readiness: Any,
    ) -> PreDeployQualityGateItem:
        details = {"status": readiness.status}
        if readiness.status == "ready":
            return PreDeployPaperPilotQualityGateService._gate(
                "live_data_ready",
                "passed",
                "Live data readiness is ready",
                details,
            )
        if readiness.status == "warning" and request.allow_data_warning:
            return PreDeployPaperPilotQualityGateService._gate(
                "live_data_ready",
                "warning",
                "Live data readiness warning is accepted by request",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "live_data_ready",
            "failed",
            "Live data readiness is not acceptable",
            details,
        )

    @staticmethod
    def _model_run_gate(
        request: PreDeployPaperPilotQualityGateRequest,
        model_run: MLModelRun | None,
    ) -> PreDeployQualityGateItem:
        if model_run is None:
            return PreDeployPaperPilotQualityGateService._gate(
                "model_run_available",
                "failed",
                "Selected model run was not found",
                {"model_run_id": request.model_run_id},
            )
        details: dict[str, Any] = {
            "model_run_id": model_run.id,
            "status": model_run.status,
            "horizon_days": model_run.horizon_days,
            "requested_horizon_days": request.horizon_days,
        }
        params = model_run.params if isinstance(model_run.params, dict) else {}
        stored_return_method = params.get("return_method")
        details["stored_return_method"] = stored_return_method
        details["requested_return_method"] = request.return_method
        if model_run.status != "completed":
            return PreDeployPaperPilotQualityGateService._gate(
                "model_run_available",
                "failed",
                "Selected model run is not completed",
                details,
            )
        if model_run.horizon_days != request.horizon_days:
            return PreDeployPaperPilotQualityGateService._gate(
                "model_run_available",
                "failed",
                "Selected model run horizon does not match request",
                details,
            )
        if stored_return_method is not None and stored_return_method != request.return_method:
            return PreDeployPaperPilotQualityGateService._gate(
                "model_run_available",
                "failed",
                "Selected model run return method does not match request",
                details,
            )
        if stored_return_method is None:
            return PreDeployPaperPilotQualityGateService._gate(
                "model_run_available",
                "warning",
                "Selected model run return method is not stored",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "model_run_available",
            "passed",
            "Selected model run is available",
            details,
        )

    @staticmethod
    def _strategy_robustness_gate(
        request: PreDeployPaperPilotQualityGateRequest,
        result: MLCandidateStrategyRobustnessResponse,
    ) -> PreDeployQualityGateItem:
        selected = result.selected_candidate
        robustness = result.robustness_analysis
        variants = [
            variant
            for variant in (robustness or {}).get("variants", [])
            if isinstance(variant, dict)
        ]
        fail_flags = PreDeployPaperPilotQualityGateService._flags_by_level(
            variants,
            "fail",
        )
        warning_flags = PreDeployPaperPilotQualityGateService._flags_by_level(
            variants,
            "warning",
        )
        completed_subperiod_count = max(
            [
                int(variant.get("completed_subperiod_count") or 0)
                for variant in variants
            ]
            or [0]
        )
        analyzed_variant_count = (
            0 if robustness is None else int(robustness.get("analyzed_variant_count") or 0)
        )
        details = {
            "selected_model_run_id": None if selected is None else selected.model_run_id,
            "selected_candidate_ready": (
                None if selected is None else selected.ready_for_strategy_research
            ),
            "analyzed_variant_count": analyzed_variant_count,
            "minimum_analyzed_variant_count": request.minimum_analyzed_variant_count,
            "completed_subperiod_count": completed_subperiod_count,
            "minimum_completed_subperiods": request.minimum_completed_subperiods,
            "fail_flag_count": len(fail_flags),
            "warning_flag_count": len(warning_flags),
            "service_warning_count": len(result.warnings),
            "fail_flags": fail_flags,
            "warning_flags": warning_flags,
        }
        failed = (
            selected is None
            or not selected.ready_for_strategy_research
            or robustness is None
            or analyzed_variant_count < request.minimum_analyzed_variant_count
            or completed_subperiod_count < request.minimum_completed_subperiods
            or bool(fail_flags)
        )
        if failed:
            return PreDeployPaperPilotQualityGateService._gate(
                "strategy_robustness_ready",
                "failed",
                "Strategy robustness is not ready",
                details,
            )
        if warning_flags or result.warnings:
            if request.allow_robustness_warning:
                return PreDeployPaperPilotQualityGateService._gate(
                    "strategy_robustness_ready",
                    "warning",
                    "Strategy robustness warnings are accepted by request",
                    details,
                )
            return PreDeployPaperPilotQualityGateService._gate(
                "strategy_robustness_ready",
                "failed",
                "Strategy robustness has warnings that are not accepted",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "strategy_robustness_ready",
            "passed",
            "Strategy robustness is ready",
            details,
        )

    @staticmethod
    def _live_paper_readiness_gate(
        request: PreDeployPaperPilotQualityGateRequest,
        readiness: Any,
    ) -> PreDeployQualityGateItem:
        details = {"readiness_status": readiness.readiness_status}
        if readiness.readiness_status == "ready":
            return PreDeployPaperPilotQualityGateService._gate(
                "live_paper_readiness_ready",
                "passed",
                "Live paper readiness is ready",
                details,
            )
        if readiness.readiness_status == "warning" and request.allow_live_paper_warning:
            return PreDeployPaperPilotQualityGateService._gate(
                "live_paper_readiness_ready",
                "warning",
                "Live paper readiness warning is accepted by request",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "live_paper_readiness_ready",
            "failed",
            "Live paper readiness is not acceptable",
            details,
        )

    @staticmethod
    def _external_risk_gate(
        request: PreDeployPaperPilotQualityGateRequest,
        regime: Any,
    ) -> PreDeployQualityGateItem:
        details = {
            "mode": regime.mode,
            "reason": regime.reason,
            "source": regime.source,
            "expires_at": regime.expires_at.isoformat()
            if regime.expires_at is not None
            else None,
            "is_active": regime.is_active,
            "accepted": False,
            "external_risk_override_used": False,
        }
        if regime.mode == "normal":
            details["accepted"] = True
            return PreDeployPaperPilotQualityGateService._gate(
                "external_risk_regime_ready",
                "passed",
                "External risk regime is normal",
                details,
            )
        if regime.mode == "elevated":
            details["accepted"] = request.allow_external_risk_warning
            return PreDeployPaperPilotQualityGateService._gate(
                "external_risk_regime_ready",
                "warning",
                "External risk regime requires manual review before virtual pilot execution",
                details,
            )
        if request.allow_external_risk_severe:
            details["accepted"] = True
            details["external_risk_override_used"] = True
            return PreDeployPaperPilotQualityGateService._gate(
                "external_risk_regime_ready",
                "warning",
                "External risk regime is severe and override was explicitly allowed",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "external_risk_regime_ready",
            "failed",
            "External risk regime is severe and blocks virtual pilot execution",
            details,
        )

    @staticmethod
    def _pilot_bootstrap_gate(
        result: Any,
    ) -> PreDeployQualityGateItem:
        details = {
            "status": result.status,
            "created_schedule_id": result.created_schedule_id,
            "readiness_status": result.readiness_status,
        }
        if result.created_schedule_id is not None:
            return PreDeployPaperPilotQualityGateService._gate(
                "pilot_bootstrap_dry_run_ready",
                "failed",
                "Pilot bootstrap dry-run created a schedule unexpectedly",
                details,
            )
        if result.status in {"prepared", "scheduled"}:
            return PreDeployPaperPilotQualityGateService._gate(
                "pilot_bootstrap_dry_run_ready",
                "passed",
                "Pilot bootstrap dry-run is safe",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "pilot_bootstrap_dry_run_ready",
            "failed",
            "Pilot bootstrap dry-run did not prepare successfully",
            details,
        )

    @staticmethod
    def _scheduler_dry_run_gate(result: Any) -> PreDeployQualityGateItem:
        details = {
            "dry_run": result.dry_run,
            "due_schedule_count": result.due_schedule_count,
            "executed_count": result.executed_count,
            "error_count": len(result.errors),
        }
        if not result.dry_run:
            return PreDeployPaperPilotQualityGateService._gate(
                "scheduler_dry_run_ready",
                "failed",
                "Scheduler check was not a dry-run",
                details,
            )
        if result.errors:
            return PreDeployPaperPilotQualityGateService._gate(
                "scheduler_dry_run_ready",
                "failed",
                "Scheduler dry-run returned errors",
                details,
            )
        if result.due_schedule_count == 0:
            return PreDeployPaperPilotQualityGateService._gate(
                "scheduler_dry_run_ready",
                "warning",
                "Scheduler dry-run found no due schedules",
                details,
            )
        return PreDeployPaperPilotQualityGateService._gate(
            "scheduler_dry_run_ready",
            "passed",
            "Scheduler dry-run completed safely",
            details,
        )

    @staticmethod
    def _manual_gates() -> list[PreDeployQualityGateItem]:
        return [
            PreDeployPaperPilotQualityGateService._gate(
                "backend_test_plan_ready",
                "warning",
                "Backend tests must be run manually before deploy",
                {
                    "commands": [
                        "python -m compileall backend/app",
                        "python -m pytest backend/tests -q",
                    ]
                },
            ),
            PreDeployPaperPilotQualityGateService._gate(
                "frontend_build_plan_ready",
                "warning",
                "Frontend build must be run manually before deploy",
                {"commands": ["cd frontend && npm run build"]},
            ),
            PreDeployPaperPilotQualityGateService._gate(
                "deployment_runbook_ready",
                "warning",
                "Deployment runbook is still required before VDS launch",
            ),
        ]

    @staticmethod
    def _gate(
        code: str,
        status_value: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> PreDeployQualityGateItem:
        return PreDeployQualityGateItem(
            code=code,
            status=status_value,
            message=message,
            details=details or {},
        )

    @staticmethod
    def _can_continue_core(gates: list[PreDeployQualityGateItem]) -> bool:
        return not any(
            gate.code in CORE_GATE_CODES and gate.status == "failed"
            for gate in gates
        )

    @staticmethod
    def _ready_for_50k(gates: list[PreDeployQualityGateItem]) -> bool:
        for gate in gates:
            if (
                gate.code == "external_risk_regime_ready"
                and gate.status == "warning"
                and not gate.details.get("accepted")
            ):
                return False
        return not any(
            gate.code in CORE_GATE_CODES and gate.status in {"failed", "skipped"}
            for gate in gates
        )

    @staticmethod
    def _status(gates: list[PreDeployQualityGateItem]) -> str:
        if any(
            gate.code in CORE_GATE_CODES and gate.status == "failed"
            for gate in gates
        ):
            return "blocked"
        if any(gate.status in {"warning", "failed", "skipped"} for gate in gates):
            return "warning"
        return "ready_for_deploy"

    @staticmethod
    def _warnings_from_gates(
        gates: list[PreDeployQualityGateItem],
    ) -> list[dict[str, Any]]:
        return [
            {
                "code": gate.code,
                "message": gate.message,
                "details": gate.details,
            }
            for gate in gates
            if gate.status in {"warning", "skipped"}
        ]

    @staticmethod
    def _errors_from_gates(
        gates: list[PreDeployQualityGateItem],
    ) -> list[dict[str, Any]]:
        return [
            {
                "code": gate.code,
                "message": gate.message,
                "details": gate.details,
            }
            for gate in gates
            if gate.status == "failed"
        ]

    @staticmethod
    def _next_steps(gates: list[PreDeployQualityGateItem]) -> list[str]:
        steps_by_code = {
            "corporate_universe_ready": "Review corporate universe action plan.",
            "live_data_ready": "Review live data action plan.",
            "model_run_available": "Run ML validation suite and select a completed model candidate.",
            "strategy_robustness_ready": "Review strategy robustness diagnostics.",
            "live_paper_readiness_ready": "Review live paper readiness diagnostics.",
            "external_risk_regime_ready": "Review external risk regime before pilot launch.",
            "pilot_bootstrap_dry_run_ready": "Run pilot bootstrap dry-run after blockers are resolved.",
            "scheduler_dry_run_ready": "Review scheduler dry-run result.",
            "backend_test_plan_ready": "Run backend compile and pytest checks before deploy.",
            "frontend_build_plan_ready": "Run frontend production build before deploy.",
            "deployment_runbook_ready": "Prepare deployment runbook before VDS launch.",
        }
        steps: list[str] = []
        for gate in gates:
            if gate.status == "passed":
                continue
            step = steps_by_code.get(gate.code)
            if step is not None and step not in steps:
                steps.append(step)
        return steps

    @staticmethod
    def _commands(
        request: PreDeployPaperPilotQualityGateRequest,
        payloads: dict[str, Any],
    ) -> list[PreDeployQualityGateCommand]:
        live_query = (
            "/api/data-readiness/live"
            f"?recent_days={request.recent_days}"
            f"&minimum_corporate_bonds={request.minimum_corporate_bonds}"
            f"&minimum_bonds_with_recent_market_snapshot={request.minimum_bonds_with_recent_market_snapshot}"
            f"&minimum_bonds_with_recent_features={request.minimum_bonds_with_recent_features}"
            f"&minimum_bonds_with_predictions={request.minimum_bonds_with_predictions}"
            f"&include_ofz={str(request.include_ofz).lower()}"
        )
        commands = [
            PreDeployQualityGateCommand(
                label="Check corporate universe plan",
                method="GET",
                path=(
                    "/api/data-readiness/corporate-universe/action-plan"
                    f"?minimum_corporate_bonds={request.minimum_corporate_bonds}"
                    f"&include_ofz={str(request.include_ofz).lower()}"
                ),
                description="Review local corporate universe and MOEX sync preview.",
            ),
            PreDeployQualityGateCommand(
                label="Check live data readiness",
                method="GET",
                path=live_query,
                description="Review live market data readiness.",
            ),
            PreDeployQualityGateCommand(
                label="Open live data action plan",
                method="GET",
                path=live_query.replace("/live?", "/live/action-plan?"),
                description="Review next pipeline actions before execution.",
            ),
            PreDeployQualityGateCommand(
                label="Run ML validation suite",
                method="POST",
                path="/api/ml/validation-suite/run",
                description="Run controlled ML validation before selecting a model candidate.",
            ),
            PreDeployQualityGateCommand(
                label="Check live paper readiness",
                method="POST",
                path="/api/paper-trading/live/readiness",
                body=payloads.get("live_paper_readiness_request"),
                description="Review live paper readiness with the same candidate payload.",
            ),
            PreDeployQualityGateCommand(
                label="Run pilot bootstrap dry-run",
                method="POST",
                path="/api/paper-trading/live/pilots/bootstrap",
                body=payloads.get("pilot_bootstrap_dry_run_request"),
                description="Prepare the virtual pilot payload without creating a schedule.",
            ),
            PreDeployQualityGateCommand(
                label="Compile backend",
                method="SHELL",
                path="python -m compileall backend/app",
                description="Run backend compile check locally before deploy.",
            ),
            PreDeployQualityGateCommand(
                label="Run backend tests",
                method="SHELL",
                path="python -m pytest backend/tests -q",
                description="Run backend tests locally before deploy.",
            ),
            PreDeployQualityGateCommand(
                label="Build frontend",
                method="SHELL",
                path="cd frontend && npm run build",
                description="Run frontend production build locally before deploy.",
            ),
        ]
        return commands

    @staticmethod
    def _flags_by_level(
        variants: list[dict[str, Any]],
        level: str,
    ) -> list[dict[str, Any]]:
        flags: list[dict[str, Any]] = []
        for variant in variants:
            for flag in variant.get("flags") or []:
                if isinstance(flag, dict) and flag.get("level") == level:
                    flags.append(flag)
        return flags
