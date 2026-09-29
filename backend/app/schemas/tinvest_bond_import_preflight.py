"""Frozen, strict contracts for the read-only Task296C import preflight.

These models describe evidence and a possible future import manifest.  They do
not authorize or perform persistence.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tinvest_bond_admission_manifest import (
    TInvestAdmissionReason,
    TInvestAdmissionUidRow,
)
from app.schemas.tinvest_bond_identity_bridge import TInvestBridgeAvailabilityClass


TINVEST_BOND_IMPORT_PREFLIGHT_VERSION = "tinvest-bond-import-preflight-v1"
MOEX_BOND_IMPORT_DESCRIPTION_VERSION = "moex-bond-import-description-v1"
COMPANY_IDENTITY_PROJECTION_VERSION = "company-identity-projection-v1"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class BondImportPreflightState(StrEnum):
    READY_FOR_CONTROLLED_IMPORT = "READY_FOR_CONTROLLED_IMPORT"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ALREADY_IMPORTED_OR_COLLISION = "ALREADY_IMPORTED_OR_COLLISION"
    IDENTITY_CONFLICT = "IDENTITY_CONFLICT"


class BondImportPreflightReason(StrEnum):
    MOEX_DESCRIPTION_EXACT_IDENTITY = "MOEX_DESCRIPTION_EXACT_IDENTITY"
    DESCRIPTION_MISSING = "DESCRIPTION_MISSING"
    DESCRIPTION_CONFLICT = "DESCRIPTION_CONFLICT"
    SECURITY_IDENTITY_CONFLICT = "SECURITY_IDENTITY_CONFLICT"
    BOND_NAME_MISSING = "BOND_NAME_MISSING"
    BOARD_CONFLICT = "BOARD_CONFLICT"
    BOARD_OBSERVATION_MISSING = "BOARD_OBSERVATION_MISSING"
    NOMINAL_CURRENCY_CONFLICT = "NOMINAL_CURRENCY_CONFLICT"
    ALREADY_IMPORTED_OR_COLLISION = "ALREADY_IMPORTED_OR_COLLISION"
    INTERNAL_IDENTITY_CONFLICT = "INTERNAL_IDENTITY_CONFLICT"
    ISSUER_NAME_MISSING = "ISSUER_NAME_MISSING"
    ISSUER_INN_MISSING_OR_INVALID = "ISSUER_INN_MISSING_OR_INVALID"
    ISSUER_IDENTITY_READY = "ISSUER_IDENTITY_READY"
    COMPANY_IDENTITY_CONFLICT = "COMPANY_IDENTITY_CONFLICT"
    COMPANY_EXISTING_BY_INN = "COMPANY_EXISTING_BY_INN"
    COMPANY_EXISTING_BY_NAME = "COMPANY_EXISTING_BY_NAME"
    COMPANY_CREATE_NEW = "COMPANY_CREATE_NEW"
    NOMINAL_VALUE_MISSING_OR_INVALID = "NOMINAL_VALUE_MISSING_OR_INVALID"
    MATURITY_STRUCTURE_UNRESOLVED = "MATURITY_STRUCTURE_UNRESOLVED"
    NOT_ACTIVE_OR_NOT_TRADED = "NOT_ACTIVE_OR_NOT_TRADED"
    ACTIVE_STATUS_UNKNOWN = "ACTIVE_STATUS_UNKNOWN"
    READY = "READY"


class BondImportCompanyPlan(StrEnum):
    USE_EXISTING_BY_INN = "USE_EXISTING_BY_INN"
    USE_EXISTING_BY_NAME = "USE_EXISTING_BY_NAME"
    CREATE_NEW = "CREATE_NEW"
    COMPANY_IDENTITY_CONFLICT = "COMPANY_IDENTITY_CONFLICT"


class TInvestBondImportPreflightErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    ADMISSION_MANIFEST_INVALID = "ADMISSION_MANIFEST_INVALID"
    DESCRIPTION_MISSING = "DESCRIPTION_MISSING"
    DESCRIPTION_CONFLICT = "DESCRIPTION_CONFLICT"
    INTERNAL_IDENTITY_PROJECTION_INVALID = "INTERNAL_IDENTITY_PROJECTION_INVALID"
    COMPANY_PROJECTION_INVALID = "COMPANY_PROJECTION_INVALID"
    CANDIDATE_HASH_MISMATCH = "CANDIDATE_HASH_MISMATCH"


class TInvestBondImportPreflightError(ValueError):
    """Value-free typed input error; never includes raw source evidence."""

    def __init__(self, code: TInvestBondImportPreflightErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


# Source scalar unions deliberately retain malformed scalar representations for
# the reducer to diagnose.  They prevent arbitrary objects/containers from
# leaking into a frozen evidence projection.
MoexSourceScalar = str | int | float | bool | Decimal | None
MoexSourceDate = date | str | None


class MoexBondImportDescriptionProjection(_StrictFrozenModel):
    """One caller-fetched MOEX description; this model performs no fetch."""

    contract_version: Literal["moex-bond-import-description-v1"] = (
        MOEX_BOND_IMPORT_DESCRIPTION_VERSION
    )
    requested_secid: str
    requested_board: str | None = None
    secid: str | None = None
    isin: str | None = None

    name: str | None = None
    shortname: str | None = None
    issuer_name: str | None = None
    issuer_inn: str | None = None

    currency: str | None = None
    nominal_value: MoexSourceScalar = None
    coupon_rate: MoexSourceScalar = None
    maturity_date: MoexSourceDate = None
    offer_date: MoexSourceDate = None

    is_subordinated: bool | None = None
    is_perpetual: bool | None = None
    has_amortization: bool | None = None

    status: str | None = None
    is_traded: bool | None = None

    board_observed: bool | None = None
    primary_board: str | None = None

    # Preserve only compact structural source evidence; no raw HTTP payloads.
    raw_structural_fields: tuple[tuple[str, MoexSourceScalar], ...] = ()


class BondImportSourceUidEvidence(_StrictFrozenModel):
    """Task295 availability/qualification evidence retained per source UID."""

    source_uid: str
    availability_classification: TInvestBridgeAvailabilityClass
    api_trade_available: bool | None
    buy_available: bool | None
    sell_available: bool | None
    for_qual_investor: bool | None
    required_tests: tuple[str, ...] | None
    required_tests_state: Literal["NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"]


class CompanyIdentityProjection(_StrictFrozenModel):
    """Minimal caller-supplied Company identity; it is not an ORM model."""

    contract_version: Literal["company-identity-projection-v1"] = (
        COMPANY_IDENTITY_PROJECTION_VERSION
    )
    company_id: int = Field(gt=0)
    name: str
    ticker: str | None = None
    inn: str | None = None


class BondImportBatchIdentity(_StrictFrozenModel):
    admission_manifest_version: str
    admission_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    import_candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_count: int = Field(ge=0)
    candidate_isins: tuple[str, ...]
    candidate_secids: tuple[str, ...]
    candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_secid_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TInvestBondImportPreflightRow(_StrictFrozenModel):
    """Complete review row for one frozen Task296B candidate ISIN."""

    isin: str
    secid: str | None
    moex_description: MoexBondImportDescriptionProjection | None = None
    source_uids: tuple[str, ...]
    uid_admissions: tuple[TInvestAdmissionUidRow, ...] = ()
    status: BondImportPreflightState
    reason_codes: tuple[BondImportPreflightReason, ...]

    company_plan: BondImportCompanyPlan | None = None
    company_id: int | None = Field(default=None, gt=0)

    name: str | None = None
    shortname: str | None = None
    issuer_name: str | None = None
    issuer_inn: str | None = None

    currency: str | None = None
    canonical_currency: str | None = None
    nominal_value: Decimal | None = None
    nominal_value_raw: MoexSourceScalar = None
    coupon_rate: Decimal | None = None
    coupon_rate_raw: MoexSourceScalar = None
    maturity_date: date | None = None
    maturity_date_raw: MoexSourceDate = None
    offer_date: date | None = None
    offer_date_raw: MoexSourceDate = None

    is_subordinated: bool | None = None
    is_perpetual: bool | None = None
    has_amortization: bool | None = None

    status_source: str | None = None
    is_traded: bool | None = None
    active_status_unknown: bool = False

    requested_secid: str | None = None
    requested_board: str | None = None
    board_observed: bool | None = None
    primary_board: str | None = None
    raw_structural_fields: tuple[tuple[str, MoexSourceScalar], ...] = ()

    required_tests: tuple[str, ...] | None = None
    required_tests_state: Literal["NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"] = (
        "NOT_SUPPLIED"
    )
    task296b_admission_reasons: tuple[TInvestAdmissionReason, ...] = ()


class TInvestBondReadyImportRow(_StrictFrozenModel):
    """Minimal exact payload planned for a separately authorized import."""

    isin: str
    secid: str
    issuer_name: str
    issuer_inn: str
    planned_company_action: BondImportCompanyPlan
    company_id: int | None = Field(default=None, gt=0)
    currency: Literal["RUB"] = "RUB"
    nominal_value: Decimal
    maturity_date: date | None
    is_perpetual: bool
    required_tests: tuple[str, ...] | None
    uid_admissions: tuple[TInvestAdmissionUidRow, ...] = ()
    task296b_admission_reasons: tuple[TInvestAdmissionReason, ...]

    name: str | None = None
    shortname: str | None = None
    coupon_rate: Decimal | None = None
    offer_date: date | None = None
    is_subordinated: bool | None = None
    has_amortization: bool | None = None
    status_source: str | None = None
    is_traded: bool | None = None
    requested_board: str
    board_observed: Literal[True] = True
    primary_board: Literal["TQCB"] = "TQCB"
    raw_structural_fields: tuple[tuple[str, MoexSourceScalar], ...] = ()


class TInvestBondImportPreflightSummary(_StrictFrozenModel):
    candidate_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    collision_count: int = Field(ge=0)
    identity_conflict_count: int = Field(ge=0)

    existing_company_by_inn_count: int = Field(ge=0)
    existing_company_by_name_count: int = Field(ge=0)
    new_company_count: int = Field(ge=0)

    missing_nominal_count: int = Field(ge=0)
    missing_maturity_count: int = Field(ge=0)
    perpetual_count: int = Field(ge=0)
    active_unknown_count: int = Field(ge=0)


class TInvestBondImportPreflightReasonBreakdown(_StrictFrozenModel):
    reason_code: BondImportPreflightReason
    count: int = Field(ge=0)
    isins: tuple[str, ...]
    secids: tuple[str, ...]
    source_uids: tuple[str, ...]


class TInvestBondImportPreflightProvenance(_StrictFrozenModel):
    task295_source_contract_version: Literal["tinvest-current-instrument-universe-v1"]
    task296_identity_bridge_contract_version: Literal["tinvest-bond-identity-bridge-v1"]
    task296b_admission_manifest_contract_version: Literal[
        "tinvest-bond-admission-manifest-v1"
    ]
    description_projection_contract_version: Literal[
        "moex-bond-import-description-v1"
    ] = MOEX_BOND_IMPORT_DESCRIPTION_VERSION

    candidate_count: int = Field(ge=0)
    description_projection_count: int = Field(ge=0)
    internal_bond_projection_count: int = Field(ge=0)
    company_projection_count: int = Field(ge=0)

    admission_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    import_candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_secid_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ready_for_import_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    candidate_hash_canonicalization: Literal["SORTED_CANONICAL_JSON_SHA256_V1"] = (
        "SORTED_CANONICAL_JSON_SHA256_V1"
    )
    ready_manifest_sort_key: Literal["ISIN_SECID"] = "ISIN_SECID"
    review_manifest_sort_key: Literal["ISIN"] = "ISIN"
    exact_security_identity_method: Literal["EXACT_ISIN_AND_SECID"] = (
        "EXACT_ISIN_AND_SECID"
    )
    currency_method: Literal["CANONICALIZE_MOEX_CURRENCY"] = (
        "CANONICALIZE_MOEX_CURRENCY"
    )

    board_ready_requires_primary_tqcb: Literal[True] = True
    board_ready_requires_board_observed: Literal[True] = True
    board_observation_requested_board: Literal["TQCB"] = "TQCB"
    is_traded_true_required: Literal[False] = False
    explicit_is_traded_false_blocks: Literal[True] = True
    explicit_inactive_status_blocks: Literal[True] = True
    unknown_active_status_blocks: Literal[False] = False


class TInvestBondImportPreflightCapabilities(_StrictFrozenModel):
    frozen_import_batch_ready: Literal[True] = True
    read_only_import_preflight_ready: Literal[True] = True
    task296b_manifest_is_source: Literal[True] = True
    moex_description_exact_identity_required: Literal[True] = True
    existing_bond_collision_check_ready: Literal[True] = True
    company_resolution_preview_ready: Literal[True] = True
    positive_nominal_required: Literal[True] = True
    maturity_or_perpetual_required: Literal[True] = True
    ready_import_manifest_hash_ready: Literal[True] = True

    board_ready_requires_primary_tqcb: Literal[True] = True
    board_ready_requires_board_observed: Literal[True] = True
    is_traded_true_required: Literal[False] = False
    explicit_is_traded_false_blocks: Literal[True] = True
    explicit_inactive_status_blocks: Literal[True] = True
    unknown_active_status_blocks: Literal[False] = False
    unknown_active_status_diagnostic_only: Literal[True] = True

    placeholder_issuer_import_allowed: Literal[False] = False
    required_tests_gate_import: Literal[False] = False
    name_based_security_classification: Literal[False] = False
    import_ready_means_m3_ready: Literal[False] = False

    database_persistence: Literal[False] = False
    database_migration: Literal[False] = False
    moex_sync_called: Literal[False] = False
    network_access: Literal[False] = False
    live_tinvest_request: Literal[False] = False
    live_moex_request: Literal[False] = False
    m3_feature_logic_changed: Literal[False] = False
    ofz_curve_changed: Literal[False] = False
    broker_write_surface: Literal[False] = False
    shadow_started: Literal[False] = False
    pit_ready: Literal[False] = False


class TInvestBondImportPreflightView(_StrictFrozenModel):
    contract_version: Literal["tinvest-bond-import-preflight-v1"] = (
        TINVEST_BOND_IMPORT_PREFLIGHT_VERSION
    )
    identity: BondImportBatchIdentity
    candidate_rows: tuple[TInvestBondImportPreflightRow, ...]
    ready_for_import_manifest: tuple[TInvestBondReadyImportRow, ...]
    review_manifest: tuple[TInvestBondImportPreflightRow, ...]

    candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_secid_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    ready_for_import_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    summary: TInvestBondImportPreflightSummary
    reason_breakdown: tuple[TInvestBondImportPreflightReasonBreakdown, ...]
    provenance: TInvestBondImportPreflightProvenance
    capabilities: TInvestBondImportPreflightCapabilities = Field(
        default_factory=TInvestBondImportPreflightCapabilities
    )
    pit_ready: Literal[False] = False
