from __future__ import annotations

from typing import Any

import httpx

from app.schemas.tinvest_instrument_universe import (
    TInvestBondsBaseResponse,
    TInvestDfasResponse,
    TInvestUniverseFailureCode,
    TInvestUniverseSourceError,
)


TINVEST_REST_BASE = "https://invest-public-api.tbank.ru/rest"
BONDS_ROUTE = "/tinkoff.public.invest.api.contract.v1.InstrumentsService/Bonds"
DFAS_ROUTE = "/tinkoff.public.invest.api.contract.v1.InstrumentsService/Dfas"
BONDS_BASE_REQUEST = {"instrumentStatus": "INSTRUMENT_STATUS_BASE"}
DFAS_REQUEST: dict[str, Any] = {}
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class TInvestInstrumentUniverseClient:
    """Narrow read-only InstrumentsService REST adapter for Bonds and DFAs."""

    def __init__(
        self,
        *,
        token: str,
        http_client: httpx.Client | None = None,
    ) -> None:
        if type(token) is not str or not token.strip():
            raise ValueError("an explicit nonblank bearer token is required")
        if http_client is not None and not isinstance(http_client, httpx.Client):
            raise ValueError("http_client must be an httpx.Client")
        self._token = token
        self._http_client = http_client

    def __repr__(self) -> str:
        return "TInvestInstrumentUniverseClient(token=<redacted>)"

    def list_base_bonds(self) -> TInvestBondsBaseResponse:
        response = self._post_instruments_read(BONDS_ROUTE, BONDS_BASE_REQUEST)
        return TInvestBondsBaseResponse(response=response)

    def list_dfas(self) -> TInvestDfasResponse:
        response = self._post_instruments_read(DFAS_ROUTE, DFAS_REQUEST)
        return TInvestDfasResponse(response=response)

    def _post_instruments_read(self, route: str, payload: dict[str, Any]) -> Any:
        if self._http_client is not None:
            return self._send(self._http_client, route, payload)

        with httpx.Client(
            timeout=REQUEST_TIMEOUT,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            return self._send(client, route, payload)

    def _send(
        self,
        client: httpx.Client,
        route: str,
        payload: dict[str, Any],
    ) -> Any:
        try:
            response = client.post(
                f"{TINVEST_REST_BASE}{route}",
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=REQUEST_TIMEOUT,
            )
        except httpx.TimeoutException:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.TIMEOUT
            ) from None
        except httpx.TransportError:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR
            ) from None

        status = response.status_code
        if status == 401:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.AUTHENTICATION_FAILED,
                http_status=status,
            )
        if status == 403:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.PERMISSION_DENIED,
                http_status=status,
            )
        if status == 429:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.RATE_LIMITED,
                http_status=status,
            )
        if status == 408:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.TIMEOUT,
                http_status=status,
            )
        if status in {500, 502, 503, 504}:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.TRANSIENT_SOURCE_ERROR,
                http_status=status,
            )
        if status < 200 or status >= 300:
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.REQUEST_REJECTED,
                http_status=status,
            )

        try:
            return response.json()
        except (ValueError, UnicodeDecodeError):
            raise TInvestUniverseSourceError(
                TInvestUniverseFailureCode.SOURCE_RESPONSE_INVALID,
                http_status=status,
            ) from None
