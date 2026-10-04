"""Immutable evidence for seven independently prepared capital experiments."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, field_validator, model_validator
from app.schemas.shadow_experiment import (
    Contract, ShadowExperimentPolicyV1, ShadowExperimentGenesisPlanV1,
    ShadowExperimentComparisonViewV1,
)
from app.schemas.shadow_execution import ShadowExecutionPlanView
from app.schemas.shadow_ledger import ShadowGenesisPlanV1

CAPITAL_GRID_RUB = tuple(Decimal(v) for v in (
    "50000", "100000", "500000", "1000000", "3000000", "5000000", "10000000"))


class ShadowScaleExperimentPolicyV1(Contract):
    contract_version: Literal["shadow-scale-experiment-policy-v1"] = "shadow-scale-experiment-policy-v1"
    capital_grid_rub: tuple[Decimal, ...] = CAPITAL_GRID_RUB
    experiment_policy: ShadowExperimentPolicyV1 = Field(default_factory=ShadowExperimentPolicyV1)
    same_genesis_date_required: Literal[True] = True
    same_source_universe_required: Literal[True] = True
    same_source_code_required: Literal[True] = True
    same_market_source_required: Literal[True] = True
    same_common_market_trade_date_required: Literal[True] = True
    same_investment_evidence_required: Literal[True] = True
    overlapping_execution_terms_equal_required: Literal[True] = True
    independent_portfolio_rebuild_required: Literal[True] = True
    aggregate_success_rule: Literal["NONE"] = "NONE"

    @field_validator("same_genesis_date_required", "same_source_universe_required",
        "same_source_code_required", "same_market_source_required", "same_common_market_trade_date_required",
        "same_investment_evidence_required", "overlapping_execution_terms_equal_required",
        "independent_portfolio_rebuild_required", mode="before")
    @classmethod
    def exact_boolean(cls, value):
        if value is not True:
            raise ValueError("Fixed policy requires true")
        return value

    @model_validator(mode="after")
    def frozen_grid(self):
        if self.capital_grid_rub != CAPITAL_GRID_RUB:
            raise ValueError("CAPITAL_GRID_INVALID")
        return self


class ShadowScaleCapabilitiesV1(Contract):
    multi_capital_policy_ready: Literal[True] = True
    multi_capital_genesis_ready: Literal[True] = True
    independent_capital_rebuild_ready: Literal[True] = True
    multi_capital_benchmark_ready: Literal[True] = True
    scale_comparison_ready: Literal[True] = True
    scale_profit_comparison_ready: Literal[True] = True
    scale_evidence_completeness_ready: Literal[True] = True
    persistence_ready: Literal[False] = False
    activation_ready: Literal[False] = False
    scheduler_ready: Literal[False] = False
    optimal_capital_claim_ready: Literal[False] = False
    monthly_income_claim_ready: Literal[False] = False
    after_tax_income_ready: Literal[False] = False
    rebalance_ready: Literal[False] = False
    broker_execution_ready: Literal[False] = False

    @field_validator("multi_capital_policy_ready", "multi_capital_genesis_ready",
        "independent_capital_rebuild_ready", "multi_capital_benchmark_ready", "scale_comparison_ready",
        "scale_profit_comparison_ready", "scale_evidence_completeness_ready", "persistence_ready",
        "activation_ready", "scheduler_ready", "optimal_capital_claim_ready", "monthly_income_claim_ready",
        "after_tax_income_ready", "rebalance_ready", "broker_execution_ready", mode="before")
    @classmethod
    def exact_capability(cls, value, info):
        if type(value) is not bool:
            raise ValueError("Invalid capability type")
        return value


class ShadowScaleExperimentCaseInputV1(Contract):
    capital_rub: Decimal
    reviewed_strategy_genesis: ShadowGenesisPlanV1
    source_shadow_execution: ShadowExecutionPlanView


class ShadowScaleExperimentGenesisCaseV1(ShadowScaleExperimentCaseInputV1):
    contract_version: Literal["shadow-scale-experiment-genesis-case-v1"] = "shadow-scale-experiment-genesis-case-v1"
    status: Literal["READY", "BLOCKED"]
    experiment_genesis: ShadowExperimentGenesisPlanV1
    blockers: tuple[str, ...] = ()
    strategy_selected_count: int
    strategy_target_invested_weight: Decimal
    strategy_target_cash_weight: Decimal
    shadow_planned_position_count: int
    shadow_planned_invested_rub: Decimal
    shadow_residual_cash_rub: Decimal
    shadow_realized_invested_weight: Decimal
    shadow_realized_cash_weight: Decimal
    shadow_execution_tracking_gap_rub: Decimal
    target_duration_years: Decimal | None
    post_rounding_risk_status: Literal["PASS", "BLOCKED"]
    max_position_weight_observed: Decimal
    max_issuer_weight_observed: Decimal
    portfolio_dv01_per_100k_rub: Decimal
    benchmark_matching_mode: Literal["EXACT_DURATION_NODE", "LINEAR_DURATION_MATCH"] | None
    benchmark_component_bond_ids: tuple[int, ...]
    investment_batch_sha256: str
    case_sha256: str = ""


class ShadowScaleExperimentGenesisPlanV1(Contract):
    contract_version: Literal["shadow-scale-experiment-genesis-plan-v1"] = "shadow-scale-experiment-genesis-plan-v1"
    status: Literal["READY", "BLOCKED"]
    scale_policy: ShadowScaleExperimentPolicyV1
    scale_policy_sha256: str
    capital_grid_rub: tuple[Decimal, ...]
    genesis_date: date | None
    planned_end_date: date | None
    common_market_trade_date: date | None
    market_source: str | None
    source_code_sha: str | None
    source_universe_sha256: str | None
    investment_batch_sha256: str | None
    experiment_policy_sha256: str | None
    cases: tuple[ShadowScaleExperimentGenesisCaseV1, ...]
    ready_case_count: int
    blocked_case_count: int
    blockers: tuple[str, ...] = ()
    scale_genesis_sha256: str = ""
    capabilities: ShadowScaleCapabilitiesV1 = Field(default_factory=ShadowScaleCapabilitiesV1)


class ShadowScaleExperimentComparisonRowV1(Contract):
    contract_version: Literal["shadow-scale-experiment-comparison-row-v1"] = "shadow-scale-experiment-comparison-row-v1"
    capital_rub: Decimal
    case_sha256: str
    experiment_genesis_sha256: str
    comparison: ShadowExperimentComparisonViewV1
    status: Literal["READY", "UNAVAILABLE"]
    verdict: Literal["IN_PROGRESS", "PASS", "FAIL", "INDETERMINATE"]
    strategy_return: Decimal | None
    benchmark_return: Decimal | None
    excess_return: Decimal | None
    strategy_profit_rub: Decimal | None
    benchmark_profit_rub: Decimal | None
    excess_profit_rub: Decimal | None
    initial_realized_invested_weight: Decimal
    initial_cash_weight: Decimal
    initial_selected_count: int
    initial_target_duration_years: Decimal | None
    blockers: tuple[str, ...] = ()
    row_sha256: str = ""


class ShadowScaleExperimentComparisonViewV1(Contract):
    contract_version: Literal["shadow-scale-experiment-comparison-v1"] = "shadow-scale-experiment-comparison-v1"
    status: Literal["IN_PROGRESS", "COMPLETE", "INDETERMINATE"]
    as_of_date: date
    scale_genesis: ShadowScaleExperimentGenesisPlanV1
    scale_genesis_sha256: str
    rows: tuple[ShadowScaleExperimentComparisonRowV1, ...]
    ready_case_count: int
    unavailable_case_count: int
    highest_strategy_return_capitals_rub: tuple[Decimal, ...] | None
    highest_excess_return_capitals_rub: tuple[Decimal, ...] | None
    blockers: tuple[str, ...] = ()
    scale_comparison_sha256: str = ""
    capabilities: ShadowScaleCapabilitiesV1 = Field(default_factory=ShadowScaleCapabilitiesV1)
