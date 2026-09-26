"""Strict immutable contracts for the pure Task296B admission manifest."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tinvest_bond_identity_bridge import (
    TInvestBondBridgeMatchState,
    TInvestBridgeAvailabilityClass,
    TInvestBondIdentityBridgeView,
)


TINVEST_BOND_ADMISSION_MANIFEST_VERSION = "tinvest-bond-admission-manifest-v1"
MOEX_BOND_RESOLUTION_PROJECTION_VERSION = "moex-bond-resolution-projection-v1"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class MoexSecurityMatchStatus(StrEnum):
    EXACT_ISIN_RECOVERED = "EXACT_ISIN_RECOVERED"
    SECURITY_NOT_FOUND = "SECURITY_NOT_FOUND"
    SECURITY_AMBIGUOUS = "SECURITY_AMBIGUOUS"
    SECURITY_IDENTIFIER_CONFLICT = "SECURITY_IDENTIFIER_CONFLICT"
    SOURCE_ERROR = "SOURCE_ERROR"


class TInvestAdmissionState(StrEnum):
    EXISTING_MATCHED = "EXISTING_MATCHED"
    IMPORT_CANDIDATE_CURRENT_PIPELINE = "IMPORT_CANDIDATE_CURRENT_PIPELINE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    MOEX_NOT_RESOLVED = "MOEX_NOT_RESOLVED"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"


class TInvestAdmissionReason(StrEnum):
    ALREADY_PRESENT = "ALREADY_PRESENT"
    MOEX_EXACT_ISIN_RECOVERED = "MOEX_EXACT_ISIN_RECOVERED"
    MOEX_NOT_FOUND = "MOEX_NOT_FOUND"
    MOEX_AMBIGUOUS = "MOEX_AMBIGUOUS"
    MOEX_SOURCE_ERROR = "MOEX_SOURCE_ERROR"
    MOEX_IDENTIFIER_CONFLICT = "MOEX_IDENTIFIER_CONFLICT"
    CANONICAL_OFZ = "CANONICAL_OFZ"
    SOURCE_SECTOR_GOVERNMENT = "SOURCE_SECTOR_GOVERNMENT"
    SOURCE_SECTOR_MUNICIPAL = "SOURCE_SECTOR_MUNICIPAL"
    API_NOT_BUYABLE = "API_NOT_BUYABLE"
    API_AVAILABILITY_UNKNOWN = "API_AVAILABILITY_UNKNOWN"
    QUAL_RESTRICTED = "QUAL_RESTRICTED"
    QUAL_UNKNOWN = "QUAL_UNKNOWN"
    CURRENCY_NOT_CURRENTLY_SUPPORTED = "CURRENCY_NOT_CURRENTLY_SUPPORTED"
    PRIMARY_BOARD_NOT_CURRENTLY_SUPPORTED = "PRIMARY_BOARD_NOT_CURRENTLY_SUPPORTED"
    REPLACED_BOND_REVIEW = "REPLACED_BOND_REVIEW"
    SOURCE_SECTOR_MISSING = "SOURCE_SECTOR_MISSING"
    SOURCE_COUNTRY_MISSING = "SOURCE_COUNTRY_MISSING"
    SOURCE_BOND_TYPE_MISSING = "SOURCE_BOND_TYPE_MISSING"
    MOEX_PRIMARY_BOARD_MISSING = "MOEX_PRIMARY_BOARD_MISSING"
    SOURCE_CLASSIFICATION_CONFLICT = "SOURCE_CLASSIFICATION_CONFLICT"
    SOURCE_ISIN_MISSING = "SOURCE_ISIN_MISSING"
    MOEX_SECID_MISSING = "MOEX_SECID_MISSING"
    INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE = (
        "INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE"
    )
    INTERNAL_IDENTITY_AMBIGUOUS = "INTERNAL_IDENTITY_AMBIGUOUS"


class TInvestAdmissionMetadataState(StrEnum):
    SOURCE_VALUE = "SOURCE_VALUE"
    NOT_SUPPLIED = "NOT_SUPPLIED"
    INVALID_SOURCE_VALUE = "INVALID_SOURCE_VALUE"


class TInvestAdmissionBoardSource(StrEnum):
    TASK295_CLASS_CODE = "TASK295_CLASS_CODE"
    MOEX_PRIMARY_BOARD = "MOEX_PRIMARY_BOARD"


class TInvestAdmissionErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    IDENTITY_BRIDGE_INVALID = "IDENTITY_BRIDGE_INVALID"
    MOEX_RESOLUTION_MISSING = "MOEX_RESOLUTION_MISSING"
    MOEX_RESOLUTION_CONFLICT = "MOEX_RESOLUTION_CONFLICT"


class TInvestBondAdmissionManifestError(ValueError):
    """Sanitized, typed error for invalid evidence envelopes."""

    def __init__(self, code: TInvestAdmissionErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class MoexBondResolutionProjection(_StrictFrozenModel):
    """Caller-supplied, frozen MOEX identity result for one source ISIN."""

    contract_version: Literal["moex-bond-resolution-projection-v1"] = (
        MOEX_BOND_RESOLUTION_PROJECTION_VERSION
    )
    source_isin: str
    security_match_status: MoexSecurityMatchStatus
    matched_secid: str | None = None
    matched_isin: str | None = None
    candidate_count: int
    matched_candidate_count: int
    primary_board: str | None = None
    issuer_metadata_status: str | None = None
    issuer_id: str | None = None
    issuer_title: str | None = None
    issuer_inn: str | None = None
    issuer_okpo: str | None = None


class TInvestAdmissionUidRow(_StrictFrozenModel):
    """Admission facts for one T-Invest UID; UID evidence is never collapsed."""

    source_uid: str
    source_isin: str | None
    task296_match_state: TInvestBondBridgeMatchState
    admission_state: TInvestAdmissionState
    reason_codes: tuple[TInvestAdmissionReason, ...]

    matched_bond_id: int | None = None
    normalized_only_candidate_bond_id: int | None = None
    core_m3_complete: bool = False

    availability_classification: TInvestBridgeAvailabilityClass
    api_trade_available: bool | None
    buy_available: bool | None
    sell_available: bool | None
    for_qual_investor: bool | None
    required_tests: tuple[str, ...] | None
    required_tests_state: Literal["NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"]

    moex_security_match_status: MoexSecurityMatchStatus | None = None
    matched_secid: str | None = None
    matched_isin: str | None = None
    primary_board_state: TInvestAdmissionMetadataState
    primary_board: str | None
    pipeline_board_source: TInvestAdmissionBoardSource | None
    pipeline_board_state: TInvestAdmissionMetadataState
    pipeline_board: str | None
    issuer_metadata_status: str | None
    issuer_id: str | None
    issuer_title: str | None
    issuer_inn: str | None
    issuer_okpo: str | None

    canonical_ofz: bool | None
    source_sector_state: TInvestAdmissionMetadataState
    source_sector: str | None
    country_of_risk_state: TInvestAdmissionMetadataState
    country_of_risk: str | None
    currency_state: TInvestAdmissionMetadataState
    currency: str | None
    bond_type_state: TInvestAdmissionMetadataState
    bond_type: str | None
    class_code_state: TInvestAdmissionMetadataState
    class_code: str | None
    exchange_state: TInvestAdmissionMetadataState
    exchange: str | None
    real_exchange_state: TInvestAdmissionMetadataState
    real_exchange: str | None
    no_government_evidence: bool | None


class TInvestBondAdmissionIsinAggregate(_StrictFrozenModel):
    """Deterministic aggregate for all source UIDs sharing one exact ISIN."""

    isin: str
    source_uids: tuple[str, ...]
    uid_count: int
    moex_security_match_status: MoexSecurityMatchStatus
    matched_secid: str | None
    matched_isin: str | None
    primary_board_state: TInvestAdmissionMetadataState
    primary_board: str | None
    canonical_ofz: bool | None
    source_sector_state: TInvestAdmissionMetadataState
    source_sector: str | None
    currency_state: TInvestAdmissionMetadataState
    currency: str | None
    bond_type_state: TInvestAdmissionMetadataState
    bond_type: str | None
    any_api_buyable_uid: bool
    any_nonqual_flag_false_buyable_uid: bool
    any_qual_restricted_buyable_uid: bool
    admission_state: TInvestAdmissionState
    reason_codes: tuple[TInvestAdmissionReason, ...]


class TInvestExistingBondAdmissionAggregate(_StrictFrozenModel):
    bond_id: int
    isin: str | None
    secid: str | None
    pipeline_board_source: Literal["TASK295_CLASS_CODE"] = "TASK295_CLASS_CODE"
    pipeline_board_state: TInvestAdmissionMetadataState
    pipeline_board: str | None
    matched_uids: tuple[str, ...]
    core_m3_complete: bool
    canonical_ofz: bool
    has_government_evidence: bool
    has_municipal_evidence: bool
    has_api_buyable_uid: bool
    has_nonqual_flag_false_buyable_uid: bool
    api_buyable_non_government: bool
    current_pipeline_compatible: bool
    current_pipeline_missing_bond_type_evidence: bool


class TInvestAdmissionCoverage(_StrictFrozenModel):
    source_uid_count: int
    existing_matched_uid_count: int
    unmatched_uid_count: int
    existing_unique_bond_count: int
    import_candidate_unique_isin_count: int
    import_candidate_uid_count: int
    review_required_unique_isin_count: int
    moex_not_resolved_unique_isin_count: int
    identity_conflict_unique_isin_count: int
    current_import_gap_unique_isin_count: int
    post_import_addressable_universe_size: int

    existing_api_buyable_non_government_unique_bond_count: int
    existing_api_buyable_non_government_core_m3_complete_count: int
    existing_api_buyable_non_government_core_coverage_pct: Decimal

    existing_current_pipeline_compatible_unique_bond_count: int
    existing_current_pipeline_compatible_core_m3_complete_count: int
    existing_current_pipeline_compatible_core_coverage_pct: Decimal
    existing_current_pipeline_missing_bond_type_evidence_count: int


class TInvestAdmissionReasonBreakdown(_StrictFrozenModel):
    reason_code: TInvestAdmissionReason
    count: int
    source_uids: tuple[str, ...]
    isins: tuple[str, ...]


class TInvestAdmissionMetadataValue(_StrictFrozenModel):
    state: TInvestAdmissionMetadataState
    value: str | None
    count: int
    source_uids: tuple[str, ...]


class TInvestAdmissionMetadataBreakdown(_StrictFrozenModel):
    field: Literal[
        "sector",
        "countryOfRisk",
        "countryOfRiskName",
        "bondType",
        "exchange",
        "realExchange",
        "class_code",
        "currency",
        "primary_board",
    ]
    entries: tuple[TInvestAdmissionMetadataValue, ...]


class TInvestAdmissionManifestProvenance(_StrictFrozenModel):
    source_contract_version: Literal["tinvest-current-instrument-universe-v1"]
    identity_bridge_contract_version: Literal["tinvest-bond-identity-bridge-v1"]
    internal_projection_contract_version: Literal["bond-identity-projection-v1"]
    core_m3_definition: Literal["CORE_M3_COMPLETE_V1"] = "CORE_M3_COMPLETE_V1"
    exact_isin_match_method: Literal["EXACT_ISIN"] = "EXACT_ISIN"
    normalized_only_method: Literal["STRIP_UPPER_AUDIT_ONLY"] = (
        "STRIP_UPPER_AUDIT_ONLY"
    )
    current_pipeline_currency: Literal["rub"] = "rub"
    existing_primary_board_source: Literal["TASK295_CLASS_CODE"] = "TASK295_CLASS_CODE"
    unmatched_primary_board_source: Literal["MOEX_PRIMARY_BOARD"] = "MOEX_PRIMARY_BOARD"
    source_uid_count: int
    internal_bond_count: int
    core_m3_complete_bond_id_count: int
    moex_resolution_count: int
    exact_moex_resolution_count: int
    admission_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    unmatched_isin_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    import_candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_required_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    moex_not_resolved_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TInvestBondAdmissionManifestCapabilities(_StrictFrozenModel):
    current_bond_admission_manifest_ready: Literal[True] = True
    exact_moex_identity_required_for_new_import: Literal[True] = True
    existing_matched_state_ready: Literal[True] = True
    import_candidate_state_ready: Literal[True] = True
    review_required_state_ready: Literal[True] = True
    moex_not_resolved_state_ready: Literal[True] = True
    identity_conflict_state_ready: Literal[True] = True
    source_government_review_ready: Literal[True] = True
    source_municipal_review_ready: Literal[True] = True
    blank_metadata_hygiene_ready: Literal[True] = True
    current_pipeline_rub_only: Literal[True] = True
    current_pipeline_tqcb_only: Literal[True] = True
    current_pipeline_currency: Literal["RUB"] = "RUB"
    current_pipeline_primary_board: Literal["TQCB"] = "TQCB"
    actionable_non_government_coverage_ready: Literal[True] = True
    current_pipeline_coverage_ready: Literal[True] = True
    ofz_excluded_from_non_government_denominator: Literal[True] = True
    import_candidate_manifest_ready: Literal[True] = True
    review_manifest_ready: Literal[True] = True
    moex_not_resolved_manifest_ready: Literal[True] = True
    admission_row_set_hash_ready: Literal[True] = True
    import_candidate_set_hash_ready: Literal[True] = True
    canonical_ofz_contract_changed: Literal[False] = False
    non_ofz_means_corporate: Literal[False] = False
    government_name_inference: Literal[False] = False
    municipal_name_inference: Literal[False] = False
    replaced_bond_auto_admission: Literal[False] = False
    personal_qualification_resolved: Literal[False] = False
    unmatched_auto_import: Literal[False] = False
    database_persistence: Literal[False] = False
    database_migration: Literal[False] = False
    network_access: Literal[False] = False
    live_tinvest_request: Literal[False] = False
    live_moex_request: Literal[False] = False
    m3_feature_logic_changed: Literal[False] = False
    ofz_curve_changed: Literal[False] = False
    broker_write_surface: Literal[False] = False
    shadow_started: Literal[False] = False
    pit_ready: Literal[False] = False


class TInvestBondAdmissionManifestView(_StrictFrozenModel):
    contract_version: Literal["tinvest-bond-admission-manifest-v1"] = (
        TINVEST_BOND_ADMISSION_MANIFEST_VERSION
    )
    source_universe_contract_version: Literal["tinvest-current-instrument-universe-v1"]
    identity_bridge: TInvestBondIdentityBridgeView
    uid_admissions: tuple[TInvestAdmissionUidRow, ...]
    unmatched_isin_aggregates: tuple[TInvestBondAdmissionIsinAggregate, ...]
    existing_bond_aggregates: tuple[TInvestExistingBondAdmissionAggregate, ...]
    import_candidate_manifest: tuple[TInvestBondAdmissionIsinAggregate, ...]
    review_manifest: tuple[TInvestBondAdmissionIsinAggregate, ...]
    moex_not_resolved_manifest: tuple[TInvestBondAdmissionIsinAggregate, ...]
    identity_conflict_manifest: tuple[TInvestBondAdmissionIsinAggregate, ...]
    reason_breakdown: tuple[TInvestAdmissionReasonBreakdown, ...]
    metadata_breakdowns: tuple[TInvestAdmissionMetadataBreakdown, ...]
    coverage: TInvestAdmissionCoverage
    provenance: TInvestAdmissionManifestProvenance
    capabilities: TInvestBondAdmissionManifestCapabilities = Field(
        default_factory=TInvestBondAdmissionManifestCapabilities
    )
    pit_ready: Literal[False] = False
