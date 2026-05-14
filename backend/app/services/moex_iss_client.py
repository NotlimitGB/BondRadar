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


class MoexIssClient:
    MAX_PAGES = 100

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
