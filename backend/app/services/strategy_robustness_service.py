from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.strategy_backtest import (
    StrategyBacktestRequest,
    StrategyBacktestResponse,
    StrategyBacktestSelectedCandidate,
)
from app.schemas.strategy_experiment import (
    EXPERIMENT_RANKING_METRICS,
    StrategyExperimentCompareResponse,
    StrategyExperimentLeaderboardItem,
    StrategyExperimentVariantResult,
)
from app.schemas.strategy_robustness import (
    ROBUSTNESS_SUBPERIOD_MODES,
    StrategyRobustnessAnalyzeRequest,
    StrategyRobustnessAnalyzeResponse,
    StrategyRobustnessConcentrationItem,
    StrategyRobustnessFlag,
    StrategyRobustnessSubperiodResult,
    StrategyRobustnessVariantResult,
    StrategyRobustnessWarning,
)
from app.services.strategy_backtest_service import StrategyBacktestService
from app.services.strategy_experiment_service import StrategyExperimentService


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True)
class RobustnessSubperiod:
    index: int
    date_from: date
    date_to: date


class StrategyRobustnessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def analyze(
        self,
        request: StrategyRobustnessAnalyzeRequest,
    ) -> StrategyRobustnessAnalyzeResponse:
        self._validate_request(request)
        experiment = StrategyExperimentService(self.db).compare(request.experiment)
        warnings: list[StrategyRobustnessWarning] = []
        selected_items = self._selected_leaderboard_items(
            experiment,
            request.selected_variant_count,
        )
        if not selected_items:
            warnings.append(
                StrategyRobustnessWarning(
                    message=(
                        "No completed experiment variants were available for "
                        "robustness analysis"
                    )
                )
            )
            return self._response(
                experiment=experiment,
                variants=[],
                warnings=warnings,
            )
        if len(selected_items) < request.selected_variant_count:
            warnings.append(
                StrategyRobustnessWarning(
                    message="Fewer completed experiment variants were available for analysis",
                    details={
                        "requested_count": request.selected_variant_count,
                        "available_count": len(selected_items),
                    },
                )
            )

        date_range = self._date_range(experiment)
        if date_range is None:
            warnings.append(
                StrategyRobustnessWarning(
                    message="Unable to derive robustness subperiod date range"
                )
            )
            variants = [
                self._variant_without_subperiods(
                    experiment=experiment,
                    item=item,
                    request=request,
                )
                for item in selected_items
            ]
            return self._response(
                experiment=experiment,
                variants=variants,
                warnings=warnings,
            )

        subperiods = self._subperiods(
            date_from=date_range[0],
            date_to=date_range[1],
            request=request,
            warnings=warnings,
        )
        variants = [
            self._analyze_variant(
                experiment=experiment,
                item=item,
                subperiods=subperiods,
                request=request,
            )
            for item in selected_items
        ]
        return self._response(
            experiment=experiment,
            variants=variants,
            warnings=warnings,
        )

    @staticmethod
    def _response(
        *,
        experiment: StrategyExperimentCompareResponse,
        variants: list[StrategyRobustnessVariantResult],
        warnings: list[StrategyRobustnessWarning],
    ) -> StrategyRobustnessAnalyzeResponse:
        return StrategyRobustnessAnalyzeResponse(
            model_run_id=experiment.model_run_id,
            model_run_ids=experiment.model_run_ids,
            model_run_count=experiment.model_run_count,
            prediction_source_mode=experiment.prediction_source_mode,
            date_from=experiment.date_from,
            date_to=experiment.date_to,
            return_method=experiment.return_method,
            horizon_days=experiment.horizon_days,
            experiment=experiment,
            analyzed_variant_count=len(variants),
            variants=variants,
            warnings=warnings,
        )

    def _analyze_variant(
        self,
        *,
        experiment: StrategyExperimentCompareResponse,
        item: StrategyExperimentLeaderboardItem,
        subperiods: list[RobustnessSubperiod],
        request: StrategyRobustnessAnalyzeRequest,
    ) -> StrategyRobustnessVariantResult:
        result = self._result_by_index(experiment, item.variant_index)
        subperiod_results: list[StrategyRobustnessSubperiodResult] = []
        successful_backtests: list[StrategyBacktestResponse] = []
        for subperiod in subperiods:
            subperiod_result, backtest = self._run_subperiod_backtest(
                result=result,
                subperiod=subperiod,
                ranking_metric=experiment.ranking_metric,
            )
            subperiod_results.append(subperiod_result)
            if backtest is not None:
                successful_backtests.append(backtest)

        top_bond = None
        top_company = None
        if request.include_candidate_concentration:
            top_bond = self._top_concentration(successful_backtests, entity_type="bond")
            top_company = self._top_concentration(successful_backtests, entity_type="company")

        aggregates = self._aggregates(subperiod_results)
        flags = self._flags(
            result=result,
            aggregates=aggregates,
            failed_subperiod_count=sum(
                subperiod.status == "failed" for subperiod in subperiod_results
            ),
            top_bond=top_bond,
            top_company=top_company,
            request=request,
        )
        return StrategyRobustnessVariantResult(
            variant_index=result.variant_index,
            variant_name=result.variant_name,
            full_period_rank=item.rank,
            full_period_status=result.status,
            full_period_ranking_value=item.ranking_value,
            full_period_metrics=result.metrics,
            full_period_final_value=result.final_portfolio_value,
            subperiod_count=len(subperiod_results),
            completed_subperiod_count=aggregates["completed_count"],
            failed_subperiod_count=sum(
                subperiod.status == "failed" for subperiod in subperiod_results
            ),
            positive_subperiod_count=aggregates["positive_count"],
            negative_subperiod_count=aggregates["negative_count"],
            positive_subperiod_ratio=aggregates["positive_ratio"],
            average_subperiod_return=aggregates["average_return"],
            median_subperiod_return=aggregates["median_return"],
            min_subperiod_return=aggregates["min_return"],
            max_subperiod_return=aggregates["max_return"],
            single_best_subperiod_return_share=aggregates["single_best_share"],
            average_max_drawdown=aggregates["average_max_drawdown"],
            worst_max_drawdown=aggregates["worst_max_drawdown"],
            average_unallocated_weight=aggregates["average_unallocated_weight"],
            top_bond_concentration=top_bond,
            top_company_concentration=top_company,
            flags=flags,
            subperiods=(
                subperiod_results if request.include_subperiod_details else []
            ),
        )

    def _run_subperiod_backtest(
        self,
        *,
        result: StrategyExperimentVariantResult,
        subperiod: RobustnessSubperiod,
        ranking_metric: str,
    ) -> tuple[StrategyRobustnessSubperiodResult, StrategyBacktestResponse | None]:
        payload = dict(result.request)
        payload["date_from"] = subperiod.date_from
        payload["date_to"] = subperiod.date_to
        try:
            backtest = StrategyBacktestService(self.db).run(
                StrategyBacktestRequest(**payload)
            )
        except HTTPException as exc:
            return (
                StrategyRobustnessSubperiodResult(
                    subperiod_index=subperiod.index,
                    date_from=subperiod.date_from,
                    date_to=subperiod.date_to,
                    status="failed",
                    ranking_value=None,
                    total_return=None,
                    annualized_return=None,
                    max_drawdown=None,
                    volatility=None,
                    hit_rate=None,
                    average_unallocated_weight=None,
                    final_portfolio_value=None,
                    period_count=None,
                    selected_period_count=None,
                    error=str(exc.detail),
                    warnings=[
                        StrategyRobustnessWarning(
                            message="Subperiod backtest failed during robustness analysis",
                            details={"detail": exc.detail},
                        )
                    ],
                ),
                None,
            )
        except Exception:
            return (
                StrategyRobustnessSubperiodResult(
                    subperiod_index=subperiod.index,
                    date_from=subperiod.date_from,
                    date_to=subperiod.date_to,
                    status="failed",
                    ranking_value=None,
                    total_return=None,
                    annualized_return=None,
                    max_drawdown=None,
                    volatility=None,
                    hit_rate=None,
                    average_unallocated_weight=None,
                    final_portfolio_value=None,
                    period_count=None,
                    selected_period_count=None,
                    error="Subperiod backtest failed during robustness analysis",
                    warnings=[
                        StrategyRobustnessWarning(
                            message="Subperiod backtest failed during robustness analysis",
                            details={"detail": "Unexpected subperiod execution error"},
                        )
                    ],
                ),
                None,
            )

        metrics = backtest.metrics
        return (
            StrategyRobustnessSubperiodResult(
                subperiod_index=subperiod.index,
                date_from=subperiod.date_from,
                date_to=subperiod.date_to,
                status="completed",
                ranking_value=self._ranking_value(backtest, ranking_metric),
                total_return=metrics.total_return,
                annualized_return=metrics.annualized_return,
                max_drawdown=metrics.max_drawdown,
                volatility=metrics.volatility,
                hit_rate=metrics.hit_rate,
                average_unallocated_weight=metrics.average_unallocated_weight,
                final_portfolio_value=backtest.final_portfolio_value,
                period_count=metrics.period_count,
                selected_period_count=metrics.selected_period_count,
                error=None,
                warnings=[
                    StrategyRobustnessWarning(
                        message=warning.message,
                        details={
                            "as_of_date": warning.as_of_date,
                            "bond_id": warning.bond_id,
                            **warning.details,
                        },
                    )
                    for warning in backtest.warnings
                ],
            ),
            backtest,
        )

    @staticmethod
    def _variant_without_subperiods(
        *,
        experiment: StrategyExperimentCompareResponse,
        item: StrategyExperimentLeaderboardItem,
        request: StrategyRobustnessAnalyzeRequest,
    ) -> StrategyRobustnessVariantResult:
        result = StrategyRobustnessService._result_by_index(experiment, item.variant_index)
        aggregates = StrategyRobustnessService._aggregates([])
        flags = StrategyRobustnessService._flags(
            result=result,
            aggregates=aggregates,
            failed_subperiod_count=0,
            top_bond=None,
            top_company=None,
            request=request,
        )
        return StrategyRobustnessVariantResult(
            variant_index=result.variant_index,
            variant_name=result.variant_name,
            full_period_rank=item.rank,
            full_period_status=result.status,
            full_period_ranking_value=item.ranking_value,
            full_period_metrics=result.metrics,
            full_period_final_value=result.final_portfolio_value,
            subperiod_count=0,
            completed_subperiod_count=0,
            failed_subperiod_count=0,
            positive_subperiod_count=0,
            negative_subperiod_count=0,
            positive_subperiod_ratio=None,
            average_subperiod_return=None,
            median_subperiod_return=None,
            min_subperiod_return=None,
            max_subperiod_return=None,
            single_best_subperiod_return_share=None,
            average_max_drawdown=None,
            worst_max_drawdown=None,
            average_unallocated_weight=None,
            top_bond_concentration=None,
            top_company_concentration=None,
            flags=flags,
            subperiods=[],
        )

    @staticmethod
    def _selected_leaderboard_items(
        experiment: StrategyExperimentCompareResponse,
        selected_variant_count: int,
    ) -> list[StrategyExperimentLeaderboardItem]:
        return [
            item
            for item in experiment.leaderboard
            if item.status == "completed" and item.ranking_value is not None
        ][:selected_variant_count]

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
    def _date_range(
        experiment: StrategyExperimentCompareResponse,
    ) -> tuple[date, date] | None:
        if experiment.date_from is not None and experiment.date_to is not None:
            return experiment.date_from, experiment.date_to

        dates: list[date] = []
        for result in experiment.results:
            if result.status != "completed":
                continue
            for period in result.periods:
                raw_date = period.get("as_of_date")
                if isinstance(raw_date, date):
                    dates.append(raw_date)
                elif isinstance(raw_date, str):
                    dates.append(date.fromisoformat(raw_date))
        if not dates:
            return None
        return min(dates), max(dates)

    def _subperiods(
        self,
        *,
        date_from: date,
        date_to: date,
        request: StrategyRobustnessAnalyzeRequest,
        warnings: list[StrategyRobustnessWarning],
    ) -> list[RobustnessSubperiod]:
        if request.subperiod_mode == "fixed_window":
            subperiods = self._fixed_window_subperiods(
                date_from,
                date_to,
                request.subperiod_days or 1,
            )
        else:
            subperiods = self._calendar_subperiods(
                date_from,
                date_to,
                quarterly=request.subperiod_mode == "quarterly",
            )
        if len(subperiods) > request.max_subperiods:
            warnings.append(
                StrategyRobustnessWarning(
                    message="Robustness subperiods were limited by max_subperiods",
                    details={
                        "generated_count": len(subperiods),
                        "max_subperiods": request.max_subperiods,
                    },
                )
            )
            subperiods = subperiods[: request.max_subperiods]
        return [
            RobustnessSubperiod(index=index, date_from=start, date_to=end)
            for index, (start, end) in enumerate(subperiods, start=1)
        ]

    @staticmethod
    def _calendar_subperiods(
        date_from: date,
        date_to: date,
        *,
        quarterly: bool,
    ) -> list[tuple[date, date]]:
        current = date_from
        subperiods: list[tuple[date, date]] = []
        while current <= date_to:
            if quarterly:
                quarter_start_month = ((current.month - 1) // 3) * 3 + 1
                period_end = StrategyRobustnessService._last_day_of_month(
                    current.year,
                    quarter_start_month + 2,
                )
            else:
                period_end = StrategyRobustnessService._last_day_of_month(
                    current.year,
                    current.month,
                )
            end = min(period_end, date_to)
            subperiods.append((current, end))
            current = end + timedelta(days=1)
        return subperiods

    @staticmethod
    def _fixed_window_subperiods(
        date_from: date,
        date_to: date,
        subperiod_days: int,
    ) -> list[tuple[date, date]]:
        current = date_from
        subperiods: list[tuple[date, date]] = []
        while current <= date_to:
            end = min(current + timedelta(days=subperiod_days - 1), date_to)
            subperiods.append((current, end))
            current = end + timedelta(days=1)
        return subperiods

    @staticmethod
    def _last_day_of_month(year: int, month: int) -> date:
        if month == 12:
            return date(year, 12, 31)
        first_next_month = date(year, month + 1, 1)
        return first_next_month - timedelta(days=1)

    @staticmethod
    def _aggregates(
        subperiod_results: list[StrategyRobustnessSubperiodResult],
    ) -> dict[str, Any]:
        completed = [
            subperiod
            for subperiod in subperiod_results
            if subperiod.status == "completed"
        ]
        returns = [
            subperiod.total_return
            for subperiod in completed
            if subperiod.total_return is not None
        ]
        positive_returns = [value for value in returns if value > 0]
        max_drawdowns = [
            subperiod.max_drawdown
            for subperiod in completed
            if subperiod.max_drawdown is not None
        ]
        unallocated_weights = [
            subperiod.average_unallocated_weight
            for subperiod in completed
            if subperiod.average_unallocated_weight is not None
        ]
        completed_count = len(completed)
        positive_count = sum(value > 0 for value in returns)
        negative_count = sum(value < 0 for value in returns)
        positive_sum = sum(positive_returns, ZERO)
        return {
            "completed_count": completed_count,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "positive_ratio": (
                Decimal(positive_count) / Decimal(completed_count)
                if completed_count
                else None
            ),
            "average_return": StrategyRobustnessService._average(returns),
            "median_return": StrategyRobustnessService._median(returns),
            "min_return": min(returns) if returns else None,
            "max_return": max(returns) if returns else None,
            "single_best_share": (
                max(positive_returns) / positive_sum
                if positive_returns and positive_sum > 0
                else None
            ),
            "average_max_drawdown": StrategyRobustnessService._average(max_drawdowns),
            "worst_max_drawdown": max(max_drawdowns) if max_drawdowns else None,
            "average_unallocated_weight": StrategyRobustnessService._average(
                unallocated_weights
            ),
        }

    @staticmethod
    def _average(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values, ZERO) / Decimal(len(values))

    @staticmethod
    def _median(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        sorted_values = sorted(values)
        middle = len(sorted_values) // 2
        if len(sorted_values) % 2:
            return sorted_values[middle]
        return (sorted_values[middle - 1] + sorted_values[middle]) / Decimal("2")

    @staticmethod
    def _top_concentration(
        backtests: list[StrategyBacktestResponse],
        *,
        entity_type: str,
    ) -> StrategyRobustnessConcentrationItem | None:
        candidates = [
            candidate
            for backtest in backtests
            for period in backtest.periods
            for candidate in period.selected_candidates
        ]
        if not candidates:
            return None

        totals: dict[int | None, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "weight_sum": ZERO, "name": None}
        )
        for candidate in candidates:
            key, name = StrategyRobustnessService._candidate_entity(candidate, entity_type)
            totals[key]["count"] += 1
            totals[key]["weight_sum"] += candidate.weight
            totals[key]["name"] = totals[key]["name"] or name

        total_count = len(candidates)
        ranked = sorted(
            totals.items(),
            key=lambda item: (
                -(item[1]["count"] / total_count),
                -item[1]["count"],
                item[0] is None,
                item[0] or 0,
            ),
        )
        entity_id, payload = ranked[0]
        selection_count = payload["count"]
        return StrategyRobustnessConcentrationItem(
            entity_type=entity_type,
            entity_id=entity_id,
            name=payload["name"],
            selection_count=selection_count,
            selection_share=Decimal(selection_count) / Decimal(total_count),
            average_allocation_weight=(
                payload["weight_sum"] / Decimal(selection_count)
                if selection_count
                else None
            ),
        )

    @staticmethod
    def _candidate_entity(
        candidate: StrategyBacktestSelectedCandidate,
        entity_type: str,
    ) -> tuple[int | None, str | None]:
        if entity_type == "company":
            return candidate.company_id, candidate.company_name
        return candidate.bond_id, candidate.bond_name

    @staticmethod
    def _flags(
        *,
        result: StrategyExperimentVariantResult,
        aggregates: dict[str, Any],
        failed_subperiod_count: int,
        top_bond: StrategyRobustnessConcentrationItem | None,
        top_company: StrategyRobustnessConcentrationItem | None,
        request: StrategyRobustnessAnalyzeRequest,
    ) -> list[StrategyRobustnessFlag]:
        flags: list[StrategyRobustnessFlag] = []
        if aggregates["completed_count"] < request.minimum_completed_subperiods:
            flags.append(
                StrategyRobustnessFlag(
                    code="too_few_completed_subperiods",
                    level="fail",
                    message="Completed subperiod count is below configured minimum",
                    details={
                        "completed_count": aggregates["completed_count"],
                        "configured_minimum": request.minimum_completed_subperiods,
                    },
                )
            )
        positive_ratio = aggregates["positive_ratio"]
        if (
            positive_ratio is not None
            and positive_ratio < request.minimum_positive_subperiod_ratio
        ):
            flags.append(
                StrategyRobustnessFlag(
                    code="low_positive_subperiod_ratio",
                    level="warning",
                    message="Positive subperiod ratio is below configured minimum",
                    details={
                        "positive_subperiod_ratio": positive_ratio,
                        "configured_minimum": request.minimum_positive_subperiod_ratio,
                    },
                )
            )
        best_share = aggregates["single_best_share"]
        if (
            best_share is not None
            and best_share > request.maximum_single_subperiod_return_share
        ):
            flags.append(
                StrategyRobustnessFlag(
                    code="single_subperiod_dominates_result",
                    level="warning",
                    message="One subperiod accounts for most positive subperiod return",
                    details={
                        "single_best_subperiod_return_share": best_share,
                        "configured_maximum": request.maximum_single_subperiod_return_share,
                    },
                )
            )
        if (
            top_bond is not None
            and top_bond.selection_share > request.maximum_top_bond_selection_share
        ):
            flags.append(
                StrategyRobustnessFlag(
                    code="high_bond_concentration",
                    level="warning",
                    message="Selection is concentrated in one bond",
                    details={
                        "entity_id": top_bond.entity_id,
                        "selection_share": top_bond.selection_share,
                        "configured_maximum": request.maximum_top_bond_selection_share,
                    },
                )
            )
        if (
            top_company is not None
            and top_company.selection_share > request.maximum_top_company_selection_share
        ):
            flags.append(
                StrategyRobustnessFlag(
                    code="high_company_concentration",
                    level="warning",
                    message="Selection is concentrated in one company",
                    details={
                        "entity_id": top_company.entity_id,
                        "selection_share": top_company.selection_share,
                        "configured_maximum": request.maximum_top_company_selection_share,
                    },
                )
            )
        full_period_return = StrategyRobustnessService._decimal_metric(
            result.metrics,
            "total_return",
        )
        if (
            full_period_return is not None
            and full_period_return > 0
            and positive_ratio is not None
            and positive_ratio < Decimal("0.50")
        ):
            flags.append(
                StrategyRobustnessFlag(
                    code="full_period_success_but_subperiods_weak",
                    level="warning",
                    message="Full-period result is positive while subperiod stability is weak",
                    details={
                        "full_period_total_return": full_period_return,
                        "positive_subperiod_ratio": positive_ratio,
                    },
                )
            )
        if failed_subperiod_count > 0:
            flags.append(
                StrategyRobustnessFlag(
                    code="subperiod_failures_present",
                    level="info",
                    message="One or more subperiod diagnostics failed",
                    details={"failed_subperiod_count": failed_subperiod_count},
                )
            )
        return flags

    @staticmethod
    def _ranking_value(
        backtest: StrategyBacktestResponse,
        ranking_metric: str,
    ) -> Decimal | None:
        if ranking_metric == "final_portfolio_value":
            return backtest.final_portfolio_value
        return getattr(backtest.metrics, ranking_metric, None)

    @staticmethod
    def _decimal_metric(metrics: dict[str, Any] | None, key: str) -> Decimal | None:
        if not metrics:
            return None
        value = metrics.get(key)
        if value is None:
            return None
        return Decimal(str(value))

    @staticmethod
    def _validate_request(request: StrategyRobustnessAnalyzeRequest) -> None:
        if request.selected_variant_count < 1 or request.selected_variant_count > 20:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="selected_variant_count must be between 1 and 20",
            )
        if request.subperiod_mode not in ROBUSTNESS_SUBPERIOD_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid subperiod mode",
            )
        if (
            request.subperiod_mode == "fixed_window"
            and (request.subperiod_days is None or request.subperiod_days <= 0)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="subperiod_days must be positive when subperiod_mode is fixed_window",
            )
        if request.max_subperiods < 1 or request.max_subperiods > 120:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_subperiods must be between 1 and 120",
            )
        if request.minimum_completed_subperiods <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_completed_subperiods must be positive",
            )
        ratios = [
            request.minimum_positive_subperiod_ratio,
            request.maximum_single_subperiod_return_share,
            request.maximum_top_bond_selection_share,
            request.maximum_top_company_selection_share,
        ]
        if any(value < ZERO or value > ONE for value in ratios):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ratio values must be between 0 and 1",
            )
        if request.experiment.ranking_metric not in EXPERIMENT_RANKING_METRICS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid ranking metric",
            )
