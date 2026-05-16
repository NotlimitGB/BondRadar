from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.strategy_experiment import StrategyExperimentCompareRequest, StrategyExperimentCompareResponse


ROBUSTNESS_SUBPERIOD_MODES = {"monthly", "quarterly", "fixed_window"}
ROBUSTNESS_SUBPERIOD_STATUSES = {"completed", "failed"}
ROBUSTNESS_FLAG_LEVELS = {"info", "warning", "fail"}


class StrategyRobustnessAnalyzeRequest(BaseModel):
    experiment: StrategyExperimentCompareRequest
    selected_variant_count: int = 5
    subperiod_mode: str = "monthly"
    subperiod_days: int | None = None
    include_subperiod_details: bool = True
    include_candidate_concentration: bool = True
    max_subperiods: int = 36
    minimum_completed_subperiods: int = 2
    minimum_positive_subperiod_ratio: Decimal = Decimal("0.50")
    maximum_single_subperiod_return_share: Decimal = Decimal("0.70")
    maximum_top_bond_selection_share: Decimal = Decimal("0.70")
    maximum_top_company_selection_share: Decimal = Decimal("0.70")


class StrategyRobustnessWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class StrategyRobustnessFlag(BaseModel):
    code: str
    level: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class StrategyRobustnessConcentrationItem(BaseModel):
    entity_type: str
    entity_id: int | None
    name: str | None
    selection_count: int
    selection_share: Decimal
    average_allocation_weight: Decimal | None


class StrategyRobustnessSubperiodResult(BaseModel):
    subperiod_index: int
    date_from: date
    date_to: date
    status: str
    ranking_value: Decimal | None
    total_return: Decimal | None
    annualized_return: Decimal | None
    max_drawdown: Decimal | None
    volatility: Decimal | None
    hit_rate: Decimal | None
    average_unallocated_weight: Decimal | None
    final_portfolio_value: Decimal | None
    period_count: int | None
    selected_period_count: int | None
    error: str | None
    warnings: list[StrategyRobustnessWarning]


class StrategyRobustnessVariantResult(BaseModel):
    variant_index: int
    variant_name: str
    full_period_rank: int | None
    full_period_status: str
    full_period_ranking_value: Decimal | None
    full_period_metrics: dict[str, Any] | None
    full_period_final_value: Decimal | None
    subperiod_count: int
    completed_subperiod_count: int
    failed_subperiod_count: int
    positive_subperiod_count: int
    negative_subperiod_count: int
    positive_subperiod_ratio: Decimal | None
    average_subperiod_return: Decimal | None
    median_subperiod_return: Decimal | None
    min_subperiod_return: Decimal | None
    max_subperiod_return: Decimal | None
    single_best_subperiod_return_share: Decimal | None
    average_max_drawdown: Decimal | None
    worst_max_drawdown: Decimal | None
    average_unallocated_weight: Decimal | None
    top_bond_concentration: StrategyRobustnessConcentrationItem | None
    top_company_concentration: StrategyRobustnessConcentrationItem | None
    flags: list[StrategyRobustnessFlag]
    subperiods: list[StrategyRobustnessSubperiodResult]


class StrategyRobustnessAnalyzeResponse(BaseModel):
    model_run_id: int | None
    model_run_ids: list[int]
    model_run_count: int
    prediction_source_mode: str
    date_from: date | None
    date_to: date | None
    return_method: str
    horizon_days: int
    experiment: StrategyExperimentCompareResponse
    analyzed_variant_count: int
    variants: list[StrategyRobustnessVariantResult]
    warnings: list[StrategyRobustnessWarning]
