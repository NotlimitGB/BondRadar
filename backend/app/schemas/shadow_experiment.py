"""Frozen, read-only forward-experiment and reference-index contracts."""
from datetime import date
from decimal import Decimal
from typing import Literal, get_args, get_origin
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.schemas.ofz_reference_curve import OfzReferenceCurveView
from app.schemas.bond_modified_duration import BondModifiedDurationView
from app.schemas.bond_dv01 import BondDv01View
from app.schemas.shadow_execution import ShadowExecutionPlanView
from app.schemas.shadow_ledger import ShadowGenesisPlanV1, DailySnapshot, ShadowAudit


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    pit_ready: Literal[False] = False

    @field_validator("pit_ready", mode="before")
    @classmethod
    def exact_literal_type(cls, value, info):
        annotation = cls.model_fields[info.field_name].annotation
        if get_origin(annotation) is Literal and type(value) not in {type(v) for v in get_args(annotation)}:
            raise ValueError("Invalid literal type")
        return value


class ShadowExperimentPolicyV1(Contract):
    contract_version: Literal["shadow-experiment-policy-v1"] = "shadow-experiment-policy-v1"
    horizon_calendar_days: Literal[90] = 90
    strategy_rebalance_policy: Literal["NONE"] = "NONE"
    strategy_cashflow_reinvestment_policy: Literal["NONE"] = "NONE"
    benchmark_type: Literal["DURATION_MATCHED_OFZ_TOTAL_RETURN_V1"] = "DURATION_MATCHED_OFZ_TOTAL_RETURN_V1"
    benchmark_allocation: Literal["FRACTIONAL_FULLY_INVESTED"] = "FRACTIONAL_FULLY_INVESTED"
    benchmark_duration_basis: Literal["MODIFIED_DURATION_TASK272"] = "MODIFIED_DURATION_TASK272"
    benchmark_rebalance_policy: Literal["NONE"] = "NONE"
    benchmark_cashflow_reinvestment_policy: Literal["NONE"] = "NONE"
    benchmark_market_source: Literal["moex"] = "moex"
    benchmark_max_market_age_days: Literal[7] = 7
    benchmark_max_curve_age_days: Literal[7] = 7
    require_common_genesis_trade_date: Literal[True] = True
    primary_success_rule: Literal["POSITIVE_ABSOLUTE_AND_POSITIVE_OFZ_EXCESS"] = "POSITIVE_ABSOLUTE_AND_POSITIVE_OFZ_EXCESS"

    @field_validator("horizon_calendar_days", "benchmark_max_market_age_days", "benchmark_max_curve_age_days",
                     "require_common_genesis_trade_date", mode="before")
    @classmethod
    def exact_policy_literal(cls, value, info):
        return cls.exact_literal_type(value, info)


class Capabilities(Contract):
    experiment_policy_ready: Literal[True] = True
    duration_matched_ofz_benchmark_ready: Literal[True] = True
    ofz_total_return_benchmark_ready: Literal[True] = True
    benchmark_cashflow_accounting_ready: Literal[True] = True
    benchmark_daily_valuation_ready: Literal[True] = True
    strategy_benchmark_comparison_ready: Literal[True] = True
    ofz_excess_return_ready: Literal[True] = True
    experiment_success_verdict_ready: Literal[True] = True
    experiment_genesis_plan_ready: Literal[True] = True
    benchmark_investable: Literal[False] = False
    benchmark_persistence_ready: Literal[False] = False
    experiment_activation_ready: Literal[False] = False
    automatic_daily_scheduler_ready: Literal[False] = False
    strategy_rebalance_ready: Literal[False] = False
    benchmark_rebalance_ready: Literal[False] = False
    cashflow_reinvestment_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    tax_model_ready: Literal[False] = False
    broker_execution_ready: Literal[False] = False
    experiment_day0_started: Literal[False] = False


Blocker = Literal[
    "INPUT_EVIDENCE_INVALID", "STRATEGY_GENESIS_NOT_EXECUTABLE", "STRATEGY_GENESIS_EVIDENCE_MISMATCH",
    "BENCHMARK_CURVE_NOT_READY", "BENCHMARK_CURVE_EVIDENCE_INVALID",
    "BENCHMARK_MODIFIED_DURATION_UNAVAILABLE", "BENCHMARK_MODIFIED_DURATION_EVIDENCE_MISMATCH",
    "TARGET_DURATION_INVALID", "TARGET_DURATION_OUTSIDE_OFZ_CURVE", "BENCHMARK_DECIMAL_INVARIANTS_FAILED",
    "BENCHMARK_MARKET_VALUE_UNAVAILABLE", "BENCHMARK_MARKET_EVIDENCE_MISMATCH",
    "GENESIS_MARKET_DATE_MISMATCH", "BENCHMARK_CASHFLOW_AMOUNT_MISSING",
    "BENCHMARK_CASHFLOW_EVIDENCE_INVALID", "BENCHMARK_CASHFLOW_DUPLICATE",
    "BENCHMARK_REDEMPTION_CONFLICT", "BENCHMARK_UNKNOWN_CASHFLOW_TYPE", "BENCHMARK_PREVIOUS_NAV_ZERO",
    "STRATEGY_OBSERVATION_UNAVAILABLE", "STRATEGY_HISTORY_INVALID", "COMPARISON_IDENTITY_MISMATCH",
]


class OfzRepresentativeV1(Contract):
    bond_id: int
    snapshot_id: int
    isin: str | None
    secid: str | None
    source_node_macaulay_duration_years: Decimal
    source_node_yield_pct: Decimal
    component_yield_pct: Decimal
    modified_duration_years: Decimal
    modified_duration_evidence: BondModifiedDurationView


class OfzBenchmarkComponentV1(Contract):
    contract_version: Literal["ofz-total-return-benchmark-component-v1"] = "ofz-total-return-benchmark-component-v1"
    representative: OfzRepresentativeV1
    weight: Decimal
    genesis_dirty_value_rub: Decimal
    genesis_market_evidence: BondDv01View


class OfzBenchmarkGenesisViewV1(Contract):
    contract_version: Literal["ofz-total-return-benchmark-genesis-v1"] = "ofz-total-return-benchmark-genesis-v1"
    status: Literal["READY", "BLOCKED"]
    genesis_date: date
    planned_end_date: date
    common_market_trade_date: date | None = None
    initial_capital_rub: Decimal
    target_duration_years: Decimal | None
    matching_mode: Literal["EXACT_DURATION_NODE", "LINEAR_DURATION_MATCH"] | None = None
    reconstructed_duration_years: Decimal | None = None
    genesis_nav_rub: Decimal | None = None
    policy: ShadowExperimentPolicyV1
    experiment_policy_sha256: str
    shadow_execution_sha256: str
    curve: OfzReferenceCurveView | None = None
    representatives: tuple[OfzRepresentativeV1, ...] = ()
    modified_duration_inputs: tuple[BondModifiedDurationView, ...] = ()
    components: tuple[OfzBenchmarkComponentV1, ...] = ()
    curve_build_count: int = 0
    modified_duration_call_count: int = 0
    dirty_value_call_count: int = 0
    blockers: tuple[Blocker, ...] = ()
    benchmark_genesis_sha256: str = ""
    capabilities: Capabilities = Field(default_factory=Capabilities)


class BenchmarkCashflowV1(Contract):
    source_event_id: int
    bond_id: int
    event_date: date
    event_type: str
    source: str
    currency: str | None
    amount: Decimal | None


class BenchmarkComponentDailyV1(Contract):
    bond_id: int
    status: Literal["ACTIVE", "REDEEMED"]
    weight: Decimal
    cash_per_original_bond_rub: Decimal
    dirty_value_rub: Decimal
    total_value_per_original_bond_rub: Decimal
    total_return_factor: Decimal
    market_evidence: BondDv01View | None


class OfzBenchmarkDailyViewV1(Contract):
    contract_version: Literal["ofz-total-return-benchmark-daily-v1"] = "ofz-total-return-benchmark-daily-v1"
    status: Literal["READY", "UNAVAILABLE"]
    as_of_date: date
    genesis_date: date
    planned_end_date: date
    initial_capital_rub: Decimal
    benchmark_genesis_sha256: str
    experiment_policy_sha256: str
    benchmark_nav_rub: Decimal | None = None
    previous_nav_rub: Decimal | None = None
    daily_return: Decimal | None = None
    cumulative_return: Decimal | None = None
    components: tuple[BenchmarkComponentDailyV1, ...] = ()
    previous_components: tuple[BenchmarkComponentDailyV1, ...] = ()
    cashflow_evidence: tuple[BenchmarkCashflowV1, ...] = ()
    cashflow_query_count: int = 0
    dirty_value_call_count: int = 0
    diagnostics: tuple[str, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    benchmark_daily_input_sha256: str = ""
    benchmark_daily_sha256: str = ""
    capabilities: Capabilities = Field(default_factory=Capabilities)


class ShadowExperimentGenesisPlanV1(Contract):
    contract_version: Literal["shadow-experiment-genesis-plan-v1"] = "shadow-experiment-genesis-plan-v1"
    status: Literal["READY", "BLOCKED"]
    policy: ShadowExperimentPolicyV1
    reviewed_strategy_genesis: ShadowGenesisPlanV1
    source_shadow_execution: ShadowExecutionPlanView
    benchmark: OfzBenchmarkGenesisViewV1 | None = None
    genesis_date: date
    planned_end_date: date
    initial_capital_rub: Decimal
    common_market_trade_date: date | None = None
    target_duration_years: Decimal | None
    experiment_policy_sha256: str
    strategy_genesis_plan_sha256: str
    shadow_execution_sha256: str
    source_universe_sha256: str
    benchmark_genesis_sha256: str | None = None
    experiment_genesis_sha256: str = ""
    blockers: tuple[Blocker, ...] = ()
    capabilities: Capabilities = Field(default_factory=Capabilities)


class StrategyExperimentObservationV1(Contract):
    status: Literal["READY", "UNAVAILABLE"]
    as_of_date: date
    genesis_date: date
    initial_capital_rub: Decimal
    run_key_sha256: str
    genesis_plan_sha256: str
    shadow_execution_sha256: str
    source_universe_sha256: str
    snapshot: DailySnapshot | None = None
    accepted_dates: tuple[date, ...] = ()
    audits: tuple[ShadowAudit, ...] = ()
    blockers: tuple[Blocker, ...] = ()


class ShadowExperimentComparisonViewV1(Contract):
    contract_version: Literal["shadow-experiment-comparison-v1"] = "shadow-experiment-comparison-v1"
    status: Literal["READY", "UNAVAILABLE"]
    verdict: Literal["IN_PROGRESS", "PASS", "FAIL", "INDETERMINATE"]
    as_of_date: date
    genesis_date: date
    planned_end_date: date
    initial_capital_rub: Decimal
    experiment_genesis_sha256: str
    strategy_return: Decimal | None = None
    benchmark_return: Decimal | None = None
    excess_return: Decimal | None = None
    reasons: tuple[Literal["ABSOLUTE_RETURN_NONPOSITIVE", "OFZ_EXCESS_RETURN_NONPOSITIVE"], ...] = ()
    blockers: tuple[Blocker, ...] = ()
    strategy_observation: StrategyExperimentObservationV1
    benchmark_daily: OfzBenchmarkDailyViewV1
    comparison_sha256: str = ""
    capabilities: Capabilities = Field(default_factory=Capabilities)
