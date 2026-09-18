"""Independent OFZ relative value and liquidity context, without an adjusted spread."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_liquidity_features import LiquidityScoreStatus
from app.schemas.ofz_reference_curve import RelativeValueStatus

CONTRACT_VERSION = "bond-liquidity-aware-relative-value-v1"
CompositionStatus = Literal[
    "READY", "LIQUIDITY_PROVENANCE_INVALID", "MARKET_EVIDENCE_MISMATCH",
    "RELATIVE_VALUE_UNAVAILABLE", "LIQUIDITY_UNAVAILABLE",
]


class CompositionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DependencyIdentity(CompositionModel):
    bond_id: int
    as_of_date: date
    market_source: str


class BondLiquidityAwareRelativeValueAvailability(CompositionModel):
    has_relative_value: bool
    has_spread_to_ofz: bool
    has_liquidity_feature: bool
    has_liquidity_score: bool
    has_matching_market_evidence: bool
    has_liquidity_aware_relative_value: bool


class BondLiquidityAwareRelativeValueProvenance(CompositionModel):
    relative_value_contract_version: str
    ofz_curve_contract_version: str
    liquidity_contract_version: str
    relative_value_identity: DependencyIdentity
    liquidity_identity: DependencyIdentity
    relative_value_target_market_snapshot_id: int | None
    relative_value_target_market_trade_date: date | None
    liquidity_latest_market_snapshot_id: int | None
    liquidity_latest_trade_date: date | None
    curve_trade_date: date | None
    liquidity_window_start_date: date
    liquidity_lookback_calendar_days: int
    liquidity_min_observation_days: int
    liquidity_provenance_valid: bool
    requested_as_of_date: date
    market_source: str


class BondLiquidityAwareRelativeValueCapabilities(CompositionModel):
    spread_to_ofz_input_ready: Literal[True] = True
    liquidity_score_input_ready: Literal[True] = True
    liquidity_aware_relative_value_ready: Literal[True] = True
    liquidity_adjusted_spread_ready: Literal[False] = False
    liquidity_premium_bps_model_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    slippage_model_ready: Literal[False] = False
    market_impact_model_ready: Literal[False] = False
    investment_ranking_ready: Literal[False] = False
    recommendation_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondLiquidityAwareRelativeValueView(CompositionModel):
    contract_version: Literal["bond-liquidity-aware-relative-value-v1"] = CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: str
    status: CompositionStatus
    relative_value_status: RelativeValueStatus
    target_yield_to_maturity_pct: Decimal | None
    target_duration_years: Decimal | None
    reference_ofz_yield_pct: Decimal | None
    spread_to_ofz_pp: Decimal | None
    spread_to_ofz_bps: Decimal | None
    interpolation_method: Literal["EXACT_NODE", "LINEAR_INTERPOLATION"] | None
    curve_trade_date: date | None
    liquidity_status: LiquidityScoreStatus
    liquidity_score_v1: Decimal | None
    turnover_percentile: Decimal | None
    trade_count_percentile: Decimal | None
    recency_percentile: Decimal | None
    median_daily_turnover_value: Decimal | None
    median_daily_trade_volume: Decimal | None
    median_daily_num_trades: Decimal | None
    latest_liquidity_trade_date: date | None
    latest_liquidity_observation_age_days: int | None
    liquidity_snapshot_observation_days: int | None
    availability: BondLiquidityAwareRelativeValueAvailability
    quality_flags: list[str]
    provenance: BondLiquidityAwareRelativeValueProvenance
    capabilities: BondLiquidityAwareRelativeValueCapabilities = Field(
        default_factory=BondLiquidityAwareRelativeValueCapabilities,
    )
    pit_ready: Literal[False] = False
