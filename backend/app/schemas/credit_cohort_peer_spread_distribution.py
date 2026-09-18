"""Descriptive spread statistics for an explicitly supplied source-native cohort."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_credit_comparability import SourceNativeRatingCohortKey

CREDIT_COHORT_PEER_SPREAD_DISTRIBUTION_CONTRACT_VERSION = "credit-cohort-peer-spread-distribution-v1"
CreditCohortPeerSpreadDistributionStatus = Literal[
    "TARGET_MEMBER_INVALID", "PEER_INPUT_INVALID", "NO_ELIGIBLE_PEERS",
    "INSUFFICIENT_PEERS", "READY",
]


class PeerDistributionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreditCohortPeerSpreadEvidence(PeerDistributionModel):
    bond_id: int
    isin: str | None
    secid: str | None
    spread_to_ofz_bps: Decimal
    rating_event_id: int | None
    market_snapshot_id: int | None


class CreditCohortPeerSpreadExclusions(PeerDistributionModel):
    excluded_target_self_count: int
    excluded_not_ready_count: int
    invalid_candidate_count: int
    excluded_as_of_mismatch_count: int
    excluded_market_source_mismatch_count: int
    excluded_cohort_mismatch_count: int
    unprocessed_candidate_count: int
    duplicate_peer_bond_ids: list[int]


class CreditCohortPeerSpreadAvailability(PeerDistributionModel):
    has_valid_target: bool
    has_eligible_peers: bool
    has_minimum_peer_count: bool
    has_peer_distribution: bool
    has_peer_median: bool
    has_target_spread_percentile: bool
    has_spread_vs_peer_median: bool
    has_ready_peer_distribution: bool


class CreditCohortPeerSpreadProvenance(PeerDistributionModel):
    target_member_contract_version: str | None
    target_rating_event_id: int | None
    target_market_snapshot_id: int | None
    candidate_count: int
    eligible_peer_bond_ids: list[int]
    min_peer_count: int
    percentile_method: Literal["TARGET_VS_PEERS_MIDRANK_V1"] = "TARGET_VS_PEERS_MIDRANK_V1"
    median_method: Literal["DECIMAL_STANDARD_MEDIAN"] = "DECIMAL_STANDARD_MEDIAN"
    mean_method: Literal["DECIMAL_ARITHMETIC_MEAN"] = "DECIMAL_ARITHMETIC_MEAN"


class CreditCohortPeerSpreadCapabilities(PeerDistributionModel):
    credit_cohort_relative_value_member_input_ready: Literal[True] = True
    credit_cohort_peer_distribution_ready: Literal[True] = True
    credit_cohort_spread_percentile_ready: Literal[True] = True
    credit_cohort_spread_vs_median_ready: Literal[True] = True
    peer_universe_discovery_ready: Literal[False] = False
    peer_batch_member_build_ready: Literal[False] = False
    rating_ordinal_mapping_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    peer_ranking_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class CreditCohortPeerSpreadDistributionView(PeerDistributionModel):
    contract_version: Literal["credit-cohort-peer-spread-distribution-v1"] = CREDIT_COHORT_PEER_SPREAD_DISTRIBUTION_CONTRACT_VERSION
    target_bond_id: int | None
    as_of_date: date | None
    market_source: str | None
    cohort_key: SourceNativeRatingCohortKey | None
    target_spread_to_ofz_bps: Decimal | None
    status: CreditCohortPeerSpreadDistributionStatus
    candidate_count: int
    eligible_peer_count: int
    min_peer_count: int
    peer_min_spread_bps: Decimal | None
    peer_median_spread_bps: Decimal | None
    peer_mean_spread_bps: Decimal | None
    peer_max_spread_bps: Decimal | None
    spread_minus_peer_median_bps: Decimal | None
    target_spread_percentile: Decimal | None
    eligible_peers: list[CreditCohortPeerSpreadEvidence]
    exclusions: CreditCohortPeerSpreadExclusions
    availability: CreditCohortPeerSpreadAvailability
    quality_flags: list[str]
    provenance: CreditCohortPeerSpreadProvenance
    capabilities: CreditCohortPeerSpreadCapabilities = Field(default_factory=CreditCohortPeerSpreadCapabilities)
    pit_ready: Literal[False] = False
