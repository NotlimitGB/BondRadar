from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx

from .contracts import CbrBridgeError, CbrBridgeSourceStatus


ALLOWED_HOSTS = frozenset({"cbr.ru", "www.cbr.ru"})
USER_AGENT = "BondRadar-Task252/1.0 read-only-identity-source"
HTML_LIMIT_BYTES = 4 * 1024 * 1024
SOAP_LIMIT_BYTES = 2 * 1024 * 1024


def validate_cbr_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
    ):
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT, "invalid CBR source URL"
        )


class CbrIdentityHttpTransport:
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client = http_client
        self._sleep = sleep or time.sleep
        self.logical_calls: dict[str, int] = {
            "fullcolist": 0,
            "finorg_get_last_update": 0,
            "finorg_search_by_ogrns": 0,
        }
        self.attempt_count = 0

    def get_html(self, url: str) -> bytes:
        self.logical_calls["fullcolist"] += 1
        return self._request("GET", url, byte_limit=HTML_LIMIT_BYTES)

    def post_soap(self, url: str, *, action: str, body: bytes) -> bytes:
        key = (
            "finorg_get_last_update"
            if action.endswith("/GetLastUpdate")
            else "finorg_search_by_ogrns"
        )
        self.logical_calls[key] += 1
        return self._request(
            "POST",
            url,
            byte_limit=SOAP_LIMIT_BYTES,
            body=body,
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"{action}"',
            },
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        byte_limit: int,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        validate_cbr_url(url)
        request_headers = {"User-Agent": USER_AGENT}
        if headers:
            request_headers.update(headers)
        client = self._client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=5.0, write=10.0, pool=5.0),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )
        close_client = self._client is None
        try:
            for attempt in range(3):
                current = url
                redirects = 0
                try:
                    while True:
                        self.attempt_count += 1
                        with client.stream(
                            method,
                            current,
                            content=body,
                            headers=request_headers,
                        ) as response:
                            if 300 <= response.status_code < 400:
                                location = response.headers.get("location")
                                if location is None or redirects >= 3:
                                    raise CbrBridgeError(
                                        CbrBridgeSourceStatus.SOURCE_ERROR,
                                        "invalid CBR redirect",
                                    )
                                current = urljoin(current, location)
                                validate_cbr_url(current)
                                redirects += 1
                                continue
                            if response.status_code == 429:
                                if attempt == 2:
                                    raise CbrBridgeError(
                                        CbrBridgeSourceStatus.RATE_LIMITED,
                                        "CBR rate limit reached",
                                    )
                                break
                            if response.status_code in {500, 502, 503, 504}:
                                if attempt == 2:
                                    raise CbrBridgeError(
                                        CbrBridgeSourceStatus.SOURCE_ERROR,
                                        "CBR source unavailable",
                                    )
                                break
                            if response.status_code >= 400:
                                raise CbrBridgeError(
                                    CbrBridgeSourceStatus.SOURCE_ERROR,
                                    "CBR request failed",
                                )
                            declared = response.headers.get("content-length")
                            if declared:
                                try:
                                    declared_size = int(declared)
                                except ValueError as exc:
                                    raise CbrBridgeError(
                                        CbrBridgeSourceStatus.INVALID_CONTENT,
                                        "invalid CBR content length",
                                    ) from exc
                                if declared_size > byte_limit:
                                    raise CbrBridgeError(
                                        CbrBridgeSourceStatus.RESPONSE_TOO_LARGE,
                                        "CBR response limit exceeded",
                                    )
                            chunks: list[bytes] = []
                            total = 0
                            for chunk in response.iter_bytes():
                                total += len(chunk)
                                if total > byte_limit:
                                    raise CbrBridgeError(
                                        CbrBridgeSourceStatus.RESPONSE_TOO_LARGE,
                                        "CBR response limit exceeded",
                                    )
                                chunks.append(chunk)
                            return b"".join(chunks)
                except httpx.TimeoutException as exc:
                    if attempt == 2:
                        raise CbrBridgeError(
                            CbrBridgeSourceStatus.TIMEOUT,
                            "CBR source timed out",
                        ) from exc
                except httpx.NetworkError as exc:
                    if attempt == 2:
                        raise CbrBridgeError(
                            CbrBridgeSourceStatus.SOURCE_ERROR,
                            "CBR source unavailable",
                        ) from exc
                self._sleep(0.1 * (2**attempt))
        finally:
            if close_client:
                client.close()
        raise CbrBridgeError(CbrBridgeSourceStatus.SOURCE_ERROR, "CBR request failed")
