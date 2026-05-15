from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.bond_return_label import BondReturnLabel
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.ml_evaluation import (
    MLCalibrationReport,
    MLClassificationMetrics,
    MLModelComparisonItem,
    MLModelComparisonResponse,
    MLPredictionEvaluationRow,
    MLPredictionEvaluationRowsResponse,
    MLProbabilityBucket,
    MLRunEvaluationReport,
)
from app.services.ml_feature_builder import RETURN_METHODS


@dataclass(frozen=True)
class MLEvaluationFilters:
    as_of_date_from: date | None = None
    as_of_date_to: date | None = None
    bond_id: int | None = None
    company_id: int | None = None
    min_probability: float | None = None
    max_probability: float | None = None
    bucket_size: float = 0.1


class MLEvaluationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate_run(
        self,
        run_id: int,
        *,
        filters: MLEvaluationFilters,
    ) -> MLRunEvaluationReport:
        self._validate_filters(filters)
        run = self._get_completed_run(run_id)
        rows = self._rows_for_run(run, filters=filters, limit=None, offset=0)
        metrics = self._classification_metrics(rows)
        calibration = self._calibration(rows, filters.bucket_size)
        warnings = self._warnings(run, rows, metrics)
        return MLRunEvaluationReport(
            model_run_id=run.id,
            model_type=run.model_type,
            status=run.status,
            horizon_days=run.horizon_days,
            return_method=self._return_method(run),
            features=run.features,
            params=run.params,
            training_metrics=run.metrics,
            evaluation_metrics=metrics,
            calibration=calibration,
            feature_importance=run.feature_importance,
            coverage=self._coverage(rows),
            warnings=warnings,
        )

    def evaluation_rows(
        self,
        run_id: int,
        *,
        filters: MLEvaluationFilters,
        limit: int = 100,
        offset: int = 0,
    ) -> MLPredictionEvaluationRowsResponse:
        self._validate_filters(filters)
        self._validate_pagination(limit=limit, offset=offset, max_limit=5000)
        run = self._get_completed_run(run_id)
        total = self._count_predictions(run, filters)
        rows = self._rows_for_run(run, filters=filters, limit=limit, offset=offset)
        return MLPredictionEvaluationRowsResponse(
            model_run_id=run.id,
            total=total,
            limit=limit,
            offset=offset,
            rows=rows,
        )

    def compare_runs(
        self,
        *,
        run_ids: list[int] | None = None,
        return_method: str | None = None,
        limit: int = 20,
    ) -> MLModelComparisonResponse:
        if return_method is not None and return_method not in RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )
        if limit <= 0 or limit > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 100",
            )

        stmt = select(MLModelRun).where(MLModelRun.status == "completed")
        if run_ids:
            stmt = stmt.where(MLModelRun.id.in_(set(run_ids)))
        stmt = stmt.order_by(MLModelRun.created_at.desc(), MLModelRun.id.desc()).limit(
            limit
        )
        runs = list(self.db.execute(stmt).scalars())
        if return_method is not None:
            runs = [run for run in runs if self._return_method(run) == return_method]

        items: list[MLModelComparisonItem] = []
        filters = MLEvaluationFilters()
        for run in runs:
            rows = self._rows_for_run(run, filters=filters, limit=None, offset=0)
            metrics = self._classification_metrics(rows)
            calibration = self._calibration(rows, filters.bucket_size)
            items.append(
                MLModelComparisonItem(
                    model_run_id=run.id,
                    model_type=run.model_type,
                    horizon_days=run.horizon_days,
                    return_method=self._return_method(run),
                    features_count=len(run.features),
                    train_rows=run.train_rows,
                    test_rows=run.test_rows,
                    prediction_count=len(rows),
                    evaluable_count=metrics.evaluable_count,
                    accuracy=metrics.accuracy,
                    precision=metrics.precision,
                    recall=metrics.recall,
                    f1=metrics.f1,
                    roc_auc=metrics.roc_auc,
                    brier_score=calibration.brier_score,
                    created_at=run.created_at,
                )
            )
        return MLModelComparisonResponse(total=len(items), rows=items)

    def _rows_for_run(
        self,
        run: MLModelRun,
        *,
        filters: MLEvaluationFilters,
        limit: int | None,
        offset: int,
    ) -> list[MLPredictionEvaluationRow]:
        return_method = self._return_method(run)
        stmt = (
            select(MLPrediction, BondReturnLabel)
            .outerjoin(
                BondReturnLabel,
                and_(
                    BondReturnLabel.bond_id == MLPrediction.bond_id,
                    BondReturnLabel.as_of_date == MLPrediction.as_of_date,
                    BondReturnLabel.horizon_days == run.horizon_days,
                    BondReturnLabel.return_method == return_method,
                ),
            )
            .where(
                MLPrediction.model_run_id == run.id,
                *self._prediction_conditions(filters),
            )
            .order_by(MLPrediction.as_of_date.asc(), MLPrediction.id.asc())
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        return [
            self._evaluation_row(prediction, label, run, return_method)
            for prediction, label in self.db.execute(stmt).all()
        ]

    def _count_predictions(
        self,
        run: MLModelRun,
        filters: MLEvaluationFilters,
    ) -> int:
        return int(
            self.db.execute(
                select(func.count())
                .select_from(MLPrediction)
                .where(
                    MLPrediction.model_run_id == run.id,
                    *self._prediction_conditions(filters),
                )
            ).scalar_one()
        )

    def _prediction_conditions(self, filters: MLEvaluationFilters) -> list[Any]:
        conditions: list[Any] = []
        if filters.as_of_date_from is not None:
            conditions.append(MLPrediction.as_of_date >= filters.as_of_date_from)
        if filters.as_of_date_to is not None:
            conditions.append(MLPrediction.as_of_date <= filters.as_of_date_to)
        if filters.bond_id is not None:
            conditions.append(MLPrediction.bond_id == filters.bond_id)
        if filters.company_id is not None:
            conditions.append(MLPrediction.company_id == filters.company_id)
        if filters.min_probability is not None:
            conditions.append(
                MLPrediction.probability_positive >= Decimal(str(filters.min_probability))
            )
        if filters.max_probability is not None:
            conditions.append(
                MLPrediction.probability_positive <= Decimal(str(filters.max_probability))
            )
        return conditions

    @staticmethod
    def _evaluation_row(
        prediction: MLPrediction,
        label: BondReturnLabel | None,
        run: MLModelRun,
        return_method: str,
    ) -> MLPredictionEvaluationRow:
        is_evaluable = (
            label is not None
            and label.label in {"positive_return", "negative_return"}
            and label.label_binary is not None
        )
        predicted_binary = MLEvaluationService._predicted_binary(
            prediction.predicted_label
        )
        is_correct = None
        if is_evaluable:
            is_correct = predicted_binary == label.label_binary
        return MLPredictionEvaluationRow(
            prediction_id=prediction.id,
            model_run_id=prediction.model_run_id,
            feature_snapshot_id=prediction.feature_snapshot_id,
            bond_id=prediction.bond_id,
            company_id=prediction.company_id,
            as_of_date=prediction.as_of_date,
            horizon_days=run.horizon_days,
            return_method=return_method,
            probability_positive=prediction.probability_positive,
            predicted_label=prediction.predicted_label,
            actual_label=None if label is None else label.label,
            actual_label_binary=None if label is None else label.label_binary,
            future_return=None if label is None else label.future_return,
            price_return=None if label is None else label.price_return,
            net_total_return=None if label is None else label.net_total_return,
            risk_adjusted_excess_return=(
                None if label is None else label.risk_adjusted_excess_return
            ),
            required_risk_premium=None if label is None else label.required_risk_premium,
            is_correct=is_correct,
            is_evaluable=is_evaluable,
            created_at=prediction.created_at,
        )

    def _classification_metrics(
        self,
        rows: list[MLPredictionEvaluationRow],
    ) -> MLClassificationMetrics:
        evaluable = [row for row in rows if row.is_evaluable]
        if not evaluable:
            return MLClassificationMetrics(
                evaluable_count=0,
                accuracy=None,
                precision=None,
                recall=None,
                f1=None,
                roc_auc=None,
                confusion_matrix={
                    "true_positive": 0,
                    "true_negative": 0,
                    "false_positive": 0,
                    "false_negative": 0,
                },
            )
        y_true = [int(row.actual_label_binary) for row in evaluable]
        y_pred = [self._predicted_binary(row.predicted_label) for row in evaluable]
        probabilities = [float(row.probability_positive) for row in evaluable]
        tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
        roc_auc = None
        if len(set(y_true)) == 2:
            roc_auc = float(roc_auc_score(y_true, probabilities))
        return MLClassificationMetrics(
            evaluable_count=len(evaluable),
            accuracy=float(accuracy_score(y_true, y_pred)),
            precision=float(precision_score(y_true, y_pred, zero_division=0)),
            recall=float(recall_score(y_true, y_pred, zero_division=0)),
            f1=float(f1_score(y_true, y_pred, zero_division=0)),
            roc_auc=roc_auc,
            confusion_matrix={
                "true_positive": int(tp),
                "true_negative": int(tn),
                "false_positive": int(fp),
                "false_negative": int(fn),
            },
        )

    def _calibration(
        self,
        rows: list[MLPredictionEvaluationRow],
        bucket_size: float,
    ) -> MLCalibrationReport:
        buckets: list[MLProbabilityBucket] = []
        bucket_count = int(round(1 / bucket_size))
        for index in range(bucket_count):
            bucket_from = round(index * bucket_size, 10)
            bucket_to = round(min(1.0, bucket_from + bucket_size), 10)
            if index == bucket_count - 1:
                bucket_rows = [
                    row
                    for row in rows
                    if float(row.probability_positive) >= bucket_from
                    and float(row.probability_positive) <= bucket_to
                ]
            else:
                bucket_rows = [
                    row
                    for row in rows
                    if float(row.probability_positive) >= bucket_from
                    and float(row.probability_positive) < bucket_to
                ]
            buckets.append(self._bucket(bucket_from, bucket_to, bucket_rows))

        evaluable = [row for row in rows if row.is_evaluable]
        brier_score = None
        actual_positive_rate = None
        if evaluable:
            y_true = [int(row.actual_label_binary) for row in evaluable]
            probabilities = [float(row.probability_positive) for row in evaluable]
            brier_score = float(brier_score_loss(y_true, probabilities))
            actual_positive_rate = sum(y_true) / len(y_true)
        average_probability = (
            sum(float(row.probability_positive) for row in rows) / len(rows)
            if rows
            else None
        )
        return MLCalibrationReport(
            bucket_size=bucket_size,
            buckets=buckets,
            brier_score=brier_score,
            average_probability=average_probability,
            actual_positive_rate=actual_positive_rate,
        )

    @staticmethod
    def _bucket(
        bucket_from: float,
        bucket_to: float,
        rows: list[MLPredictionEvaluationRow],
    ) -> MLProbabilityBucket:
        evaluable = [row for row in rows if row.is_evaluable]
        positive_count = sum(row.actual_label_binary == 1 for row in evaluable)
        negative_count = sum(row.actual_label_binary == 0 for row in evaluable)
        insufficient_count = sum(row.actual_label == "insufficient_data" for row in rows)
        missing_label_count = sum(row.actual_label is None for row in rows)
        return MLProbabilityBucket(
            bucket_from=bucket_from,
            bucket_to=bucket_to,
            predictions_count=len(rows),
            evaluable_count=len(evaluable),
            positive_count=positive_count,
            negative_count=negative_count,
            insufficient_count=insufficient_count,
            missing_label_count=missing_label_count,
            actual_positive_rate=(
                positive_count / len(evaluable) if evaluable else None
            ),
            avg_probability_positive=(
                sum(float(row.probability_positive) for row in rows) / len(rows)
                if rows
                else None
            ),
            avg_future_return=MLEvaluationService._avg_decimal(
                [row.future_return for row in rows]
            ),
            avg_price_return=MLEvaluationService._avg_decimal(
                [row.price_return for row in rows]
            ),
            avg_net_total_return=MLEvaluationService._avg_decimal(
                [row.net_total_return for row in rows]
            ),
            avg_risk_adjusted_excess_return=MLEvaluationService._avg_decimal(
                [row.risk_adjusted_excess_return for row in rows]
            ),
        )

    @staticmethod
    def _coverage(rows: list[MLPredictionEvaluationRow]) -> dict[str, Any]:
        return {
            "total_predictions": len(rows),
            "evaluable_predictions": sum(row.is_evaluable for row in rows),
            "missing_label_predictions": sum(row.actual_label is None for row in rows),
            "insufficient_label_predictions": sum(
                row.actual_label == "insufficient_data" for row in rows
            ),
            "positive_labels": sum(row.actual_label_binary == 1 for row in rows),
            "negative_labels": sum(row.actual_label_binary == 0 for row in rows),
            "as_of_date_min": min((row.as_of_date for row in rows), default=None),
            "as_of_date_max": max((row.as_of_date for row in rows), default=None),
            "bond_count": len({row.bond_id for row in rows}),
            "company_count": len({row.company_id for row in rows}),
        }

    def _warnings(
        self,
        run: MLModelRun,
        rows: list[MLPredictionEvaluationRow],
        metrics: MLClassificationMetrics,
    ) -> list[str]:
        warnings: list[str] = []
        if not rows:
            warnings.append("No predictions found for this model run")
        if metrics.evaluable_count == 0:
            warnings.append("No evaluable predictions found")
        classes = {
            row.actual_label_binary for row in rows if row.actual_label_binary is not None
        }
        if metrics.evaluable_count > 0 and len(classes) == 1:
            warnings.append("Only one actual class is present in evaluation rows")
        if self._return_method(run) == "price":
            warnings.append("Model run uses legacy price return method")
        return warnings

    def _get_completed_run(self, run_id: int) -> MLModelRun:
        run = self.db.get(MLModelRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ML model run not found",
            )
        if run.status != "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML model run is not completed",
            )
        return run

    @staticmethod
    def _return_method(run: MLModelRun) -> str:
        return (run.params or {}).get("return_method") or "price"

    @staticmethod
    def _predicted_binary(predicted_label: str) -> int:
        return 1 if predicted_label == "predicted_positive_return" else 0

    @staticmethod
    def _avg_decimal(values: list[Decimal | None]) -> Decimal | None:
        clean = [value for value in values if value is not None]
        if not clean:
            return None
        return sum(clean) / Decimal(len(clean))

    @staticmethod
    def _validate_filters(filters: MLEvaluationFilters) -> None:
        if (
            filters.as_of_date_from is not None
            and filters.as_of_date_to is not None
            and filters.as_of_date_from > filters.as_of_date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        probabilities = [filters.min_probability, filters.max_probability]
        if any(
            probability is not None and (probability < 0 or probability > 1)
            for probability in probabilities
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="probability filters must be between 0 and 1",
            )
        if (
            filters.min_probability is not None
            and filters.max_probability is not None
            and filters.min_probability > filters.max_probability
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_probability cannot exceed max_probability",
            )
        if filters.bucket_size <= 0 or filters.bucket_size > 0.5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bucket_size must be greater than 0 and at most 0.5",
            )

    @staticmethod
    def _validate_pagination(*, limit: int, offset: int, max_limit: int) -> None:
        if limit <= 0 or limit > max_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"limit must be between 1 and {max_limit}",
            )
        if offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="offset must be non-negative",
            )
