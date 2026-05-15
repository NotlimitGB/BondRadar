from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.bond import Bond
from app.models.company import Company
from app.models.data_pipeline_run import DataPipelineRun
from app.models.data_pipeline_step_run import DataPipelineStepRun
from app.schemas.cashflow import BondTotalReturnLabelBuildRequest
from app.schemas.data_pipeline import (
    PIPELINE_MODES,
    PIPELINE_RETURN_METHODS,
    PIPELINE_STATUSES,
    PIPELINE_STEPS,
    DataPipelineRunRequest,
    DataPipelineRunResult,
)
from app.schemas.data_readiness import DataReadinessCheckRequest
from app.schemas.ml_dataset import DatasetBuildRequest
from app.schemas.ml_evaluation import MLRunEvaluationReport
from app.schemas.ml_model import MLTrainRequest, MLPredictionRequest
from app.schemas.moex import MoexCashflowSyncRequest, MoexMarketDataSyncRequest
from app.services.bond_risk_assessment_service import BondRiskAssessmentService
from app.services.company_credit_health_service import CompanyCreditHealthService
from app.services.data_readiness_service import DataReadinessService
from app.services.dataset_build_service import DatasetBuildService
from app.services.ml_evaluation_service import MLEvaluationFilters, MLEvaluationService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.total_return_label_service import TotalReturnLabelService


BASE_STEPS = [
    "moex_market_sync",
    "moex_cashflow_sync",
    "credit_health",
    "bond_risk_assessment",
]
RETURN_METHOD_STEPS = {
    "price": "dataset_build_price",
    "total_return": "labels_total_return",
    "risk_adjusted": "labels_risk_adjusted",
}
READINESS_STEP = "data_readiness_check"
ML_STEPS = {"ml_train", "ml_predict", "ml_evaluate"}


@dataclass
class StepExecutionResult:
    result: dict[str, Any]
    errors: list[dict[str, Any]]
    warnings: list[dict[str, Any]]


class DataPipelineService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, request: DataPipelineRunRequest) -> DataPipelineRunResult:
        steps = self._validate_and_resolve_steps(request)
        bond_ids = self._selected_bond_ids(request)
        company_ids = self._selected_company_ids(request, bond_ids)
        run = self._create_run(
            request,
            steps=steps,
            bond_ids=bond_ids,
            company_ids=company_ids,
        )
        step_runs = self._create_step_runs(
            run.id,
            steps=steps,
            request=request,
            bond_ids=bond_ids,
            company_ids=company_ids,
        )
        summary: dict[str, Any] = {}
        run_errors: list[dict[str, Any]] = []
        run_warnings: list[dict[str, Any]] = []
        model_run_id: int | None = None
        readiness_status: str | None = None
        readiness_enabled = READINESS_STEP in steps
        guard_warnings_emitted: set[str] = set()

        if self._ml_requested_by_steps(steps) and not readiness_enabled:
            run_warnings.append(
                {
                    "step": None,
                    "message": "ML steps are running without readiness check",
                }
            )

        try:
            for step in step_runs:
                self._start_step(step.id)
                try:
                    pre_step_warnings: list[dict[str, Any]] = []
                    if step.step_name in ML_STEPS:
                        skip_message = self._ml_skip_message(
                            readiness_status=readiness_status,
                            readiness_enabled=readiness_enabled,
                            request=request,
                        )
                        if skip_message is not None:
                            raise PipelineStepSkipped(skip_message)
                        guard_warning = self._ml_guard_warning(
                            readiness_status=readiness_status,
                            readiness_enabled=readiness_enabled,
                            request=request,
                        )
                        if (
                            guard_warning is not None
                            and guard_warning not in guard_warnings_emitted
                        ):
                            guard_warnings_emitted.add(guard_warning)
                            pre_step_warnings.append(
                                {"step": step.step_name, "message": guard_warning}
                            )
                    execution = self._execute_step(
                        step.step_name,
                        request=request,
                        bond_ids=bond_ids,
                        company_ids=company_ids,
                        model_run_id=model_run_id,
                    )
                    if step.step_name == "ml_train":
                        model_run_id = execution.result.get("model_run_id")
                    if step.step_name == READINESS_STEP:
                        readiness_status = execution.result.get("status")
                    self._merge_summary(
                        summary,
                        step.step_name,
                        execution.result,
                    )
                    run_errors.extend(execution.errors)
                    run_warnings.extend(pre_step_warnings)
                    run_warnings.extend(execution.warnings)
                    self._finish_step(
                        step.id,
                        status_value="completed",
                        result=execution.result,
                        errors=execution.errors,
                        warnings=pre_step_warnings + execution.warnings,
                    )
                except PipelineStepSkipped as exc:
                    warning = {"step": step.step_name, "message": exc.message}
                    run_warnings.append(warning)
                    self._finish_step(
                        step.id,
                        status_value="skipped",
                        result={},
                        errors=[],
                        warnings=[warning],
                    )
                except Exception as exc:
                    self.db.rollback()
                    error = {"step": step.step_name, "message": self._error_detail(exc)}
                    run_errors.append(error)
                    self._finish_step(
                        step.id,
                        status_value="failed",
                        result={},
                        errors=[error],
                        warnings=[],
                    )

            run = self._finalize_run(
                run.id,
                summary=summary,
                errors=run_errors,
                warnings=run_warnings,
            )
            return self._result(run)
        except Exception:
            self.db.rollback()
            run = self._mark_run_failed(run.id)
            return self._result(run)

    def list_runs(
        self,
        *,
        status_filter: str | None = None,
        mode: str | None = None,
        limit: int = 20,
    ) -> list[DataPipelineRun]:
        if status_filter is not None and status_filter not in PIPELINE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid pipeline status",
            )
        if mode is not None and mode not in PIPELINE_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid pipeline mode",
            )
        stmt = select(DataPipelineRun).options(selectinload(DataPipelineRun.steps))
        if status_filter is not None:
            stmt = stmt.where(DataPipelineRun.status == status_filter)
        if mode is not None:
            stmt = stmt.where(DataPipelineRun.mode == mode)
        stmt = stmt.order_by(DataPipelineRun.started_at.desc(), DataPipelineRun.id.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def get_run(self, run_id: int) -> DataPipelineRun:
        run = self.db.execute(
            select(DataPipelineRun)
            .options(selectinload(DataPipelineRun.steps))
            .where(DataPipelineRun.id == run_id)
        ).scalar_one_or_none()
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pipeline run not found",
            )
        return run

    def list_steps(self, run_id: int) -> list[DataPipelineStepRun]:
        self.get_run(run_id)
        return list(
            self.db.execute(
                select(DataPipelineStepRun)
                .where(DataPipelineStepRun.pipeline_run_id == run_id)
                .order_by(DataPipelineStepRun.id.asc())
            ).scalars()
        )

    def _execute_step(
        self,
        step_name: str,
        *,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
        company_ids: list[int],
        model_run_id: int | None,
    ) -> StepExecutionResult:
        if step_name == "moex_market_sync":
            return self._moex_market_sync(request, bond_ids)
        if step_name == "moex_cashflow_sync":
            return self._moex_cashflow_sync(request, bond_ids)
        if step_name == "credit_health":
            return self._credit_health(request, company_ids)
        if step_name == "bond_risk_assessment":
            return self._bond_risk_assessment(request, bond_ids)
        if step_name == "dataset_build_price":
            return self._dataset_build_price(request, bond_ids)
        if step_name == "labels_total_return":
            return self._labels_total_return(request, bond_ids)
        if step_name == "labels_risk_adjusted":
            return self._labels_risk_adjusted(request, bond_ids)
        if step_name == READINESS_STEP:
            return self._data_readiness_check(request, bond_ids, company_ids)
        if step_name == "ml_train":
            return self._ml_train(request, bond_ids, company_ids)
        if step_name == "ml_predict":
            if model_run_id is None:
                raise PipelineStepSkipped("ml_predict skipped because model_run_id is missing")
            return self._ml_predict(request, model_run_id)
        if step_name == "ml_evaluate":
            if model_run_id is None:
                raise PipelineStepSkipped("ml_evaluate skipped because model_run_id is missing")
            return self._ml_evaluate(request, model_run_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pipeline step",
        )

    def _moex_market_sync(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> StepExecutionResult:
        result = MoexMarketDataService(self.db).sync(
            MoexMarketDataSyncRequest(
                bond_ids=bond_ids,
                date_from=request.date_from,
                date_to=request.date_to,
                board=request.moex_board,
                rebuild_existing=request.rebuild_existing,
            )
        )
        return self._service_result(result)

    def _moex_cashflow_sync(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> StepExecutionResult:
        result = MoexCashflowService(self.db).sync(
            MoexCashflowSyncRequest(
                bond_ids=bond_ids,
                date_from=request.date_from,
                date_to=request.date_to,
                rebuild_existing=request.rebuild_existing,
            )
        )
        return self._service_result(result)

    def _credit_health(
        self,
        request: DataPipelineRunRequest,
        company_ids: list[int],
    ) -> StepExecutionResult:
        service = CompanyCreditHealthService(self.db)
        calculated = 0
        errors: list[dict[str, Any]] = []
        for company_id in company_ids:
            try:
                service.calculate_for_company(company_id, as_of_date=request.date_to)
                calculated += 1
            except Exception as exc:
                self.db.rollback()
                errors.append(
                    {
                        "entity_type": "company",
                        "entity_id": company_id,
                        "message": self._error_detail(exc),
                    }
                )
        return StepExecutionResult(
            result={
                "total": len(company_ids),
                "calculated": calculated,
                "failed": len(errors),
            },
            errors=errors,
            warnings=[],
        )

    def _bond_risk_assessment(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> StepExecutionResult:
        service = BondRiskAssessmentService(self.db)
        assessed = 0
        errors: list[dict[str, Any]] = []
        for bond_id in bond_ids:
            try:
                service.assess_bond(bond_id, as_of_date=request.date_to)
                assessed += 1
            except Exception as exc:
                self.db.rollback()
                errors.append(
                    {
                        "entity_type": "bond",
                        "entity_id": bond_id,
                        "message": self._error_detail(exc),
                    }
                )
        return StepExecutionResult(
            result={
                "total": len(bond_ids),
                "assessed": assessed,
                "failed": len(errors),
            },
            errors=errors,
            warnings=[],
        )

    def _dataset_build_price(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> StepExecutionResult:
        result = DatasetBuildService(self.db).build(
            DatasetBuildRequest(
                as_of_date_from=request.date_from,
                as_of_date_to=request.date_to,
                horizon_days=request.horizon_days,
                bond_ids=bond_ids,
                return_method="price",
                benchmark_return=request.benchmark_return,
                transaction_cost_rate=request.transaction_cost_rate,
                rebuild_existing=request.rebuild_existing,
            )
        )
        return self._service_result(result)

    def _labels_total_return(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> StepExecutionResult:
        result = TotalReturnLabelService(self.db).build_labels(
            BondTotalReturnLabelBuildRequest(
                as_of_date_from=request.date_from,
                as_of_date_to=request.date_to,
                horizon_days=request.horizon_days,
                bond_ids=bond_ids,
                return_method="total_return",
                transaction_cost_rate=request.transaction_cost_rate,
                rebuild_existing=request.rebuild_existing,
            )
        )
        return self._service_result(result)

    def _labels_risk_adjusted(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> StepExecutionResult:
        result = TotalReturnLabelService(self.db).build_labels(
            BondTotalReturnLabelBuildRequest(
                as_of_date_from=request.date_from,
                as_of_date_to=request.date_to,
                horizon_days=request.horizon_days,
                bond_ids=bond_ids,
                return_method="risk_adjusted",
                benchmark_return=request.benchmark_return,
                transaction_cost_rate=request.transaction_cost_rate,
                rebuild_existing=request.rebuild_existing,
            )
        )
        return self._service_result(result)

    def _data_readiness_check(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> StepExecutionResult:
        result = DataReadinessService(self.db).check(
            self._readiness_request(request, bond_ids, company_ids)
        )
        payload = self._to_json(result)
        return StepExecutionResult(
            result=payload,
            errors=[],
            warnings=self._readiness_warnings(payload),
        )

    def _ml_train(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> StepExecutionResult:
        result = MLTrainingService(self.db).train(
            MLTrainRequest(
                horizon_days=request.horizon_days,
                return_method=request.ml_return_method,
                include_credit_risk_features=request.ml_include_credit_risk_features,
                as_of_date_from=request.date_from,
                as_of_date_to=request.date_to,
                bond_ids=bond_ids,
                company_ids=company_ids,
                model_type="logistic_regression",
                test_size=request.ml_test_size,
                min_rows=request.ml_min_rows,
            )
        )
        payload = self._to_json(result)
        payload["model_run_id"] = result.run_id
        return StepExecutionResult(result=payload, errors=[], warnings=[])

    def _ml_predict(
        self,
        request: DataPipelineRunRequest,
        model_run_id: int,
    ) -> StepExecutionResult:
        result = MLPredictionService(self.db).predict(
            MLPredictionRequest(
                model_run_id=model_run_id,
                as_of_date_from=request.date_from,
                as_of_date_to=request.date_to,
                limit=5000,
                offset=0,
                save_predictions=True,
            )
        )
        return self._service_result(result)

    def _ml_evaluate(
        self,
        request: DataPipelineRunRequest,
        model_run_id: int,
    ) -> StepExecutionResult:
        report = MLEvaluationService(self.db).evaluate_run(
            model_run_id,
            filters=MLEvaluationFilters(
                as_of_date_from=request.date_from,
                as_of_date_to=request.date_to,
            ),
        )
        payload = self._to_json(report)
        metrics = report.evaluation_metrics
        calibration = report.calibration
        compact = {
            "model_run_id": report.model_run_id,
            "return_method": report.return_method,
            "evaluable_predictions": metrics.evaluable_count,
            "accuracy": metrics.accuracy,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
            "roc_auc": metrics.roc_auc,
            "brier_score": calibration.brier_score,
            "warnings": report.warnings,
            "report": payload,
        }
        return StepExecutionResult(
            result=compact,
            errors=[],
            warnings=self._warnings_from_value(report.warnings),
        )

    def _selected_bond_ids(self, request: DataPipelineRunRequest) -> list[int]:
        if request.bond_ids:
            return sorted(set(request.bond_ids))
        stmt = select(Bond.id)
        if request.company_ids:
            stmt = stmt.where(Bond.company_id.in_(set(request.company_ids)))
        stmt = stmt.order_by(Bond.id)
        return list(self.db.execute(stmt).scalars())

    def _selected_company_ids(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
    ) -> list[int]:
        if request.company_ids:
            return sorted(set(request.company_ids))
        if bond_ids:
            return list(
                self.db.execute(
                    select(Bond.company_id)
                    .where(Bond.id.in_(bond_ids))
                    .distinct()
                    .order_by(Bond.company_id)
                ).scalars()
            )
        return list(self.db.execute(select(Company.id).order_by(Company.id)).scalars())

    @staticmethod
    def _ml_requested_by_flags(request: DataPipelineRunRequest) -> bool:
        return request.run_ml or request.run_predictions or request.run_evaluation

    @staticmethod
    def _ml_requested_by_steps(steps: list[str]) -> bool:
        return any(step in ML_STEPS for step in steps)

    @staticmethod
    def _insert_readiness_step(steps: list[str]) -> list[str]:
        if READINESS_STEP in steps:
            return steps
        updated = list(steps)
        if "ml_train" in updated:
            updated.insert(updated.index("ml_train"), READINESS_STEP)
        else:
            updated.append(READINESS_STEP)
        return updated

    @staticmethod
    def _ml_skip_message(
        *,
        readiness_status: str | None,
        readiness_enabled: bool,
        request: DataPipelineRunRequest,
    ) -> str | None:
        if not readiness_enabled:
            return None
        if readiness_status is None:
            return "ML steps skipped because readiness check did not complete"
        if readiness_status == "not_ready" and request.fail_on_not_ready:
            return "ML steps skipped because dataset readiness is not_ready"
        if readiness_status == "warning" and not request.allow_readiness_warning:
            return "ML steps skipped because readiness status is warning"
        return None

    @staticmethod
    def _ml_guard_warning(
        *,
        readiness_status: str | None,
        readiness_enabled: bool,
        request: DataPipelineRunRequest,
    ) -> str | None:
        if not readiness_enabled:
            return None
        if readiness_status == "not_ready" and not request.fail_on_not_ready:
            return "ML is running despite not_ready dataset readiness status"
        return None

    def _create_run(
        self,
        request: DataPipelineRunRequest,
        *,
        steps: list[str],
        bond_ids: list[int],
        company_ids: list[int],
    ) -> DataPipelineRun:
        run = DataPipelineRun(
            status="running",
            mode=request.mode,
            date_from=request.date_from,
            date_to=request.date_to,
            horizon_days=request.horizon_days,
            bond_ids_json=bond_ids,
            company_ids_json=company_ids,
            return_methods_json=list(request.return_methods),
            params_json={
                **self._to_json(request),
                "resolved_steps": list(steps),
                "resolved_bond_ids": list(bond_ids),
                "resolved_company_ids": list(company_ids),
            },
            summary_json={},
            errors_json=[],
            warnings_json=[],
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _create_step_runs(
        self,
        run_id: int,
        *,
        steps: list[str],
        request: DataPipelineRunRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> list[DataPipelineStepRun]:
        step_runs: list[DataPipelineStepRun] = []
        for step_name in steps:
            step_run = DataPipelineStepRun(
                pipeline_run_id=run_id,
                step_name=step_name,
                status="pending",
                input_json=self._step_input(
                    step_name,
                    request=request,
                    bond_ids=bond_ids,
                    company_ids=company_ids,
                ),
                result_json={},
                errors_json=[],
                warnings_json=[],
            )
            self.db.add(step_run)
            step_runs.append(step_run)
        self.db.commit()
        for step_run in step_runs:
            self.db.refresh(step_run)
        return step_runs

    def _start_step(self, step_id: int) -> None:
        step = self._get_step(step_id)
        step.status = "running"
        step.started_at = datetime.now(timezone.utc)
        self.db.add(step)
        self.db.commit()

    def _finish_step(
        self,
        step_id: int,
        *,
        status_value: str,
        result: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> None:
        step = self._get_step(step_id)
        finished_at = datetime.now(timezone.utc)
        step.status = status_value
        step.finished_at = finished_at
        if step.started_at is not None:
            step.duration_ms = self._duration_ms(step.started_at, finished_at)
        step.result_json = self._to_json(result)
        step.errors_json = self._to_json(errors)
        step.warnings_json = self._to_json(warnings)
        self.db.add(step)
        self.db.commit()

    def _finalize_run(
        self,
        run_id: int,
        *,
        summary: dict[str, Any],
        errors: list[dict[str, Any]],
        warnings: list[dict[str, Any]],
    ) -> DataPipelineRun:
        run = self._get_run_model(run_id)
        has_skips_or_failures = any(
            step.status in {"skipped", "failed"} for step in run.steps
        )
        run.status = (
            "completed_with_errors"
            if errors or warnings or has_skips_or_failures
            else "completed"
        )
        run.finished_at = datetime.now(timezone.utc)
        run.summary_json = self._to_json(summary)
        run.errors_json = self._to_json(errors)
        run.warnings_json = self._to_json(warnings)
        self.db.add(run)
        self.db.commit()
        return self.get_run(run.id)

    def _mark_run_failed(self, run_id: int) -> DataPipelineRun:
        run = self._get_run_model(run_id)
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        self.db.add(run)
        self.db.commit()
        return self.get_run(run.id)

    def _get_step(self, step_id: int) -> DataPipelineStepRun:
        step = self.db.get(DataPipelineStepRun, step_id)
        if step is None:
            raise RuntimeError(f"Pipeline step {step_id} was not found")
        return step

    def _get_run_model(self, run_id: int) -> DataPipelineRun:
        run = self.db.execute(
            select(DataPipelineRun)
            .options(selectinload(DataPipelineRun.steps))
            .where(DataPipelineRun.id == run_id)
        ).scalar_one_or_none()
        if run is None:
            raise RuntimeError(f"Pipeline run {run_id} was not found")
        return run

    def _validate_and_resolve_steps(
        self,
        request: DataPipelineRunRequest,
    ) -> list[str]:
        if request.date_from > request.date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        if request.mode not in PIPELINE_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid pipeline mode",
            )
        if not request.return_methods:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="return_methods cannot be empty",
            )
        invalid_methods = set(request.return_methods) - PIPELINE_RETURN_METHODS
        if invalid_methods or request.ml_return_method not in PIPELINE_RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )
        if request.ml_test_size <= 0 or request.ml_test_size >= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ml_test_size must be greater than 0 and less than 1",
            )
        if request.ml_min_rows <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ml_min_rows must be positive",
            )
        if request.readiness_min_rows is not None and request.readiness_min_rows <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="readiness_min_rows must be positive",
            )
        if (
            request.readiness_min_positive_rows is not None
            and request.readiness_min_positive_rows < 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="readiness_min_positive_rows must be non-negative",
            )
        if (
            request.readiness_min_negative_rows is not None
            and request.readiness_min_negative_rows < 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="readiness_min_negative_rows must be non-negative",
            )
        if (
            request.readiness_max_insufficient_ratio is not None
            and (
                request.readiness_max_insufficient_ratio < 0
                or request.readiness_max_insufficient_ratio > 1
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="readiness_max_insufficient_ratio must be between 0 and 1",
            )
        if request.readiness_max_bond_issues < 1 or request.readiness_max_bond_issues > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="readiness_max_bond_issues must be between 1 and 500",
            )
        if request.run_predictions and not request.run_ml:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="run_predictions requires run_ml",
            )
        if request.run_evaluation and not request.run_predictions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="run_evaluation requires run_predictions",
            )

        if request.steps:
            invalid_steps = set(request.steps) - PIPELINE_STEPS
            if invalid_steps:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid pipeline step",
                )
            steps = list(dict.fromkeys(request.steps))
        else:
            steps = list(BASE_STEPS)
            for method in request.return_methods:
                step = RETURN_METHOD_STEPS[method]
                if step not in steps:
                    steps.append(step)
            if request.run_ml:
                steps.append("ml_train")
            if request.run_predictions:
                steps.append("ml_predict")
            if request.run_evaluation:
                steps.append("ml_evaluate")

        if "ml_train" in steps:
            request.run_ml = True
        ml_requested = self._ml_requested_by_steps(steps) or self._ml_requested_by_flags(request)
        readiness_enabled = (
            request.run_readiness_check
            if request.run_readiness_check is not None
            else ml_requested
        )
        if readiness_enabled and READINESS_STEP not in steps:
            steps = self._insert_readiness_step(steps)
        if "ml_predict" in steps and "ml_train" not in steps:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ml_predict requires ml_train in the same pipeline run",
            )
        if "ml_evaluate" in steps and "ml_predict" not in steps:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ml_evaluate requires ml_predict in the same pipeline run",
            )
        return steps

    def _step_input(
        self,
        step_name: str,
        *,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> dict[str, Any]:
        base = {
            "date_from": request.date_from,
            "date_to": request.date_to,
            "horizon_days": request.horizon_days,
            "bond_ids": bond_ids,
            "company_ids": company_ids,
            "rebuild_existing": request.rebuild_existing,
        }
        if step_name == "moex_market_sync":
            base["board"] = request.moex_board
        if step_name in {"labels_total_return", "labels_risk_adjusted"}:
            base["transaction_cost_rate"] = request.transaction_cost_rate
        if step_name == "labels_risk_adjusted":
            base["benchmark_return"] = request.benchmark_return
        if step_name == READINESS_STEP:
            base.update(
                {
                    "return_method": self._readiness_return_method(request),
                    "min_rows": request.readiness_min_rows or request.ml_min_rows,
                    "min_positive_rows": request.readiness_min_positive_rows,
                    "min_negative_rows": request.readiness_min_negative_rows,
                    "max_insufficient_ratio": request.readiness_max_insufficient_ratio,
                    "require_credit_risk": request.readiness_require_credit_risk,
                    "require_financial_reports": (
                        request.readiness_require_financial_reports
                    ),
                    "require_cashflows": request.readiness_require_cashflows,
                    "require_moex_secid": request.readiness_require_moex_secid,
                    "max_bond_issues": request.readiness_max_bond_issues,
                }
            )
        if step_name == "ml_train":
            base.update(
                {
                    "return_method": request.ml_return_method,
                    "min_rows": request.ml_min_rows,
                    "test_size": request.ml_test_size,
                    "include_credit_risk_features": (
                        request.ml_include_credit_risk_features
                    ),
                }
            )
        return self._to_json(base)

    @staticmethod
    def _service_result(result: BaseModel) -> StepExecutionResult:
        payload = DataPipelineService._to_json(result)
        return StepExecutionResult(
            result=payload,
            errors=DataPipelineService._errors_from_value(payload.get("errors", [])),
            warnings=DataPipelineService._warnings_from_value(payload.get("warnings", [])),
        )

    def _readiness_request(
        self,
        request: DataPipelineRunRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> DataReadinessCheckRequest:
        payload: dict[str, Any] = {
            "date_from": request.date_from,
            "date_to": request.date_to,
            "horizon_days": request.horizon_days,
            "bond_ids": bond_ids,
            "company_ids": company_ids,
            "return_method": self._readiness_return_method(request),
            "min_rows": request.readiness_min_rows or request.ml_min_rows,
            "require_credit_risk": request.readiness_require_credit_risk,
            "require_financial_reports": request.readiness_require_financial_reports,
            "require_cashflows": request.readiness_require_cashflows,
            "require_moex_secid": request.readiness_require_moex_secid,
            "max_bond_issues": request.readiness_max_bond_issues,
        }
        if request.readiness_min_positive_rows is not None:
            payload["min_positive_rows"] = request.readiness_min_positive_rows
        if request.readiness_min_negative_rows is not None:
            payload["min_negative_rows"] = request.readiness_min_negative_rows
        if request.readiness_max_insufficient_ratio is not None:
            payload["max_insufficient_ratio"] = request.readiness_max_insufficient_ratio
        return DataReadinessCheckRequest(**payload)

    @staticmethod
    def _readiness_return_method(request: DataPipelineRunRequest) -> str:
        if DataPipelineService._ml_requested_by_flags(request):
            return request.ml_return_method
        if "risk_adjusted" in request.return_methods:
            return "risk_adjusted"
        if "total_return" in request.return_methods:
            return "total_return"
        return "price"

    @staticmethod
    def _readiness_warnings(payload: dict[str, Any]) -> list[dict[str, Any]]:
        warnings: list[dict[str, Any]] = [
            {"step": READINESS_STEP, "message": warning}
            for warning in payload.get("warnings", [])
        ]
        for gate in payload.get("gates", []):
            if gate.get("status") == "warning":
                warnings.append(
                    {
                        "step": READINESS_STEP,
                        "gate": gate.get("name"),
                        "message": gate.get("message"),
                    }
                )
        return warnings

    @staticmethod
    def _errors_from_value(value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        if isinstance(value, list):
            return [
                item if isinstance(item, dict) else {"message": str(item)}
                for item in value
            ]
        return [{"message": str(value)}]

    @staticmethod
    def _warnings_from_value(value: Any) -> list[dict[str, Any]]:
        if not value:
            return []
        if isinstance(value, list):
            return [
                item if isinstance(item, dict) else {"message": str(item)}
                for item in value
            ]
        return [{"message": str(value)}]

    @staticmethod
    def _merge_summary(
        summary: dict[str, Any],
        step_name: str,
        result: dict[str, Any],
    ) -> None:
        mapping = {
            "moex_market_sync": {
                "created": "market_snapshots_created",
                "updated": "market_snapshots_updated",
            },
            "moex_cashflow_sync": {
                "created": "cashflow_events_created",
                "updated": "cashflow_events_updated",
            },
            "credit_health": {"calculated": "credit_health_calculated"},
            "bond_risk_assessment": {
                "assessed": "bond_risk_assessments_calculated"
            },
            "dataset_build_price": {
                "features_created": "features_created",
                "features_updated": "features_updated",
                "labels_created": "price_labels_created",
                "labels_updated": "price_labels_updated",
            },
            "labels_total_return": {
                "created": "total_return_labels_created",
                "updated": "total_return_labels_updated",
            },
            "labels_risk_adjusted": {
                "created": "risk_adjusted_labels_created",
                "updated": "risk_adjusted_labels_updated",
            },
            "ml_train": {"model_run_id": "ml_model_run_id"},
            "ml_predict": {"total": "predictions_created_or_updated"},
            "ml_evaluate": {"evaluation_metrics": "evaluation_metrics"},
        }
        if step_name == "ml_evaluate":
            summary["evaluation_metrics"] = {
                key: result.get(key)
                for key in (
                    "evaluable_predictions",
                    "accuracy",
                    "precision",
                    "recall",
                    "f1",
                    "roc_auc",
                    "brier_score",
                )
            }
            return
        if step_name == READINESS_STEP:
            gates = result.get("gates", [])
            readiness_summary = result.get("summary", {})
            summary.update(
                {
                    "readiness_status": result.get("status"),
                    "ready_for_ml_training": readiness_summary.get(
                        "ready_for_ml_training"
                    ),
                    "readiness_failed_gates": [
                        gate.get("name")
                        for gate in gates
                        if gate.get("status") == "fail"
                    ],
                    "readiness_warning_gates": [
                        gate.get("name")
                        for gate in gates
                        if gate.get("status") == "warning"
                    ],
                    "readiness_evaluable_rows": readiness_summary.get(
                        "evaluable_label_count"
                    ),
                    "readiness_positive_rows": readiness_summary.get(
                        "positive_label_count"
                    ),
                    "readiness_negative_rows": readiness_summary.get(
                        "negative_label_count"
                    ),
                    "readiness_insufficient_ratio": readiness_summary.get(
                        "insufficient_ratio"
                    ),
                }
            )
            return
        for source_key, target_key in mapping.get(step_name, {}).items():
            if source_key in result:
                summary[target_key] = result[source_key]

    @staticmethod
    def _to_json(value: Any) -> Any:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json")
        return jsonable_encoder(value)

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)

    @staticmethod
    def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
        if started_at.tzinfo is None and finished_at.tzinfo is not None:
            finished_at = finished_at.replace(tzinfo=None)
        elif started_at.tzinfo is not None and finished_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=None)
        return int((finished_at - started_at).total_seconds() * 1000)

    @staticmethod
    def _result(run: DataPipelineRun) -> DataPipelineRunResult:
        return DataPipelineRunResult(
            run=run,
            status=run.status,
            summary=run.summary_json,
            errors=run.errors_json,
            warnings=run.warnings_json,
        )


class PipelineStepSkipped(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
