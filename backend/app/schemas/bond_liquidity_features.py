"""Recent observed liquidity and relative score; no execution-quality verdict."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

BOND_LIQUIDITY_FEATURE_CONTRACT_VERSION = "bond-liquidity-feature-v1"
LiquidityScoreStatus = Literal[
    "READY", "NO_MARKET_DATA", "INSUFFICIENT_TARGET_EVIDENCE",
    "MISSING_SCORE_COMPONENT", "INSUFFICIENT_UNIVERSE",
]


class LiquidityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BondLiquidityWindowStatistics(LiquidityModel):
    calendar_window_days: int
    snapshot_observation_days: int
    trade_volume_observation_days: int
    turnover_value_observation_days: int
    num_trades_observation_days: int
    latest_trade_date: date | None
    latest_observation_age_days: int | None
    median_daily_turnover_value: Decimal | None
    mean_daily_turnover_value: Decimal | None
    total_turnover_value: Decimal | None
    median_daily_trade_volume: Decimal | None
    mean_daily_trade_volume: Decimal | None
    total_trade_volume: Decimal | None
    median_daily_num_trades: Decimal | None
    mean_daily_num_trades: Decimal | None
    total_num_trades: int | None
    days_with_positive_turnover: int
    days_with_positive_trade_volume: int
    days_with_positive_num_trades: int
    positive_turnover_share_of_turnover_observations: Decimal | None
    positive_trade_count_share_of_trade_count_observations: Decimal | None


class BondLiquidityScoreComponents(LiquidityModel):
    turnover_percentile: Decimal | None = None
    trade_count_percentile: Decimal | None = None
    recency_percentile: Decimal | None = None


class BondLiquidityAvailability(LiquidityModel):
    has_market_snapshots: bool
    has_trade_volume: bool
    has_turnover_value: bool
    has_num_trades: bool
    has_sufficient_observation_days: bool
    has_score_components: bool
    has_liquidity_score: bool


class BondLiquidityProvenance(LiquidityModel):
    market_source: str
    window_start_date: date
    as_of_date: date
    lookback_calendar_days: int
    min_observation_days: int
    selected_market_snapshot_ids: list[int]
    selected_trade_dates: list[date]
    universe_candidate_count: int
    score_eligible_universe_count: int


class BondLiquidityCapabilities(LiquidityModel):
    liquidity_raw_inputs_ready: Literal[True] = True
    liquidity_window_aggregation_ready: Literal[True] = True
    liquidity_relative_percentiles_ready: Literal[True] = True
    liquidity_score_v1_ready: Literal[True] = True
    liquidity_score_credit_independent: Literal[True] = True
    legacy_liquidity_score_used_as_input: Literal[False] = False
    legacy_liquidity_score_updated: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    liquidity_adjusted_spread_ready: Literal[False] = False
    modified_duration_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False


class BondLiquidityFeatureView(BondLiquidityWindowStatistics):
    contract_version: Literal["bond-liquidity-feature-v1"] = BOND_LIQUIDITY_FEATURE_CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    window_start_date: date
    market_source: str
    lookback_calendar_days: int
    min_observation_days: int
    score_status: LiquidityScoreStatus
    score_components: BondLiquidityScoreComponents
    liquidity_score_v1: Decimal | None
    availability: BondLiquidityAvailability
    provenance: BondLiquidityProvenance
    quality_flags: list[str]
    capabilities: BondLiquidityCapabilities = Field(default_factory=BondLiquidityCapabilities)
    pit_ready: Literal[False] = False
