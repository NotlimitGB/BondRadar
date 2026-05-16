from __future__ import annotations

from itertools import product
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
    StrategyExperimentGridRequest,
    StrategyExperimentLeaderboardItem,
    StrategyExperimentSensitivityItem,
    StrategyExperimentVariantRequest,
    StrategyExperimentVariantResult,
    StrategyExperimentWarning,
)
from app.services.ml_feature_builder import RETURN_METHODS
from app.services.strategy_backtest_service import StrategyBacktestService


GRID_FIELDS = [
    "top_n_values",
    "min_probability_positive_values",
    "rebalance_frequency_values",
    "rebalance_gap_days_values",
    "use_portfolio_constraints_values",
    "max_position_weight_values",
    "max_issuer_weight_values",
    "max_high_risk_weight_values",
    "min_liquidity_score_values",
    "exclude_blocked_by_risk_values",
    "exclude_insufficient_credit_data_values",
    "allowed_risk_levels_values",
    "allowed_decision_statuses_values",
]

SENSITIVITY_PARAMETERS = [
    "top_n",
    "min_probability_positive",
    "rebalance_frequency",
    "max_position_weight",
    "max_issuer_weight",
    "max_high_risk_weight",
    "min_liquidity_score",
    "exclude_insufficient_credit_data",
    "use_portfolio_constraints",
]

EXPERIMENT_PRESETS = {
    "conservative": StrategyExperimentGridRequest(
        top_n_values=[5, 10],
        min_probability_positive_values=[Decimal("0.60"), Decimal("0.65")],
        rebalance_frequency_values=["label_dates", "monthly"],
        max_position_weight_values=[Decimal("0.10"), Decimal("0.15")],
        max_issuer_weight_values=[Decimal("0.20"), Decimal("0.30")],
        max_high_risk_weight_values=[Decimal("0"), Decimal("0.10")],
        exclude_blocked_by_risk_values=[True],
        exclude_insufficient_credit_data_values=[True],
    ),
    "balanced": StrategyExperimentGridRequest(
        top_n_values=[5, 10],
        min_probability_positive_values=[Decimal("0.55"), Decimal("0.60")],
        rebalance_frequency_values=["label_dates", "monthly"],
        max_position_weight_values=[Decimal("0.15"), Decimal("0.20")],
        max_issuer_weight_values=[Decimal("0.30")],
        max_high_risk_weight_values=[Decimal("0.10"), Decimal("0.20")],
        exclude_blocked_by_risk_values=[True],
        exclude_insufficient_credit_data_values=[False, True],
    ),
    "aggressive": StrategyExperimentGridRequest(
        top_n_values=[5, 10, 15],
        min_probability_positive_values=[
            Decimal("0.50"),
            Decimal("0.55"),
            Decimal("0.60"),
        ],
        rebalance_frequency_values=["label_dates", "weekly", "monthly"],
        max_position_weight_values=[Decimal("0.20"), Decimal("0.25")],
        max_issuer_weight_values=[Decimal("0.30"), Decimal("0.40")],
        max_high_risk_weight_values=[Decimal("0.20"), Decimal("0.40")],
        exclude_blocked_by_risk_values=[True],
        exclude_insufficient_credit_data_values=[False],
    ),
    "liquidity_focused": StrategyExperimentGridRequest(
        top_n_values=[5, 10],
        min_probability_positive_values=[Decimal("0.55"), Decimal("0.60")],
        rebalance_frequency_values=["label_dates", "monthly"],
        max_position_weight_values=[
            Decimal("0.10"),
            Decimal("0.15"),
            Decimal("0.20"),
        ],
        max_issuer_weight_values=[Decimal("0.30")],
        max_high_risk_weight_values=[Decimal("0.10"), Decimal("0.20")],
        min_liquidity_score_values=[60, 80],
        exclude_blocked_by_risk_values=[True],
        exclude_insufficient_credit_data_values=[True],
    ),
    "low_drawdown": StrategyExperimentGridRequest(
        top_n_values=[10, 15],
        min_probability_positive_values=[Decimal("0.60"), Decimal("0.65")],
        rebalance_frequency_values=["monthly"],
        max_position_weight_values=[Decimal("0.10")],
        max_issuer_weight_values=[Decimal("0.20"), Decimal("0.30")],
        max_high_risk_weight_values=[Decimal("0"), Decimal("0.10")],
        exclude_blocked_by_risk_values=[True],
        exclude_insufficient_credit_data_values=[True],
    ),
}


class StrategyExperimentService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def compare(
        self,
        request: StrategyExperimentCompareRequest,
    ) -> StrategyExperimentCompareResponse:
        self._validate_request(request)
        model_runs = self._load_completed_model_runs(request)
        model_run_ids = [model_run.id for model_run in model_runs]
        return_method = self._return_method(model_runs[0])
        horizon_days = model_runs[0].horizon_days
        variants, generation_mode = self._resolve_variants(request)
        if len(variants) > request.max_variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="variants must not exceed max_variants",
            )
        results = [
            self._run_variant(
                request=request,
                variant=variant,
                variant_index=index,
            )
            for index, variant in enumerate(variants, start=1)
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
        generated_variants = (
            self._jsonable([variant.model_dump() for variant in variants])
            if generation_mode != "manual" and request.include_generated_variants
            else []
        )
        return StrategyExperimentCompareResponse(
            model_run_id=model_run_ids[0] if len(model_run_ids) == 1 else None,
            model_run_ids=model_run_ids,
            model_run_count=len(model_run_ids),
            prediction_source_mode=(
                "single_model_run"
                if len(model_run_ids) == 1
                else "multiple_model_runs"
            ),
            return_method=return_method,
            horizon_days=horizon_days,
            date_from=request.date_from,
            date_to=request.date_to,
            initial_capital=request.initial_capital,
            transaction_cost_rate=request.transaction_cost_rate,
            ranking_metric=request.ranking_metric,
            ranking_direction=request.ranking_direction,
            variant_count=len(results),
            successful_variant_count=sum(result.status == "completed" for result in results),
            failed_variant_count=sum(result.status == "failed" for result in results),
            generation_mode=generation_mode,
            preset=request.preset,
            generated_variant_count=(len(variants) if generation_mode != "manual" else 0),
            generated_variants=generated_variants,
            sensitivity=self._sensitivity(
                variants=variants,
                results=results,
                ranking_metric=request.ranking_metric,
                ranking_direction=request.ranking_direction,
            ),
            best_variant=self._best_variant(leaderboard),
            leaderboard=leaderboard,
            results=results,
            warnings=warnings,
        )

    def _resolve_variants(
        self,
        request: StrategyExperimentCompareRequest,
    ) -> tuple[list[StrategyExperimentVariantRequest], str]:
        if request.variants:
            return request.variants, "manual"
        if request.grid is not None:
            return self._variants_from_grid(request.grid), "grid"

        preset_grid = EXPERIMENT_PRESETS.get(str(request.preset))
        if preset_grid is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid experiment preset",
            )
        if request.preset_overrides is not None:
            preset_grid = self._apply_preset_overrides(
                preset_grid,
                request.preset_overrides,
            )
        return self._variants_from_grid(preset_grid), "preset"

    @staticmethod
    def _apply_preset_overrides(
        preset_grid: StrategyExperimentGridRequest,
        overrides: StrategyExperimentGridRequest,
    ) -> StrategyExperimentGridRequest:
        payload = preset_grid.model_dump()
        for field_name in overrides.model_fields_set:
            payload[field_name] = getattr(overrides, field_name)
        return StrategyExperimentGridRequest(**payload)

    def _variants_from_grid(
        self,
        grid: StrategyExperimentGridRequest,
    ) -> list[StrategyExperimentVariantRequest]:
        self._validate_grid(grid)
        variants = []
        for index, values in enumerate(
            product(*(getattr(grid, field_name) for field_name in GRID_FIELDS)),
            start=1,
        ):
            variant = StrategyExperimentVariantRequest(
                name=self._generated_variant_name(index, values),
                top_n=values[0],
                min_probability_positive=values[1],
                rebalance_frequency=values[2],
                rebalance_gap_days=values[3],
                use_portfolio_constraints=values[4],
                max_position_weight=values[5],
                max_issuer_weight=values[6],
                max_high_risk_weight=values[7],
                min_liquidity_score=values[8],
                exclude_blocked_by_risk=values[9],
                exclude_insufficient_credit_data=values[10],
                allowed_risk_levels=values[11],
                allowed_decision_statuses=values[12],
            )
            variants.append(variant)
        return variants

    @staticmethod
    def _generated_variant_name(index: int, values: tuple[Any, ...]) -> str:
        probability = StrategyExperimentService._compact_decimal(values[1])
        position = StrategyExperimentService._compact_decimal(values[5])
        issuer = StrategyExperimentService._compact_decimal(values[6])
        high_risk = StrategyExperimentService._compact_decimal(values[7])
        return (
            f"grid_{index:03d}_top{values[0]}_prob{probability}_"
            f"freq_{values[2]}_pos{position}_issuer{issuer}_hr{high_risk}"
        )

    @staticmethod
    def _compact_decimal(value: Any) -> str:
        decimal = Decimal(str(value))
        digits = f"{decimal:.4f}".rstrip("0").rstrip(".")
        return digits.replace(".", "")

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
            model_run_ids=request.model_run_ids,
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
    def _best_variant(
        leaderboard: list[StrategyExperimentLeaderboardItem],
    ) -> StrategyExperimentLeaderboardItem | None:
        for item in leaderboard:
            if item.status == "completed" and item.ranking_value is not None:
                return item
        return None

    def _sensitivity(
        self,
        *,
        variants: list[StrategyExperimentVariantRequest],
        results: list[StrategyExperimentVariantResult],
        ranking_metric: str,
        ranking_direction: str,
    ) -> list[StrategyExperimentSensitivityItem]:
        items: list[StrategyExperimentSensitivityItem] = []
        result_by_index = {result.variant_index: result for result in results}
        for parameter in SENSITIVITY_PARAMETERS:
            values = {
                self._parameter_value(getattr(variant, parameter))
                for variant in variants
            }
            if len(values) <= 1:
                continue
            for value in sorted(values):
                grouped = []
                for index, variant in enumerate(variants, start=1):
                    if self._parameter_value(getattr(variant, parameter)) != value:
                        continue
                    result = result_by_index.get(index)
                    if result is None or result.status != "completed":
                        continue
                    ranking_value = self._ranking_value(result, ranking_metric)
                    if ranking_value is not None:
                        grouped.append((result, ranking_value))
                if not grouped:
                    items.append(
                        StrategyExperimentSensitivityItem(
                            parameter=parameter,
                            value=value,
                            completed_count=0,
                            average_ranking_value=None,
                            best_ranking_value=None,
                            best_variant_name=None,
                        )
                    )
                    continue
                best_result, best_value = self._best_group_value(
                    grouped,
                    ranking_direction=ranking_direction,
                )
                items.append(
                    StrategyExperimentSensitivityItem(
                        parameter=parameter,
                        value=value,
                        completed_count=len(grouped),
                        average_ranking_value=(
                            sum(value for _, value in grouped) / Decimal(len(grouped))
                        ),
                        best_ranking_value=best_value,
                        best_variant_name=best_result.variant_name,
                    )
                )
        return items

    @staticmethod
    def _parameter_value(value: Any) -> str:
        if isinstance(value, Decimal):
            return str(value.normalize())
        if value is None:
            return "none"
        return str(value).lower()

    @staticmethod
    def _best_group_value(
        grouped: list[tuple[StrategyExperimentVariantResult, Decimal]],
        *,
        ranking_direction: str,
    ) -> tuple[StrategyExperimentVariantResult, Decimal]:
        if ranking_direction == "asc":
            return min(grouped, key=lambda item: (item[1], item[0].variant_index))
        return max(grouped, key=lambda item: (item[1], -item[0].variant_index))

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
        mode_count = sum(
            [
                bool(request.variants),
                request.grid is not None,
                request.preset is not None,
            ]
        )
        if mode_count == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Provide variants, grid, or preset",
            )
        if mode_count > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use only one of variants, grid, or preset",
            )
        if request.preset_overrides is not None and request.preset is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="preset_overrides requires preset",
            )
        if request.preset is not None and request.preset not in EXPERIMENT_PRESETS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid experiment preset",
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

    @staticmethod
    def _validate_grid(grid: StrategyExperimentGridRequest) -> None:
        if any(not getattr(grid, field_name) for field_name in GRID_FIELDS):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Grid value lists must not be empty",
            )

    def _load_completed_model_runs(
        self,
        request: StrategyExperimentCompareRequest,
    ) -> list[MLModelRun]:
        model_run_ids = (
            [request.model_run_id]
            if request.model_run_id is not None
            else list(request.model_run_ids or [])
        )
        model_runs = [self._load_model_run(model_run_id) for model_run_id in model_run_ids]
        self._validate_model_run_compatibility(model_runs)
        return model_runs

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

    def _validate_model_run_compatibility(
        self,
        model_runs: list[MLModelRun],
    ) -> None:
        if not model_runs:
            return
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
