"""Pure Task280-to-Task277 peer-distribution orchestration contracts."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_credit_comparability import RatingTargetKind
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.credit_cohort_batch_members import (
    CreditCohortBatchItemStatus,
    CreditCohortBatchStatus,
)
from app.schemas.credit_cohort_peer_spread_distribution import (
    CreditCohortPeerSpreadDistributionStatus,
    CreditCohortPeerSpreadDistributionView,
)


CREDIT_COHORT_PEER_DISTRIBUTION_ORCHESTRATION_CONTRACT_VERSION = (
    "credit-cohort-peer-distribution-orchestration-v1"
)
CreditCohortPeerDistributionOrchestrationStatus = Literal[
    "BATCH_EVIDENCE_INVALID",
    "TARGET_NOT_REQUESTED",
    "TARGET_BOND_NOT_FOUND",
    "TARGET_MEMBER_INVALID",
    "PEER_INPUT_INVALID",
    "NO_ELIGIBLE_PEERS",
    "INSUFFICIENT_PEERS",
    "READY",
]


class PeerDistributionOrchestrationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreditCohortPeerDistributionOrchestrationAvailability(
    PeerDistributionOrchestrationModel
):
    has_valid_batch: bool
    target_requested: bool
    target_exists: bool
    has_target_member: bool
    has_candidate_members: bool
    has_distribution_result: bool
    has_ready_distribution: bool


class CreditCohortPeerDistributionOrchestrationProvenance(
    PeerDistributionOrchestrationModel
):
    batch_contract_version: str | None
    batch_status: CreditCohortBatchStatus | None
    batch_as_of_date: date | None
    batch_market_source: str | None
    batch_target_kind: RatingTargetKind | None
    batch_rating_agency: RatingAgency | None
    requested_bond_ids: list[int]
    existing_bond_ids: list[int]
    missing_bond_ids: list[int]
    target_bond_id: int
    target_item_build_status: CreditCohortBatchItemStatus | None
    candidate_member_bond_ids: list[int]
    min_peer_count: int
    reducer_contract_version: str | None
    reducer_status: CreditCohortPeerSpreadDistributionStatus | None
    reducer_call_count: int


class CreditCohortPeerDistributionOrchestrationCapabilities(
    PeerDistributionOrchestrationModel
):
    task280_batch_input_ready: Literal[True] = True
    task277_reducer_input_ready: Literal[True] = True
    peer_distribution_orchestration_ready: Literal[True] = True
    explicit_target_bond_ready: Literal[True] = True
    peer_universe_discovery_ready: Literal[False] = False
    batch_member_build_ready: Literal[False] = False
    peer_distribution_ready: Literal[True] = True
    peer_spread_percentile_ready: Literal[True] = True
    peer_spread_vs_median_ready: Literal[True] = True
    peer_ranking_ready: Literal[False] = False
    rating_ordinal_mapping_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class CreditCohortPeerDistributionOrchestrationView(
    PeerDistributionOrchestrationModel
):
    contract_version: Literal[
        "credit-cohort-peer-distribution-orchestration-v1"
    ] = CREDIT_COHORT_PEER_DISTRIBUTION_ORCHESTRATION_CONTRACT_VERSION
    target_bond_id: int
    min_peer_count: int
    as_of_date: date | None
    market_source: str | None
    requested_target_kind: RatingTargetKind | None
    requested_rating_agency: RatingAgency | None
    status: CreditCohortPeerDistributionOrchestrationStatus
    batch_status: CreditCohortBatchStatus | None
    requested_bond_count: int | None
    existing_bond_count: int | None
    missing_bond_count: int | None
    built_member_count: int | None
    candidate_member_count: int
    distribution: CreditCohortPeerSpreadDistributionView | None
    availability: CreditCohortPeerDistributionOrchestrationAvailability
    quality_flags: list[str]
    provenance: CreditCohortPeerDistributionOrchestrationProvenance
    capabilities: CreditCohortPeerDistributionOrchestrationCapabilities = Field(
        default_factory=CreditCohortPeerDistributionOrchestrationCapabilities
    )
    pit_ready: Literal[False] = False
