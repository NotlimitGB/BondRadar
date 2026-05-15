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
    CASHFLOW_PATH_TEMPLATE = (
        "/iss/statistics/engines/stock/markets/bonds/bondization/{secid}.json"
    )
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
