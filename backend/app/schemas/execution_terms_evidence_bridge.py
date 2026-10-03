"""Frozen evidence bridge contracts; implementation readiness is not APPLY permission."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.schemas.tinvest_bond_import_preflight import MoexBondImportBoardObservation


class BridgeContract(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    pit_ready: Literal[False] = False


Blocker = Literal["TARGET_BOND_NOT_FOUND", "TARGET_IDENTITY_MISMATCH", "FROZEN_ADMISSION_INVALID",
    "PREFLIGHT_INVALID", "TARGET_IDS_INVALID", "SOURCE_UID_MISSING", "SOURCE_UID_IDENTITY_MISMATCH", "SOURCE_KEY_NOT_REPRESENTABLE",
    "TQCB_SOURCE_UID_MISSING", "LOT_SIZE_NOT_AVAILABLE", "LOT_SIZE_INVALID", "LOT_SIZE_CONFLICT",
    "BOARD_SCAN_INCOMPLETE", "BOARD_OBSERVATION_MISSING", "BOARD_IDENTITY_CONFLICT",
    "CURRENT_LOT_CONFLICT", "CURRENT_BOARD_CONFLICT", "CURRENT_LINEAGE_CONFLICT", "SECURITY_MASTER_PROFILE_MISSING",
    "SECURITY_MASTER_PROFILE_INVALID", "CURRENT_DB_DRIFT", "SOURCE_HASH_MISMATCH", "PLAN_SHA_MISMATCH",
    "AUTHORIZATION_MISMATCH", "BOARD_OBSERVED_AT_MISSING", "BOARD_OBSERVED_AT_INVALID",
    "SESSION_NOT_FRESH", "APPLY_DIALECT_UNSUPPORTED", "LOCK_OR_TRANSACTION_ERROR",
    "POST_APPLY_VERIFICATION_FAILED", "EVIDENCE_OR_RESOLUTION_ERROR", "COMMIT_ERROR"]


class ExecutionLotEvidenceV1(BridgeContract):
    contract_version: Literal["execution-lot-evidence-v1"] = "execution-lot-evidence-v1"
    bond_id: int
    isin: str | None
    secid: str | None
    source_uid: str
    source_isin: str | None
    source_class_code: str | None
    source_lot: int | None
    api_trade_available: bool | None
    buy_available: bool | None
    availability_classification: str | None
    source_contract_version: Literal["tinvest-current-instrument-universe-v1"] = "tinvest-current-instrument-universe-v1"
    evidence_state: Literal["VERIFIED", "MISSING", "CONFLICT", "IDENTITY_MISMATCH", "BOARD_BINDING_MISSING", "INVALID"]


class ExecutionBoardEvidenceV1(BridgeContract):
    contract_version: Literal["execution-board-evidence-v1"] = "execution-board-evidence-v1"
    bond_id: int
    isin: str | None
    secid: str | None
    requested_board: str | None
    scan_status: Literal["COMPLETE", "INCOMPLETE", "SOURCE_ERROR"] | None
    matched_observation_count: int
    matched_observations: tuple[MoexBondImportBoardObservation, ...]
    board: Literal["TQCB"] | None
    evidence_state: Literal["VERIFIED", "MISSING", "CONFLICT", "INCOMPLETE"]


class ExecutionTermsAssertion(BridgeContract):
    field_name: Literal["lot_size", "trading_board"]
    source: Literal["tinvest_universe", "moex_universe"]
    assertion_type: Literal["scalar_value"] = "scalar_value"
    normalized_value: int | str
    source_key: str
    source_table: str
    observed_at: datetime | None
    raw_value: dict[str, str | int | bool | None]


class ExecutionTermsBridgeRow(BridgeContract):
    bond_id: int
    isin: str | None
    secid: str | None
    profile_id: int | None
    lot: int | None
    lot_evidence: tuple[ExecutionLotEvidenceV1, ...]
    board_evidence: ExecutionBoardEvidenceV1
    assertions: tuple[ExecutionTermsAssertion, ...]
    status: Literal["READY_TO_APPLY", "ALREADY_SATISFIED", "BLOCKED"]
    blockers: tuple[Blocker, ...]
    diagnostics: tuple[Literal["SOURCE_LOT_NOT_SUPPLIED", "OFF_BOARD_UID_NOT_USED"], ...] = ()


class ExecutionTermsBridgeCapabilities(BridgeContract):
    frozen_execution_terms_bridge_ready: Literal[True] = True
    frozen_tinvest_lot_evidence_ready: Literal[True] = True
    frozen_moex_board_evidence_ready: Literal[True] = True
    exact_execution_identity_ready: Literal[True] = True
    execution_terms_plan_ready: Literal[True] = True
    explicit_apply_authorization_ready: Literal[True] = True
    current_db_drift_gate_ready: Literal[True] = True
    atomic_execution_terms_apply_ready: Literal[True] = True
    execution_terms_idempotency_ready: Literal[True] = True
    post_apply_execution_terms_audit_ready: Literal[True] = True
    live_tinvest_required: Literal[False] = False
    live_moex_required: Literal[False] = False
    broker_write_surface: Literal[False] = False
    shadow_ledger_ready: Literal[False] = False
    shadow_started: Literal[False] = False


class ExecutionTermsBridgeProvenance(BridgeContract):
    bridge_version: Literal["execution-terms-evidence-bridge-v1"] = "execution-terms-evidence-bridge-v1"
    lot_source_contract: Literal["tinvest-current-instrument-universe-v1"] = "tinvest-current-instrument-universe-v1"
    frozen_contract: Literal["tinvest-frozen-admission-evidence-v1"] = "tinvest-frozen-admission-evidence-v1"
    identity_bridge_contract: Literal["tinvest-bond-identity-bridge-v1"] = "tinvest-bond-identity-bridge-v1"
    preflight_contract: Literal["tinvest-bond-import-preflight-v2"] = "tinvest-bond-import-preflight-v2"
    board_contract: Literal["moex-bond-import-board-evidence-v1"] = "moex-bond-import-board-evidence-v1"
    security_master_contract: Literal["bond-security-master-v2"] = "bond-security-master-v2"
    hashing: Literal["SORTED_CANONICAL_JSON_SHA256_V1"] = "SORTED_CANONICAL_JSON_SHA256_V1"
    live_tinvest_request: Literal[False] = False
    live_moex_request: Literal[False] = False
    exact_identity_only: Literal[True] = True
    lot_source: Literal["TINVEST_FROZEN_UNIVERSE"] = "TINVEST_FROZEN_UNIVERSE"
    board_source: Literal["MOEX_FROZEN_TQCB_OBSERVATION"] = "MOEX_FROZEN_TQCB_OBSERVATION"
    lot_size_fallback: Literal[False] = False
    fuzzy_matching: Literal[False] = False


class ExecutionTermsEvidenceBridgePlanV1(BridgeContract):
    contract_version: Literal["execution-terms-evidence-bridge-plan-v1"] = "execution-terms-evidence-bridge-plan-v1"
    status: Literal["EXECUTABLE", "BLOCKED"]
    target_bond_ids: tuple[int, ...] = ()
    target_bond_id_set_sha256: str | None = None
    frozen_admission_sha256: str | None = None
    import_preflight_sha256: str | None = None
    current_db_state_sha256: str | None = None
    board_observed_at: datetime | None = None
    rows: tuple[ExecutionTermsBridgeRow, ...] = ()
    ready_count: int = 0
    already_satisfied_count: int = 0
    blocked_count: int = 0
    blockers: tuple[Blocker, ...] = ()
    plan_sha256: str | None = None
    capabilities: ExecutionTermsBridgeCapabilities = Field(default_factory=ExecutionTermsBridgeCapabilities)
    provenance: ExecutionTermsBridgeProvenance = Field(default_factory=ExecutionTermsBridgeProvenance)


class ExecutionTermsEvidenceApplyAuthorizationV1(BridgeContract):
    contract_version: Literal["execution-terms-evidence-apply-authorization-v1"] = "execution-terms-evidence-apply-authorization-v1"
    explicit_apply: Literal[True]
    plan_sha256: str
    target_bond_id_set_sha256: str
    frozen_admission_sha256: str
    import_preflight_sha256: str
    current_db_state_sha256: str


class ExecutionTermsEvidenceAudit(BridgeContract):
    status: Literal["VERIFIED", "FAILED"]
    target_bond_ids: tuple[int, ...]
    verified_bond_ids: tuple[int, ...]
    lot_verified_count: int
    board_verified_count: int
    execution_terms_ready_count: int
    current_db_state_sha256: str
    blockers: tuple[Blocker, ...]


class ExecutionTermsEvidenceApplyReceipt(BridgeContract):
    contract_version: Literal["execution-terms-evidence-apply-receipt-v1"] = "execution-terms-evidence-apply-receipt-v1"
    status: Literal["APPLIED", "IDEMPOTENT_NOOP", "BLOCKED", "ROLLED_BACK", "COMMIT_OUTCOME_UNKNOWN", "POST_COMMIT_AUDIT_FAILED"]
    target_bond_ids: tuple[int, ...] = ()
    plan_sha256: str | None = None
    authorization: ExecutionTermsEvidenceApplyAuthorizationV1 | None = None
    authorization_sha256: str | None = None
    pre_state_sha256: str | None = None
    post_state_sha256: str | None = None
    attempted_rows: int = 0
    evidence_created: int = 0
    evidence_reused: int = 0
    committed_evidence_created: int | None = 0
    profiles_verified: int = 0
    lot_verified_count: int = 0
    board_verified_count: int = 0
    execution_terms_ready_count: int = 0
    commit_count: int | None = 0
    rollback_confirmed: bool = False
    db_mutated: bool | None = False
    pre_commit_audit: ExecutionTermsEvidenceAudit | None = None
    post_commit_audit: ExecutionTermsEvidenceAudit | None = None
    blockers: tuple[Blocker, ...] = ()
