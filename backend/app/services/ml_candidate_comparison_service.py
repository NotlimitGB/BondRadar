from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonCandidateRequest,
    MLCandidateComparisonCandidateResult,
    MLCandidateComparisonLeaderboardItem,
    MLCandidateComparisonRequest,
    MLCandidateComparisonResponse,
    MLCandidateComparisonSelectedCandidate,
    MLCandidateComparisonWarning,
)
from app.schemas.ml_prediction_quality import (
    MLPredictionQualityReportRequest,
    MLPredictionQualityReportResponse,
)
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.ml_prediction_quality_service import MLPredictionQualityService


RANKING_METRICS = {
    "ready_for_strategy_research",
    "accuracy",
    "precision",
    "recall",
    "f1_score",
    "probability_separation",
    "average_realized_return_for_predicted_positive",
    "average_realized_return",
    "missing_label_ratio",
    "evaluable_prediction_count",
    "prediction_count",
}
RANKING_DIRECTIONS = {"asc", "desc"}


class MLCandidateComparisonService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compare(
        self,
        request: MLCandidateComparisonRequest,
    ) -> MLCandidateComparisonResponse:
        self._validate_request(request)

        quality_service = MLPredictionQualityService(self.db)
        candidate_results = [
            self._candidate_result(
                request=request,
                candidate=candidate,
                index=index,
                quality_service=quality_service,
            )
            for index, candidate in enumerate(request.candidates, start=1)
        ]
        completed_count = sum(
            candidate.status == "completed" for candidate in candidate_results
        )
        failed_count = sum(candidate.status == "failed" for candidate in candidate_results)
        visible_candidates = (
            candidate_results
            if request.include_failed_candidates
            else [
                candidate
                for candidate in candidate_results
                if candidate.status != "failed"
            ]
        )
        leaderboard = self._leaderboard(
            visible_candidates,
            metric=request.ranking_metric,
            direction=request.ranking_direction,
        )
        selected_candidate = self._selected_candidate(
            leaderboard,
            metric=request.ranking_metric,
        )
        warnings: list[MLCandidateComparisonWarning] = []
        if selected_candidate is None:
            warnings.append(
                MLCandidateComparisonWarning(
                    message="No ready completed candidate with ranking value was available"
                )
            )

        return MLCandidateComparisonResponse(
            ranking_metric=request.ranking_metric,
            ranking_direction=request.ranking_direction,
            candidate_count=len(request.candidates),
            completed_candidate_count=completed_count,
            failed_candidate_count=failed_count,
            selected_candidate=selected_candidate,
            leaderboard=leaderboard,
            candidates=visible_candidates,
            limit=request.limit,
            offset=request.offset,
            warnings=warnings,
        )

    def _candidate_result(
        self,
        *,
        request: MLCandidateComparisonRequest,
        candidate: MLCandidateComparisonCandidateRequest,
        index: int,
        quality_service: MLPredictionQualityService,
    ) -> MLCandidateComparisonCandidateResult:
        name = self._candidate_name(candidate, index)
        effective_date_from = candidate.date_from or request.date_from
        effective_date_to = candidate.date_to or request.date_to
        effective_return_method = candidate.return_method or request.return_method
        effective_horizon_days = candidate.horizon_days or request.horizon_days
        model_run_ids = self._candidate_model_run_ids(candidate)
        quality_request = MLPredictionQualityReportRequest(
            model_run_id=candidate.model_run_id,
            model_run_ids=candidate.model_run_ids,
            date_from=effective_date_from,
            date_to=effective_date_to,
            return_method=effective_return_method,
            horizon_days=effective_horizon_days,
            positive_probability_cutoff=request.positive_probability_cutoff,
            include_run_rows=request.include_prediction_quality,
            include_date_rows=request.include_prediction_quality,
            include_probability_buckets=request.include_prediction_quality,
            include_missing_label_examples=request.include_prediction_quality,
            bucket_count=request.bucket_count,
            minimum_evaluable_predictions=request.minimum_evaluable_predictions,
            minimum_positive_labels=request.minimum_positive_labels,
            minimum_negative_labels=request.minimum_negative_labels,
            maximum_missing_label_ratio=request.maximum_missing_label_ratio,
            limit=request.limit,
            offset=request.offset,
        )
        try:
            report = quality_service.report(quality_request)
        except HTTPException as exc:
            return self._failed_candidate(
                name=name,
                candidate=candidate,
                model_run_ids=model_run_ids,
                date_from=effective_date_from,
                date_to=effective_date_to,
                horizon_days=effective_horizon_days,
                return_method=effective_return_method,
                error=str(exc.detail),
            )
        except Exception as exc:
            return self._failed_candidate(
                name=name,
                candidate=candidate,
                model_run_ids=model_run_ids,
                date_from=effective_date_from,
                date_to=effective_date_to,
                horizon_days=effective_horizon_days,
                return_method=effective_return_method,
                error=str(exc) or "Candidate evaluation failed",
            )
        return self._completed_candidate(
            name=name,
            report=report,
            metric=request.ranking_metric,
            include_prediction_quality=request.include_prediction_quality,
        )

    def _completed_candidate(
        self,
        *,
        name: str,
        report: MLPredictionQualityReportResponse,
        metric: str,
        include_prediction_quality: bool,
    ) -> MLCandidateComparisonCandidateResult:
        return MLCandidateComparisonCandidateResult(
            name=name,
            status="completed",
            model_run_id=report.model_run_id,
            model_run_ids=report.model_run_ids,
            model_run_count=report.model_run_count,
            prediction_source_mode=report.prediction_source_mode,
            date_from=report.date_from,
            date_to=report.date_to,
            horizon_days=report.horizon_days,
            return_method=report.return_method,
            ranking_value=self._ranking_value(report, metric),
            prediction_count=report.overview.prediction_count,
            evaluable_prediction_count=report.overview.evaluable_prediction_count,
            missing_label_count=report.overview.missing_label_count,
            positive_label_count=report.overview.positive_label_count,
            negative_label_count=report.overview.negative_label_count,
            ready_for_strategy_research=(
                report.overview.ready_for_strategy_research
            ),
            accuracy=report.metrics.accuracy,
            precision=report.metrics.precision,
            recall=report.metrics.recall,
            f1_score=report.metrics.f1_score,
            probability_separation=report.metrics.probability_separation,
            average_realized_return=report.metrics.average_realized_return,
            average_realized_return_for_predicted_positive=(
                report.metrics.average_realized_return_for_predicted_positive
            ),
            missing_label_ratio=report.overview.missing_label_ratio,
            issues=self._issues(report),
            warnings=[warning.message for warning in report.warnings],
            error=None,
            prediction_quality=(
                report.model_dump(mode="json") if include_prediction_quality else None
            ),
        )

    @staticmethod
    def _failed_candidate(
        *,
        name: str,
        candidate: MLCandidateComparisonCandidateRequest,
        model_run_ids: list[int],
        date_from,
        date_to,
        horizon_days: int | None,
        return_method: str | None,
        error: str,
    ) -> MLCandidateComparisonCandidateResult:
        return MLCandidateComparisonCandidateResult(
            name=name,
            status="failed",
            model_run_id=candidate.model_run_id,
            model_run_ids=model_run_ids,
            model_run_count=len(model_run_ids),
            prediction_source_mode=None,
            date_from=date_from,
            date_to=date_to,
            horizon_days=horizon_days,
            return_method=return_method,
            ranking_value=None,
            prediction_count=None,
            evaluable_prediction_count=None,
            missing_label_count=None,
            positive_label_count=None,
            negative_label_count=None,
            ready_for_strategy_research=None,
            accuracy=None,
            precision=None,
            recall=None,
            f1_score=None,
            probability_separation=None,
            average_realized_return=None,
            average_realized_return_for_predicted_positive=None,
            missing_label_ratio=None,
            issues=[],
            warnings=[],
            error=error,
            prediction_quality=None,
        )

    @staticmethod
    def _leaderboard(
        candidates: list[MLCandidateComparisonCandidateResult],
        *,
        metric: str,
        direction: str,
    ) -> list[MLCandidateComparisonLeaderboardItem]:
        sorted_candidates = sorted(
            enumerate(candidates),
            key=lambda item: MLCandidateComparisonService._sort_key(
                item[0],
                item[1],
                direction=direction,
            ),
        )
        return [
            MLCandidateComparisonLeaderboardItem(
                rank=rank,
                name=candidate.name,
                status=candidate.status,
                ranking_value=candidate.ranking_value,
                model_run_id=candidate.model_run_id,
                model_run_ids=candidate.model_run_ids,
                prediction_source_mode=candidate.prediction_source_mode,
                ready_for_strategy_research=candidate.ready_for_strategy_research,
                issues=candidate.issues,
                error=candidate.error,
            )
            for rank, (_, candidate) in enumerate(sorted_candidates, start=1)
        ]

    @staticmethod
    def _sort_key(
        original_index: int,
        candidate: MLCandidateComparisonCandidateResult,
        *,
        direction: str,
    ) -> tuple[int, Decimal, int]:
        if candidate.status == "failed":
            return (2, Decimal("0"), original_index)
        if candidate.ranking_value is None:
            return (1, Decimal("0"), original_index)
        value = MLCandidateComparisonService._sortable_value(candidate.ranking_value)
        if direction == "desc":
            value = -value
        return (0, value, original_index)

    @staticmethod
    def _selected_candidate(
        leaderboard: list[MLCandidateComparisonLeaderboardItem],
        *,
        metric: str,
    ) -> MLCandidateComparisonSelectedCandidate | None:
        for item in leaderboard:
            if (
                item.status == "completed"
                and item.ranking_value is not None
                and item.ready_for_strategy_research is True
                and item.prediction_source_mode is not None
            ):
                return MLCandidateComparisonSelectedCandidate(
                    name=item.name,
                    rank=item.rank,
                    ranking_metric=metric,
                    ranking_value=item.ranking_value,
                    model_run_id=item.model_run_id,
                    model_run_ids=item.model_run_ids,
                    prediction_source_mode=item.prediction_source_mode,
                    ready_for_strategy_research=True,
                    issues=item.issues,
                )
        return None

    @staticmethod
    def _ranking_value(
        report: MLPredictionQualityReportResponse,
        metric: str,
    ) -> Decimal | int | bool | None:
        if metric == "ready_for_strategy_research":
            return report.overview.ready_for_strategy_research
        if metric == "accuracy":
            return report.metrics.accuracy
        if metric == "precision":
            return report.metrics.precision
        if metric == "recall":
            return report.metrics.recall
        if metric == "f1_score":
            return report.metrics.f1_score
        if metric == "probability_separation":
            return report.metrics.probability_separation
        if metric == "average_realized_return_for_predicted_positive":
            return report.metrics.average_realized_return_for_predicted_positive
        if metric == "average_realized_return":
            return report.metrics.average_realized_return
        if metric == "missing_label_ratio":
            return report.overview.missing_label_ratio
        if metric == "evaluable_prediction_count":
            return report.overview.evaluable_prediction_count
        if metric == "prediction_count":
            return report.overview.prediction_count
        return None

    @staticmethod
    def _issues(report: MLPredictionQualityReportResponse) -> list[str]:
        issues: list[str] = [
            key
            for key, value in report.issue_summary.model_dump().items()
            if value == 1
            and key
            not in {
                "missing_model_run_count",
                "non_completed_model_run_count",
                "incompatible_model_run_count",
            }
        ]
        if not report.overview.ready_for_strategy_research:
            issues.append("not_ready_for_strategy_research")
        for run_row in report.run_rows:
            for issue in run_row.issues:
                if issue not in issues:
                    issues.append(issue)
        return issues

    @staticmethod
    def _candidate_name(
        candidate: MLCandidateComparisonCandidateRequest,
        index: int,
    ) -> str:
        name = (candidate.name or "").strip()
        return name or f"candidate_{index}"

    @staticmethod
    def _candidate_model_run_ids(
        candidate: MLCandidateComparisonCandidateRequest,
    ) -> list[int]:
        if candidate.model_run_id is not None:
            ids = [candidate.model_run_id]
            if candidate.model_run_ids is not None:
                ids.extend(candidate.model_run_ids)
            return ids
        return list(candidate.model_run_ids or [])

    @staticmethod
    def _sortable_value(value: Decimal | int | bool) -> Decimal:
        if isinstance(value, bool):
            return Decimal(1 if value else 0)
        return Decimal(str(value))

    @staticmethod
    def _validate_request(request: MLCandidateComparisonRequest) -> None:
        if not request.candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="candidates must not be empty",
            )
        if request.max_candidates < 1 or request.max_candidates > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_candidates must be between 1 and 100",
            )
        if len(request.candidates) > request.max_candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="candidates must not exceed max_candidates",
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
        if request.ranking_metric not in RANKING_METRICS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking metric",
            )
        if request.ranking_direction not in RANKING_DIRECTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking direction",
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
