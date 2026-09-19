"""Explicit-output-universe batch contract for Task270 liquidity features."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_liquidity_features import BondLiquidityFeatureView


BOND_LIQUIDITY_BATCH_FEATURE_CONTRACT_VERSION = (
    "bond-liquidity-batch-feature-v1"
)
BondLiquidityBatchItemStatus = Literal["BUILT", "BOND_NOT_FOUND"]
BondLiquidityBatchStatus = Literal["COMPLETE", "PARTIAL", "NO_EXISTING_BONDS"]


class BondLiquidityBatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BondLiquidityBatchFeatureItem(BondLiquidityBatchModel):
    bond_id: int
    build_status: BondLiquidityBatchItemStatus
    feature: BondLiquidityFeatureView | None


class BondLiquidityBatchDiagnostics(BondLiquidityBatchModel):
    requested_identity_query_count: int
    market_window_query_count: int
    universe_evaluation_count: int
    universe_candidate_count: int
    score_eligible_universe_count: int
    feature_projection_count: int


class BondLiquidityBatchCapabilities(BondLiquidityBatchModel):
    explicit_output_bond_universe_ready: Literal[True] = True
    shared_liquidity_window_load_ready: Literal[True] = True
    shared_liquidity_universe_evaluation_ready: Literal[True] = True
    task270_equivalent_batch_build_ready: Literal[True] = True
    automatic_investment_universe_discovery_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    liquidity_adjusted_spread_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondLiquidityBatchFeatureView(BondLiquidityBatchModel):
    contract_version: Literal["bond-liquidity-batch-feature-v1"] = (
        BOND_LIQUIDITY_BATCH_FEATURE_CONTRACT_VERSION
    )
    as_of_date: date
    market_source: str
    lookback_calendar_days: int
    min_observation_days: int
    status: BondLiquidityBatchStatus
    requested_bond_ids: list[int]
    existing_bond_ids: list[int]
    missing_bond_ids: list[int]
    requested_bond_count: int
    existing_bond_count: int
    missing_bond_count: int
    built_feature_count: int
    ready_feature_count: int
    unavailable_feature_count: int
    items: list[BondLiquidityBatchFeatureItem]
    diagnostics: BondLiquidityBatchDiagnostics
    capabilities: BondLiquidityBatchCapabilities = Field(
        default_factory=BondLiquidityBatchCapabilities
    )
    pit_ready: Literal[False] = False
