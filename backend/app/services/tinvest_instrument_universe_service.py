from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Callable, TypeVar

from app.schemas.tinvest_instrument_universe import (
    TInvestAvailabilityClass,
    TInvestBondsBaseResponse,
    TInvestBondUniverseInstrument,
    TInvestCurrentInstrumentUniverse,
    TInvestDfasResponse,
    TInvestDfaUniverseInstrument,
    TInvestUniverseFailureCode,
    TInvestUniverseSourceError,
)


_InstrumentT = TypeVar("_InstrumentT", TInvestBondUniverseInstrument, TInvestDfaUniverseInstrument)


class TInvestInstrumentUniverseService:
    """Pure reducer for already-fetched Bonds(BASE) and Dfas responses."""

    @staticmethod
    def reduce_current_universe(
        bonds_response: TInvestBondsBaseResponse,
        dfas_response: TInvestDfasResponse,
    ) -> TInvestCurrentInstrumentUniverse:
        if (
            type(bonds_response) is not TInvestBondsBaseResponse
            or bonds_response.method != "Bonds"
            or bonds_response.instrument_status != "INSTRUMENT_STATUS_BASE"
            or type(dfas_response) is not TInvestDfasResponse
            or dfas_response.method != "Dfas"
            or type(dfas_response.request_body) is not dict
            or dfas_response.request_body != {}
        ):
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        bonds = TInvestInstrumentUniverseService._normalize_collection(
            bonds_response.response,
            TInvestInstrumentUniverseService._normalize_bond,
        )
        dfas = TInvestInstrumentUniverseService._normalize_collection(
            dfas_response.response,
            TInvestInstrumentUniverseService._normalize_dfa,
        )
        return TInvestCurrentInstrumentUniverse(
            bonds=bonds,
            dfas=dfas,
            bond_count=len(bonds),
            dfa_count=len(dfas),
        )

    @staticmethod
    def _normalize_collection(
        response: Any,
        normalize: Callable[[Mapping[str, Any]], _InstrumentT],
    ) -> tuple[_InstrumentT, ...]:
        if not isinstance(response, Mapping) or any(
            type(key) is not str for key in response
        ):
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        instruments = response.get("instruments")
        if type(instruments) is not list:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )

        rows: list[_InstrumentT] = []
        seen_uids: set[str] = set()
        for item in instruments:
            if not isinstance(item, Mapping) or any(
                type(key) is not str for key in item
            ):
                raise TInvestUniverseSourceError(
                    TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
                )
            instrument = normalize(item)
            if instrument.uid in seen_uids:
                raise TInvestUniverseSourceError(
                    TInvestUniverseFailureCode.SOURCE_IDENTITY_CONFLICT
                )
            seen_uids.add(instrument.uid)
            rows.append(instrument)
        return tuple(sorted(rows, key=lambda item: item.uid))

    @staticmethod
    def _normalize_bond(row: Mapping[str, Any]) -> TInvestBondUniverseInstrument:
        uid = TInvestInstrumentUniverseService._required_uid(row)
        required_tests, required_tests_state = (
            TInvestInstrumentUniverseService._optional_string_list(row, "requiredTests")
        )
        api_trade_available = TInvestInstrumentUniverseService._optional_bool(
            row, "apiTradeAvailableFlag"
        )
        buy_available = TInvestInstrumentUniverseService._optional_bool(
            row, "buyAvailableFlag"
        )
        return TInvestBondUniverseInstrument(
            uid=uid,
            position_uid=TInvestInstrumentUniverseService._optional_string(
                row, "positionUid", identifier=True
            ),
            asset_uid=TInvestInstrumentUniverseService._optional_string(
                row, "assetUid", identifier=True
            ),
            figi=TInvestInstrumentUniverseService._optional_string(
                row, "figi", identifier=True
            ),
            isin=TInvestInstrumentUniverseService._optional_string(
                row, "isin", identifier=True
            ),
            ticker=TInvestInstrumentUniverseService._optional_string(
                row, "ticker", identifier=True
            ),
            class_code=TInvestInstrumentUniverseService._optional_string(
                row, "classCode", identifier=True
            ),
            name=TInvestInstrumentUniverseService._optional_string(row, "name"),
            lot=TInvestInstrumentUniverseService._optional_int(row, "lot"),
            currency=TInvestInstrumentUniverseService._optional_string(row, "currency"),
            buy_available=buy_available,
            sell_available=TInvestInstrumentUniverseService._optional_bool(
                row, "sellAvailableFlag"
            ),
            api_trade_available=api_trade_available,
            for_qual_investor=TInvestInstrumentUniverseService._optional_bool(
                row, "forQualInvestorFlag"
            ),
            required_tests=required_tests,
            required_tests_state=required_tests_state,
            floating_coupon=TInvestInstrumentUniverseService._optional_bool(
                row, "floatingCouponFlag"
            ),
            perpetual=TInvestInstrumentUniverseService._optional_bool(
                row, "perpetualFlag"
            ),
            amortizing=TInvestInstrumentUniverseService._optional_bool(
                row, "amortizationFlag"
            ),
            subordinated=TInvestInstrumentUniverseService._optional_bool(
                row, "subordinatedFlag"
            ),
            maturity_date=TInvestInstrumentUniverseService._optional_string(
                row, "maturityDate"
            ),
            call_date=TInvestInstrumentUniverseService._optional_string(
                row, "callDate"
            ),
            availability_classification=TInvestInstrumentUniverseService._classify_availability(
                api_trade_available, buy_available
            ),
            source_fields=deepcopy(dict(row)),
        )

    @staticmethod
    def _normalize_dfa(row: Mapping[str, Any]) -> TInvestDfaUniverseInstrument:
        uid = TInvestInstrumentUniverseService._required_uid(row)
        api_trade_available = TInvestInstrumentUniverseService._optional_bool(
            row, "apiTradeAvailableFlag"
        )
        buy_available = TInvestInstrumentUniverseService._optional_bool(
            row, "buyAvailableFlag"
        )
        basic_assets = TInvestInstrumentUniverseService._optional_object_list(
            row, "basicAssets"
        )
        return TInvestDfaUniverseInstrument(
            uid=uid,
            position_uid=TInvestInstrumentUniverseService._optional_string(
                row, "positionUid", identifier=True
            ),
            asset_uid=TInvestInstrumentUniverseService._optional_string(
                row, "assetUid", identifier=True
            ),
            figi=TInvestInstrumentUniverseService._optional_string(
                row, "figi", identifier=True
            ),
            isin=TInvestInstrumentUniverseService._optional_string(
                row, "isin", identifier=True
            ),
            ticker=TInvestInstrumentUniverseService._optional_string(
                row, "ticker", identifier=True
            ),
            class_code=TInvestInstrumentUniverseService._optional_string(
                row, "classCode", identifier=True
            ),
            name=TInvestInstrumentUniverseService._optional_string(row, "name"),
            lot=TInvestInstrumentUniverseService._optional_int(row, "lot"),
            currency=TInvestInstrumentUniverseService._optional_string(row, "currency"),
            maturity_date=TInvestInstrumentUniverseService._optional_string(
                row, "maturityDate"
            ),
            api_trade_available=api_trade_available,
            buy_available=buy_available,
            sell_available=TInvestInstrumentUniverseService._optional_bool(
                row, "sellAvailableFlag"
            ),
            limit_order_available=TInvestInstrumentUniverseService._optional_bool(
                row, "limitOrderAvailableFlag"
            ),
            market_order_available=TInvestInstrumentUniverseService._optional_bool(
                row, "marketOrderAvailableFlag"
            ),
            bestprice_order_available=TInvestInstrumentUniverseService._optional_bool(
                row, "bestpriceOrderAvailableFlag"
            ),
            for_iis=TInvestInstrumentUniverseService._optional_bool(row, "forIisFlag"),
            for_qual_investor=TInvestInstrumentUniverseService._optional_bool(
                row, "forQualInvestorFlag"
            ),
            dfa_type=TInvestInstrumentUniverseService._optional_string(row, "type"),
            basic_assets=basic_assets,
            availability_classification=TInvestInstrumentUniverseService._classify_availability(
                api_trade_available, buy_available
            ),
            source_fields=deepcopy(dict(row)),
        )

    @staticmethod
    def _required_uid(row: Mapping[str, Any]) -> str:
        value = row.get("uid")
        if type(value) is not str or not value.strip():
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        return value

    @staticmethod
    def _optional_string(
        row: Mapping[str, Any], key: str, *, identifier: bool = False
    ) -> str | None:
        if key not in row or row[key] is None:
            return None
        value = row[key]
        if type(value) is not str or (identifier and not value.strip()):
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        return value

    @staticmethod
    def _optional_bool(row: Mapping[str, Any], key: str) -> bool | None:
        if key not in row:
            return None
        value = row[key]
        if type(value) is not bool:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        return value

    @staticmethod
    def _optional_int(row: Mapping[str, Any], key: str) -> int | None:
        if key not in row or row[key] is None:
            return None
        value = row[key]
        if type(value) is not int:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        return value

    @staticmethod
    def _optional_string_list(
        row: Mapping[str, Any], key: str
    ) -> tuple[tuple[str, ...] | None, str]:
        if key not in row or row[key] is None:
            return None, "NOT_SUPPLIED"
        value = row[key]
        if type(value) is not list or any(type(item) is not str for item in value):
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        result = tuple(value)
        return result, "SOURCE_EMPTY" if not result else "SOURCE_VALUES"

    @staticmethod
    def _optional_object_list(
        row: Mapping[str, Any], key: str
    ) -> tuple[dict[str, Any], ...] | None:
        if key not in row or row[key] is None:
            return None
        value = row[key]
        if type(value) is not list or any(
            not isinstance(item, Mapping)
            or any(type(item_key) is not str for item_key in item)
            for item in value
        ):
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID
            )
        return tuple(deepcopy(dict(item)) for item in value)

    @staticmethod
    def _classify_availability(
        api_trade_available: bool | None,
        buy_available: bool | None,
    ) -> TInvestAvailabilityClass | None:
        if api_trade_available is False:
            return TInvestAvailabilityClass.API_TRADE_UNAVAILABLE
        if api_trade_available is True and buy_available is True:
            return TInvestAvailabilityClass.API_BUY_AVAILABLE
        if api_trade_available is True and buy_available is False:
            return TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE
        return None
