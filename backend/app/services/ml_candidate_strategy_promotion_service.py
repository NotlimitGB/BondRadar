from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonCandidateResult,
    MLCandidateComparisonResponse,
)
from app.schemas.ml_candidate_strategy_promotion import (
    MLCandidateStrategyPromotionRequest,
    MLCandidateStrategyPromotionResponse,
    MLCandidateStrategyPromotionSelectedCandidate,
    MLCandidateStrategyPromotionWarning,
)
from app.schemas.strategy_experiment import (
    StrategyExperimentCompareRequest,
    StrategyExperimentCompareResponse,
)
from app.services.ml_candidate_comparison_service import (
    RANKING_DIRECTIONS,
    RANKING_METRICS,
    MLCandidateComparisonService,
)
from app.services.strategy_experiment_service import StrategyExperimentService


@dataclass(frozen=True)
class SelectedCandidateContext:
    candidate: MLCandidateComparisonCandidateResult
    rank: int | None
    ranking_metric: str
    ranking_direction: str
    ranking_value: Decimal | int | bool | None


class MLCandidateStrategyPromotionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def promote(
        self,
        request: MLCandidateStrategyPromotionRequest,
    ) -> MLCandidateStrategyPromotionResponse:
        self._validate_request(request)
        comparison = MLCandidateComparisonService(self.db).compare(
            request.candidate_comparison
        )
        warnings: list[MLCandidateStrategyPromotionWarning] = []

        selected = self._select_candidate(
            comparison,
            request=request,
        )
        if selected is None:
            warnings.append(
                MLCandidateStrategyPromotionWarning(
                    message=(
                        "No ready ML candidate was available for "
                        "strategy experiment promotion"
                    )
                )
            )
            return self._response(
                selected_candidate=None,
                comparison=comparison,
                strategy_experiment=None,
                include_candidate_comparison=request.include_candidate_comparison,
                warnings=warnings,
            )

        selected_candidate = self._selected_schema(selected)
        if not selected_candidate.ready_for_strategy_research:
            warnings.append(
                MLCandidateStrategyPromotionWarning(
                    message=(
                        "Selected ML candidate is not marked ready for "
                        "strategy research"
                    )
                )
            )

        strategy_request = self._strategy_request(
            request,
            selected_candidate=selected_candidate,
        )
        strategy_result: StrategyExperimentCompareResponse | None = None
        try:
            strategy_result = StrategyExperimentService(self.db).compare(
                strategy_request
            )
        except HTTPException as exc:
            warnings.append(
                MLCandidateStrategyPromotionWarning(
                    message="Strategy experiment failed for selected ML candidate",
                    details={"error": str(exc.detail)},
                )
            )
        except Exception as exc:
            warnings.append(
                MLCandidateStrategyPromotionWarning(
                    message="Strategy experiment failed for selected ML candidate",
                    details={"error": str(exc) or "Strategy experiment failed"},
                )
            )

        return self._response(
            selected_candidate=selected_candidate,
            comparison=comparison,
            strategy_experiment=strategy_result,
            include_candidate_comparison=request.include_candidate_comparison,
            warnings=warnings,
        )

    def _select_candidate(
        self,
        comparison: MLCandidateComparisonResponse,
        *,
        request: MLCandidateStrategyPromotionRequest,
    ) -> SelectedCandidateContext | None:
        metric = request.promote_ranking_metric or comparison.ranking_metric
        direction = request.promote_ranking_direction or comparison.ranking_direction
        ranked = self._rank_candidates(
            comparison.candidates,
            metric=metric,
            direction=direction,
        )
        for rank, candidate, ranking_value in ranked:
            if candidate.status != "completed" or ranking_value is None:
                continue
            ready = candidate.ready_for_strategy_research is True
            if request.require_ready_candidate and not ready:
                continue
            if candidate.prediction_source_mode is None:
                continue
            return SelectedCandidateContext(
                candidate=candidate,
                rank=rank,
                ranking_metric=metric,
                ranking_direction=direction,
                ranking_value=ranking_value,
            )
        return None

    @staticmethod
    def _rank_candidates(
        candidates: list[MLCandidateComparisonCandidateResult],
        *,
        metric: str,
        direction: str,
    ) -> list[tuple[int, MLCandidateComparisonCandidateResult, Decimal | int | bool | None]]:
        sortable: list[
            tuple[int, MLCandidateComparisonCandidateResult, Decimal | int | bool | None]
        ] = []
        for index, candidate in enumerate(candidates):
            ranking_value = (
                MLCandidateStrategyPromotionService._candidate_ranking_value(
                    candidate,
                    metric,
                )
                if candidate.status == "completed"
                else None
            )
            sortable.append((index, candidate, ranking_value))
        sorted_items = sorted(
            sortable,
            key=lambda item: MLCandidateStrategyPromotionService._sort_key(
                item[0],
                item[1],
                item[2],
                direction=direction,
            ),
        )
        return [
            (rank, candidate, ranking_value)
            for rank, (_, candidate, ranking_value) in enumerate(sorted_items, start=1)
        ]

    @staticmethod
    def _sort_key(
        original_index: int,
        candidate: MLCandidateComparisonCandidateResult,
        ranking_value: Decimal | int | bool | None,
        *,
        direction: str,
    ) -> tuple[int, Decimal, int]:
        if candidate.status != "completed":
            return (2, Decimal("0"), original_index)
        if ranking_value is None:
            return (1, Decimal("0"), original_index)
        value = MLCandidateStrategyPromotionService._sortable_value(ranking_value)
        if direction == "desc":
            value = -value
        return (0, value, original_index)

    @staticmethod
    def _candidate_ranking_value(
        candidate: MLCandidateComparisonCandidateResult,
        metric: str,
    ) -> Decimal | int | bool | None:
        if metric == "ready_for_strategy_research":
            return candidate.ready_for_strategy_research
        return getattr(candidate, metric, None)

    @staticmethod
    def _sortable_value(value: Decimal | int | bool) -> Decimal:
        if isinstance(value, bool):
            return Decimal(1 if value else 0)
        return Decimal(str(value))

    @staticmethod
    def _selected_schema(
        selected: SelectedCandidateContext,
    ) -> MLCandidateStrategyPromotionSelectedCandidate:
        candidate = selected.candidate
        return MLCandidateStrategyPromotionSelectedCandidate(
            name=candidate.name,
            rank=selected.rank,
            ranking_metric=selected.ranking_metric,
            ranking_direction=selected.ranking_direction,
            ranking_value=selected.ranking_value,
            model_run_id=candidate.model_run_id,
            model_run_ids=candidate.model_run_ids,
            model_run_count=candidate.model_run_count,
            prediction_source_mode=str(candidate.prediction_source_mode),
            ready_for_strategy_research=bool(candidate.ready_for_strategy_research),
            issues=candidate.issues,
        )

    @staticmethod
    def _strategy_request(
        request: MLCandidateStrategyPromotionRequest,
        *,
        selected_candidate: MLCandidateStrategyPromotionSelectedCandidate,
    ) -> StrategyExperimentCompareRequest:
        payload = request.strategy_experiment.model_dump()
        if selected_candidate.model_run_id is not None:
            payload["model_run_id"] = selected_candidate.model_run_id
            payload["model_run_ids"] = None
        else:
            payload["model_run_id"] = None
            payload["model_run_ids"] = list(selected_candidate.model_run_ids)
        payload["include_periods"] = request.include_strategy_periods
        payload["include_baselines"] = request.include_strategy_baselines
        return StrategyExperimentCompareRequest(**payload)

    @staticmethod
    def _response(
        *,
        selected_candidate: MLCandidateStrategyPromotionSelectedCandidate | None,
        comparison: MLCandidateComparisonResponse,
        strategy_experiment: StrategyExperimentCompareResponse | None,
        include_candidate_comparison: bool,
        warnings: list[MLCandidateStrategyPromotionWarning],
    ) -> MLCandidateStrategyPromotionResponse:
        return MLCandidateStrategyPromotionResponse(
            selected_candidate=selected_candidate,
            candidate_comparison=(
                comparison.model_dump(mode="json")
                if include_candidate_comparison
                else None
            ),
            strategy_experiment=(
                strategy_experiment.model_dump(mode="json")
                if strategy_experiment is not None
                else None
            ),
            warnings=warnings,
        )

    @staticmethod
    def _validate_request(request: MLCandidateStrategyPromotionRequest) -> None:
        if (
            request.promote_ranking_metric is not None
            and request.promote_ranking_metric not in RANKING_METRICS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid promote ranking metric",
            )
        if (
            request.promote_ranking_direction is not None
            and request.promote_ranking_direction not in RANKING_DIRECTIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid promote ranking direction",
            )
