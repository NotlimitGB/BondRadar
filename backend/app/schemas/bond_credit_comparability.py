"""Exact source-native equality descriptors, without credit interpretation."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_credit_features import (
    CreditRatingEventEvidence, IssuerLinkStatus, RatingAgency,
)

BOND_CREDIT_COMPARABILITY_CONTRACT_VERSION = "bond-credit-comparability-v1"
RatingTargetKind = Literal["BOND", "LEGAL_ISSUER"]
RatingComparabilityStatus = Literal[
    "READY", "MULTIPLE_LATEST_EVENTS", "RATING_VALUE_MISSING", "EVIDENCE_INVALID",
]
CreditComparabilityStatus = Literal[
    "READY", "NO_COMPARABLE_RATING", "DEPENDENCY_EVIDENCE_INVALID",
]


class CreditComparabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceNativeRatingCohortKey(CreditComparabilityModel):
    target_kind: RatingTargetKind
    rating_agency: RatingAgency
    source_provider: str
    rating_scale_raw: str | None
    rating_value_raw: str


class RatingComparabilityEntry(CreditComparabilityModel):
    target_kind: RatingTargetKind
    rating_agency: RatingAgency | None
    status: RatingComparabilityStatus
    latest_event_date: date | None
    event_count: int | None
    event_ids: list[int]
    selected_event_id: int | None
    source_provider: str | None
    rating_scale_raw: str | None
    rating_value_raw: str | None
    has_declared_rating_scale: bool
    has_rating_value: bool
    cohort_key: SourceNativeRatingCohortKey | None
    selected_event: CreditRatingEventEvidence | None
    quality_flags: list[str]


class BondCreditComparabilityAvailability(CreditComparabilityModel):
    has_bond_rating_evidence: bool
    has_issuer_rating_evidence: bool
    has_bond_comparable_cohort: bool
    has_issuer_comparable_cohort: bool
    bond_comparable_agencies: list[RatingAgency]
    issuer_comparable_agencies: list[RatingAgency]
    has_any_comparable_cohort: bool


class BondCreditComparabilityProvenance(CreditComparabilityModel):
    credit_feature_contract_version: str | None
    credit_feature_bond_id: int | None
    credit_feature_as_of_date: date | None
    as_of_date: date
    bond_legal_issuer_profile_id: int | None
    legal_issuer_id: int | None
    issuer_link_status: IssuerLinkStatus | None
    bond_rating_event_ids: list[int]
    issuer_rating_event_ids: list[int]
    selected_bond_rating_event_ids: list[int]
    selected_issuer_rating_event_ids: list[int]
    bond_rating_agencies: list[RatingAgency]
    issuer_rating_agencies: list[RatingAgency]


class BondCreditComparabilityCapabilities(CreditComparabilityModel):
    credit_feature_input_ready: Literal[True] = True
    source_native_rating_cohort_keys_ready: Literal[True] = True
    exact_rating_label_comparability_ready: Literal[True] = True
    rating_scale_inferred: Literal[False] = False
    rating_ordinal_mapping_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    unified_credit_score_ready: Literal[False] = False
    pd_model_ready: Literal[False] = False
    issuer_to_bond_rating_inheritance: Literal[False] = False
    bond_to_issuer_rating_inheritance: Literal[False] = False
    credit_cohort_relative_value_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondCreditComparabilityView(CreditComparabilityModel):
    contract_version: Literal["bond-credit-comparability-v1"] = BOND_CREDIT_COMPARABILITY_CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    status: CreditComparabilityStatus
    comparison_method: Literal["EXACT_SOURCE_NATIVE_LABEL"] = "EXACT_SOURCE_NATIVE_LABEL"
    bond_rating_entries: list[RatingComparabilityEntry]
    issuer_rating_entries: list[RatingComparabilityEntry]
    availability: BondCreditComparabilityAvailability
    provenance: BondCreditComparabilityProvenance
    quality_flags: list[str]
    capabilities: BondCreditComparabilityCapabilities = Field(default_factory=BondCreditComparabilityCapabilities)
    pit_ready: Literal[False] = False
