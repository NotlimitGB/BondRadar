"""Explicit-universe batch contracts for Task276-equivalent members."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_credit_cohort_relative_value import (
    BondCreditCohortRelativeValueMemberView,
)
from app.schemas.bond_credit_comparability import RatingTargetKind
from app.schemas.bond_credit_features import RatingAgency
from app.schemas.ofz_reference_curve import CurveStatus, OfzReferenceCurveView


CREDIT_COHORT_BATCH_MEMBERS_CONTRACT_VERSION = "credit-cohort-batch-members-v1"
CreditCohortBatchStatus = Literal["COMPLETE", "PARTIAL", "NO_EXISTING_BONDS"]
CreditCohortBatchItemStatus = Literal["BUILT", "BOND_NOT_FOUND"]


class CreditCohortBatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreditCohortBatchMemberItem(CreditCohortBatchModel):
    bond_id: int
    build_status: CreditCohortBatchItemStatus
    member: BondCreditCohortRelativeValueMemberView | None


class CreditCohortBatchMemberProvenance(CreditCohortBatchModel):
    as_of_date: date
    market_source: str
    target_kind: RatingTargetKind
    rating_agency: RatingAgency
    max_market_age_days: int
    max_curve_age_days: int
    requested_bond_ids: list[int]
    existing_bond_ids: list[int]
    missing_bond_ids: list[int]
    shared_curve_contract_version: str | None
    shared_curve_status: CurveStatus | None
    shared_curve_trade_date: date | None
    shared_curve_node_count: int | None
    curve_build_count: int
    market_feature_call_count: int
    credit_comparability_call_count: int
    relative_evaluator_call_count: int
    member_composer_call_count: int


class CreditCohortBatchMemberCapabilities(CreditCohortBatchModel):
    explicit_bond_id_universe_ready: Literal[True] = True
    shared_ofz_curve_batch_reuse_ready: Literal[True] = True
    batch_member_build_ready: Literal[True] = True
    task276_equivalent_member_build_ready: Literal[True] = True
    peer_universe_discovery_ready: Literal[False] = False
    peer_distribution_orchestration_ready: Literal[False] = False
    peer_ranking_ready: Literal[False] = False
    rating_ordinal_mapping_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class CreditCohortBatchMemberView(CreditCohortBatchModel):
    contract_version: Literal["credit-cohort-batch-members-v1"] = (
        CREDIT_COHORT_BATCH_MEMBERS_CONTRACT_VERSION
    )
    as_of_date: date
    market_source: str
    requested_target_kind: RatingTargetKind
    requested_rating_agency: RatingAgency
    status: CreditCohortBatchStatus
    requested_bond_ids: list[int]
    existing_bond_ids: list[int]
    missing_bond_ids: list[int]
    requested_bond_count: int
    existing_bond_count: int
    missing_bond_count: int
    built_member_count: int
    ready_member_count: int
    unavailable_member_count: int
    curve_built: bool
    items: list[CreditCohortBatchMemberItem]
    shared_curve: OfzReferenceCurveView | None
    provenance: CreditCohortBatchMemberProvenance
    capabilities: CreditCohortBatchMemberCapabilities = Field(
        default_factory=CreditCohortBatchMemberCapabilities
    )
    pit_ready: Literal[False] = False
