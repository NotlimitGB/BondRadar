from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from app.core.config import settings


class MoexIssClientError(RuntimeError):
    pass


@dataclass
class MoexHistoryResult:
    rows: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)


@dataclass
class MoexCashflowScheduleResult:
    coupons: list[dict[str, Any]] = field(default_factory=list)
    amortizations: list[dict[str, Any]] = field(default_factory=list)
    offers: list[dict[str, Any]] = field(default_factory=list)
    redemptions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class MoexIssClient:
    MAX_PAGES = 100
    BOND_UNIVERSE_PATH_TEMPLATE = (
        "/iss/engines/stock/markets/bonds/boards/{board}/securities.json"
    )
    BOND_DESCRIPTION_PATH_TEMPLATE = "/iss/securities/{secid}.json"
    CASHFLOW_PATH_TEMPLATE = (
        "/iss/statistics/engines/stock/markets/bonds/bondization/{secid}.json"
    )
    BOND_METADATA_ALIASES = {
        "secid": ("secid", "SECID"),
        "isin": ("isin", "ISIN", "isincode", "ISINCODE"),
        "shortname": ("shortname", "SHORTNAME", "short_name"),
        "name": ("secname", "SECNAME", "name", "NAME", "fullname", "FULLNAME"),
        "issuer_name": (
            "issuer_name",
            "ISSUER_NAME",
            "emitent_title",
            "EMITENT_TITLE",
            "emitentname",
            "EMITENTNAME",
            "emitent_full_name",
            "EMITENT_FULL_NAME",
            "issuer",
            "ISSUER",
        ),
        "issuer_inn": (
            "issuer_inn",
            "ISSUER_INN",
            "emitent_inn",
            "EMITENT_INN",
            "inn",
            "INN",
        ),
        "currency": (
            "currency",
            "CURRENCY",
            "currencyid",
            "CURRENCYID",
            "faceunit",
            "FACEUNIT",
        ),
        "nominal_value": (
            "nominal_value",
            "NOMINAL_VALUE",
            "facevalue",
            "FACEVALUE",
            "faceval",
            "FACEVAL",
            "nominal",
            "NOMINAL",
        ),
        "coupon_rate": (
            "coupon_rate",
            "COUPON_RATE",
            "couponpercent",
            "COUPONPERCENT",
            "coupon_rate_percent",
        ),
        "maturity_date": (
            "maturity_date",
            "MATURITY_DATE",
            "matdate",
            "MATDATE",
            "maturitydate",
            "MATURITYDATE",
        ),
        "offer_date": ("offer_date", "OFFER_DATE", "offerdate", "OFFERDATE"),
        "is_perpetual": ("is_perpetual", "IS_PERPETUAL", "perpetual", "PERPETUAL"),
        "is_subordinated": (
            "is_subordinated",
            "IS_SUBORDINATED",
            "subordinated",
            "SUBORDINATED",
        ),
        "has_amortization": (
            "has_amortization",
            "HAS_AMORTIZATION",
            "amortization",
            "AMORTIZATION",
            "amortized",
            "AMORTIZED",
        ),
        "status": ("status", "STATUS", "secstatus", "SECSTATUS"),
        "is_traded": ("is_traded", "IS_TRADED", "istraded", "ISTRADED"),
    }
    CASHFLOW_TABLE_ALIASES = {
        "coupons": (
            "coupons",
            "coupon",
            "coupon_schedule",
            "coupons_schedule",
            "bondization_coupons",
        ),
        "amortizations": (
            "amortizations",
            "amortization",
            "amortization_schedule",
            "amortizations_schedule",
            "bondization_amortizations",
        ),
        "offers": (
            "offers",
            "offer",
            "offer_schedule",
            "offers_schedule",
            "bondization_offers",
        ),
        "redemptions": (
            "redemptions",
            "redemption",
            "maturities",
            "maturity",
            "bondization_redemptions",
        ),
    }
    BOND_MARKET_HISTORY_ALIASES = {
        "secid": ("secid", "SECID"),
        "trade_date": ("tradedate", "TRADEDATE"),
        "close_price": ("close", "CLOSE"),
        "last_price": ("last", "LAST", "lastprice", "LASTPRICE"),
        "market_price": (
            "marketprice",
            "MARKETPRICE",
            "marketprice2",
            "MARKETPRICE2",
        ),
        "weighted_average_price": ("waprice", "WAPRICE"),
        "legal_close_price": ("legalcloseprice", "LEGALCLOSEPRICE"),
        "yield_to_maturity": (
            "yield",
            "YIELD",
            "yieldclose",
            "YIELDCLOSE",
            "yieldatwaprice",
            "YIELDATWAPRICE",
        ),
        "duration": ("duration", "DURATION"),
        "volume": ("volume", "VOLUME"),
        "value": ("value", "VALUE"),
        "num_trades": ("numtrades", "NUMTRADES"),
        "currency": (
            "faceunit",
            "FACEUNIT",
            "currency",
            "CURRENCY",
            "currencyid",
            "CURRENCYID",
        ),
        "board": ("boardid", "BOARDID", "board", "BOARD"),
        "accrued_interest": ("accruedint", "ACCRUEDINT"),
    }

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
        http_client: httpx.Client | None = None,
        max_pages: int = MAX_PAGES,
    ) -> None:
        self.base_url = (base_url or settings.MOEX_ISS_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds or settings.MOEX_ISS_TIMEOUT_SECONDS
        self.http_client = http_client
        self.max_pages = max_pages

    def fetch_history(
        self,
        *,
        secid: str,
        board: str,
        date_from: date,
        date_to: date,
    ) -> MoexHistoryResult:
        rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        start = 0

        for page in range(self.max_pages):
            page_rows = self._fetch_page(
                secid=secid,
                board=board,
                date_from=date_from,
                date_to=date_to,
                start=start,
            )
            if not page_rows:
                return MoexHistoryResult(rows=rows, warnings=warnings)
            rows.extend(page_rows)
            start += len(page_rows)

        warnings.append("MOEX pagination max_pages reached")
        return MoexHistoryResult(rows=rows, warnings=warnings)

    def fetch_bond_cashflows(self, secid: str) -> MoexCashflowScheduleResult:
        path = self.CASHFLOW_PATH_TEMPLATE.format(secid=secid)
        params = {"iss.meta": "off"}

        try:
            if self.http_client is not None:
                response = self.http_client.get(path, params=params)
            else:
                with httpx.Client(
                    base_url=self.base_url,
                    timeout=self.timeout_seconds,
                ) as client:
                    response = client.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MoexIssClientError(f"MOEX request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MoexIssClientError("Invalid MOEX JSON response") from exc

        return self._parse_cashflow_tables(payload, secid)

    def fetch_bond_universe(
        self,
        board: str,
        start: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        path = self.BOND_UNIVERSE_PATH_TEMPLATE.format(board=board)
        payload = self._request_json(
            path,
            params={
                "iss.meta": "off",
                "start": start,
                "limit": limit,
            },
        )
        table_name = self._find_table_name(payload, ("securities",))
        if table_name is None:
            return [], [f"MOEX securities table is missing for board {board}"]
        rows = self._parse_named_table(payload, table_name)
        return [self._normalize_bond_metadata_row(row) for row in rows], []

    def fetch_bond_market_history(
        self,
        secid: str,
        *,
        board: str,
        date_from: date,
        date_to: date,
        start: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        path = (
            f"/iss/history/engines/stock/markets/bonds/boards/{board}"
            f"/securities/{secid}.json"
        )
        payload = self._request_json(
            path,
            params={
                "from": date_from.isoformat(),
                "till": date_to.isoformat(),
                "start": start,
                "limit": limit,
            },
        )
        table_name = self._find_table_name(payload, ("history",))
        if table_name is None:
            return [], [f"MOEX history table is missing for {secid}"]
        rows = self._parse_named_table(payload, table_name)
        return [self._normalize_bond_market_history_row(row) for row in rows], []

    def fetch_bond_description(
        self,
        secid: str,
        board: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        path = self.BOND_DESCRIPTION_PATH_TEMPLATE.format(secid=secid)
        params = {"iss.meta": "off"}
        if board:
            params["boards"] = board
        payload = self._request_json(path, params=params)
        warnings: list[str] = []
        raw: dict[str, Any] = {}

        description_table = self._find_table_name(payload, ("description",))
        if description_table is not None:
            rows = self._parse_named_table(payload, description_table)
            raw.update(self._description_rows_to_dict(rows))
        else:
            warnings.append(f"MOEX description table is missing for {secid}")

        securities_table = self._find_table_name(payload, ("securities",))
        if securities_table is not None:
            security_rows = self._parse_named_table(payload, securities_table)
            if security_rows:
                raw.update(security_rows[0])

        if not raw:
            warnings.append(f"No MOEX metadata rows found for {secid}")
            raw = {"SECID": secid}
        elif not self._has_value(self._first_value(raw, ("secid", "SECID"))):
            raw["SECID"] = secid

        return self._normalize_bond_metadata_row(raw), warnings

    def _fetch_page(
        self,
        *,
        secid: str,
        board: str,
        date_from: date,
        date_to: date,
        start: int,
    ) -> list[dict[str, Any]]:
        path = (
            f"/iss/history/engines/stock/markets/bonds/boards/{board}"
            f"/securities/{secid}.json"
        )
        params = {
            "from": date_from.isoformat(),
            "till": date_to.isoformat(),
            "start": start,
        }

        try:
            if self.http_client is not None:
                response = self.http_client.get(path, params=params)
            else:
                with httpx.Client(
                    base_url=self.base_url,
                    timeout=self.timeout_seconds,
                ) as client:
                    response = client.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MoexIssClientError(f"MOEX request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MoexIssClientError("Invalid MOEX JSON response") from exc

        return self._parse_history_table(payload)

    def _request_json(
        self,
        path: str,
        *,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            if self.http_client is not None:
                response = self.http_client.get(path, params=params)
            else:
                with httpx.Client(
                    base_url=self.base_url,
                    timeout=self.timeout_seconds,
                ) as client:
                    response = client.get(path, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise MoexIssClientError(f"MOEX request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise MoexIssClientError("Invalid MOEX JSON response") from exc
        if not isinstance(payload, dict):
            raise MoexIssClientError("Invalid MOEX JSON response")
        return payload

    @staticmethod
    def _parse_history_table(payload: dict[str, Any]) -> list[dict[str, Any]]:
        history = payload.get("history")
        if not isinstance(history, dict):
            raise MoexIssClientError("Invalid MOEX response: history table is missing")

        columns = history.get("columns")
        data = history.get("data")
        if not isinstance(columns, list) or not isinstance(data, list):
            raise MoexIssClientError("Invalid MOEX response: columns/data are missing")

        rows: list[dict[str, Any]] = []
        for raw_row in data:
            if not isinstance(raw_row, list):
                raise MoexIssClientError("Invalid MOEX response: row is not a list")
            rows.append(dict(zip(columns, raw_row, strict=False)))
        return rows

    @classmethod
    def _parse_cashflow_tables(
        cls,
        payload: dict[str, Any],
        secid: str,
    ) -> MoexCashflowScheduleResult:
        warnings: list[str] = []
        normalized: dict[str, list[dict[str, Any]]] = {
            "coupons": [],
            "amortizations": [],
            "offers": [],
            "redemptions": [],
        }

        for normalized_name, aliases in cls.CASHFLOW_TABLE_ALIASES.items():
            table_name = cls._find_table_name(payload, aliases)
            if table_name is None:
                warnings.append(
                    f"MOEX cashflow table {normalized_name} is missing for {secid}"
                )
                continue
            rows = cls._parse_named_table(payload, table_name)
            for row in rows:
                row["__moex_source_table"] = table_name
            normalized[normalized_name] = rows

        return MoexCashflowScheduleResult(
            coupons=normalized["coupons"],
            amortizations=normalized["amortizations"],
            offers=normalized["offers"],
            redemptions=normalized["redemptions"],
            warnings=warnings,
        )

    @staticmethod
    def _find_table_name(
        payload: dict[str, Any],
        aliases: tuple[str, ...],
    ) -> str | None:
        payload_keys = {key.lower(): key for key in payload}
        for alias in aliases:
            table_name = payload_keys.get(alias.lower())
            if table_name is not None:
                return table_name
        return None

    @staticmethod
    def _parse_named_table(
        payload: dict[str, Any],
        table_name: str,
    ) -> list[dict[str, Any]]:
        table = payload.get(table_name)
        if not isinstance(table, dict):
            raise MoexIssClientError(
                f"Invalid MOEX response: {table_name} table is invalid"
            )

        columns = table.get("columns")
        data = table.get("data")
        if not isinstance(columns, list) or not isinstance(data, list):
            raise MoexIssClientError(
                f"Invalid MOEX response: {table_name} columns/data are missing"
            )

        rows: list[dict[str, Any]] = []
        for raw_row in data:
            if not isinstance(raw_row, list):
                raise MoexIssClientError(
                    f"Invalid MOEX response: {table_name} row is not a list"
                )
            rows.append(dict(zip(columns, raw_row, strict=False)))
        return rows

    @classmethod
    def _normalize_bond_metadata_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for target, aliases in cls.BOND_METADATA_ALIASES.items():
            normalized[target] = cls._first_value(row, aliases)
        normalized["raw"] = dict(row)
        return normalized

    @classmethod
    def _normalize_bond_market_history_row(cls, row: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for target, aliases in cls.BOND_MARKET_HISTORY_ALIASES.items():
            normalized[target] = cls._first_value(row, aliases)
        normalized["raw"] = dict(row)
        return normalized

    @staticmethod
    def _description_rows_to_dict(rows: list[dict[str, Any]]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for row in rows:
            key = MoexIssClient._first_value(row, ("name", "NAME", "code", "CODE"))
            if not MoexIssClient._has_value(key):
                key = MoexIssClient._first_value(row, ("title", "TITLE"))
            value = MoexIssClient._first_value(row, ("value", "VALUE"))
            if MoexIssClient._has_value(key):
                values[str(key)] = value
        return values

    @staticmethod
    def _first_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
        normalized = {str(key).lower(): value for key, value in row.items()}
        for alias in aliases:
            value = normalized.get(str(alias).lower())
            if MoexIssClient._has_value(value):
                return value
        return None

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value is not None and value != ""
