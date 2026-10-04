"""Frozen authorizable Shadow accounting contracts; no broker operations."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.shadow_execution import ShadowExecutionPlanView
from app.schemas.bond_dv01 import BondDv01View


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    pit_ready: Literal[False] = False


Blocker = Literal[
    "INPUT_INVALID", "GENESIS_NOT_READY", "GENESIS_RECONCILIATION_FAILED",
    "RUN_NOT_FOUND", "RUN_NOT_ACTIVE", "DAILY_SEQUENCE_GAP", "DATE_NOT_FORWARD",
    "HORIZON_EXCEEDED", "CASHFLOW_AMOUNT_MISSING", "CASHFLOW_EVIDENCE_INVALID",
    "UNKNOWN_CASHFLOW_EVENT", "CASHFLOW_DUPLICATE", "DIRTY_VALUE_UNAVAILABLE",
    "MARKET_EVIDENCE_INVALID", "PREVIOUS_NAV_ZERO", "HISTORICAL_SOURCE_DRIFT",
    "CURRENT_SHADOW_STATE_DRIFT", "AUTHORIZATION_MISMATCH", "PLAN_SHA_MISMATCH",
    "SESSION_NOT_FRESH", "APPLY_DIALECT_UNSUPPORTED", "LOCK_OR_TRANSACTION_ERROR",
    "PRE_COMMIT_AUDIT_FAILED", "POST_COMMIT_AUDIT_FAILED", "COMMIT_OUTCOME_UNKNOWN",
    "SHADOW_HISTORY_INVALID", "SOURCE_REFERENCE_MISSING",
    "ROLLBACK_FAILED",
]


class ShadowLedgerCapabilities(Contract):
    shadow_run_persistence_ready: Literal[True] = True
    shadow_genesis_plan_ready: Literal[True] = True
    shadow_genesis_apply_ready: Literal[True] = True
    append_only_shadow_ledger_ready: Literal[True] = True
    shadow_daily_mark_plan_ready: Literal[True] = True
    shadow_daily_mark_apply_ready: Literal[True] = True
    contractual_coupon_accounting_ready: Literal[True] = True
    contractual_amortization_accounting_ready: Literal[True] = True
    contractual_redemption_accounting_ready: Literal[True] = True
    immutable_daily_snapshot_ready: Literal[True] = True
    simple_shadow_nav_return_ready: Literal[True] = True
    daily_cycle_idempotency_ready: Literal[True] = True
    historical_source_drift_gate_ready: Literal[True] = True
    automatic_shadow_scheduler_ready: Literal[False] = False
    shadow_rebalance_ready: Literal[False] = False
    target_reoptimization_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    tax_model_ready: Literal[False] = False
    broker_execution_ready: Literal[False] = False
    sandbox_order_execution_ready: Literal[False] = False
    real_order_execution_ready: Literal[False] = False
    benchmark_comparison_ready: Literal[False] = False
    ofz_excess_return_ready: Literal[False] = False
    experiment_success_verdict_ready: Literal[False] = False
    cfa_comparison_ready: Literal[False] = False


class ShadowGenesisRequestV1(Contract):
    contract_version: Literal["shadow-genesis-request-v1"] = "shadow-genesis-request-v1"
    shadow_execution: ShadowExecutionPlanView
    source_code_sha: str


class LedgerEvent(Contract):
    event_key_sha256: str
    event_date: date
    sequence_number: int
    event_type: Literal["INITIAL_CAPITAL", "GENESIS_PURCHASE", "COUPON", "AMORTIZATION", "REDEMPTION"]
    bond_id: int | None
    quantity_delta: int
    cash_delta_rub: Decimal
    unit_amount_rub: Decimal | None
    source_market_snapshot_id: int | None = None
    source_cashflow_event_id: int | None = None
    source_contract_version: str
    source_fingerprint_sha256: str


class DailyPosition(Contract):
    bond_id: int
    position_status: Literal["ACTIVE", "REDEEMED"]
    quantity: int
    market_snapshot_id: int | None = None
    market_trade_date: date | None = None
    market_age_days: int | None = None
    price_basis: Literal["CLEAN_PRICE", "PRICE_FALLBACK"] | None = None
    dirty_value_rub_per_bond: Decimal | None = None
    market_value_rub: Decimal
    cashflow_rub_on_date: Decimal
    source_dv01_contract_version: str | None = None
    source_security_master_profile_id: int | None = None
    position_state_sha256: str = ""


class DailySnapshot(Contract):
    as_of_date: date
    previous_snapshot_sha256: str | None
    cash_rub: Decimal
    market_value_rub: Decimal
    nav_rub: Decimal
    daily_return: Decimal
    cumulative_return: Decimal
    active_position_count: int
    redeemed_position_count: int
    ledger_entry_count_to_date: int
    applied_cashflow_count_for_day: int
    input_state_sha256: str
    snapshot_sha256: str = ""
    return_formula_version: Literal["SHADOW_SIMPLE_NAV_RETURN_V1"] = "SHADOW_SIMPLE_NAV_RETURN_V1"


class Plan(Contract):
    status: Literal["EXECUTABLE", "BLOCKED", "IDEMPOTENT_NOOP"]
    run_key_sha256: str = ""
    as_of_date: date | None = None
    current_shadow_db_state_sha256: str = ""
    input_state_sha256: str = ""
    plan_sha256: str = ""
    events: tuple[LedgerEvent, ...] = ()
    positions: tuple[DailyPosition, ...] = ()
    snapshot: DailySnapshot | None = None
    valuation_evidence: tuple[BondDv01View, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    diagnostics: tuple[str, ...] = ()
    capabilities: ShadowLedgerCapabilities = Field(default_factory=ShadowLedgerCapabilities)


class ShadowGenesisPlanV1(Plan):
    contract_version: Literal["shadow-genesis-plan-v1"] = "shadow-genesis-plan-v1"
    shadow_execution_sha256: str = ""
    source_universe_sha256: str = ""
    source_code_sha: str = ""
    planned_end_date: date | None = None


class ShadowDailyPlanV1(Plan):
    contract_version: Literal["shadow-daily-plan-v1"] = "shadow-daily-plan-v1"


class Authorization(Contract):
    explicit_apply: Literal[True]
    plan_sha256: str
    run_key_sha256: str
    current_shadow_db_state_sha256: str
    input_state_sha256: str


class ShadowGenesisAuthorizationV1(Authorization):
    contract_version: Literal["shadow-genesis-authorization-v1"] = "shadow-genesis-authorization-v1"
    shadow_execution_sha256: str
    source_universe_sha256: str
    source_code_sha: str


class ShadowDailyAuthorizationV1(Authorization):
    contract_version: Literal["shadow-daily-authorization-v1"] = "shadow-daily-authorization-v1"


class ShadowAudit(Contract):
    status: Literal["VERIFIED", "FAILED"]
    run_key_sha256: str
    snapshot_sha256: str
    ledger_entry_count: int
    position_count: int
    blockers: tuple[Blocker, ...] = ()


class Receipt(Contract):
    status: Literal["APPLIED", "IDEMPOTENT_NOOP", "BLOCKED", "ROLLED_BACK", "ROLLBACK_FAILED", "COMMIT_OUTCOME_UNKNOWN", "POST_COMMIT_AUDIT_FAILED"]
    plan_sha256: str = ""
    run_key_sha256: str = ""
    attempted_rows: int = 0
    committed_rows: int | None = 0
    pre_commit_audit: ShadowAudit | None = None
    post_commit_audit: ShadowAudit | None = None
    blockers: tuple[Blocker, ...] = ()
    capabilities: ShadowLedgerCapabilities = Field(default_factory=ShadowLedgerCapabilities)


class ShadowGenesisApplyReceiptV1(Receipt):
    contract_version: Literal["shadow-genesis-apply-receipt-v1"] = "shadow-genesis-apply-receipt-v1"


class ShadowDailyApplyReceiptV1(Receipt):
    contract_version: Literal["shadow-daily-apply-receipt-v1"] = "shadow-daily-apply-receipt-v1"
