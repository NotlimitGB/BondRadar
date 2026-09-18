"""Computed Task272 duration under an explicit engineering convention."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = "bond-modified-duration-v1"
ModifiedDurationStatus = Literal[
    "READY", "MARKET_DATA_MISSING", "MARKET_DATA_STALE",
    "MACAULAY_DURATION_MISSING", "MACAULAY_DURATION_INVALID",
    "YIELD_TO_MATURITY_MISSING", "YIELD_TO_MATURITY_INVALID",
    "SECURITY_MASTER_MISSING", "COUPON_STRUCTURE_NOT_FIXED",
    "COUPON_FREQUENCY_NOT_VERIFIED", "COUPON_FREQUENCY_MISSING", "COUPON_FREQUENCY_INVALID",
    "PERPETUAL_STRUCTURE_NOT_DATED", "INVALID_MODIFIED_DURATION_DENOMINATOR",
]


class DurationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BondModifiedDurationAvailability(DurationModel):
    has_market_snapshot: bool
    has_fresh_market_data: bool
    has_macaulay_duration: bool
    has_yield_to_maturity: bool
    has_security_master: bool
    has_fixed_coupon_structure: bool
    has_verified_coupon_frequency: bool
    has_dated_structure: bool
    has_valid_denominator: bool
    has_modified_duration: bool


class BondModifiedDurationProvenance(DurationModel):
    market_contract_version: str
    market_snapshot_id: int | None
    market_trade_date: date | None
    market_source: str
    as_of_date: date
    max_market_age_days: int
    security_master_profile_id: int | None
    security_master_contract_version: str | None
    coupon_frequency_state: Literal["unknown", "verified", "conflict"] | None
    coupon_frequency_per_year: int | None = Field(strict=True)
    formula_version: Literal["modified-duration-v1"] = "modified-duration-v1"


class BondModifiedDurationCapabilities(DurationModel):
    macaulay_duration_input_ready: Literal[True] = True
    modified_duration_ready: Literal[True] = True
    dv01_ready: Literal[False] = False
    pvbp_ready: Literal[False] = False
    convexity_ready: Literal[False] = False
    effective_duration_ready: Literal[False] = False
    rate_shock_approximation_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondModifiedDurationView(DurationModel):
    contract_version: Literal["bond-modified-duration-v1"] = CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: str
    market_snapshot_id: int | None
    market_trade_date: date | None
    market_age_days: int | None
    market_status: Literal["MISSING", "FRESH", "STALE"]
    macaulay_duration_years: Decimal | None
    yield_to_maturity_pct: Decimal | None
    yield_decimal: Decimal | None
    coupon_structure: Literal["unknown", "fixed", "floating", "conflict"] | None
    coupon_frequency_state: Literal["unknown", "verified", "conflict"] | None
    coupon_frequency_per_year: int | None = Field(strict=True)
    perpetual_structure: Literal["unknown", "dated", "perpetual", "conflict"] | None
    modified_duration_denominator: Decimal | None
    modified_duration_years: Decimal | None
    status: ModifiedDurationStatus
    availability: BondModifiedDurationAvailability
    quality_flags: list[str]
    provenance: BondModifiedDurationProvenance
    capabilities: BondModifiedDurationCapabilities = Field(default_factory=BondModifiedDurationCapabilities)
    pit_ready: Literal[False] = False
