"""Read-only M3 market/structural feature contract; not a PIT engine."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "bond-market-feature-v1"


class FeatureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BondMarketFeatureAvailability(FeatureModel):
    has_market_snapshot: bool
    has_price: bool
    has_clean_price: bool
    has_nkd: bool
    has_yield_to_maturity: bool
    has_duration: bool
    has_trade_volume: bool
    has_turnover_value: bool
    has_num_trades: bool
    has_cashflow_schedule: bool
    has_maturity: bool
    has_offer: bool


class BondMarketFeatureProvenance(FeatureModel):
    market_snapshot_id: int | None
    market_source: str
    market_trade_date: date | None
    cashflow_source: Literal["moex"] = "moex"
    selected_cashflow_event_ids: list[int]


class BondMarketFeatureCapabilities(FeatureModel):
    liquidity_raw_inputs_ready: Literal[True] = True
    ofz_reference_curve_ready: Literal[False] = False
    spread_to_ofz_derivation_ready: Literal[False] = False
    liquidity_score_v1_ready: Literal[False] = False
    modified_duration_ready: Literal[False] = False
    credit_feature_join_ready: Literal[False] = False


class BondMarketFeatureView(FeatureModel):
    contract_version: Literal["bond-market-feature-v1"] = CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_snapshot_id: int | None
    market_trade_date: date | None
    market_source: str
    market_age_days: int | None
    market_status: Literal["MISSING", "FRESH", "STALE"]
    price: Decimal | None
    clean_price: Decimal | None
    nkd: Decimal | None
    yield_to_maturity_pct: Decimal | None
    duration_years: Decimal | None
    trade_volume: Decimal | None
    turnover_value: Decimal | None
    num_trades: int | None
    currency: str
    nominal_value: Decimal | None
    coupon_rate: Decimal | None
    maturity_date: date | None
    offer_date: date | None
    days_to_maturity: int | None
    days_to_offer: int | None
    is_matured: bool
    is_floating_coupon: bool
    is_subordinated: bool
    is_perpetual: bool
    metadata_has_amortization: bool | None
    future_cashflow_event_count: int
    future_coupon_count: int
    future_amortization_count: int
    next_coupon_date: date | None
    next_amortization_date: date | None
    next_redemption_date: date | None
    next_offer_redemption_date: date | None
    has_future_coupon: bool
    has_future_amortization: bool
    has_future_redemption: bool
    has_future_offer_redemption: bool
    spread_to_ofz: None = None
    availability: BondMarketFeatureAvailability
    quality_flags: list[str]
    provenance: BondMarketFeatureProvenance
    capabilities: BondMarketFeatureCapabilities = Field(
        default_factory=BondMarketFeatureCapabilities
    )
    pit_ready: Literal[False] = False
