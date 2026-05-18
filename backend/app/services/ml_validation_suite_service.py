from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.live_data_readiness import LiveDataReadinessResponse
from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonCandidateRequest,
    MLCandidateComparisonRequest,
    MLCandidateComparisonResponse,
)
from app.schemas.ml_model import MLPredictionRequest, MLTrainRequest
from app.schemas.ml_validation_suite import (
    MLValidationPredictionResult,
    MLValidationSelectedCandidate,
    MLValidationSuiteRequest,
    MLValidationSuiteResponse,
    MLValidationTrainingConfig,
    MLValidationTrainingResult,
)
from app.services.live_data_readiness_service import LiveDataReadinessService
from app.services.ml_candidate_comparison_service import (
    MLCandidateComparisonService,
    RANKING_DIRECTIONS,
    RANKING_METRICS,
)
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService


PREDICTION_GAP_CHECKS = {
    "predictions_available",
    "recent_predictions_available",
}


class MLValidationSuiteService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, request: MLValidationSuiteRequest) -> MLValidationSuiteResponse:
        configs = self._resolved_training_configs(request.training_configs)
        self._validate_request(request, configs)
        warnings: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        readiness: LiveDataReadinessResponse | None = None
        readiness_status: str | None = None

        if request.require_live_data_ready:
            readiness = LiveDataReadinessService(self.db).check(
                recent_days=request.recent_days,
                minimum_corporate_bonds=request.minimum_corporate_bonds,
                minimum_bonds_with_recent_market_snapshot=(
                    request.minimum_bonds_with_recent_market_snapshot
                ),
                minimum_bonds_with_recent_features=(
                    request.minimum_bonds_with_recent_features
                ),
                minimum_bonds_with_predictions=(
                    request.minimum_bonds_with_predictions
                ),
                include_ofz=request.include_ofz,
            )
            readiness_status = readiness.status
            gate = self._readiness_gate_status(readiness, request)
            if gate["blocked"]:
                errors.append(
                    {
                        "code": "readiness_blocked",
                        "message": "Live data readiness is not acceptable for ML validation",
                        "details": gate["details"],
                    }
                )
                return self._response(
                    request=request,
                    status_value="blocked",
                    readiness=readiness,
                    readiness_status=readiness_status,
                    training_results=[],
                    prediction_results=[],
                    selected_candidate=None,
                    candidate_comparison=None,
                    warnings=warnings,
                    errors=errors,
                    next_steps=self._blocked_next_steps(readiness),
                )
            warnings.extend(gate["warnings"])
        else:
            warnings.append(
                {
                    "code": "readiness_gate_disabled",
                    "message": "ML validation suite is running without live data readiness gate",
                    "details": {},
                }
            )

        training_results = self._run_training(configs)
        completed_training = [
            result
            for result in training_results
            if result.status == "completed" and result.model_run_id is not None
        ]
        prediction_results: list[MLValidationPredictionResult] = []
        candidate_comparison: MLCandidateComparisonResponse | None = None
        selected_candidate: MLValidationSelectedCandidate | None = None

        if completed_training and request.generate_predictions:
            prediction_results = self._run_predictions(request, completed_training)
        elif not request.generate_predictions:
            warnings.append(
                {
                    "code": "prediction_generation_skipped",
                    "message": "Prediction generation was skipped for this validation suite",
                    "details": {},
                }
            )

        if request.run_candidate_comparison and completed_training:
            if request.generate_predictions and not any(
                result.status == "completed" for result in prediction_results
            ):
                warnings.append(
                    {
                        "code": "candidate_comparison_limited",
                        "message": "Candidate comparison may be limited because predictions were not completed",
                        "details": {},
                    }
                )
            candidate_comparison = self._run_candidate_comparison(
                request,
                completed_training,
            )
            if candidate_comparison is not None:
                warnings.extend(
                    {
                        "code": "candidate_comparison_warning",
                        "message": warning.message,
                        "details": warning.details,
                    }
                    for warning in candidate_comparison.warnings
                )
                if candidate_comparison.selected_candidate is not None:
                    selected_candidate = self._selected_candidate(
                        candidate_comparison
                    )

        status_value = self._status(
            readiness_blocked=False,
            training_results=training_results,
            prediction_results=prediction_results,
            request=request,
            candidate_comparison=candidate_comparison,
            selected_candidate=selected_candidate,
            warnings=warnings,
        )
        return self._response(
            request=request,
            status_value=status_value,
            readiness=readiness,
            readiness_status=readiness_status,
            training_results=training_results,
            prediction_results=prediction_results,
            selected_candidate=selected_candidate,
            candidate_comparison=candidate_comparison,
            warnings=warnings,
            errors=errors,
            next_steps=self._next_steps(status_value, selected_candidate),
        )

    @staticmethod
    def _resolved_training_configs(
        configs: list[MLValidationTrainingConfig],
    ) -> list[MLValidationTrainingConfig]:
        if configs:
            return configs
        return [
            MLValidationTrainingConfig(
                name="risk_adjusted_h30_seed42",
                return_method="risk_adjusted",
                horizon_days=30,
                random_state=42,
                min_rows=30,
            ),
            MLValidationTrainingConfig(
                name="risk_adjusted_h30_seed7",
                return_method="risk_adjusted",
                horizon_days=30,
                random_state=7,
                min_rows=30,
            ),
            MLValidationTrainingConfig(
                name="risk_adjusted_h30_min_rows_50",
                return_method="risk_adjusted",
                horizon_days=30,
                random_state=42,
                min_rows=50,
            ),
        ]

    @staticmethod
    def _validate_request(
        request: MLValidationSuiteRequest,
        configs: list[MLValidationTrainingConfig],
    ) -> None:
        if not request.suite_name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="suite_name must not be blank",
            )
        if request.recent_days < 1 or request.recent_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="recent_days must be between 1 and 365",
            )
        minimums = {
            "minimum_corporate_bonds": request.minimum_corporate_bonds,
            "minimum_bonds_with_recent_market_snapshot": (
                request.minimum_bonds_with_recent_market_snapshot
            ),
            "minimum_bonds_with_recent_features": (
                request.minimum_bonds_with_recent_features
            ),
            "minimum_bonds_with_predictions": (
                request.minimum_bonds_with_predictions
            ),
        }
        for name, value in minimums.items():
            if value < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{name} must be non-negative",
                )
        if len(configs) < 1 or len(configs) > 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="training_configs length must be between 1 and 10",
            )
        for config in configs:
            MLValidationSuiteService._validate_training_config(config)
        MLValidationSuiteService._validate_date_range(
            request.prediction_as_of_date_from,
            request.prediction_as_of_date_to,
        )
        MLValidationSuiteService._validate_date_range(
            request.comparison_date_from,
            request.comparison_date_to,
        )
        if request.prediction_limit < 1 or request.prediction_limit > 5000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="prediction_limit must be between 1 and 5000",
            )
        if request.comparison_return_method not in RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="return_method must be one of supported return methods",
            )
        if request.comparison_horizon_days < 1 or request.comparison_horizon_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be between 1 and 365",
            )
        if request.ranking_metric not in RANKING_METRICS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ranking_metric must be supported",
            )
        if request.ranking_direction not in RANKING_DIRECTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ranking_direction must be asc or desc",
            )
        if (
            request.maximum_missing_label_ratio < 0
            or request.maximum_missing_label_ratio > 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="maximum_missing_label_ratio must be between 0 and 1",
            )
        if request.positive_probability_cutoff < 0 or request.positive_probability_cutoff > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="positive_probability_cutoff must be between 0 and 1",
            )
        if request.minimum_evaluable_predictions <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_evaluable_predictions must be positive",
            )
        if request.minimum_positive_labels < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_positive_labels must be non-negative",
            )
        if request.minimum_negative_labels < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_negative_labels must be non-negative",
            )

    @staticmethod
    def _validate_training_config(config: MLValidationTrainingConfig) -> None:
        if not config.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="training config name must not be blank",
            )
        if config.horizon_days < 1 or config.horizon_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be between 1 and 365",
            )
        if config.return_method not in RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="return_method must be one of supported return methods",
            )
        if config.model_type != MLTrainingService.MODEL_TYPE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_type must be supported by MLTrainingService",
            )
        if config.test_size <= 0 or config.test_size >= 0.5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="test_size must be greater than 0 and less than 0.5",
            )
        if config.min_rows < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_rows must be at least 10",
            )
        if config.max_rows is not None and config.max_rows <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_rows must be positive when provided",
            )
        MLValidationSuiteService._validate_date_range(
            config.as_of_date_from,
            config.as_of_date_to,
        )

    @staticmethod
    def _validate_date_range(start, end) -> None:
        if start is not None and end is not None and start > end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )

    @staticmethod
    def _readiness_gate_status(
        readiness: LiveDataReadinessResponse,
        request: MLValidationSuiteRequest,
    ) -> dict[str, Any]:
        failed_checks = [check for check in readiness.checks if check.status == "failed"]
        non_prediction_failures = [
            check
            for check in failed_checks
            if not MLValidationSuiteService._is_prediction_only_failure(check)
        ]
        training_mode_allows_prediction_gap = (
            request.minimum_bonds_with_predictions == 0
            and request.generate_predictions
            and not non_prediction_failures
        )
        if readiness.status == "ready":
            return {"blocked": False, "warnings": [], "details": {}}
        if readiness.status == "warning":
            if request.allow_readiness_warning:
                return {
                    "blocked": False,
                    "warnings": [
                        {
                            "code": "readiness_warning",
                            "message": "Live data readiness returned warnings",
                            "details": {},
                        }
                    ],
                    "details": {},
                }
            return {
                "blocked": True,
                "warnings": [],
                "details": {"readiness_status": readiness.status},
            }
        if training_mode_allows_prediction_gap:
            return {
                "blocked": False,
                "warnings": [
                    {
                        "code": "prediction_readiness_gap",
                        "message": "Prediction readiness gaps will be handled by this validation suite",
                        "details": {
                            "failed_checks": [check.name for check in failed_checks]
                        },
                    }
                ],
                "details": {},
            }
        return {
            "blocked": True,
            "warnings": [],
            "details": {
                "readiness_status": readiness.status,
                "failed_checks": [check.name for check in failed_checks],
            },
        }

    @staticmethod
    def _is_prediction_only_failure(check) -> bool:
        if check.name in PREDICTION_GAP_CHECKS:
            return True
        if check.name != "paper_pilot_data_ready":
            return False
        blocking = set(check.details.get("blocking_checks") or [])
        return bool(blocking) and blocking.issubset(PREDICTION_GAP_CHECKS)

    def _run_training(
        self,
        configs: list[MLValidationTrainingConfig],
    ) -> list[MLValidationTrainingResult]:
        results: list[MLValidationTrainingResult] = []
        service = MLTrainingService(self.db)
        for config in configs:
            try:
                training = service.train(
                    MLTrainRequest(
                        horizon_days=config.horizon_days,
                        return_method=config.return_method,
                        include_credit_risk_features=config.include_credit_risk_features,
                        as_of_date_from=config.as_of_date_from,
                        as_of_date_to=config.as_of_date_to,
                        bond_ids=config.bond_ids,
                        company_ids=config.company_ids,
                        model_type=config.model_type,
                        test_size=config.test_size,
                        min_rows=config.min_rows,
                        random_state=config.random_state,
                        max_rows=config.max_rows,
                    )
                )
                if training.status == "completed":
                    results.append(
                        MLValidationTrainingResult(
                            name=config.name,
                            status="completed",
                            model_run_id=training.run_id,
                            train_rows=training.train_rows,
                            test_rows=training.test_rows,
                            positive_rows=training.positive_rows,
                            negative_rows=training.negative_rows,
                            metrics=training.metrics,
                            artifact_path=training.artifact_path,
                            error=None,
                            warnings=[],
                        )
                    )
                else:
                    results.append(
                        MLValidationTrainingResult(
                            name=config.name,
                            status="failed",
                            model_run_id=training.run_id,
                            train_rows=training.train_rows,
                            test_rows=training.test_rows,
                            positive_rows=training.positive_rows,
                            negative_rows=training.negative_rows,
                            metrics=training.metrics,
                            artifact_path=training.artifact_path,
                            error=f"Training returned status {training.status}",
                            warnings=[],
                        )
                    )
            except Exception as exc:
                results.append(
                    MLValidationTrainingResult(
                        name=config.name,
                        status="failed",
                        model_run_id=None,
                        train_rows=None,
                        test_rows=None,
                        positive_rows=None,
                        negative_rows=None,
                        metrics={},
                        artifact_path=None,
                        error=self._error_detail(exc),
                        warnings=[],
                    )
                )
        return results

    def _run_predictions(
        self,
        request: MLValidationSuiteRequest,
        training_results: list[MLValidationTrainingResult],
    ) -> list[MLValidationPredictionResult]:
        results: list[MLValidationPredictionResult] = []
        service = MLPredictionService(self.db)
        for training in training_results:
            if training.model_run_id is None:
                continue
            try:
                prediction = service.predict(
                    MLPredictionRequest(
                        model_run_id=training.model_run_id,
                        as_of_date_from=request.prediction_as_of_date_from,
                        as_of_date_to=request.prediction_as_of_date_to,
                        limit=request.prediction_limit,
                        offset=0,
                        save_predictions=request.save_predictions,
                    )
                )
                results.append(
                    MLValidationPredictionResult(
                        name=training.name,
                        model_run_id=training.model_run_id,
                        status="completed",
                        total=prediction.total,
                        saved=request.save_predictions,
                        error=None,
                        warnings=[],
                    )
                )
            except Exception as exc:
                results.append(
                    MLValidationPredictionResult(
                        name=training.name,
                        model_run_id=training.model_run_id,
                        status="failed",
                        total=None,
                        saved=request.save_predictions,
                        error=self._error_detail(exc),
                        warnings=[],
                    )
                )
        return results

    def _run_candidate_comparison(
        self,
        request: MLValidationSuiteRequest,
        training_results: list[MLValidationTrainingResult],
    ) -> MLCandidateComparisonResponse | None:
        candidates = [
            MLCandidateComparisonCandidateRequest(
                name=result.name,
                model_run_id=result.model_run_id,
            )
            for result in training_results
            if result.model_run_id is not None
        ]
        if not candidates:
            return None
        return MLCandidateComparisonService(self.db).compare(
            MLCandidateComparisonRequest(
                candidates=candidates,
                date_from=request.comparison_date_from,
                date_to=request.comparison_date_to,
                return_method=request.comparison_return_method,
                horizon_days=request.comparison_horizon_days,
                positive_probability_cutoff=request.positive_probability_cutoff,
                ranking_metric=request.ranking_metric,
                ranking_direction=request.ranking_direction,
                include_prediction_quality=True,
                include_failed_candidates=True,
                minimum_evaluable_predictions=request.minimum_evaluable_predictions,
                minimum_positive_labels=request.minimum_positive_labels,
                minimum_negative_labels=request.minimum_negative_labels,
                maximum_missing_label_ratio=request.maximum_missing_label_ratio,
                max_candidates=max(20, len(candidates)),
                limit=100,
                offset=0,
            )
        )

    @staticmethod
    def _selected_candidate(
        comparison: MLCandidateComparisonResponse,
    ) -> MLValidationSelectedCandidate | None:
        selected = comparison.selected_candidate
        if selected is None:
            return None
        return MLValidationSelectedCandidate(
            name=selected.name,
            model_run_id=selected.model_run_id,
            ranking_metric=selected.ranking_metric,
            ranking_value=selected.ranking_value,
            ready_for_strategy_research=selected.ready_for_strategy_research,
            issues=selected.issues,
        )

    @staticmethod
    def _status(
        *,
        readiness_blocked: bool,
        training_results: list[MLValidationTrainingResult],
        prediction_results: list[MLValidationPredictionResult],
        request: MLValidationSuiteRequest,
        candidate_comparison: MLCandidateComparisonResponse | None,
        selected_candidate: MLValidationSelectedCandidate | None,
        warnings: list[dict[str, Any]],
    ) -> str:
        if readiness_blocked:
            return "blocked"
        completed_training_count = sum(
            result.status == "completed" for result in training_results
        )
        if completed_training_count == 0:
            return "failed"
        failed_training_count = sum(
            result.status == "failed" for result in training_results
        )
        failed_prediction_count = sum(
            result.status == "failed" for result in prediction_results
        )
        if (
            failed_training_count > 0
            or failed_prediction_count > 0
            or warnings
            or (
                request.run_candidate_comparison
                and candidate_comparison is not None
                and selected_candidate is None
            )
            or (
                request.run_candidate_comparison
                and candidate_comparison is None
            )
        ):
            return "completed_with_warnings"
        return "completed"

    def _response(
        self,
        *,
        request: MLValidationSuiteRequest,
        status_value: str,
        readiness: LiveDataReadinessResponse | None,
        readiness_status: str | None,
        training_results: list[MLValidationTrainingResult],
        prediction_results: list[MLValidationPredictionResult],
        selected_candidate: MLValidationSelectedCandidate | None,
        candidate_comparison: MLCandidateComparisonResponse | None,
        warnings: list[dict[str, Any]],
        errors: list[dict[str, Any]],
        next_steps: list[str],
    ) -> MLValidationSuiteResponse:
        completed_training_count = sum(
            result.status == "completed" for result in training_results
        )
        failed_training_count = sum(
            result.status == "failed" for result in training_results
        )
        completed_prediction_count = sum(
            result.status == "completed" for result in prediction_results
        )
        failed_prediction_count = sum(
            result.status == "failed" for result in prediction_results
        )
        recommended_model_run_id = (
            selected_candidate.model_run_id
            if selected_candidate is not None
            and selected_candidate.ready_for_strategy_research
            else None
        )
        can_continue = recommended_model_run_id is not None
        return MLValidationSuiteResponse(
            status=status_value,
            suite_name=request.suite_name,
            as_of=datetime.now(timezone.utc),
            readiness_status=readiness_status,
            readiness=readiness,
            training_result_count=len(training_results),
            completed_training_count=completed_training_count,
            failed_training_count=failed_training_count,
            prediction_result_count=len(prediction_results),
            completed_prediction_count=completed_prediction_count,
            failed_prediction_count=failed_prediction_count,
            selected_candidate=selected_candidate,
            candidate_comparison=candidate_comparison,
            training_results=training_results,
            prediction_results=prediction_results,
            recommended_model_run_id=recommended_model_run_id,
            can_continue_to_robustness=can_continue,
            can_continue_to_paper_readiness=can_continue,
            warnings=warnings,
            errors=errors,
            next_steps=next_steps,
        )

    @staticmethod
    def _blocked_next_steps(readiness: LiveDataReadinessResponse | None) -> list[str]:
        steps = [
            "Run corporate universe action plan.",
            "Run live data action plan.",
            "Run pipeline before ML validation suite.",
        ]
        if readiness is not None:
            for step in readiness.next_steps:
                if step not in steps:
                    steps.append(step)
        return steps

    @staticmethod
    def _next_steps(
        status_value: str,
        selected_candidate: MLValidationSelectedCandidate | None,
    ) -> list[str]:
        if status_value == "blocked":
            return [
                "Run corporate universe action plan.",
                "Run live data action plan.",
                "Run pipeline before ML validation suite.",
            ]
        if selected_candidate is None:
            return [
                "Review failed training or prediction results.",
                "Check label quality and prediction coverage.",
                "Run validation suite again after data fixes.",
            ]
        return [
            "Run strategy robustness for selected model run.",
            "Run live paper readiness gate.",
            "Run pilot bootstrap dry-run only after robustness and readiness are acceptable.",
        ]

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)
