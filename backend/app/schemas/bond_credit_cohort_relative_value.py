"""Atomic source-native cohort membership with an unadjusted OFZ spread."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_credit_comparability import (
    CreditComparabilityStatus, RatingComparabilityStatus, RatingTargetKind,
    SourceNativeRatingCohortKey,
)
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.ofz_reference_curve import RelativeValueStatus

BOND_CREDIT_COHORT_RELATIVE_VALUE_CONTRACT_VERSION = "bond-credit-cohort-relative-value-member-v1"
CreditCohortRelativeValueMemberStatus = Literal[
    "READY", "DEPENDENCY_IDENTITY_MISMATCH", "CREDIT_COMPARABILITY_UNAVAILABLE",
    "CREDIT_COHORT_EVIDENCE_INVALID", "SELECTED_COHORT_MISSING",
    "SELECTED_COHORT_UNAVAILABLE", "RELATIVE_VALUE_UNAVAILABLE",
    "RELATIVE_VALUE_EVIDENCE_INVALID",
]


class CreditCohortMemberModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreditCohortDependencyIdentity(CreditCohortMemberModel):
    bond_id: int | None
    as_of_date: date | None
    market_source: str | None = None


class BondCreditCohortRelativeValueMemberAvailability(CreditCohortMemberModel):
    has_credit_comparability: bool
    has_selected_rating_entry: bool
    has_selected_cohort_key: bool
    has_relative_value: bool
    has_spread_to_ofz: bool
    has_credit_cohort_relative_value_member: bool


class BondCreditCohortRelativeValueMemberProvenance(CreditCohortMemberModel):
    credit_comparability_contract_version: str | None
    credit_identity: CreditCohortDependencyIdentity
    relative_value_identity: CreditCohortDependencyIdentity
    requested_target_kind: RatingTargetKind
    requested_rating_agency: RatingAgency
    selected_entry_count: int
    selected_entry_status: RatingComparabilityStatus | None
    selected_rating_event_id: int | None
    selected_rating_event_date: date | None
    selected_source_provider: str | None
    selected_rating_scale_raw: str | None
    selected_rating_value_raw: str | None
    bond_legal_issuer_profile_id: int | None
    legal_issuer_id: int | None
    relative_value_contract_version: str | None
    ofz_curve_contract_version: str | None
    target_market_snapshot_id: int | None
    target_market_trade_date: date | None
    curve_trade_date: date | None
    market_source: str
    as_of_date: date


class BondCreditCohortRelativeValueMemberCapabilities(CreditCohortMemberModel):
    source_native_rating_cohort_input_ready: Literal[True] = True
    spread_to_ofz_input_ready: Literal[True] = True
    credit_cohort_relative_value_member_ready: Literal[True] = True
    credit_cohort_peer_distribution_ready: Literal[False] = False
    credit_cohort_spread_percentile_ready: Literal[False] = False
    rating_ordinal_mapping_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    unified_credit_score_ready: Literal[False] = False
    pd_model_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondCreditCohortRelativeValueMemberView(CreditCohortMemberModel):
    contract_version: Literal["bond-credit-cohort-relative-value-member-v1"] = BOND_CREDIT_COHORT_RELATIVE_VALUE_CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: str
    requested_target_kind: RatingTargetKind
    requested_rating_agency: RatingAgency
    status: CreditCohortRelativeValueMemberStatus
    credit_comparability_status: CreditComparabilityStatus | None
    relative_value_status: RelativeValueStatus | None
    cohort_key: SourceNativeRatingCohortKey | None
    rating_event_id: int | None
    rating_event_date: date | None
    rating_source_provider: str | None
    rating_scale_raw: str | None
    rating_value_raw: str | None
    target_yield_to_maturity_pct: Decimal | None
    target_duration_years: Decimal | None
    reference_ofz_yield_pct: Decimal | None
    spread_to_ofz_pp: Decimal | None
    spread_to_ofz_bps: Decimal | None
    interpolation_method: Literal["EXACT_NODE", "LINEAR_INTERPOLATION"] | None
    market_snapshot_id: int | None
    market_trade_date: date | None
    curve_trade_date: date | None
    availability: BondCreditCohortRelativeValueMemberAvailability
    quality_flags: list[str]
    provenance: BondCreditCohortRelativeValueMemberProvenance
    capabilities: BondCreditCohortRelativeValueMemberCapabilities = Field(default_factory=BondCreditCohortRelativeValueMemberCapabilities)
    pit_ready: Literal[False] = False
