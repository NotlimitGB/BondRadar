from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


CONTRACT_VERSION = "tinvest-current-instrument-universe-v1"


class TInvestUniverseFailureCode(StrEnum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    TRANSIENT_SOURCE_ERROR = "TRANSIENT_SOURCE_ERROR"
    SOURCE_RESPONSE_INVALID = "SOURCE_RESPONSE_INVALID"
    SOURCE_IDENTITY_CONFLICT = "SOURCE_IDENTITY_CONFLICT"
    REQUEST_REJECTED = "REQUEST_REJECTED"


class TInvestUniverseSourceError(RuntimeError):
    """Sanitized source failure; never contains request or response contents."""

    _MESSAGES = {
        TInvestUniverseFailureCode.AUTHENTICATION_FAILED: "T-Invest authentication failed",
        TInvestUniverseFailureCode.PERMISSION_DENIED: "T-Invest permission denied",
        TInvestUniverseFailureCode.RATE_LIMITED: "T-Invest rate limit reached",
        TInvestUniverseFailureCode.TIMEOUT: "T-Invest request timed out",
        TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR: "T-Invest source temporarily unavailable",
        TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID: "T-Invest response is invalid",
        TInvestUniverseFailureCode.SOURCE_IDENTITY_CONFLICT: "T-Invest response contains conflicting instrument identity",
        TInvestUniverseFailureCode.REQUEST_REJECTED: "T-Invest rejected the read request",
    }

    def __init__(
        self,
        code: TInvestUniverseFailureCode,
        *,
        http_status: int | None = None,
    ) -> None:
        self.code = code
        self.http_status = http_status
        super().__init__(self._MESSAGES[code])


class TInvestAvailabilityClass(StrEnum):
    API_BUY_AVAILABLE = "API_BUY_AVAILABLE"
    API_VISIBLE_NOT_BUYABLE = "API_VISIBLE_NOT_BUYABLE"
    API_TRADE_UNAVAILABLE = "API_TRADE_UNAVAILABLE"


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class TInvestBondsBaseResponse(_StrictFrozenModel):
    """Raw response bound to the exact actionable Bonds request contract."""

    method: Literal["Bonds"] = "Bonds"
    instrument_status: Literal["INSTRUMENT_STATUS_BASE"] = "INSTRUMENT_STATUS_BASE"
    response: Any


class TInvestDfasResponse(_StrictFrozenModel):
    """Raw response bound to the documented empty Dfas request contract."""

    method: Literal["Dfas"] = "Dfas"
    request_body: dict[str, Any] = Field(default_factory=dict)
    response: Any


class TInvestBondUniverseInstrument(_StrictFrozenModel):
    contract_version: Literal["tinvest-current-instrument-universe-v1"] = CONTRACT_VERSION
    source_universe: Literal["BASE"] = "BASE"
    instrument_classification: Literal["TINVEST_BASE_BOND"] = "TINVEST_BASE_BOND"
    listed_in_source_universe: Literal[True] = True

    uid: str
    position_uid: str | None = None
    asset_uid: str | None = None
    figi: str | None = None
    isin: str | None = None
    ticker: str | None = None
    class_code: str | None = None
    name: str | None = None
    lot: int | None = None
    currency: str | None = None

    buy_available: bool | None = None
    sell_available: bool | None = None
    api_trade_available: bool | None = None
    for_qual_investor: bool | None = None
    required_tests: tuple[str, ...] | None = None
    required_tests_state: Literal["NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"]

    floating_coupon: bool | None = None
    perpetual: bool | None = None
    amortizing: bool | None = None
    subordinated: bool | None = None
    maturity_date: str | None = None
    call_date: str | None = None

    availability_classification: TInvestAvailabilityClass | None = None
    source_fields: dict[str, Any] = Field(repr=False)


class TInvestDfaUniverseInstrument(_StrictFrozenModel):
    contract_version: Literal["tinvest-current-instrument-universe-v1"] = CONTRACT_VERSION
    source_universe: Literal["DFA_DISCOVERED"] = "DFA_DISCOVERED"
    instrument_classification: Literal["TINVEST_DFA_DISCOVERED"] = "TINVEST_DFA_DISCOVERED"
    listed_in_source_universe: Literal[True] = True

    uid: str
    position_uid: str | None = None
    asset_uid: str | None = None
    figi: str | None = None
    isin: str | None = None
    ticker: str | None = None
    class_code: str | None = None
    name: str | None = None
    lot: int | None = None
    currency: str | None = None
    maturity_date: str | None = None

    api_trade_available: bool | None = None
    buy_available: bool | None = None
    sell_available: bool | None = None
    limit_order_available: bool | None = None
    market_order_available: bool | None = None
    bestprice_order_available: bool | None = None
    for_iis: bool | None = None
    for_qual_investor: bool | None = None
    dfa_type: str | None = None
    basic_assets: tuple[dict[str, Any], ...] | None = None

    # DfasRequest has no required-tests field. None is not equivalent to an empty list.
    required_tests: tuple[str, ...] | None = None
    required_tests_state: Literal["NOT_SUPPLIED"] = "NOT_SUPPLIED"

    availability_classification: TInvestAvailabilityClass | None = None
    source_fields: dict[str, Any] = Field(repr=False)


class TInvestCurrentInstrumentUniverse(_StrictFrozenModel):
    contract_version: Literal["tinvest-current-instrument-universe-v1"] = CONTRACT_VERSION
    bond_universe_method: Literal["Bonds"] = "Bonds"
    bond_universe_status: Literal["INSTRUMENT_STATUS_BASE"] = "INSTRUMENT_STATUS_BASE"
    dfa_universe_method: Literal["Dfas"] = "Dfas"
    dfa_has_instrument_status_filter: Literal[False] = False

    bonds: tuple[TInvestBondUniverseInstrument, ...]
    dfas: tuple[TInvestDfaUniverseInstrument, ...]
    bond_count: int
    dfa_count: int

    research_universe_preserved: Literal[True] = True
    personal_qualification_resolved: Literal[False] = False
    historical_universe_ready: Literal[False] = False
    pit_ready: Literal[False] = False
