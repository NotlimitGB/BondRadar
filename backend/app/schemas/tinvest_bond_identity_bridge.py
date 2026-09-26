"""Strict immutable contracts for the pure Task296 identity bridge."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


TINVEST_BOND_IDENTITY_BRIDGE_CONTRACT_VERSION = (
    "tinvest-bond-identity-bridge-v1"
)
TINVEST_BOND_IDENTITY_PROJECTION_CONTRACT_VERSION = "bond-identity-projection-v1"
TINVEST_CURRENT_UNIVERSE_CONTRACT_VERSION = "tinvest-current-instrument-universe-v1"


class TInvestBridgeAvailabilityClass(StrEnum):
    """Task295 availability categories, with an explicit Task296 unknown state."""

    API_BUY_AVAILABLE = "API_BUY_AVAILABLE"
    API_VISIBLE_NOT_BUYABLE = "API_VISIBLE_NOT_BUYABLE"
    API_TRADE_UNAVAILABLE = "API_TRADE_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class TInvestBondBridgeMatchState(StrEnum):
    MATCHED_EXACT_ISIN = "MATCHED_EXACT_ISIN"
    UNRESOLVED_NO_SOURCE_ISIN = "UNRESOLVED_NO_SOURCE_ISIN"
    UNRESOLVED_NO_INTERNAL_EXACT_ISIN = "UNRESOLVED_NO_INTERNAL_EXACT_ISIN"
    UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE = (
        "UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE"
    )
    CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS = (
        "CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS"
    )
    CONFLICT_NORMALIZED_ISIN_AMBIGUOUS = "CONFLICT_NORMALIZED_ISIN_AMBIGUOUS"


class TInvestUnmatchedReviewPriority(StrEnum):
    BUYABLE_NONQUAL_FLAG_FALSE = "BUYABLE_NONQUAL_FLAG_FALSE"
    BUYABLE_QUAL_RESTRICTED = "BUYABLE_QUAL_RESTRICTED"
    BUYABLE_QUAL_UNKNOWN = "BUYABLE_QUAL_UNKNOWN"
    NOT_CURRENTLY_BUYABLE = "NOT_CURRENTLY_BUYABLE"
    AVAILABILITY_UNKNOWN = "AVAILABILITY_UNKNOWN"


class TInvestAuditValueState(StrEnum):
    SOURCE_VALUE = "SOURCE_VALUE"
    NOT_SUPPLIED = "NOT_SUPPLIED"
    INVALID_SOURCE_VALUE = "INVALID_SOURCE_VALUE"


class TInvestUnmatchedAuditField(StrEnum):
    AVAILABILITY_CLASSIFICATION = "availability_classification"
    FOR_QUAL_INVESTOR = "for_qual_investor"
    REQUIRED_TESTS_STATE = "required_tests_state"
    CURRENCY = "currency"
    CLASS_CODE = "class_code"
    COUNTRY_OF_RISK = "countryOfRisk"
    COUNTRY_OF_RISK_NAME = "countryOfRiskName"
    SECTOR = "sector"
    BOND_TYPE = "bondType"
    EXCHANGE = "exchange"
    REAL_EXCHANGE = "realExchange"
    OTC_FLAG = "otcFlag"
    FOR_IIS_FLAG = "forIisFlag"


class TInvestBondIdentityBridgeErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    SOURCE_UID_CONFLICT = "SOURCE_UID_CONFLICT"
    INTERNAL_BOND_ID_CONFLICT = "INTERNAL_BOND_ID_CONFLICT"
    CORE_BOND_ID_CONFLICT = "CORE_BOND_ID_CONFLICT"
    CORE_BOND_ID_UNKNOWN = "CORE_BOND_ID_UNKNOWN"


class TInvestBondIdentityBridgeError(ValueError):
    """Typed, value-free input error for deterministic bridge failures."""

    def __init__(self, code: TInvestBondIdentityBridgeErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BondIdentityProjection(_StrictFrozenModel):
    """Minimal caller-provided Bond identity; it is not an ORM projection."""

    contract_version: Literal["bond-identity-projection-v1"] = (
        TINVEST_BOND_IDENTITY_PROJECTION_CONTRACT_VERSION
    )
    bond_id: int
    isin: str | None
    secid: str | None


class TInvestBondBridgeRow(_StrictFrozenModel):
    """One source UID and its exact-ISIN bridge evidence."""

    contract_version: Literal["tinvest-bond-identity-bridge-v1"] = (
        TINVEST_BOND_IDENTITY_BRIDGE_CONTRACT_VERSION
    )
    source_uid: str
    source_isin: str | None
    source_figi: str | None
    source_ticker: str | None
    source_class_code: str | None
    source_isin_group_size: int | None
    availability_classification: TInvestBridgeAvailabilityClass
    api_trade_available: bool | None
    buy_available: bool | None
    sell_available: bool | None
    for_qual_investor: bool | None
    required_tests: tuple[str, ...] | None
    required_tests_state: Literal["NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"]
    match_state: TInvestBondBridgeMatchState
    match_method: Literal["EXACT_ISIN"] | None
    bond_id: int | None
    bond_isin: str | None
    bond_secid: str | None
    is_ofz: bool | None
    normalized_only_candidate_bond_id: int | None


class TInvestBondUidAggregate(_StrictFrozenModel):
    """UID-level availability facts aggregated only after exact Bond matching."""

    bond_id: int
    bond_isin: str | None
    bond_secid: str | None
    is_ofz: bool
    matched_uid_count: int
    matched_uids: tuple[str, ...]
    api_buyable_uid_count: int
    api_buyable_uids: tuple[str, ...]
    has_api_buyable_uid: bool
    api_buyable_nonqual_flag_false_uid_count: int
    api_buyable_nonqual_flag_false_uids: tuple[str, ...]
    has_nonqual_flag_false_buyable_uid: bool
    api_buyable_qual_restricted_uid_count: int
    api_buyable_qual_restricted_uids: tuple[str, ...]
    has_qual_restricted_buyable_uid: bool


class TInvestBridgeMatchStateCount(_StrictFrozenModel):
    match_state: TInvestBondBridgeMatchState
    count: int
    source_uids: tuple[str, ...]


class TInvestBridgeReviewPriorityCount(_StrictFrozenModel):
    review_priority: TInvestUnmatchedReviewPriority
    count: int
    source_uids: tuple[str, ...]


class TInvestUnmatchedMetadataValue(_StrictFrozenModel):
    state: TInvestAuditValueState
    value: str | bool | None
    count: int
    source_uids: tuple[str, ...]


class TInvestUnmatchedAuditBreakdown(_StrictFrozenModel):
    """One deterministic metadata dimension, grouped without raw bad values."""

    field: TInvestUnmatchedAuditField
    entries: tuple[TInvestUnmatchedMetadataValue, ...]


class TInvestUnmatchedBondEvidence(_StrictFrozenModel):
    source_uid: str
    source_isin: str | None
    source_figi: str | None
    source_ticker: str | None
    source_class_code: str | None
    source_isin_group_size: int | None
    match_state: TInvestBondBridgeMatchState
    review_priority: TInvestUnmatchedReviewPriority
    availability_classification: TInvestBridgeAvailabilityClass
    api_trade_available: bool | None
    buy_available: bool | None
    sell_available: bool | None
    for_qual_investor: bool | None
    required_tests: tuple[str, ...] | None
    required_tests_state: Literal["NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"]


class TInvestBondIdentityBridgeCoverage(_StrictFrozenModel):
    """Current UID and unique-Bond counts; percentages use unique Bond IDs."""

    source_bond_uid_count: int
    source_api_buyable_uid_count: int
    matched_source_uid_count: int
    matched_unique_bond_count: int
    matched_non_ofz_unique_bond_count: int
    matched_ofz_unique_bond_count: int
    matched_api_buyable_unique_bond_count: int
    matched_api_buyable_nonqual_flag_false_unique_bond_count: int
    matched_core_m3_complete_count: int
    matched_api_buyable_core_m3_complete_count: int
    matched_api_buyable_nonqual_flag_false_core_m3_complete_count: int
    api_buyable_core_coverage_pct: Decimal
    api_buyable_nonqual_flag_false_core_coverage_pct: Decimal

    unmatched_uid_count: int
    unmatched_api_buyable_uid_count: int
    unmatched_api_buyable_nonqual_flag_false_uid_count: int
    unmatched_api_buyable_qual_restricted_uid_count: int
    unmatched_visible_not_buyable_uid_count: int
    unmatched_api_trade_unavailable_uid_count: int
    unmatched_unknown_availability_uid_count: int


class TInvestBondIdentityBridgeProvenance(_StrictFrozenModel):
    source_contract_version: Literal["tinvest-current-instrument-universe-v1"] = (
        TINVEST_CURRENT_UNIVERSE_CONTRACT_VERSION
    )
    internal_projection_contract_version: Literal["bond-identity-projection-v1"] = (
        TINVEST_BOND_IDENTITY_PROJECTION_CONTRACT_VERSION
    )
    core_m3_definition: Literal["CORE_M3_COMPLETE_V1"] = "CORE_M3_COMPLETE_V1"
    source_uid_count: int
    internal_bond_count: int
    core_m3_complete_bond_id_count: int
    source_duplicate_isin_group_count: int
    source_duplicate_isin_row_count: int
    exact_isin_match_method: Literal["EXACT_ISIN"] = "EXACT_ISIN"
    normalized_only_audit_method: Literal["STRIP_UPPER_ONLY_NOT_AUTOMATIC"] = (
        "STRIP_UPPER_ONLY_NOT_AUTOMATIC"
    )
    coverage_percentage_method: Literal["TASK283_DECIMAL_RATIO_V1"] = (
        "TASK283_DECIMAL_RATIO_V1"
    )
    hash_canonicalization: Literal["SORTED_CANONICAL_JSON_SHA256_V1"] = (
        "SORTED_CANONICAL_JSON_SHA256_V1"
    )
    bridge_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unmatched_uid_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    matched_uid_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    matched_unique_bond_id_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TInvestBondIdentityBridgeCapabilities(_StrictFrozenModel):
    tinvest_bond_identity_bridge_ready: Literal[True] = True
    exact_isin_auto_match_ready: Literal[True] = True
    one_bond_to_many_tinvest_uids_ready: Literal[True] = True
    duplicate_source_isin_allowed: Literal[True] = True
    duplicate_uid_fail_closed: Literal[True] = True
    actionable_coverage_rebase_ready: Literal[True] = True
    bond_uid_aggregation_ready: Literal[True] = True
    uid_counts_separate_from_bond_counts: Literal[True] = True
    qualification_dimension_preserved: Literal[True] = True
    unmatched_audit_ready: Literal[True] = True
    optional_source_metadata_audit_ready: Literal[True] = True
    deterministic_hashes_ready: Literal[True] = True
    bridge_row_set_hash_ready: Literal[True] = True
    unmatched_set_hash_ready: Literal[True] = True
    current_tinvest_universe: Literal[True] = True
    ticker_only_match: Literal[False] = False
    name_match: Literal[False] = False
    fuzzy_match: Literal[False] = False
    figi_only_auto_match: Literal[False] = False
    normalized_only_auto_match: Literal[False] = False
    personal_qualification_resolved: Literal[False] = False
    unmatched_auto_import: Literal[False] = False
    historical_universe_ready: Literal[False] = False
    tinvest_universe_pit_ready: Literal[False] = False
    database_persistence: Literal[False] = False
    database_migration: Literal[False] = False
    network_access: Literal[False] = False
    broker_write_surface: Literal[False] = False
    m3_feature_logic_changed: Literal[False] = False
    moex_logic_changed: Literal[False] = False
    ofz_curve_changed: Literal[False] = False
    strategy_changed: Literal[False] = False
    risk_engine_changed: Literal[False] = False
    pit_ready: Literal[False] = False


class TInvestBondIdentityBridgeView(_StrictFrozenModel):
    contract_version: Literal["tinvest-bond-identity-bridge-v1"] = (
        TINVEST_BOND_IDENTITY_BRIDGE_CONTRACT_VERSION
    )
    bridge_rows: tuple[TInvestBondBridgeRow, ...]
    bond_aggregates: tuple[TInvestBondUidAggregate, ...]
    unmatched_rows: tuple[TInvestUnmatchedBondEvidence, ...]
    unmatched_match_state_counts: tuple[TInvestBridgeMatchStateCount, ...]
    unmatched_review_priority_counts: tuple[TInvestBridgeReviewPriorityCount, ...]
    unmatched_metadata_breakdowns: tuple[TInvestUnmatchedAuditBreakdown, ...]
    source_duplicate_isin_group_count: int
    source_duplicate_isin_row_count: int
    coverage: TInvestBondIdentityBridgeCoverage
    provenance: TInvestBondIdentityBridgeProvenance
    capabilities: TInvestBondIdentityBridgeCapabilities = Field(
        default_factory=TInvestBondIdentityBridgeCapabilities
    )
    pit_ready: Literal[False] = False
