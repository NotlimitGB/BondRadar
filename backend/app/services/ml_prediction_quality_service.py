from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_return_label import BondReturnLabel
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.ml_prediction_quality import (
    MLPredictionQualityDateRow,
    MLPredictionQualityIssueSummary,
    MLPredictionQualityMetricSet,
    MLPredictionQualityMissingLabelExample,
    MLPredictionQualityOverview,
    MLPredictionQualityProbabilityBucket,
    MLPredictionQualityReportRequest,
    MLPredictionQualityReportResponse,
    MLPredictionQualityRunRow,
    MLPredictionQualityWarning,
)
from app.services.ml_feature_builder import RETURN_METHODS


EVALUABLE_LABELS = {"positive_return", "negative_return"}
NOT_READY_ISSUES = {
    "no_predictions",
    "no_evaluable_predictions",
    "high_missing_label_ratio",
    "low_positive_labels",
    "low_negative_labels",
}


@dataclass(frozen=True)
class PredictionQualityRow:
    prediction: MLPrediction
    label: BondReturnLabel | None
    probability_positive: Decimal | None

    @property
    def is_missing_label(self) -> bool:
        return self.label is None

    @property
    def is_evaluable(self) -> bool:
        return (
            self.label is not None
            and self.label.label in EVALUABLE_LABELS
            and self.label.label_binary is not None
            and self.label.future_return is not None
        )

    @property
    def actual_positive(self) -> int | None:
        if not self.is_evaluable or self.label is None:
            return None
        return int(self.label.label_binary)

    @property
    def realized_return(self) -> Decimal | None:
        if not self.is_evaluable or self.label is None:
            return None
        return self.label.future_return


class MLPredictionQualityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def report(
        self,
        request: MLPredictionQualityReportRequest,
    ) -> MLPredictionQualityReportResponse:
        model_run_ids = self._validate_request(request)
        model_runs = self._load_model_runs(model_run_ids)
        horizon_days = model_runs[0].horizon_days
        return_method = self._return_method(model_runs[0])
        self._validate_model_run_compatibility(model_runs)
        self._validate_requested_metadata(
            request,
            horizon_days=horizon_days,
            return_method=return_method,
        )

        predictions = self._load_predictions(model_run_ids, request)
        predictions, duplicates_resolved = self._resolve_duplicate_predictions(
            predictions,
            model_run_ids,
        )
        labels = self._label_map(
            predictions=predictions,
            horizon_days=horizon_days,
            return_method=return_method,
        )
        rows = [
            PredictionQualityRow(
                prediction=prediction,
                label=labels.get((prediction.bond_id, prediction.as_of_date)),
                probability_positive=prediction.probability_positive,
            )
            for prediction in predictions
        ]

        warnings: list[MLPredictionQualityWarning] = []
        if duplicates_resolved:
            warnings.append(
                MLPredictionQualityWarning(
                    message=(
                        "Duplicate walk-forward predictions were resolved "
                        "by model_run_ids order"
                    )
                )
            )
        missing_probability_count = sum(
            row.probability_positive is None for row in rows
        )
        if missing_probability_count:
            warnings.append(
                MLPredictionQualityWarning(
                    message="Some predictions have no probability_positive value",
                    details={"count": missing_probability_count},
                )
            )

        overview = self._overview(rows, request)
        metrics = self._metrics(rows, request)
        date_rows = self._date_rows(rows, request)
        missing_label_examples = self._missing_label_examples(
            rows,
            horizon_days=horizon_days,
            return_method=return_method,
            limit=request.limit,
        )

        return MLPredictionQualityReportResponse(
            model_run_id=model_run_ids[0] if len(model_run_ids) == 1 else None,
            model_run_ids=model_run_ids,
            model_run_count=len(model_run_ids),
            prediction_source_mode=(
                "single_model_run"
                if len(model_run_ids) == 1
                else "multiple_model_runs"
            ),
            date_from=request.date_from,
            date_to=request.date_to,
            horizon_days=horizon_days,
            return_method=return_method,
            overview=overview,
            metrics=metrics,
            issue_summary=self._issue_summary(
                overview=overview,
                metrics=metrics,
                request=request,
            ),
            run_rows=(
                self._run_rows(
                    rows=rows,
                    model_runs=model_runs,
                    return_method=return_method,
                    request=request,
                )
                if request.include_run_rows
                else []
            ),
            date_rows=(
                date_rows[request.offset : request.offset + request.limit]
                if request.include_date_rows
                else []
            ),
            probability_buckets=(
                self._probability_buckets(rows, request)
                if request.include_probability_buckets
                else []
            ),
            missing_label_examples=(
                missing_label_examples
                if request.include_missing_label_examples
                else []
            ),
            limit=request.limit,
            offset=request.offset,
            warnings=warnings,
        )

    def _load_model_runs(self, model_run_ids: list[int]) -> list[MLModelRun]:
        model_runs: list[MLModelRun] = []
        for model_run_id in model_run_ids:
            model_run = self.db.get(MLModelRun, model_run_id)
            if model_run is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="ML model run not found",
                )
            if model_run.status != "completed":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="ML model run is not completed",
                )
            model_runs.append(model_run)
        return model_runs

    def _validate_model_run_compatibility(
        self,
        model_runs: list[MLModelRun],
    ) -> None:
        horizon_days = model_runs[0].horizon_days
        return_method = self._return_method(model_runs[0])
        if any(
            model_run.horizon_days != horizon_days
            or self._return_method(model_run) != return_method
            for model_run in model_runs[1:]
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Model runs must use the same horizon and return method",
            )

    @staticmethod
    def _validate_requested_metadata(
        request: MLPredictionQualityReportRequest,
        *,
        horizon_days: int,
        return_method: str,
    ) -> None:
        if request.horizon_days is not None and request.horizon_days != horizon_days:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Model runs must use the same horizon and return method",
            )
        if (
            request.return_method is not None
            and request.return_method != return_method
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Model runs must use the same horizon and return method",
            )

    def _load_predictions(
        self,
        model_run_ids: list[int],
        request: MLPredictionQualityReportRequest,
    ) -> list[MLPrediction]:
        conditions = [MLPrediction.model_run_id.in_(set(model_run_ids))]
        if request.date_from is not None:
            conditions.append(MLPrediction.as_of_date >= request.date_from)
        if request.date_to is not None:
            conditions.append(MLPrediction.as_of_date <= request.date_to)
        return list(
            self.db.execute(
                select(MLPrediction)
                .where(*conditions)
                .order_by(
                    MLPrediction.as_of_date.asc(),
                    MLPrediction.model_run_id.asc(),
                    MLPrediction.bond_id.asc(),
                    MLPrediction.id.asc(),
                )
            ).scalars()
        )

    @staticmethod
    def _resolve_duplicate_predictions(
        predictions: list[MLPrediction],
        model_run_ids: list[int],
    ) -> tuple[list[MLPrediction], bool]:
        if len(model_run_ids) <= 1:
            return predictions, False
        model_run_order = {
            model_run_id: index for index, model_run_id in enumerate(model_run_ids)
        }
        prediction_by_key: dict[tuple[int, date], MLPrediction] = {}
        duplicates_resolved = False
        for prediction in predictions:
            key = (prediction.bond_id, prediction.as_of_date)
            existing = prediction_by_key.get(key)
            if existing is not None:
                duplicates_resolved = True
                if (
                    model_run_order[prediction.model_run_id]
                    < model_run_order[existing.model_run_id]
                ):
                    continue
            prediction_by_key[key] = prediction
        return (
            sorted(
                prediction_by_key.values(),
                key=lambda prediction: (
                    prediction.as_of_date,
                    model_run_order[prediction.model_run_id],
                    prediction.bond_id,
                    prediction.id,
                ),
            ),
            duplicates_resolved,
        )

    def _label_map(
        self,
        *,
        predictions: list[MLPrediction],
        horizon_days: int,
        return_method: str,
    ) -> dict[tuple[int, date], BondReturnLabel]:
        if not predictions:
            return {}
        bond_ids = {prediction.bond_id for prediction in predictions}
        as_of_dates = {prediction.as_of_date for prediction in predictions}
        labels = self.db.execute(
            select(BondReturnLabel)
            .where(
                BondReturnLabel.bond_id.in_(bond_ids),
                BondReturnLabel.as_of_date.in_(as_of_dates),
                BondReturnLabel.horizon_days == horizon_days,
                BondReturnLabel.return_method == return_method,
            )
            .order_by(BondReturnLabel.id.asc())
        ).scalars()
        return {(label.bond_id, label.as_of_date): label for label in labels}

    def _overview(
        self,
        rows: list[PredictionQualityRow],
        request: MLPredictionQualityReportRequest,
    ) -> MLPredictionQualityOverview:
        prediction_count = len(rows)
        evaluable_count = sum(row.is_evaluable for row in rows)
        missing_label_count = sum(row.is_missing_label for row in rows)
        positive_label_count = sum(row.actual_positive == 1 for row in rows)
        negative_label_count = sum(row.actual_positive == 0 for row in rows)
        predicted_positive_count = sum(
            self._predicted_positive(row, request) is True for row in rows
        )
        predicted_negative_count = sum(
            self._predicted_positive(row, request) is False for row in rows
        )
        probability_count = predicted_positive_count + predicted_negative_count
        missing_label_ratio = self._ratio(missing_label_count, prediction_count)
        ready_for_strategy_research = (
            evaluable_count >= request.minimum_evaluable_predictions
            and positive_label_count >= request.minimum_positive_labels
            and negative_label_count >= request.minimum_negative_labels
            and (
                missing_label_ratio is None
                or missing_label_ratio <= request.maximum_missing_label_ratio
            )
        )
        return MLPredictionQualityOverview(
            prediction_count=prediction_count,
            evaluable_prediction_count=evaluable_count,
            missing_label_count=missing_label_count,
            positive_label_count=positive_label_count,
            negative_label_count=negative_label_count,
            predicted_positive_count=predicted_positive_count,
            predicted_negative_count=predicted_negative_count,
            missing_label_ratio=missing_label_ratio,
            positive_label_ratio=self._ratio(positive_label_count, evaluable_count),
            predicted_positive_ratio=self._ratio(
                predicted_positive_count,
                probability_count,
            ),
            ready_for_strategy_research=ready_for_strategy_research,
        )

    def _metrics(
        self,
        rows: list[PredictionQualityRow],
        request: MLPredictionQualityReportRequest,
    ) -> MLPredictionQualityMetricSet:
        metric_rows = [
            row
            for row in rows
            if row.is_evaluable and row.probability_positive is not None
        ]
        true_positive = sum(
            self._predicted_positive(row, request) is True
            and row.actual_positive == 1
            for row in metric_rows
        )
        true_negative = sum(
            self._predicted_positive(row, request) is False
            and row.actual_positive == 0
            for row in metric_rows
        )
        false_positive = sum(
            self._predicted_positive(row, request) is True
            and row.actual_positive == 0
            for row in metric_rows
        )
        false_negative = sum(
            self._predicted_positive(row, request) is False
            and row.actual_positive == 1
            for row in metric_rows
        )
        accuracy = self._ratio(true_positive + true_negative, len(metric_rows))
        precision = self._ratio(true_positive, true_positive + false_positive)
        recall = self._ratio(true_positive, true_positive + false_negative)
        f1_score = None
        if precision is not None and recall is not None and precision + recall != 0:
            f1_score = (Decimal("2") * precision * recall) / (precision + recall)

        probabilities = [
            row.probability_positive
            for row in rows
            if row.probability_positive is not None
        ]
        positive_label_probabilities = [
            row.probability_positive
            for row in metric_rows
            if row.actual_positive == 1 and row.probability_positive is not None
        ]
        negative_label_probabilities = [
            row.probability_positive
            for row in metric_rows
            if row.actual_positive == 0 and row.probability_positive is not None
        ]
        average_positive_probability = self._average(positive_label_probabilities)
        average_negative_probability = self._average(negative_label_probabilities)
        probability_separation = None
        if (
            average_positive_probability is not None
            and average_negative_probability is not None
        ):
            probability_separation = (
                average_positive_probability - average_negative_probability
            )
        predicted_positive_returns = [
            row.realized_return
            for row in metric_rows
            if self._predicted_positive(row, request) is True
            and row.realized_return is not None
        ]
        predicted_negative_returns = [
            row.realized_return
            for row in metric_rows
            if self._predicted_positive(row, request) is False
            and row.realized_return is not None
        ]
        return MLPredictionQualityMetricSet(
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1_score,
            true_positive_count=true_positive,
            true_negative_count=true_negative,
            false_positive_count=false_positive,
            false_negative_count=false_negative,
            average_probability_positive=self._average(probabilities),
            median_probability_positive=self._median(probabilities),
            average_probability_for_positive_labels=average_positive_probability,
            average_probability_for_negative_labels=average_negative_probability,
            probability_separation=probability_separation,
            average_realized_return=self._average(
                [
                    row.realized_return
                    for row in rows
                    if row.is_evaluable and row.realized_return is not None
                ]
            ),
            average_realized_return_for_predicted_positive=self._average(
                predicted_positive_returns
            ),
            average_realized_return_for_predicted_negative=self._average(
                predicted_negative_returns
            ),
        )

    def _issue_summary(
        self,
        *,
        overview: MLPredictionQualityOverview,
        metrics: MLPredictionQualityMetricSet,
        request: MLPredictionQualityReportRequest,
    ) -> MLPredictionQualityIssueSummary:
        return MLPredictionQualityIssueSummary(
            missing_model_run_count=0,
            non_completed_model_run_count=0,
            incompatible_model_run_count=0,
            missing_label_count=overview.missing_label_count,
            high_missing_label_ratio=int(
                overview.missing_label_ratio is not None
                and overview.missing_label_ratio
                > request.maximum_missing_label_ratio
            ),
            low_evaluable_predictions=int(
                overview.evaluable_prediction_count
                < request.minimum_evaluable_predictions
            ),
            low_positive_labels=int(
                overview.positive_label_count < request.minimum_positive_labels
            ),
            low_negative_labels=int(
                overview.negative_label_count < request.minimum_negative_labels
            ),
            zero_predicted_positive_count=int(
                overview.prediction_count > 0
                and overview.predicted_positive_count == 0
            ),
            zero_predicted_negative_count=int(
                overview.prediction_count > 0
                and overview.predicted_negative_count == 0
            ),
            weak_probability_separation=int(
                metrics.probability_separation is not None
                and metrics.probability_separation <= 0
            ),
        )

    def _run_rows(
        self,
        *,
        rows: list[PredictionQualityRow],
        model_runs: list[MLModelRun],
        return_method: str,
        request: MLPredictionQualityReportRequest,
    ) -> list[MLPredictionQualityRunRow]:
        rows_by_run: dict[int, list[PredictionQualityRow]] = defaultdict(list)
        for row in rows:
            rows_by_run[row.prediction.model_run_id].append(row)
        return [
            self._run_row(
                model_run,
                rows_by_run.get(model_run.id, []),
                return_method=return_method,
                request=request,
            )
            for model_run in model_runs
        ]

    def _run_row(
        self,
        model_run: MLModelRun,
        rows: list[PredictionQualityRow],
        *,
        return_method: str,
        request: MLPredictionQualityReportRequest,
    ) -> MLPredictionQualityRunRow:
        overview = self._overview(rows, request)
        metrics = self._metrics(rows, request)
        issues = self._row_issues(overview, metrics, request)
        return MLPredictionQualityRunRow(
            model_run_id=model_run.id,
            status=self._status_from_issues(issues),
            horizon_days=model_run.horizon_days,
            return_method=return_method,
            prediction_count=overview.prediction_count,
            evaluable_prediction_count=overview.evaluable_prediction_count,
            missing_label_count=overview.missing_label_count,
            positive_label_count=overview.positive_label_count,
            negative_label_count=overview.negative_label_count,
            predicted_positive_count=overview.predicted_positive_count,
            predicted_negative_count=overview.predicted_negative_count,
            missing_label_ratio=overview.missing_label_ratio,
            accuracy=metrics.accuracy,
            precision=metrics.precision,
            recall=metrics.recall,
            f1_score=metrics.f1_score,
            average_probability_positive=metrics.average_probability_positive,
            probability_separation=metrics.probability_separation,
            first_prediction_date=min(
                (row.prediction.as_of_date for row in rows),
                default=None,
            ),
            last_prediction_date=max(
                (row.prediction.as_of_date for row in rows),
                default=None,
            ),
            issues=issues,
        )

    def _date_rows(
        self,
        rows: list[PredictionQualityRow],
        request: MLPredictionQualityReportRequest,
    ) -> list[MLPredictionQualityDateRow]:
        rows_by_date: dict[date, list[PredictionQualityRow]] = defaultdict(list)
        for row in rows:
            rows_by_date[row.prediction.as_of_date].append(row)
        date_rows: list[MLPredictionQualityDateRow] = []
        for as_of_date in sorted(rows_by_date):
            scoped_rows = rows_by_date[as_of_date]
            overview = self._overview(scoped_rows, request)
            metrics = self._metrics(scoped_rows, request)
            date_rows.append(
                MLPredictionQualityDateRow(
                    as_of_date=as_of_date,
                    prediction_count=overview.prediction_count,
                    evaluable_prediction_count=overview.evaluable_prediction_count,
                    missing_label_count=overview.missing_label_count,
                    positive_label_count=overview.positive_label_count,
                    negative_label_count=overview.negative_label_count,
                    predicted_positive_count=overview.predicted_positive_count,
                    predicted_negative_count=overview.predicted_negative_count,
                    missing_label_ratio=overview.missing_label_ratio,
                    accuracy=metrics.accuracy,
                    average_probability_positive=(
                        metrics.average_probability_positive
                    ),
                    average_realized_return=metrics.average_realized_return,
                    issues=self._row_issues(overview, metrics, request),
                )
            )
        return date_rows

    def _probability_buckets(
        self,
        rows: list[PredictionQualityRow],
        request: MLPredictionQualityReportRequest,
    ) -> list[MLPredictionQualityProbabilityBucket]:
        bucket_width = Decimal("1") / Decimal(request.bucket_count)
        bucket_rows: list[list[PredictionQualityRow]] = [
            [] for _ in range(request.bucket_count)
        ]
        for row in rows:
            if row.probability_positive is None:
                continue
            bucket_index = int(row.probability_positive / bucket_width)
            bucket_index = max(0, min(bucket_index, request.bucket_count - 1))
            bucket_rows[bucket_index].append(row)

        buckets: list[MLPredictionQualityProbabilityBucket] = []
        for index, scoped_rows in enumerate(bucket_rows):
            bucket_start = bucket_width * Decimal(index)
            bucket_end = (
                Decimal("1")
                if index == request.bucket_count - 1
                else bucket_width * Decimal(index + 1)
            )
            evaluable_rows = [row for row in scoped_rows if row.is_evaluable]
            positive_label_count = sum(row.actual_positive == 1 for row in evaluable_rows)
            negative_label_count = sum(row.actual_positive == 0 for row in evaluable_rows)
            buckets.append(
                MLPredictionQualityProbabilityBucket(
                    bucket_index=index,
                    bucket_start=bucket_start,
                    bucket_end=bucket_end,
                    prediction_count=len(scoped_rows),
                    evaluable_prediction_count=len(evaluable_rows),
                    positive_label_count=positive_label_count,
                    negative_label_count=negative_label_count,
                    positive_label_ratio=self._ratio(
                        positive_label_count,
                        len(evaluable_rows),
                    ),
                    average_realized_return=self._average(
                        [
                            row.realized_return
                            for row in evaluable_rows
                            if row.realized_return is not None
                        ]
                    ),
                )
            )
        return buckets

    @staticmethod
    def _missing_label_examples(
        rows: list[PredictionQualityRow],
        *,
        horizon_days: int,
        return_method: str,
        limit: int,
    ) -> list[MLPredictionQualityMissingLabelExample]:
        missing_rows = sorted(
            [row for row in rows if row.is_missing_label],
            key=lambda row: (
                row.prediction.as_of_date,
                row.prediction.model_run_id,
                row.prediction.bond_id,
            ),
        )
        return [
            MLPredictionQualityMissingLabelExample(
                model_run_id=row.prediction.model_run_id,
                bond_id=row.prediction.bond_id,
                as_of_date=row.prediction.as_of_date,
                horizon_days=horizon_days,
                return_method=return_method,
                probability_positive=row.probability_positive,
                reason="No matching realized label",
            )
            for row in missing_rows[:limit]
        ]

    def _row_issues(
        self,
        overview: MLPredictionQualityOverview,
        metrics: MLPredictionQualityMetricSet,
        request: MLPredictionQualityReportRequest,
    ) -> list[str]:
        issues: list[str] = []
        if overview.prediction_count == 0:
            issues.append("no_predictions")
        if overview.evaluable_prediction_count == 0:
            issues.append("no_evaluable_predictions")
        if (
            overview.missing_label_ratio is not None
            and overview.missing_label_ratio > request.maximum_missing_label_ratio
        ):
            issues.append("high_missing_label_ratio")
        if overview.positive_label_count < request.minimum_positive_labels:
            issues.append("low_positive_labels")
        if overview.negative_label_count < request.minimum_negative_labels:
            issues.append("low_negative_labels")
        if (
            overview.prediction_count > 0
            and overview.predicted_positive_count == 0
        ):
            issues.append("zero_predicted_positive")
        if (
            overview.prediction_count > 0
            and overview.predicted_negative_count == 0
        ):
            issues.append("zero_predicted_negative")
        if (
            metrics.probability_separation is not None
            and metrics.probability_separation <= 0
        ):
            issues.append("weak_probability_separation")
        return issues

    @staticmethod
    def _status_from_issues(issues: list[str]) -> str:
        if any(issue in NOT_READY_ISSUES for issue in issues):
            return "not_ready"
        if issues:
            return "warning"
        return "ready"

    @staticmethod
    def _predicted_positive(
        row: PredictionQualityRow,
        request: MLPredictionQualityReportRequest,
    ) -> bool | None:
        if row.probability_positive is None:
            return None
        return row.probability_positive >= request.positive_probability_cutoff

    @staticmethod
    def _return_method(model_run: MLModelRun) -> str:
        return_method = (model_run.params or {}).get("return_method") or "price"
        return return_method if return_method in RETURN_METHODS else "price"

    @staticmethod
    def _validate_request(request: MLPredictionQualityReportRequest) -> list[int]:
        if request.model_run_id is None and request.model_run_ids is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide model_run_id or model_run_ids",
            )
        if request.model_run_id is not None and request.model_run_ids is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use only one of model_run_id or model_run_ids",
            )
        if request.model_run_ids is not None:
            if not request.model_run_ids:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="model_run_ids must not be empty",
                )
            if len(set(request.model_run_ids)) != len(request.model_run_ids):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="model_run_ids must not contain duplicates",
                )
            if len(request.model_run_ids) > 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="model_run_ids must not exceed 200",
                )
        if (
            request.date_from is not None
            and request.date_to is not None
            and request.date_from > request.date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if (
            request.return_method is not None
            and request.return_method not in RETURN_METHODS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )
        if request.horizon_days is not None and request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        if (
            request.positive_probability_cutoff < 0
            or request.positive_probability_cutoff > 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="positive_probability_cutoff must be between 0 and 1",
            )
        if request.bucket_count < 2 or request.bucket_count > 50:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bucket_count must be between 2 and 50",
            )
        if request.minimum_evaluable_predictions <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_evaluable_predictions must be positive",
            )
        if request.minimum_positive_labels < 0 or request.minimum_negative_labels < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="class minimums must be non-negative",
            )
        if (
            request.maximum_missing_label_ratio < 0
            or request.maximum_missing_label_ratio > 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="maximum_missing_label_ratio must be between 0 and 1",
            )
        if request.limit < 1 or request.limit > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 500",
            )
        if request.offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="offset must be non-negative",
            )
        return (
            [request.model_run_id]
            if request.model_run_id is not None
            else list(request.model_run_ids or [])
        )

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> Decimal | None:
        if denominator == 0:
            return None
        return Decimal(numerator) / Decimal(denominator)

    @staticmethod
    def _average(values: list[Decimal | None]) -> Decimal | None:
        clean = [value for value in values if value is not None]
        if not clean:
            return None
        return sum(clean, Decimal("0")) / Decimal(len(clean))

    @staticmethod
    def _median(values: list[Decimal | None]) -> Decimal | None:
        clean = sorted(value for value in values if value is not None)
        if not clean:
            return None
        midpoint = len(clean) // 2
        if len(clean) % 2 == 1:
            return clean[midpoint]
        return (clean[midpoint - 1] + clean[midpoint]) / Decimal("2")
