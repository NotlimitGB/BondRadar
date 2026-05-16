from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.paper_trading_scenario import PaperTradingScenarioRunResponse
from app.schemas.strategy_experiment import StrategyExperimentCompareRequest, StrategyExperimentCompareResponse


class StrategyPromotionWarning(BaseModel):
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class StrategyPromotionSelectedVariant(BaseModel):
    variant_index: int
    variant_name: str
    rank: int
    ranking_metric: str
    ranking_direction: str
    ranking_value: Decimal | None
    request: dict[str, Any]
    metrics: dict[str, Any] | None
    final_portfolio_value: Decimal | None


class StrategyPromotionRequest(BaseModel):
    experiment: StrategyExperimentCompareRequest
    portfolio_id: int | None = None
    paper_portfolio_name: str | None = None
    paper_portfolio_description: str | None = None
    paper_initial_capital: Decimal | None = None
    paper_base_currency: str = "RUB"
    scenario_date_from: date | None = None
    scenario_date_to: date | None = None
    scenario_max_cycles: int = 100
    scenario_allow_partial_marking: bool = True
    scenario_stop_on_rebalance_error: bool = False
    scenario_stop_on_mark_error: bool = False
    scenario_include_performance_report: bool = True
    scenario_include_cycle_details: bool = True
    promote_ranking_metric: str | None = None
    promote_ranking_direction: str | None = None


class StrategyPromotionResponse(BaseModel):
    model_run_id: int
    return_method: str
    horizon_days: int
    selected_variant: StrategyPromotionSelectedVariant | None
    experiment: StrategyExperimentCompareResponse
    scenario: PaperTradingScenarioRunResponse | None
    warnings: list[StrategyPromotionWarning]
