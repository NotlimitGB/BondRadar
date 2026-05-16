from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.schemas.data_readiness import DataReadinessCheckRequest
from app.schemas.ml_model import MLTrainRequest, MLPredictionRequest
from app.schemas.ml_walk_forward import (
    MLWalkForwardFoldResult,
    MLWalkForwardRunRequest,
    MLWalkForwardRunResponse,
    MLWalkForwardSummary,
    MLWalkForwardWarning,
)
from app.services.data_readiness_service import DataReadinessService
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService


PREDICTION_PAGE_SIZE = 5000


class MLWalkForwardService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def run(self, request: MLWalkForwardRunRequest) -> MLWalkForwardRunResponse:
        self._validate_request(request)
        folds, warnings = self._generate_folds(request)
        fold_results = [
            self._run_fold(fold_index=index, fold=fold, request=request)
            for index, fold in enumerate(folds, start=1)
        ]
        completed_count = sum(fold.status == "completed" for fold in fold_results)
        skipped_count = sum(fold.status == "skipped" for fold in fold_results)
        failed_count = sum(fold.status == "failed" for fold in fold_results)
        return MLWalkForwardRunResponse(
            date_from=request.date_from,
            date_to=request.date_to,
            return_method=request.return_method,
            horizon_days=request.horizon_days,
            model_type=request.model_type,
            fold_count=len(fold_results),
            completed_fold_count=completed_count,
            skipped_fold_count=skipped_count,
            failed_fold_count=failed_count,
            summary=self._summary(fold_results),
            folds=fold_results,
            warnings=warnings,
        )

    def _validate_request(self, request: MLWalkForwardRunRequest) -> None:
        if request.date_from > request.date_to:
            self._bad_request("Invalid date range")
        if request.train_window_days is not None and request.train_window_days <= 0:
            self._bad_request("train_window_days must be positive when provided")
        if request.test_window_days <= 0:
            self._bad_request("test_window_days must be positive")
        if request.step_days <= 0:
            self._bad_request("step_days must be positive")
        if request.horizon_days <= 0:
            self._bad_request("horizon_days must be positive")
        if request.return_method not in RETURN_METHODS:
            self._bad_request("Invalid return method")
        if request.model_type != MLTrainingService.MODEL_TYPE:
            self._bad_request("Invalid model type")
        if request.min_rows <= 0:
            self._bad_request("min_rows must be positive")
        if request.min_positive_rows < 0 or request.min_negative_rows < 0:
            self._bad_request("min class rows must be non-negative")
        if (
            request.readiness_min_rows is not None
            and request.readiness_min_rows <= 0
        ):
            self._bad_request("readiness_min_rows must be positive when provided")
        if (
            request.readiness_min_positive_rows is not None
            and request.readiness_min_positive_rows < 0
        ):
            self._bad_request("min class rows must be non-negative")
        if (
            request.readiness_min_negative_rows is not None
            and request.readiness_min_negative_rows < 0
        ):
            self._bad_request("min class rows must be non-negative")
        if (
            request.readiness_max_insufficient_ratio is not None
            and (
                request.readiness_max_insufficient_ratio < Decimal("0")
                or request.readiness_max_insufficient_ratio > Decimal("1")
            )
        ):
            self._bad_request("readiness_max_insufficient_ratio must be between 0 and 1")
        if request.test_size <= Decimal("0") or request.test_size >= Decimal("0.5"):
            self._bad_request("test_size must be greater than 0 and less than 0.5")
        if request.max_folds < 1 or request.max_folds > 200:
            self._bad_request("max_folds must be between 1 and 200")

    def _generate_folds(
        self,
        request: MLWalkForwardRunRequest,
    ) -> tuple[list[dict[str, date | None]], list[MLWalkForwardWarning]]:
        warnings: list[MLWalkForwardWarning] = []
        start_floor = self._training_start_floor(request)
        folds: list[dict[str, date | None]] = []
        current_date = request.date_from
        while current_date <= request.date_to:
            train_date_to = current_date - timedelta(days=1)
            train_date_from = self._train_date_from(
                request=request,
                train_date_to=train_date_to,
                start_floor=start_floor,
            )
            folds.append(
                {
                    "train_date_from": train_date_from,
                    "train_date_to": train_date_to,
                    "predict_date_from": current_date,
                    "predict_date_to": min(
                        current_date + timedelta(days=request.test_window_days - 1),
                        request.date_to,
                    ),
                }
            )
            current_date += timedelta(days=request.step_days)

        if len(folds) > request.max_folds:
            folds = folds[: request.max_folds]
            warnings.append(
                MLWalkForwardWarning(
                    message="Walk-forward folds were truncated by max_folds",
                    details={"max_folds": request.max_folds},
                )
            )
        return folds, warnings

    def _training_start_floor(self, request: MLWalkForwardRunRequest) -> date | None:
        if request.train_window_days is not None:
            return request.min_train_date
        if request.min_train_date is not None:
            return request.min_train_date
        return self._earliest_joined_training_date(request)

    def _train_date_from(
        self,
        *,
        request: MLWalkForwardRunRequest,
        train_date_to: date,
        start_floor: date | None,
    ) -> date | None:
        train_date_from: date | None = None
        if request.train_window_days is not None:
            train_date_from = train_date_to - timedelta(
                days=request.train_window_days - 1
            )
        if start_floor is not None:
            train_date_from = (
                max(train_date_from, start_floor)
                if train_date_from is not None
                else start_floor
            )
        return train_date_from

    def _earliest_joined_training_date(
        self,
        request: MLWalkForwardRunRequest,
    ) -> date | None:
        stmt = (
            select(func.min(BondFeatureSnapshot.as_of_date))
            .join(
                BondReturnLabel,
                and_(
                    BondFeatureSnapshot.bond_id == BondReturnLabel.bond_id,
                    BondFeatureSnapshot.as_of_date == BondReturnLabel.as_of_date,
                ),
            )
            .where(
                BondReturnLabel.horizon_days == request.horizon_days,
                BondReturnLabel.return_method == request.return_method,
                BondReturnLabel.label.in_(("positive_return", "negative_return")),
                BondReturnLabel.label_binary.is_not(None),
                BondFeatureSnapshot.as_of_date <= request.date_to,
            )
        )
        if request.bond_ids:
            stmt = stmt.where(BondFeatureSnapshot.bond_id.in_(set(request.bond_ids)))
        if request.company_ids:
            stmt = stmt.where(
                BondFeatureSnapshot.company_id.in_(set(request.company_ids))
            )
        return self.db.execute(stmt).scalar_one_or_none()

    def _run_fold(
        self,
        *,
        fold_index: int,
        fold: dict[str, date | None],
        request: MLWalkForwardRunRequest,
    ) -> MLWalkForwardFoldResult:
        train_date_from = fold["train_date_from"]
        train_date_to = fold["train_date_to"]
        predict_date_from = fold["predict_date_from"]
        predict_date_to = fold["predict_date_to"]
        assert isinstance(train_date_to, date)
        assert isinstance(predict_date_from, date)
        assert isinstance(predict_date_to, date)
        warnings: list[MLWalkForwardWarning] = []
        if train_date_from is None or train_date_from > train_date_to:
            warnings.append(
                MLWalkForwardWarning(
                    message="Training window is empty for fold",
                    fold_index=fold_index,
                    details={
                        "train_date_from": (
                            train_date_from.isoformat()
                            if train_date_from is not None
                            else None
                        ),
                        "train_date_to": train_date_to.isoformat(),
                    },
                )
            )
            return self._fold_result(
                fold_index=fold_index,
                status="skipped",
                fold=fold,
                warnings=warnings,
                error="Training window is empty for fold",
            )

        readiness_info = self._empty_readiness_info()
        model_run_id: int | None = None
        train_rows: int | None = None
        test_rows: int | None = None
        positive_rows: int | None = None
        negative_rows: int | None = None
        metrics: dict[str, Any] | None = None
        try:
            if request.run_readiness_check:
                readiness, readiness_warnings = self._check_readiness(
                    request=request,
                    fold_index=fold_index,
                    train_date_from=train_date_from,
                    train_date_to=train_date_to,
                )
                readiness_info = readiness
                warnings.extend(readiness_warnings)
                if readiness["status"] == "not_ready":
                    if request.skip_not_ready_folds:
                        return self._fold_result(
                            fold_index=fold_index,
                            status="skipped",
                            fold=fold,
                            readiness_info=readiness_info,
                            warnings=warnings,
                            error="Fold readiness status was not ready",
                        )
                    warnings.append(
                        MLWalkForwardWarning(
                            message="Fold readiness status was not ready; training was attempted",
                            fold_index=fold_index,
                        )
                    )

            train_result = MLTrainingService(self.db).train(
                MLTrainRequest(
                    horizon_days=request.horizon_days,
                    return_method=request.return_method,
                    include_credit_risk_features=request.include_credit_risk_features,
                    as_of_date_from=train_date_from,
                    as_of_date_to=train_date_to,
                    bond_ids=request.bond_ids,
                    company_ids=request.company_ids,
                    model_type=request.model_type,
                    test_size=float(request.test_size),
                    min_rows=request.min_rows,
                )
            )
            model_run_id = train_result.run_id
            train_rows = train_result.train_rows
            test_rows = train_result.test_rows
            positive_rows = train_result.positive_rows
            negative_rows = train_result.negative_rows
            metrics = train_result.metrics
            if train_result.status != "completed":
                return self._fold_result(
                    fold_index=fold_index,
                    status="failed",
                    fold=fold,
                    readiness_info=readiness_info,
                    model_run_id=model_run_id,
                    train_rows=train_rows,
                    test_rows=test_rows,
                    positive_rows=positive_rows,
                    negative_rows=negative_rows,
                    metrics=metrics,
                    warnings=warnings,
                    error="ML training did not complete",
                )

            prediction_count, saved_prediction_count = self._predict_for_scope(
                model_run_id=train_result.run_id,
                request=request,
                predict_date_from=predict_date_from,
                predict_date_to=predict_date_to,
            )
            return self._fold_result(
                fold_index=fold_index,
                status="completed",
                fold=fold,
                readiness_info=readiness_info,
                model_run_id=model_run_id,
                train_rows=train_rows,
                test_rows=test_rows,
                positive_rows=positive_rows,
                negative_rows=negative_rows,
                prediction_count=prediction_count,
                saved_prediction_count=saved_prediction_count,
                metrics=metrics,
                warnings=warnings,
            )
        except HTTPException as exc:
            return self._fold_result(
                fold_index=fold_index,
                status="failed",
                fold=fold,
                readiness_info=readiness_info,
                model_run_id=model_run_id,
                train_rows=train_rows,
                test_rows=test_rows,
                positive_rows=positive_rows,
                negative_rows=negative_rows,
                metrics=metrics,
                warnings=[
                    *warnings,
                    MLWalkForwardWarning(
                        message="Fold failed during walk-forward execution",
                        fold_index=fold_index,
                        details={"detail": str(exc.detail)},
                    ),
                ],
                error=str(exc.detail),
            )
        except Exception as exc:
            return self._fold_result(
                fold_index=fold_index,
                status="failed",
                fold=fold,
                readiness_info=readiness_info,
                model_run_id=model_run_id,
                train_rows=train_rows,
                test_rows=test_rows,
                positive_rows=positive_rows,
                negative_rows=negative_rows,
                metrics=metrics,
                warnings=[
                    *warnings,
                    MLWalkForwardWarning(
                        message="Fold failed during walk-forward execution",
                        fold_index=fold_index,
                        details={"detail": str(exc)},
                    ),
                ],
                error="Fold failed during walk-forward execution",
            )

    def _check_readiness(
        self,
        *,
        request: MLWalkForwardRunRequest,
        fold_index: int,
        train_date_from: date,
        train_date_to: date,
    ) -> tuple[dict[str, Any], list[MLWalkForwardWarning]]:
        readiness_request = DataReadinessCheckRequest(
            date_from=train_date_from,
            date_to=train_date_to,
            horizon_days=request.horizon_days,
            bond_ids=request.bond_ids,
            company_ids=request.company_ids,
            return_method=request.return_method,
            min_rows=request.readiness_min_rows or request.min_rows,
            min_positive_rows=(
                request.readiness_min_positive_rows
                if request.readiness_min_positive_rows is not None
                else request.min_positive_rows
            ),
            min_negative_rows=(
                request.readiness_min_negative_rows
                if request.readiness_min_negative_rows is not None
                else request.min_negative_rows
            ),
            max_insufficient_ratio=(
                request.readiness_max_insufficient_ratio
                if request.readiness_max_insufficient_ratio is not None
                else Decimal("0.30")
            ),
            require_credit_risk=request.include_credit_risk_features,
        )
        response = DataReadinessService(self.db).check(readiness_request)
        summary = response.summary
        warnings = [
            MLWalkForwardWarning(
                message=warning,
                fold_index=fold_index,
            )
            for warning in response.warnings
        ]
        return (
            {
                "status": response.status,
                "evaluable_rows": summary.evaluable_label_count,
                "positive_rows": summary.positive_label_count,
                "negative_rows": summary.negative_label_count,
                "insufficient_ratio": summary.insufficient_ratio,
            },
            warnings,
        )

    def _predict_for_scope(
        self,
        *,
        model_run_id: int,
        request: MLWalkForwardRunRequest,
        predict_date_from: date,
        predict_date_to: date,
    ) -> tuple[int, int]:
        prediction_count = 0
        saved_prediction_count = 0
        prediction_service = MLPredictionService(self.db)
        for scope in self._prediction_scopes(request):
            offset = 0
            while True:
                prediction_response = prediction_service.predict(
                    MLPredictionRequest(
                        model_run_id=model_run_id,
                        bond_id=scope["bond_id"],
                        company_id=scope["company_id"],
                        as_of_date_from=predict_date_from,
                        as_of_date_to=predict_date_to,
                        limit=PREDICTION_PAGE_SIZE,
                        offset=offset,
                        save_predictions=request.save_predictions,
                    )
                )
                row_count = len(prediction_response.predictions)
                prediction_count += row_count
                if request.save_predictions:
                    saved_prediction_count += row_count
                if (
                    row_count == 0
                    or offset + PREDICTION_PAGE_SIZE >= prediction_response.total
                ):
                    break
                offset += PREDICTION_PAGE_SIZE
        return prediction_count, saved_prediction_count

    @staticmethod
    def _prediction_scopes(
        request: MLWalkForwardRunRequest,
    ) -> list[dict[str, int | None]]:
        if request.bond_ids:
            return [
                {"bond_id": bond_id, "company_id": None}
                for bond_id in request.bond_ids
            ]
        if request.company_ids:
            return [
                {"bond_id": None, "company_id": company_id}
                for company_id in request.company_ids
            ]
        return [{"bond_id": None, "company_id": None}]

    def _summary(
        self,
        folds: list[MLWalkForwardFoldResult],
    ) -> MLWalkForwardSummary:
        completed = [fold for fold in folds if fold.status == "completed"]
        average_auc = self._average_metric(completed, "auc")
        if average_auc is None:
            average_auc = self._average_metric(completed, "roc_auc")
        return MLWalkForwardSummary(
            model_run_ids=[
                fold.model_run_id
                for fold in completed
                if fold.model_run_id is not None
            ],
            total_predictions=sum(fold.prediction_count for fold in completed),
            total_saved_predictions=sum(
                fold.saved_prediction_count for fold in completed
            ),
            average_train_rows=self._average(
                fold.train_rows for fold in completed if fold.train_rows is not None
            ),
            average_test_rows=self._average(
                fold.test_rows for fold in completed if fold.test_rows is not None
            ),
            average_positive_rows=self._average(
                fold.positive_rows
                for fold in completed
                if fold.positive_rows is not None
            ),
            average_negative_rows=self._average(
                fold.negative_rows
                for fold in completed
                if fold.negative_rows is not None
            ),
            average_accuracy=self._average_metric(completed, "accuracy"),
            average_auc=average_auc,
        )

    @staticmethod
    def _average(values: Any) -> Decimal | None:
        items = [Decimal(str(value)) for value in values if value is not None]
        if not items:
            return None
        return sum(items, Decimal("0")) / Decimal(len(items))

    def _average_metric(
        self,
        folds: list[MLWalkForwardFoldResult],
        metric_name: str,
    ) -> Decimal | None:
        return self._average(
            fold.metrics.get(metric_name)
            for fold in folds
            if fold.metrics and fold.metrics.get(metric_name) is not None
        )

    @staticmethod
    def _fold_result(
        *,
        fold_index: int,
        status: str,
        fold: dict[str, date | None],
        readiness_info: dict[str, Any] | None = None,
        model_run_id: int | None = None,
        train_rows: int | None = None,
        test_rows: int | None = None,
        positive_rows: int | None = None,
        negative_rows: int | None = None,
        prediction_count: int = 0,
        saved_prediction_count: int = 0,
        metrics: dict[str, Any] | None = None,
        warnings: list[MLWalkForwardWarning] | None = None,
        error: str | None = None,
    ) -> MLWalkForwardFoldResult:
        readiness = readiness_info or MLWalkForwardService._empty_readiness_info()
        return MLWalkForwardFoldResult(
            fold_index=fold_index,
            status=status,
            train_date_from=fold["train_date_from"],
            train_date_to=fold["train_date_to"],  # type: ignore[arg-type]
            predict_date_from=fold["predict_date_from"],  # type: ignore[arg-type]
            predict_date_to=fold["predict_date_to"],  # type: ignore[arg-type]
            readiness_status=readiness["status"],
            readiness_evaluable_rows=readiness["evaluable_rows"],
            readiness_positive_rows=readiness["positive_rows"],
            readiness_negative_rows=readiness["negative_rows"],
            readiness_insufficient_ratio=readiness["insufficient_ratio"],
            model_run_id=model_run_id,
            train_rows=train_rows,
            test_rows=test_rows,
            positive_rows=positive_rows,
            negative_rows=negative_rows,
            prediction_count=prediction_count,
            saved_prediction_count=saved_prediction_count,
            metrics=metrics,
            warnings=warnings or [],
            error=error,
        )

    @staticmethod
    def _empty_readiness_info() -> dict[str, Any]:
        return {
            "status": None,
            "evaluable_rows": None,
            "positive_rows": None,
            "negative_rows": None,
            "insufficient_ratio": None,
        }

    @staticmethod
    def _bad_request(detail: str) -> None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
