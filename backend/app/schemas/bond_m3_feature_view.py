"""Immutable composite surface for already-built M3 bond feature evidence."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_credit_comparability import BondCreditComparabilityView
from app.schemas.bond_credit_features import BondCreditFeatureView
from app.schemas.bond_dv01 import BondDv01View
from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
from app.schemas.bond_liquidity_relative_value import (
    BondLiquidityAwareRelativeValueView,
)
from app.schemas.bond_market_features import BondMarketFeatureView
from app.schemas.bond_modified_duration import BondModifiedDurationView
from app.schemas.credit_cohort_peer_distribution_orchestration import (
    CreditCohortPeerDistributionOrchestrationView,
)
from app.schemas.ofz_reference_curve import BondRelativeValueView


BOND_M3_FEATURE_VIEW_CONTRACT_VERSION = "bond-m3-feature-view-v1"
BondM3FeatureStatus = Literal["CONSISTENT", "EVIDENCE_INVALID"]


class BondM3FeatureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BondM3FeatureAvailability(BondM3FeatureModel):
    market_fresh: bool
    relative_value_ready: bool
    has_credit_rating_evidence: bool
    has_credit_comparable_cohort: bool
    liquidity_score_ready: bool
    modified_duration_ready: bool
    dv01_ready: bool
    liquidity_relative_value_ready: bool
    has_peer_distribution_context: bool
    peer_distribution_ready: bool
    all_supplied_evidence_consistent: bool


class BondM3FeatureProvenance(BondM3FeatureModel):
    market_contract_version: str | None
    relative_value_contract_version: str | None
    ofz_curve_contract_version: str | None
    credit_contract_version: str | None
    credit_comparability_contract_version: str | None
    liquidity_contract_version: str | None
    modified_duration_contract_version: str | None
    dv01_contract_version: str | None
    liquidity_relative_value_contract_version: str | None
    peer_distribution_contract_version: str | None
    requested_bond_id: int
    requested_as_of_date: date
    requested_market_source: str
    market_snapshot_id: int | None
    market_trade_date: date | None
    curve_trade_date: date | None
    peer_context_supplied: bool


class BondM3FeatureCapabilities(BondM3FeatureModel):
    m3_composite_feature_view_ready: Literal[True] = True
    market_feature_input_ready: Literal[True] = True
    ofz_relative_value_input_ready: Literal[True] = True
    credit_feature_input_ready: Literal[True] = True
    credit_comparability_input_ready: Literal[True] = True
    liquidity_feature_input_ready: Literal[True] = True
    modified_duration_input_ready: Literal[True] = True
    dv01_input_ready: Literal[True] = True
    liquidity_aware_relative_value_input_ready: Literal[True] = True
    peer_relative_value_context_supported: Literal[True] = True
    cross_feature_evidence_validation_ready: Literal[True] = True
    investment_score_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    rating_ordinal_mapping_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    liquidity_adjusted_spread_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    portfolio_construction_ready: Literal[False] = False
    risk_engine_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondM3FeatureView(BondM3FeatureModel):
    contract_version: Literal[
        "bond-m3-feature-view-v1"
    ] = BOND_M3_FEATURE_VIEW_CONTRACT_VERSION
    bond_id: int
    as_of_date: date
    market_source: str
    isin: str | None
    secid: str | None
    status: BondM3FeatureStatus
    market: BondMarketFeatureView
    relative_value: BondRelativeValueView
    credit: BondCreditFeatureView
    credit_comparability: BondCreditComparabilityView
    liquidity: BondLiquidityFeatureView
    modified_duration: BondModifiedDurationView
    dv01: BondDv01View
    liquidity_relative_value: BondLiquidityAwareRelativeValueView
    peer_distribution: CreditCohortPeerDistributionOrchestrationView | None
    availability: BondM3FeatureAvailability
    quality_flags: list[str]
    provenance: BondM3FeatureProvenance
    capabilities: BondM3FeatureCapabilities = Field(
        default_factory=BondM3FeatureCapabilities
    )
    pit_ready: Literal[False] = False
