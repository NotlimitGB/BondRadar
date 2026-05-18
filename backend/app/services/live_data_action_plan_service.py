from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.schemas.data_pipeline import PIPELINE_MODES, PIPELINE_RETURN_METHODS
from app.schemas.live_data_action_plan import (
    LiveDataActionPlanCommand,
    LiveDataActionPlanResponse,
    LiveDataActionPlanStep,
    LiveDataPipelinePayloadPreview,
)
from app.schemas.live_data_readiness import LiveDataReadinessResponse
from app.services.live_data_readiness_service import LiveDataReadinessService


PIPELINE_STEP_ORDER = [
    "moex_market_sync",
    "moex_cashflow_sync",
    "credit_health",
    "bond_risk_assessment",
    "dataset_build_price",
    "labels_total_return",
    "labels_risk_adjusted",
    "data_readiness_check",
    "ml_train",
    "ml_predict",
    "ml_evaluate",
]

FEATURE_AND_LABEL_STEPS = [
    "credit_health",
    "bond_risk_assessment",
    "dataset_build_price",
    "labels_total_return",
    "labels_risk_adjusted",
    "data_readiness_check",
]


class LiveDataActionPlanService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def plan(
        self,
        *,
        recent_days: int = 7,
        minimum_corporate_bonds: int = 20,
        minimum_bonds_with_recent_market_snapshot: int = 20,
        minimum_bonds_with_recent_features: int = 20,
        minimum_bonds_with_predictions: int = 20,
        include_ofz: bool = False,
        date_from: date | None = None,
        date_to: date | None = None,
        horizon_days: int = 30,
        mode: str = "manual",
        moex_board: str = "TQCB",
        return_method: str = "risk_adjusted",
        allow_readiness_warning: bool = True,
        fail_on_not_ready: bool = True,
        include_ml_training: bool = True,
        include_predictions: bool = True,
        include_evaluation: bool = True,
        rebuild_existing: bool = False,
        transaction_cost_rate: Decimal = Decimal("0.001"),
    ) -> LiveDataActionPlanResponse:
        self._validate(
            recent_days=recent_days,
            minimum_corporate_bonds=minimum_corporate_bonds,
            minimum_bonds_with_recent_market_snapshot=(
                minimum_bonds_with_recent_market_snapshot
            ),
            minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
            minimum_bonds_with_predictions=minimum_bonds_with_predictions,
            date_from=date_from,
            date_to=date_to,
            horizon_days=horizon_days,
            mode=mode,
            return_method=return_method,
        )

        resolved_date_to = date_to or datetime.now(timezone.utc).date()
        resolved_date_from = date_from or (resolved_date_to - timedelta(days=recent_days))
        readiness = LiveDataReadinessService(self.db).check(
            recent_days=recent_days,
            minimum_corporate_bonds=minimum_corporate_bonds,
            minimum_bonds_with_recent_market_snapshot=(
                minimum_bonds_with_recent_market_snapshot
            ),
            minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
            minimum_bonds_with_predictions=minimum_bonds_with_predictions,
            include_ofz=include_ofz,
        )

        check_status = {check.name: check.status for check in readiness.checks}
        actions: list[LiveDataActionPlanStep] = []
        recommended_steps: list[str] = []
        optional_steps: list[str] = []
        blocked_steps: list[str] = []
        warnings: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        next_steps: list[str] = []

        universe_blocked = check_status.get("corporate_universe_available") == "failed"
        if universe_blocked:
            blocked_steps.append("corporate_universe_available")
            actions.append(
                LiveDataActionPlanStep(
                    name="corporate_universe_available",
                    status="blocked",
                    reason="Corporate bond universe is below configured minimum",
                    details={
                        "configured_minimum": minimum_corporate_bonds,
                        "corporate_bond_count": readiness.corporate_bond_count,
                    },
                )
            )
            errors.append(
                {
                    "code": "corporate_universe_blocked",
                    "message": "Corporate bond universe is below configured minimum",
                }
            )
            next_steps.append("Sync or import more corporate bonds before pipeline run.")
        else:
            self._add_data_steps(
                readiness=readiness,
                check_status=check_status,
                actions=actions,
                recommended_steps=recommended_steps,
            )
            self._add_ml_steps(
                readiness=readiness,
                check_status=check_status,
                actions=actions,
                recommended_steps=recommended_steps,
                warnings=warnings,
                next_steps=next_steps,
                include_ml_training=include_ml_training,
                include_predictions=include_predictions,
                include_evaluation=include_evaluation,
            )
            if not recommended_steps:
                optional_steps.extend(["moex_market_sync", "data_readiness_check"])
                actions.extend(
                    [
                        LiveDataActionPlanStep(
                            name="moex_market_sync",
                            status="optional",
                            reason="Market data refresh can keep the working universe current",
                            details={},
                        ),
                        LiveDataActionPlanStep(
                            name="data_readiness_check",
                            status="optional",
                            reason="Readiness re-check can confirm the latest pipeline state",
                            details={},
                        ),
                    ]
                )

        recommended_steps = self._ordered_unique(recommended_steps)
        optional_steps = self._ordered_unique(optional_steps)
        planned_steps = [] if universe_blocked else recommended_steps or optional_steps
        pipeline_payload = self._pipeline_payload(
            mode=mode,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            horizon_days=horizon_days,
            steps=planned_steps,
            return_method=return_method,
            rebuild_existing=rebuild_existing,
            moex_board=moex_board,
            allow_readiness_warning=allow_readiness_warning,
            fail_on_not_ready=fail_on_not_ready,
            transaction_cost_rate=transaction_cost_rate,
        )

        can_run_pipeline = not universe_blocked and bool(planned_steps)
        can_run_ml_training = (
            can_run_pipeline
            and include_ml_training
            and (
                "ml_train" in planned_steps
                or check_status.get("recent_feature_snapshots_available") == "passed"
            )
        )
        can_generate_predictions = (
            can_run_pipeline
            and include_predictions
            and (
                readiness.latest_completed_model_run_id is not None
                or "ml_train" in planned_steps
            )
        )
        can_bootstrap_paper_pilot = readiness.status == "ready" or (
            readiness.status == "warning" and allow_readiness_warning
        )

        status_value = self._plan_status(
            universe_blocked=universe_blocked,
            readiness=readiness,
            recommended_steps=recommended_steps,
        )
        commands = self._commands(
            readiness_query=self._readiness_query(
                recent_days=recent_days,
                minimum_corporate_bonds=minimum_corporate_bonds,
                minimum_bonds_with_recent_market_snapshot=(
                    minimum_bonds_with_recent_market_snapshot
                ),
                minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
                minimum_bonds_with_predictions=minimum_bonds_with_predictions,
                include_ofz=include_ofz,
            ),
            pipeline_payload=pipeline_payload,
            can_run_pipeline=can_run_pipeline,
            can_bootstrap_paper_pilot=can_bootstrap_paper_pilot,
        )

        if not can_bootstrap_paper_pilot:
            next_steps.append(
                "Use pilot bootstrap only after readiness is ready or warnings are explicitly accepted."
            )
        for step in readiness.next_steps:
            if step not in next_steps:
                next_steps.append(step)

        return LiveDataActionPlanResponse(
            status=status_value,
            as_of=readiness.as_of,
            readiness_status=readiness.status,
            readiness=readiness,
            date_from=resolved_date_from,
            date_to=resolved_date_to,
            horizon_days=horizon_days,
            include_ofz=include_ofz,
            recommended_steps=recommended_steps,
            blocked_steps=blocked_steps,
            optional_steps=optional_steps,
            actions=actions,
            commands=commands,
            pipeline_payload=pipeline_payload,
            curl_example=self._curl_example(pipeline_payload),
            can_run_pipeline=can_run_pipeline,
            can_run_ml_training=can_run_ml_training,
            can_generate_predictions=can_generate_predictions,
            can_bootstrap_paper_pilot=can_bootstrap_paper_pilot,
            warnings=warnings,
            errors=errors,
            next_steps=next_steps,
        )

    @staticmethod
    def _validate(
        *,
        recent_days: int,
        minimum_corporate_bonds: int,
        minimum_bonds_with_recent_market_snapshot: int,
        minimum_bonds_with_recent_features: int,
        minimum_bonds_with_predictions: int,
        date_from: date | None,
        date_to: date | None,
        horizon_days: int,
        mode: str,
        return_method: str,
    ) -> None:
        if recent_days < 1 or recent_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="recent_days must be between 1 and 365",
            )
        if horizon_days < 1 or horizon_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be between 1 and 365",
            )
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date_from must be before or equal to date_to",
            )
        if mode not in PIPELINE_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode must be one of existing PIPELINE_MODES",
            )
        if return_method not in PIPELINE_RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="return_method must be one of existing PIPELINE_RETURN_METHODS",
            )
        minimums = {
            "minimum_corporate_bonds": minimum_corporate_bonds,
            "minimum_bonds_with_recent_market_snapshot": (
                minimum_bonds_with_recent_market_snapshot
            ),
            "minimum_bonds_with_recent_features": minimum_bonds_with_recent_features,
            "minimum_bonds_with_predictions": minimum_bonds_with_predictions,
        }
        for name, value in minimums.items():
            if value < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{name} must be non-negative",
                )

    def _add_data_steps(
        self,
        *,
        readiness: LiveDataReadinessResponse,
        check_status: dict[str, str],
        actions: list[LiveDataActionPlanStep],
        recommended_steps: list[str],
    ) -> None:
        if self._not_passed(check_status, "market_snapshots_available") or self._not_passed(
            check_status,
            "recent_market_snapshots_available",
        ):
            self._recommend(
                actions,
                recommended_steps,
                "moex_market_sync",
                "Market snapshots need refresh for the working universe",
                {
                    "latest_market_snapshot_date": readiness.latest_market_snapshot_date,
                    "market_snapshot_count": readiness.market_snapshot_count,
                    "bonds_with_recent_market_snapshot_count": (
                        readiness.bonds_with_recent_market_snapshot_count
                    ),
                },
            )
        if self._not_passed(check_status, "cashflows_available"):
            self._recommend(
                actions,
                recommended_steps,
                "moex_cashflow_sync",
                "Cashflow data should be refreshed for the working universe",
                {
                    "cashflow_event_count": readiness.cashflow_event_count,
                    "bonds_with_cashflows_count": readiness.bonds_with_cashflows_count,
                },
            )
        if self._not_passed(check_status, "feature_snapshots_available") or self._not_passed(
            check_status,
            "recent_feature_snapshots_available",
        ):
            for step in FEATURE_AND_LABEL_STEPS:
                self._recommend(
                    actions,
                    recommended_steps,
                    step,
                    "Feature and label data should be rebuilt for the selected period",
                    {
                        "latest_feature_snapshot_date": (
                            readiness.latest_feature_snapshot_date
                        ),
                        "feature_snapshot_count": readiness.feature_snapshot_count,
                        "bonds_with_recent_features_count": (
                            readiness.bonds_with_recent_features_count
                        ),
                    },
                )

    def _add_ml_steps(
        self,
        *,
        readiness: LiveDataReadinessResponse,
        check_status: dict[str, str],
        actions: list[LiveDataActionPlanStep],
        recommended_steps: list[str],
        warnings: list[dict[str, Any]],
        next_steps: list[str],
        include_ml_training: bool,
        include_predictions: bool,
        include_evaluation: bool,
    ) -> None:
        model_missing = check_status.get("completed_model_run_available") == "failed"
        if model_missing:
            if include_ml_training:
                self._recommend(
                    actions,
                    recommended_steps,
                    "ml_train",
                    "A completed model run is needed before prediction generation",
                    {
                        "latest_completed_model_run_id": (
                            readiness.latest_completed_model_run_id
                        )
                    },
                )
            else:
                warnings.append(
                    {
                        "code": "ml_training_not_included",
                        "message": "Model training is needed before prediction generation",
                    }
                )
                next_steps.append(
                    "Enable ML training in the pipeline plan or train a model separately."
                )

        prediction_gap = self._not_passed(check_status, "predictions_available") or self._not_passed(
            check_status,
            "recent_predictions_available",
        )
        can_plan_predictions = include_predictions and (
            readiness.latest_completed_model_run_id is not None
            or "ml_train" in recommended_steps
        )
        if prediction_gap and can_plan_predictions:
            self._recommend(
                actions,
                recommended_steps,
                "ml_predict",
                "Predictions should be generated for the latest available model run",
                {
                    "latest_completed_model_run_id": readiness.latest_completed_model_run_id,
                    "prediction_count_for_latest_run": (
                        readiness.prediction_count_for_latest_run
                    ),
                    "bonds_with_predictions_for_latest_run_count": (
                        readiness.bonds_with_predictions_for_latest_run_count
                    ),
                    "latest_prediction_date": readiness.latest_prediction_date,
                },
            )
            if include_evaluation:
                self._recommend(
                    actions,
                    recommended_steps,
                    "ml_evaluate",
                    "Prediction quality report should be refreshed after predictions",
                    {},
                )
        elif prediction_gap and not include_predictions:
            warnings.append(
                {
                    "code": "predictions_not_included",
                    "message": "Prediction generation is disabled for this plan",
                }
            )
            next_steps.append("Enable prediction generation before pilot bootstrap.")

    @staticmethod
    def _recommend(
        actions: list[LiveDataActionPlanStep],
        recommended_steps: list[str],
        name: str,
        reason: str,
        details: dict[str, Any],
    ) -> None:
        if name not in recommended_steps:
            recommended_steps.append(name)
        if not any(action.name == name for action in actions):
            actions.append(
                LiveDataActionPlanStep(
                    name=name,
                    status="recommended",
                    reason=reason,
                    details=details,
                )
            )

    @staticmethod
    def _not_passed(check_status: dict[str, str], name: str) -> bool:
        return check_status.get(name) in {"failed", "warning"}

    @staticmethod
    def _ordered_unique(steps: list[str]) -> list[str]:
        unique = set(steps)
        return [step for step in PIPELINE_STEP_ORDER if step in unique]

    @staticmethod
    def _pipeline_payload(
        *,
        mode: str,
        date_from: date,
        date_to: date,
        horizon_days: int,
        steps: list[str],
        return_method: str,
        rebuild_existing: bool,
        moex_board: str,
        allow_readiness_warning: bool,
        fail_on_not_ready: bool,
        transaction_cost_rate: Decimal,
    ) -> dict[str, Any]:
        preview = LiveDataPipelinePayloadPreview(
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            horizon_days=horizon_days,
            steps=steps,
            return_methods=[return_method],
            rebuild_existing=rebuild_existing,
            moex_board=moex_board,
            run_ml="ml_train" in steps,
            run_predictions="ml_predict" in steps,
            run_evaluation="ml_evaluate" in steps,
            ml_return_method=return_method,
            allow_readiness_warning=allow_readiness_warning,
            fail_on_not_ready=fail_on_not_ready,
            transaction_cost_rate=transaction_cost_rate,
        )
        payload = preview.model_dump()
        payload.update(
            {
                "run_readiness_check": "data_readiness_check" in steps,
                "readiness_require_cashflows": True,
                "readiness_require_moex_secid": True,
            }
        )
        return payload

    @staticmethod
    def _plan_status(
        *,
        universe_blocked: bool,
        readiness: LiveDataReadinessResponse,
        recommended_steps: list[str],
    ) -> str:
        if universe_blocked:
            return "blocked"
        if readiness.status != "ready" and recommended_steps:
            return "needs_attention"
        return "ready_to_run"

    @staticmethod
    def _readiness_query(
        *,
        recent_days: int,
        minimum_corporate_bonds: int,
        minimum_bonds_with_recent_market_snapshot: int,
        minimum_bonds_with_recent_features: int,
        minimum_bonds_with_predictions: int,
        include_ofz: bool,
    ) -> str:
        params = {
            "recent_days": recent_days,
            "minimum_corporate_bonds": minimum_corporate_bonds,
            "minimum_bonds_with_recent_market_snapshot": (
                minimum_bonds_with_recent_market_snapshot
            ),
            "minimum_bonds_with_recent_features": minimum_bonds_with_recent_features,
            "minimum_bonds_with_predictions": minimum_bonds_with_predictions,
            "include_ofz": str(include_ofz).lower(),
        }
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return f"/api/data-readiness/live?{query}"

    @staticmethod
    def _commands(
        *,
        readiness_query: str,
        pipeline_payload: dict[str, Any],
        can_run_pipeline: bool,
        can_bootstrap_paper_pilot: bool,
    ) -> list[LiveDataActionPlanCommand]:
        commands = [
            LiveDataActionPlanCommand(
                label="Check live data readiness",
                method="GET",
                path=readiness_query,
                description="Review current live data readiness diagnostics.",
            )
        ]
        if can_run_pipeline:
            commands.append(
                LiveDataActionPlanCommand(
                    label="Run proposed data pipeline",
                    method="POST",
                    path="/api/pipeline/run",
                    body=pipeline_payload,
                    description="Start the proposed data pipeline run manually.",
                )
            )
        commands.extend(
            [
                LiveDataActionPlanCommand(
                    label="List pipeline runs",
                    method="GET",
                    path="/api/pipeline/runs?limit=20",
                    description="Inspect recent pipeline run records.",
                ),
                LiveDataActionPlanCommand(
                    label="Re-check live readiness",
                    method="GET",
                    path=readiness_query,
                    description="Run the readiness report again after pipeline completion.",
                ),
            ]
        )
        if can_bootstrap_paper_pilot:
            commands.append(
                LiveDataActionPlanCommand(
                    label="Open pilot bootstrap",
                    method="POST",
                    path="/api/paper-trading/live/pilots/bootstrap",
                    body=None,
                    description="Prepare the virtual paper pilot after reviewing readiness.",
                )
            )
        return commands

    @staticmethod
    def _curl_example(pipeline_payload: dict[str, Any]) -> str:
        payload_json = json.dumps(
            jsonable_encoder(pipeline_payload),
            ensure_ascii=False,
            indent=2,
        )
        return (
            'curl -s -X POST "http://127.0.0.1:8000/api/pipeline/run" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            f"  -d '{payload_json}'"
        )
