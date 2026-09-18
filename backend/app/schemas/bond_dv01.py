"""Computed per-bond MOEX/RUB one-basis-point sensitivity contract."""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.bond_modified_duration import ModifiedDurationStatus

CONTRACT_VERSION = "bond-dv01-v1"
EvidenceState = Literal["unknown", "verified", "conflict"]
PriceBasis = Literal["CLEAN_PRICE", "PRICE_FALLBACK"]
Dv01Status = Literal[
    "READY", "MARKET_CONTRACT_MISMATCH", "MODIFIED_DURATION_UNAVAILABLE",
    "SECURITY_MASTER_MISSING", "CURRENCY_NOT_VERIFIED", "CURRENCY_MISSING",
    "UNSUPPORTED_CURRENCY", "NOMINAL_NOT_VERIFIED", "NOMINAL_MISSING", "NOMINAL_INVALID",
    "MARKET_PRICE_MISSING", "CLEAN_PRICE_INVALID", "MARKET_PRICE_INVALID",
    "NKD_MISSING", "NKD_INVALID", "DIRTY_VALUE_INVALID",
]


class Dv01Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BondDv01MarketIdentity(Dv01Model):
    bond_id: int
    market_snapshot_id: int | None
    market_trade_date: date | None
    market_source: str


class BondDv01Availability(Dv01Model):
    has_modified_duration: bool
    has_matching_market_snapshot: bool
    has_security_master: bool
    has_verified_currency: bool
    is_supported_currency: bool
    has_verified_nominal: bool
    has_clean_quote: bool
    has_nkd: bool
    has_clean_value: bool
    has_dirty_value: bool
    has_relative_1bp_sensitivity: bool
    has_dv01: bool


class BondDv01Provenance(Dv01Model):
    modified_duration_contract_version: str
    modified_duration_formula_version: str
    modified_duration_market_identity: BondDv01MarketIdentity
    market_contract_version: str
    market_identity: BondDv01MarketIdentity
    market_snapshot_id: int | None
    market_trade_date: date | None
    market_source: str
    as_of_date: date
    max_market_age_days: int
    security_master_profile_id: int | None
    security_master_contract_version: str | None
    currency_state: EvidenceState | None
    nominal_state: EvidenceState | None
    price_basis: PriceBasis | None
    dv01_formula_version: Literal["dv01-v1"] = "dv01-v1"


class BondDv01Capabilities(Dv01Model):
    modified_duration_input_ready: Literal[True] = True
    relative_1bp_sensitivity_ready: Literal[True] = True
    dv01_ready: Literal[True] = True
    pvbp_ready: Literal[True] = True
    dv01_price_basis: Literal["DIRTY_VALUE"] = "DIRTY_VALUE"
    dv01_sign_convention: Literal["MAGNITUDE"] = "MAGNITUDE"
    portfolio_dv01_ready: Literal[False] = False
    key_rate_duration_ready: Literal[False] = False
    convexity_ready: Literal[False] = False
    effective_duration_ready: Literal[False] = False
    arbitrary_rate_shock_scenarios_ready: Literal[False] = False
    transaction_cost_model_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class BondDv01View(Dv01Model):
    contract_version: Literal["bond-dv01-v1"] = CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    market_source: Literal["moex"]
    market_snapshot_id: int | None
    market_trade_date: date | None
    market_age_days: int | None
    modified_duration_status: ModifiedDurationStatus
    modified_duration_years: Decimal | None
    currency_code: str | None
    currency_state: EvidenceState | None
    nominal_state: EvidenceState | None
    nominal_value: Decimal | None
    price_basis: PriceBasis | None
    clean_quote_pct: Decimal | None
    nkd_currency: Decimal | None
    clean_value_currency: Decimal | None
    dirty_value_currency: Decimal | None
    relative_price_sensitivity_per_1bp: Decimal | None
    dv01_currency_per_bond: Decimal | None
    status: Dv01Status
    availability: BondDv01Availability
    quality_flags: list[str]
    provenance: BondDv01Provenance
    capabilities: BondDv01Capabilities = Field(default_factory=BondDv01Capabilities)
    pit_ready: Literal[False] = False
