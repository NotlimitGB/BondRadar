from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import ssl
import time
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.services.credit_risk_evidence.acra.contracts import (
    AcraError, CATALOG_URL, FetchedPage, HOST, MAX_LIST_PAGES,
    MAX_RESPONSE_BYTES, issuer_url, listing_url, safe_url,
)


USER_AGENT = "BondRadar-credit-research/1.0"
TRANSIENT = {429, 500, 502, 503, 504}


def parse_listing(content: bytes, url: str) -> tuple[tuple[str, ...], str | None]:
    try:
        soup = BeautifulSoup(content.decode("utf-8", "strict"), "html.parser")
    except UnicodeError:
        raise AcraError("INVALID_HTML_ENCODING") from None
    issuers, next_pages = set(), set()
    for anchor in soup.find_all("a", href=True):
        candidate = urljoin(url, anchor["href"])
        try:
            canonical, _ = issuer_url(candidate)
            issuers.add(canonical)
        except AcraError:
            pass
        label = " ".join(anchor.get_text(" ", strip=True).split()).casefold()
        if "next" in anchor.get("rel", []) or label == "next":
            next_pages.add(listing_url(candidate))
    if len(next_pages) > 1:
        raise AcraError("AMBIGUOUS_PAGINATION")
    if not issuers:
        raise AcraError("UNRECOGNIZED_CATALOG_STRUCTURE")
    return tuple(sorted(issuers)), next(iter(next_pages), None)


class AcraClient:
    def __init__(
        self, *, client: httpx.Client | None = None, clock=None,
        monotonic=time.monotonic, sleep=time.sleep, max_attempts: int | None = None,
    ):
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.monotonic, self.sleep = monotonic, sleep
        self.client = client or httpx.Client(
            verify=ssl.create_default_context(), trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(30, connect=5, write=10, pool=5),
            headers={"User-Agent": USER_AGENT},
        )
        self.owned_client = client is None
        self.max_attempts = max_attempts
        self.http_requests = self.list_pages = self.issuer_pages = 0
        self.last_request = None
        self.robots = None

    def close(self):
        if self.owned_client:
            self.client.close()

    def _pace(self):
        if self.last_request is not None:
            self.sleep(max(0.0, 1.0 - (self.monotonic() - self.last_request)))
        if self.max_attempts is not None and self.http_requests >= self.max_attempts:
            raise AcraError("REQUEST_BUDGET_EXHAUSTED")
        self.last_request = self.monotonic()
        self.http_requests += 1

    def fetch(self, url: str, *, robots_request=False) -> FetchedPage | None:
        safe_url(url)
        if not robots_request:
            if self.robots is None:
                raise AcraError("ROBOTS_NOT_VERIFIED")
            if not self.robots.can_fetch(USER_AGENT, url):
                raise AcraError("SOURCE_ACCESS_BLOCKED")
        current, redirects = url, 0
        for attempt in range(3):
            try:
                while True:
                    self._pace()
                    self.client.cookies.clear()
                    with self.client.stream("GET", current, headers={"User-Agent": USER_AGENT}) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            if redirects >= 3 or not response.headers.get("location"):
                                raise AcraError("REDIRECT_LIMIT")
                            target = safe_url(urljoin(current, response.headers["location"]))
                            if not robots_request and not self.robots.can_fetch(USER_AGENT, target):
                                raise AcraError("SOURCE_ACCESS_BLOCKED")
                            current, redirects = target, redirects + 1
                            continue
                        if response.status_code == 404 and robots_request:
                            return None
                        if response.status_code in TRANSIENT:
                            if attempt == 2:
                                raise AcraError("TRANSIENT_SOURCE_FAILURE")
                            delay = self._retry_after(response.headers.get("retry-after"), attempt)
                            break
                        if response.status_code != 200:
                            raise AcraError("SOURCE_ACCESS_BLOCKED")
                        if sum(len(k) + len(v) for k, v in response.headers.items()) > 65536:
                            raise AcraError("OVERSIZED_HEADERS")
                        length = response.headers.get("content-length")
                        if length is not None:
                            try:
                                if int(length) < 0 or int(length) > MAX_RESPONSE_BYTES:
                                    raise AcraError("OVERSIZED_RESPONSE")
                            except ValueError as exc:
                                if isinstance(exc, AcraError):
                                    raise
                                raise AcraError("INVALID_CONTENT_LENGTH") from None
                        content_type = response.headers.get("content-type", "")
                        if not robots_request and content_type.split(";", 1)[0].lower() not in ("text/html", "application/xhtml+xml"):
                            raise AcraError("INVALID_CONTENT_TYPE")
                        body = bytearray()
                        for chunk in response.iter_bytes():
                            if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise AcraError("OVERSIZED_RESPONSE")
                            body.extend(chunk)
                        if not body:
                            raise AcraError("EMPTY_SOURCE_CONTENT")
                        lowered = bytes(body).lower()
                        if b"captcha" in lowered or b"cf-chl-" in lowered:
                            raise AcraError("SOURCE_ACCESS_BLOCKED")
                        return FetchedPage(current, bytes(body), content_type, self.clock())
                self.sleep(delay)
            except httpx.TimeoutException:
                if attempt == 2:
                    raise AcraError("SOURCE_TIMEOUT") from None
                self.sleep(2 ** (attempt + 1))
            except httpx.RequestError:
                # TLS/connection failures are not bypassed or treated as absence.
                raise AcraError("SOURCE_TRANSPORT_FAILURE") from None
        raise AcraError("TRANSIENT_SOURCE_FAILURE")

    def _retry_after(self, value: str | None, attempt: int) -> float:
        delay = float(2 ** (attempt + 1))
        if value:
            try:
                delay = max(delay, float(value))
            except ValueError:
                try:
                    delay = max(delay, (parsedate_to_datetime(value) - self.clock()).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(30.0, max(0.0, delay))

    def verify_robots(self):
        url = f"https://{HOST}/robots.txt"
        page = self.fetch(url, robots_request=True)
        rules = RobotFileParser(url)
        try:
            rules.parse([] if page is None else page.content_bytes.decode("utf-8", "strict").splitlines())
        except UnicodeError:
            raise AcraError("INVALID_ROBOTS") from None
        self.robots = rules
        if not rules.can_fetch(USER_AGENT, CATALOG_URL):
            raise AcraError("SOURCE_ACCESS_BLOCKED")

    def discover(self, *, max_list_pages=MAX_LIST_PAGES) -> tuple[str, ...]:
        if type(max_list_pages) is not int or not 1 <= max_list_pages <= MAX_LIST_PAGES:
            raise AcraError("INVALID_LIST_PAGE_CAP")
        self.verify_robots()
        current, visited, issuers = CATALOG_URL, set(), set()
        while current:
            canonical = listing_url(current)
            if canonical in visited:
                raise AcraError("PAGINATION_LOOP")
            if len(visited) >= max_list_pages:
                raise AcraError("LIST_PAGE_CAP_REACHED")
            visited.add(canonical)
            page = self.fetch(canonical)
            self.list_pages += 1
            links, current = parse_listing(page.content_bytes, page.canonical_url)
            issuers.update(links)
        return tuple(sorted(issuers))

    def fetch_issuer(self, url: str) -> FetchedPage:
        canonical, _ = issuer_url(url)
        page = self.fetch(canonical)
        final, source_id = issuer_url(page.canonical_url)
        if issuer_url(canonical)[1] != source_id:
            raise AcraError("ISSUER_REDIRECT_CONFLICT")
        self.issuer_pages += 1
        return FetchedPage(final, page.content_bytes, page.content_type, page.retrieved_at)
