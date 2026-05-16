from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.ml_model_run import MLModelRun
from app.schemas.strategy_backtest import StrategyBacktestRequest
from app.schemas.strategy_experiment import (
    EXPERIMENT_RANKING_DIRECTIONS,
    EXPERIMENT_RANKING_METRICS,
    StrategyExperimentCompareRequest,
    StrategyExperimentCompareResponse,
    StrategyExperimentLeaderboardItem,
    StrategyExperimentVariantRequest,
    StrategyExperimentVariantResult,
    StrategyExperimentWarning,
)
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.strategy_backtest_service import StrategyBacktestService


class StrategyExperimentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compare(
        self,
        request: StrategyExperimentCompareRequest,
    ) -> StrategyExperimentCompareResponse:
        self._validate_request(request)
        model_run = self._load_model_run(request.model_run_id)
        results = [
            self._run_variant(
                request=request,
                variant=variant,
                variant_index=index,
            )
            for index, variant in enumerate(request.variants, start=1)
        ]
        leaderboard = self._leaderboard(
            results,
            ranking_metric=request.ranking_metric,
            ranking_direction=request.ranking_direction,
        )
        warnings = [
            warning
            for result in results
            for warning in result.warnings
            if result.status == "failed"
        ]
        return StrategyExperimentCompareResponse(
            model_run_id=model_run.id,
            return_method=self._return_method(model_run),
            horizon_days=model_run.horizon_days,
            date_from=request.date_from,
            date_to=request.date_to,
            initial_capital=request.initial_capital,
            transaction_cost_rate=request.transaction_cost_rate,
            ranking_metric=request.ranking_metric,
            ranking_direction=request.ranking_direction,
            variant_count=len(results),
            successful_variant_count=sum(result.status == "completed" for result in results),
            failed_variant_count=sum(result.status == "failed" for result in results),
            leaderboard=leaderboard,
            results=results,
            warnings=warnings,
        )

    def _run_variant(
        self,
        *,
        request: StrategyExperimentCompareRequest,
        variant: StrategyExperimentVariantRequest,
        variant_index: int,
    ) -> StrategyExperimentVariantResult:
        variant_name = variant.name or f"variant_{variant_index}"
        backtest_request = self._backtest_request(request, variant)
        try:
            backtest = StrategyBacktestService(self.db).run(backtest_request)
        except HTTPException as exc:
            warning = StrategyExperimentWarning(
                message="Variant failed during backtest execution",
                variant_index=variant_index,
                variant_name=variant_name,
                details={"detail": exc.detail},
            )
            return StrategyExperimentVariantResult(
                variant_index=variant_index,
                variant_name=variant_name,
                status="failed",
                request=self._jsonable(backtest_request.model_dump()),
                metrics=None,
                final_portfolio_value=None,
                period_count=None,
                baseline_summaries=[],
                periods=[],
                warnings=[warning],
                error=str(exc.detail),
            )
        except Exception as exc:
            warning = StrategyExperimentWarning(
                message="Variant failed during backtest execution",
                variant_index=variant_index,
                variant_name=variant_name,
                details={"detail": str(exc)},
            )
            return StrategyExperimentVariantResult(
                variant_index=variant_index,
                variant_name=variant_name,
                status="failed",
                request=self._jsonable(backtest_request.model_dump()),
                metrics=None,
                final_portfolio_value=None,
                period_count=None,
                baseline_summaries=[],
                periods=[],
                warnings=[warning],
                error="Variant failed during backtest execution",
            )

        warnings = [
            StrategyExperimentWarning(
                message=warning.message,
                variant_index=variant_index,
                variant_name=variant_name,
                details=warning.details,
            )
            for warning in backtest.warnings
        ]
        return StrategyExperimentVariantResult(
            variant_index=variant_index,
            variant_name=variant_name,
            status="completed",
            request=self._jsonable(backtest_request.model_dump()),
            metrics=self._jsonable(backtest.metrics.model_dump()),
            final_portfolio_value=backtest.final_portfolio_value,
            period_count=backtest.metrics.period_count,
            baseline_summaries=(
                self._baseline_summaries(backtest.baselines)
                if request.include_baselines
                else []
            ),
            periods=(
                self._jsonable([period.model_dump() for period in backtest.periods])
                if request.include_periods
                else []
            ),
            warnings=warnings,
            error=None,
        )

    @staticmethod
    def _backtest_request(
        request: StrategyExperimentCompareRequest,
        variant: StrategyExperimentVariantRequest,
    ) -> StrategyBacktestRequest:
        return StrategyBacktestRequest(
            model_run_id=request.model_run_id,
            date_from=request.date_from,
            date_to=request.date_to,
            initial_capital=request.initial_capital,
            top_n=variant.top_n,
            min_probability_positive=variant.min_probability_positive,
            rebalance_frequency=variant.rebalance_frequency,
            rebalance_gap_days=variant.rebalance_gap_days,
            max_position_weight=variant.max_position_weight,
            transaction_cost_rate=request.transaction_cost_rate,
            min_liquidity_score=variant.min_liquidity_score,
            exclude_blocked_by_risk=variant.exclude_blocked_by_risk,
            exclude_insufficient_credit_data=variant.exclude_insufficient_credit_data,
            use_portfolio_constraints=variant.use_portfolio_constraints,
            max_issuer_weight=variant.max_issuer_weight,
            max_high_risk_weight=variant.max_high_risk_weight,
            allowed_risk_levels=variant.allowed_risk_levels,
            allowed_decision_statuses=variant.allowed_decision_statuses,
            include_excluded_candidates=False,
            include_baselines=request.include_baselines,
        )

    def _leaderboard(
        self,
        results: list[StrategyExperimentVariantResult],
        *,
        ranking_metric: str,
        ranking_direction: str,
    ) -> list[StrategyExperimentLeaderboardItem]:
        indexed = [(result, self._ranking_value(result, ranking_metric)) for result in results]
        reverse = ranking_direction == "desc"

        def sort_key(item: tuple[StrategyExperimentVariantResult, Decimal | None]) -> tuple:
            result, value = item
            failed = result.status != "completed"
            null_value = value is None
            comparable = value if value is not None else Decimal("0")
            metric_value = -comparable if reverse else comparable
            return (failed, null_value, metric_value, result.variant_index)

        ranked = sorted(indexed, key=sort_key)
        return [
            self._leaderboard_item(
                result=result,
                ranking_value=value,
                rank=rank,
            )
            for rank, (result, value) in enumerate(ranked, start=1)
        ]

    @staticmethod
    def _leaderboard_item(
        *,
        result: StrategyExperimentVariantResult,
        ranking_value: Decimal | None,
        rank: int,
    ) -> StrategyExperimentLeaderboardItem:
        metrics = result.metrics or {}
        return StrategyExperimentLeaderboardItem(
            rank=rank,
            variant_name=result.variant_name,
            variant_index=result.variant_index,
            status=result.status,
            ranking_value=ranking_value,
            final_portfolio_value=result.final_portfolio_value,
            total_return=StrategyExperimentService._decimal_metric(metrics, "total_return"),
            annualized_return=StrategyExperimentService._decimal_metric(metrics, "annualized_return"),
            max_drawdown=StrategyExperimentService._decimal_metric(metrics, "max_drawdown"),
            volatility=StrategyExperimentService._decimal_metric(metrics, "volatility"),
            hit_rate=StrategyExperimentService._decimal_metric(metrics, "hit_rate"),
            average_unallocated_weight=StrategyExperimentService._decimal_metric(
                metrics, "average_unallocated_weight"
            ),
            negative_periods_count=metrics.get("negative_periods_count"),
            selected_period_count=metrics.get("selected_period_count"),
        )

    @staticmethod
    def _ranking_value(
        result: StrategyExperimentVariantResult,
        ranking_metric: str,
    ) -> Decimal | None:
        if result.status != "completed":
            return None
        if ranking_metric == "final_portfolio_value":
            return result.final_portfolio_value
        metrics = result.metrics or {}
        return StrategyExperimentService._decimal_metric(metrics, ranking_metric)

    @staticmethod
    def _decimal_metric(metrics: dict[str, Any], key: str) -> Decimal | None:
        value = metrics.get(key)
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _baseline_summaries(baselines: list[Any]) -> list[dict[str, Any]]:
        summaries = []
        for baseline in baselines:
            summaries.append(
                {
                    "name": baseline.name,
                    "final_portfolio_value": baseline.final_portfolio_value,
                    "total_return": baseline.metrics.total_return,
                    "annualized_return": baseline.metrics.annualized_return,
                    "max_drawdown": baseline.metrics.max_drawdown,
                    "volatility": baseline.metrics.volatility,
                    "hit_rate": baseline.metrics.hit_rate,
                    "negative_periods_count": baseline.metrics.negative_periods_count,
                }
            )
        return StrategyExperimentService._jsonable(summaries)

    def _validate_request(self, request: StrategyExperimentCompareRequest) -> None:
        if request.model_run_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="model_run_id is required",
            )
        if not request.variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="variants must not be empty",
            )
        if request.max_variants < 1 or request.max_variants > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_variants must be between 1 and 100",
            )
        if len(request.variants) > request.max_variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="variants must not exceed max_variants",
            )
        if request.ranking_metric not in EXPERIMENT_RANKING_METRICS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking metric",
            )
        if request.ranking_direction not in EXPERIMENT_RANKING_DIRECTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking direction",
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
        if request.initial_capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="initial_capital must be positive",
            )
        if request.transaction_cost_rate < 0 or request.transaction_cost_rate > Decimal("0.1"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="transaction_cost_rate must be between 0 and 0.1",
            )

    def _load_model_run(self, model_run_id: int) -> MLModelRun:
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
        return model_run

    @staticmethod
    def _return_method(model_run: MLModelRun) -> str:
        return_method = (model_run.params or {}).get("return_method") or "price"
        return return_method if return_method in RETURN_METHODS else "price"

    @classmethod
    def _jsonable(cls, value: Any) -> Any:
        if isinstance(value, Decimal):
            return str(value)
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, list):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [cls._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): cls._jsonable(item) for key, item in value.items()}
        return value
