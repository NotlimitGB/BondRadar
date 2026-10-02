"""Frozen Shadow reference plans, not orders or persisted executions."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_dv01 import PriceBasis
from app.schemas.portfolio_strategy import PortfolioStrategyView, PortfolioStrategyPosition
from app.schemas.risk_engine import PortfolioRiskEvaluationView


class ShadowContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class ShadowExecutionPolicyV1(ShadowContract):
    contract_version: Literal["shadow-execution-policy-v1"] = "shadow-execution-policy-v1"
    lot_rounding_mode: Literal["FLOOR_TO_TARGET"] = "FLOOR_TO_TARGET"
    allow_target_overshoot: Literal[False] = False
    redistribute_residual_cash: Literal[False] = False
    use_transaction_costs: Literal[False] = False
    use_slippage: Literal[False] = False
    use_market_impact: Literal[False] = False
    require_verified_lot_size: Literal[True] = True
    require_verified_trading_board: Literal[True] = True
    pit_ready: Literal[False] = False


EvidenceState = Literal["unknown", "verified", "conflict"]
TermsBlocker = Literal["SECURITY_MASTER_PROFILE_MISSING", "SECURITY_MASTER_PROFILE_ID_MISMATCH",
    "SECURITY_MASTER_CONTRACT_MISMATCH", "SECURITY_MASTER_PROFILE_DRIFT", "CURRENCY_NOT_VERIFIED",
    "CURRENCY_MISMATCH", "NOMINAL_NOT_VERIFIED", "NOMINAL_MISMATCH", "LOT_SIZE_NOT_VERIFIED",
    "TRADING_BOARD_NOT_VERIFIED"]


class ShadowExecutionTermsView(ShadowContract):
    contract_version: Literal["shadow-execution-terms-v1"] = "shadow-execution-terms-v1"
    bond_id: int
    isin: str | None
    secid: str | None
    expected_security_master_profile_id: int
    loaded_security_master_profile_id: int | None
    security_master_contract_version: str | None
    currency_state: EvidenceState | None
    currency_code: str | None
    nominal_state: EvidenceState | None
    nominal_value: Decimal | None
    lot_size_state: EvidenceState | None
    lot_size: int | None
    trading_board_state: EvidenceState | None
    trading_board: str | None
    status: Literal["READY", "UNAVAILABLE"]
    blockers: tuple[TermsBlocker, ...]
    pit_ready: Literal[False] = False


class ShadowExecutionTermsBatchView(ShadowContract):
    contract_version: Literal["shadow-execution-terms-batch-v1"] = "shadow-execution-terms-batch-v1"
    as_of_date: date
    market_source: Literal["moex"]
    strategy_selected_bond_ids: tuple[int, ...]
    ready_bond_ids: tuple[int, ...]
    unavailable_bond_ids: tuple[int, ...]
    selected_count: int
    ready_count: int
    unavailable_count: int
    terms: tuple[ShadowExecutionTermsView, ...]
    source_strategy_contract_version: Literal["portfolio-strategy-v1"] = "portfolio-strategy-v1"
    pit_ready: Literal[False] = False


class ShadowExecutionPositionView(ShadowContract):
    bond_id: int
    isin: str | None
    secid: str | None
    investment_rank: int
    target_weight: Decimal
    target_amount_rub: Decimal
    terms_status: Literal["READY", "UNAVAILABLE"]
    terms_blockers: tuple[TermsBlocker, ...]
    security_master_profile_id: int | None
    lot_size: int | None
    trading_board: str | None
    market_snapshot_id: int
    market_trade_date: date
    price_basis: PriceBasis
    clean_quote_pct: Decimal
    nominal_value: Decimal
    nkd_currency: Decimal
    clean_value_currency: Decimal
    dirty_value_currency: Decimal
    lot_dirty_value_rub: Decimal | None
    planned_lot_count: int
    planned_bond_quantity: int
    planned_cash_cost_rub: Decimal
    target_shortfall_rub: Decimal
    realized_shadow_weight: Decimal
    target_weight_gap: Decimal
    status: Literal["PLANNED", "TERMS_UNAVAILABLE", "TARGET_BELOW_ONE_LOT"]
    source_strategy_position: PortfolioStrategyPosition
    execution_terms: ShadowExecutionTermsView


class ShadowExecutionSummary(ShadowContract):
    capital_rub: Decimal
    strategy_selected_count: int
    strategy_target_invested_weight: Decimal
    strategy_target_invested_rub: Decimal
    terms_ready_count: int
    terms_unavailable_count: int
    planned_position_count: int
    zero_lot_position_count: int
    planned_total_lot_count: int
    planned_total_bond_quantity: int
    planned_shadow_invested_rub: Decimal
    shadow_cash_rub: Decimal
    realized_invested_weight: Decimal
    realized_cash_weight: Decimal
    execution_tracking_gap_rub: Decimal
    execution_tracking_gap_weight: Decimal
    target_amount_shortfall_rub: Decimal
    post_rounding_risk_status: Literal["PASS", "BLOCKED"]


class ShadowExecutionProvenance(ShadowContract):
    strategy_contract_version: Literal["portfolio-strategy-v1"] = "portfolio-strategy-v1"
    strategy_policy_version: Literal["portfolio-strategy-policy-v1"] = "portfolio-strategy-policy-v1"
    risk_policy_version: Literal["risk-engine-policy-v1"] = "risk-engine-policy-v1"
    execution_policy_version: Literal["shadow-execution-policy-v1"] = "shadow-execution-policy-v1"
    execution_terms_contract_version: Literal["shadow-execution-terms-v1"] = "shadow-execution-terms-v1"
    security_master_contract_version: Literal["bond-security-master-v2"] = "bond-security-master-v2"
    dirty_value_contract_version: Literal["bond-dv01-v1"] = "bond-dv01-v1"
    dirty_value_formula_versions: tuple[str, ...]
    execution_algorithm_version: Literal["FLOOR_TO_TARGET_INTEGER_LOTS_V1"] = "FLOOR_TO_TARGET_INTEGER_LOTS_V1"
    selected_bond_ids: tuple[int, ...]
    terms_ready_bond_ids: tuple[int, ...]
    planned_bond_ids: tuple[int, ...]
    zero_lot_bond_ids: tuple[int, ...]
    market_snapshot_ids: tuple[int, ...]
    security_master_profile_ids: tuple[int, ...]
    as_of_date: date
    market_source: Literal["moex"]
    capital_rub: Decimal


class ShadowExecutionCapabilities(ShadowContract):
    shadow_execution_planning_ready: Literal[True] = True
    verified_execution_terms_gate_ready: Literal[True] = True
    integer_lot_planning_ready: Literal[True] = True
    dirty_value_shadow_cash_basis_ready: Literal[True] = True
    residual_cash_accounting_ready: Literal[True] = True
    realized_shadow_weights_ready: Literal[True] = True
    post_rounding_risk_verification_ready: Literal[True] = True
    shadow_position_persistence_ready: Literal[False] = False
    shadow_ledger_ready: Literal[False] = False
    shadow_rebalance_ready: Literal[False] = False
    broker_execution_ready: Literal[False] = False
    sandbox_order_execution_ready: Literal[False] = False
    real_order_execution_ready: Literal[False] = False
    bid_ask_execution_model_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    realized_performance_ready: Literal[False] = False
    benchmark_comparison_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class ShadowExecutionPlanView(ShadowContract):
    contract_version: Literal["shadow-execution-plan-v1"] = "shadow-execution-plan-v1"
    status: Literal["READY", "PARTIAL", "UNEXECUTABLE", "EMPTY", "RISK_BLOCKED"]
    as_of_date: date
    market_source: Literal["moex"]
    policy: ShadowExecutionPolicyV1 = Field(default_factory=ShadowExecutionPolicyV1)
    positions: tuple[ShadowExecutionPositionView, ...]
    summary: ShadowExecutionSummary
    execution_terms_batch: ShadowExecutionTermsBatchView
    source_strategy: PortfolioStrategyView
    post_rounding_risk_evaluation: PortfolioRiskEvaluationView
    provenance: ShadowExecutionProvenance
    capabilities: ShadowExecutionCapabilities = Field(default_factory=ShadowExecutionCapabilities)
    quality_flags: tuple[Literal["EXECUTION_TERMS_UNAVAILABLE", "TARGET_BELOW_ONE_LOT", "POST_ROUNDING_RISK_BLOCKED"], ...]
    pit_ready: Literal[False] = False
