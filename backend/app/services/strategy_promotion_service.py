from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.paper_trading_scenario import PaperTradingScenarioRunRequest
from app.schemas.strategy_experiment import (
    EXPERIMENT_RANKING_DIRECTIONS,
    EXPERIMENT_RANKING_METRICS,
    StrategyExperimentCompareResponse,
    StrategyExperimentLeaderboardItem,
    StrategyExperimentVariantResult,
)
from app.schemas.strategy_promotion import (
    StrategyPromotionRequest,
    StrategyPromotionResponse,
    StrategyPromotionSelectedVariant,
    StrategyPromotionWarning,
)
from app.services.paper_trading_scenario_service import PaperTradingScenarioService
from app.services.paper_trading_service import PaperTradingService
from app.services.strategy_experiment_service import StrategyExperimentService


class StrategyPromotionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def promote_best_experiment_to_paper_scenario(
        self,
        request: StrategyPromotionRequest,
    ) -> StrategyPromotionResponse:
        self._validate_request(request)
        self._validate_portfolio(request.portfolio_id)

        experiment_service = StrategyExperimentService(self.db)
        experiment = experiment_service.compare(request.experiment)
        ranking_metric = request.promote_ranking_metric or experiment.ranking_metric
        ranking_direction = request.promote_ranking_direction or experiment.ranking_direction
        leaderboard = (
            experiment.leaderboard
            if (
                ranking_metric == experiment.ranking_metric
                and ranking_direction == experiment.ranking_direction
            )
            else self._leaderboard(
                experiment.results,
                ranking_metric=ranking_metric,
                ranking_direction=ranking_direction,
            )
        )

        warnings: list[StrategyPromotionWarning] = []
        selected_item = self._best_variant(leaderboard)
        if selected_item is None:
            warnings.append(
                StrategyPromotionWarning(
                    message=(
                        "No completed experiment variant with ranking value was "
                        "available for promotion"
                    )
                )
            )
            return StrategyPromotionResponse(
                model_run_id=experiment.model_run_id,
                return_method=experiment.return_method,
                horizon_days=experiment.horizon_days,
                selected_variant=None,
                experiment=experiment,
                scenario=None,
                warnings=warnings,
            )

        selected_result = self._result_by_index(
            experiment,
            selected_item.variant_index,
        )
        selected_variant = StrategyPromotionSelectedVariant(
            variant_index=selected_item.variant_index,
            variant_name=selected_item.variant_name,
            rank=selected_item.rank,
            ranking_metric=ranking_metric,
            ranking_direction=ranking_direction,
            ranking_value=selected_item.ranking_value,
            request=selected_result.request,
            metrics=selected_result.metrics,
            final_portfolio_value=selected_result.final_portfolio_value,
        )

        if selected_result.request.get("use_portfolio_constraints") is False:
            warnings.append(
                StrategyPromotionWarning(
                    message=(
                        "Selected experiment variant used simplified backtest mode; "
                        "paper scenario uses portfolio construction constraints"
                    )
                )
            )

        scenario_request = self._scenario_request(
            request=request,
            experiment=experiment,
            selected_variant_name=selected_item.variant_name,
            selected_request=selected_result.request,
        )
        scenario = None
        try:
            scenario = PaperTradingScenarioService(self.db).run(scenario_request)
        except HTTPException as exc:
            self.db.rollback()
            warnings.append(
                StrategyPromotionWarning(
                    message="Paper trading scenario failed after experiment promotion",
                    details={"detail": exc.detail},
                )
            )
        except Exception:
            self.db.rollback()
            warnings.append(
                StrategyPromotionWarning(
                    message="Paper trading scenario failed after experiment promotion",
                    details={"detail": "Unexpected scenario execution error"},
                )
            )

        return StrategyPromotionResponse(
            model_run_id=experiment.model_run_id,
            return_method=experiment.return_method,
            horizon_days=experiment.horizon_days,
            selected_variant=selected_variant,
            experiment=experiment,
            scenario=scenario,
            warnings=warnings,
        )

    def _validate_request(self, request: StrategyPromotionRequest) -> None:
        if (
            request.paper_initial_capital is not None
            and request.paper_initial_capital <= 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="paper_initial_capital must be positive",
            )
        if request.scenario_max_cycles < 1 or request.scenario_max_cycles > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scenario_max_cycles must be between 1 and 500",
            )
        if (
            request.promote_ranking_metric is not None
            and request.promote_ranking_metric not in EXPERIMENT_RANKING_METRICS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking metric",
            )
        if (
            request.promote_ranking_direction is not None
            and request.promote_ranking_direction not in EXPERIMENT_RANKING_DIRECTIONS
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking direction",
            )

    def _validate_portfolio(self, portfolio_id: int | None) -> None:
        if portfolio_id is None:
            return
        portfolio = PaperTradingService(self.db).get_portfolio(portfolio_id)
        if portfolio.status == "archived":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Paper portfolio is archived",
            )

    @staticmethod
    def _result_by_index(
        experiment: StrategyExperimentCompareResponse,
        variant_index: int,
    ) -> StrategyExperimentVariantResult:
        for result in experiment.results:
            if result.variant_index == variant_index:
                return result
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected experiment variant result was not found",
        )

    @staticmethod
    def _scenario_request(
        *,
        request: StrategyPromotionRequest,
        experiment: StrategyExperimentCompareResponse,
        selected_variant_name: str,
        selected_request: dict[str, Any],
    ) -> PaperTradingScenarioRunRequest:
        initial_capital = (
            request.paper_initial_capital
            if request.paper_initial_capital is not None
            else request.experiment.initial_capital
        )
        return PaperTradingScenarioRunRequest(
            portfolio_id=request.portfolio_id,
            name=request.paper_portfolio_name
            or f"Promoted scenario {selected_variant_name}",
            description=request.paper_portfolio_description,
            initial_capital=initial_capital,
            base_currency=request.paper_base_currency,
            model_run_id=experiment.model_run_id,
            date_from=request.scenario_date_from or experiment.date_from,
            date_to=request.scenario_date_to or experiment.date_to,
            rebalance_frequency=selected_request.get("rebalance_frequency", "label_dates"),
            rebalance_gap_days=selected_request.get("rebalance_gap_days"),
            max_cycles=request.scenario_max_cycles,
            top_n=selected_request.get("top_n", 10),
            min_probability_positive=selected_request.get(
                "min_probability_positive",
                Decimal("0.55"),
            ),
            max_position_weight=selected_request.get(
                "max_position_weight",
                Decimal("0.20"),
            ),
            max_issuer_weight=selected_request.get(
                "max_issuer_weight",
                Decimal("0.30"),
            ),
            max_high_risk_weight=selected_request.get(
                "max_high_risk_weight",
                Decimal("0.20"),
            ),
            min_liquidity_score=selected_request.get("min_liquidity_score"),
            exclude_blocked_by_risk=selected_request.get(
                "exclude_blocked_by_risk",
                True,
            ),
            exclude_insufficient_credit_data=selected_request.get(
                "exclude_insufficient_credit_data",
                False,
            ),
            allowed_risk_levels=selected_request.get("allowed_risk_levels"),
            allowed_decision_statuses=selected_request.get(
                "allowed_decision_statuses"
            ),
            transaction_cost_rate=experiment.transaction_cost_rate,
            allow_partial_marking=request.scenario_allow_partial_marking,
            stop_on_rebalance_error=request.scenario_stop_on_rebalance_error,
            stop_on_mark_error=request.scenario_stop_on_mark_error,
            include_performance_report=request.scenario_include_performance_report,
            include_cycle_details=request.scenario_include_cycle_details,
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
            self._leaderboard_item(result=result, ranking_value=value, rank=rank)
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
            total_return=StrategyPromotionService._decimal_metric(metrics, "total_return"),
            annualized_return=StrategyPromotionService._decimal_metric(
                metrics,
                "annualized_return",
            ),
            max_drawdown=StrategyPromotionService._decimal_metric(
                metrics,
                "max_drawdown",
            ),
            volatility=StrategyPromotionService._decimal_metric(metrics, "volatility"),
            hit_rate=StrategyPromotionService._decimal_metric(metrics, "hit_rate"),
            average_unallocated_weight=StrategyPromotionService._decimal_metric(
                metrics,
                "average_unallocated_weight",
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
        return StrategyPromotionService._decimal_metric(metrics, ranking_metric)

    @staticmethod
    def _decimal_metric(metrics: dict[str, Any], key: str) -> Decimal | None:
        value = metrics.get(key)
        if value is None:
            return None
        return Decimal(str(value))
