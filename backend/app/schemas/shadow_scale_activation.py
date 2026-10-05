"""Frozen group activation authorization and type-stable receipts."""
from datetime import date
from decimal import Decimal
from typing import Literal
from pydantic import Field, field_validator
from app.schemas.shadow_experiment import Contract
from app.schemas.shadow_scale_experiment import ShadowScaleExperimentGenesisPlanV1

Blocker = Literal[
    "INPUT_INVALID", "SCALE_GENESIS_NOT_READY", "CHILD_GENESIS_PLAN_MISMATCH",
    "SOURCE_REFERENCE_MISSING", "PARTIAL_SCALE_ACTIVATION_STATE", "ORPHAN_CHILD_RUN_CONFLICT",
    "HISTORICAL_SCALE_STATE_DRIFT", "AUTHORIZATION_MISMATCH", "PLAN_SHA_MISMATCH",
    "CURRENT_SCALE_STATE_DRIFT", "SESSION_NOT_FRESH", "APPLY_DIALECT_UNSUPPORTED",
    "LOCK_OR_TRANSACTION_ERROR", "PRE_COMMIT_AUDIT_FAILED", "POST_COMMIT_AUDIT_FAILED",
    "COMMIT_OUTCOME_UNKNOWN", "ROLLBACK_FAILED", "BENCHMARK_PERSISTENCE_INVALID",
]

class ShadowScaleActivationCapabilitiesV1(Contract):
    scale_experiment_persistence_ready: Literal[True] = True
    scale_experiment_activation_ready: Literal[True] = True
    experiment_group_persistence_ready: Literal[True] = True
    experiment_case_persistence_ready: Literal[True] = True
    child_shadow_run_atomic_activation_ready: Literal[True] = True
    benchmark_genesis_persistence_ready: Literal[True] = True
    benchmark_component_provenance_ready: Literal[True] = True
    grouped_activation_plan_ready: Literal[True] = True
    grouped_explicit_authorization_ready: Literal[True] = True
    grouped_atomic_apply_ready: Literal[True] = True
    grouped_activation_idempotency_ready: Literal[True] = True
    grouped_activation_drift_gate_ready: Literal[True] = True
    grouped_post_commit_audit_ready: Literal[True] = True
    official_day0_started: Literal[False] = False
    grouped_daily_cycle_ready: Literal[False] = False
    grouped_daily_atomic_apply_ready: Literal[False] = False
    benchmark_daily_persistence_ready: Literal[False] = False
    scale_comparison_persistence_ready: Literal[False] = False
    automatic_daily_scheduler_ready: Literal[False] = False
    strategy_rebalance_ready: Literal[False] = False
    benchmark_rebalance_ready: Literal[False] = False
    broker_execution_ready: Literal[False] = False
    optimal_capital_claim_ready: Literal[False] = False
    monthly_income_claim_ready: Literal[False] = False
    after_tax_profit_ready: Literal[False] = False

    @field_validator('*', mode='before')
    @classmethod
    def exact_boolean(cls, value):
        if type(value) is not bool:
            raise ValueError("INPUT_INVALID")
        return value


class ShadowScaleActivationRequestV1(Contract):
    contract_version: Literal["shadow-scale-activation-request-v1"] = "shadow-scale-activation-request-v1"
    scale_genesis: ShadowScaleExperimentGenesisPlanV1


class ShadowScaleActivationCasePlanV1(Contract):
    ordinal: int
    capital_rub: Decimal
    case_sha256: str
    experiment_genesis_sha256: str
    strategy_genesis_plan_sha256: str
    shadow_execution_sha256: str
    benchmark_genesis_sha256: str
    child_run_key_sha256: str
    child_genesis_plan_sha256: str
    child_current_db_state_sha256: str
    child_status: Literal["EXECUTABLE", "IDEMPOTENT_NOOP", "BLOCKED"]
    benchmark_component_count: int
    blockers: tuple[Blocker, ...] = ()


class ShadowScaleActivationPlanV1(Contract):
    contract_version: Literal["shadow-scale-activation-plan-v1"] = "shadow-scale-activation-plan-v1"
    status: Literal["EXECUTABLE", "IDEMPOTENT_NOOP", "BLOCKED"]
    group_key_sha256: str | None = None
    scale_genesis_sha256: str | None = None
    scale_policy_sha256: str | None = None
    experiment_policy_sha256: str | None = None
    genesis_date: date | None = None
    planned_end_date: date | None = None
    common_market_trade_date: date | None = None
    source_code_sha: str | None = None
    source_universe_sha256: str | None = None
    investment_batch_sha256: str | None = None
    capital_grid_rub: tuple[Decimal, ...] = ()
    cases: tuple[ShadowScaleActivationCasePlanV1, ...] = ()
    current_db_state_sha256: str | None = None
    input_state_sha256: str | None = None
    plan_sha256: str | None = None
    blockers: tuple[Blocker, ...] = ()
    capabilities: ShadowScaleActivationCapabilitiesV1 = Field(default_factory=ShadowScaleActivationCapabilitiesV1)


class ShadowScaleActivationAuthorizationV1(Contract):
    contract_version: Literal["shadow-scale-activation-authorization-v1"] = "shadow-scale-activation-authorization-v1"
    explicit_apply: Literal[True]
    plan_sha256: str
    group_key_sha256: str
    scale_genesis_sha256: str
    current_db_state_sha256: str
    input_state_sha256: str
    case_sha256s: tuple[str, ...]
    child_run_key_sha256s: tuple[str, ...]
    child_genesis_plan_sha256s: tuple[str, ...]
    benchmark_genesis_sha256s: tuple[str, ...]

    @field_validator('explicit_apply', mode='before')
    @classmethod
    def explicit_boolean(cls, value):
        if value is not True:
            raise ValueError("AUTHORIZATION_MISMATCH")
        return value


class ShadowScaleActivationAuditV1(Contract):
    status: Literal["VERIFIED", "FAILED"]
    group_id: int | None = None
    case_ids: tuple[int, ...] = ()
    child_run_ids: tuple[int, ...] = ()
    benchmark_ids: tuple[int, ...] = ()
    benchmark_component_count: int = 0
    verified_child_count: int = 0
    blockers: tuple[Blocker, ...] = ()


class ShadowScaleActivationReceiptV1(Contract):
    contract_version: Literal["shadow-scale-activation-receipt-v1"] = "shadow-scale-activation-receipt-v1"
    status: Literal["APPLIED", "IDEMPOTENT_NOOP", "BLOCKED", "ROLLED_BACK", "COMMIT_OUTCOME_UNKNOWN", "POST_COMMIT_AUDIT_FAILED"]
    group_key_sha256: str | None = None
    plan_sha256: str | None = None
    group_id: int | None = None
    case_ids: tuple[int, ...] = ()
    child_run_ids: tuple[int, ...] = ()
    benchmark_ids: tuple[int, ...] = ()
    planned_group_count: int = 0
    planned_case_count: int = 0
    planned_child_run_count: int = 0
    planned_benchmark_count: int = 0
    planned_component_count: int = 0
    attempted_mutation_count: int = 0
    committed_mutation_count: int | None = 0
    committed_group_count: int | None = 0
    committed_case_count: int | None = 0
    committed_child_run_count: int | None = 0
    committed_benchmark_count: int | None = 0
    committed_component_count: int | None = 0
    commit_count: int | None = 0
    rollback_confirmed: bool = False
    pre_commit_audit: ShadowScaleActivationAuditV1 | None = None
    post_commit_audit: ShadowScaleActivationAuditV1 | None = None
    blockers: tuple[Blocker, ...] = ()
    capabilities: ShadowScaleActivationCapabilitiesV1 = Field(default_factory=ShadowScaleActivationCapabilitiesV1)
