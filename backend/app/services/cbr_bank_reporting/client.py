from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx

from .contracts import (
    SOURCE_PAGE,
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
)


ALLOWED_HOSTS = frozenset({"cbr.ru", "www.cbr.ru"})
ARTIFACT_RE = re.compile(r"^(101|102|123|135)-(\d{8})\.rar$", re.IGNORECASE)
MAX_SOURCE_PAGE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_ARTIFACT_BYTES = 8 * 1024 * 1024
HISTORICAL_MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
HISTORICAL_MAX_TOTAL_ARTIFACT_BYTES = 512 * 1024 * 1024
EXPECTED_CURRENT = {
    (CbrBankForm.FORM_101, date(2026, 8, 1)): (
        360046,
        "7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a",
    ),
    (CbrBankForm.FORM_102, date(2026, 8, 1)): (
        74392,
        "0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21",
    ),
    (CbrBankForm.FORM_123, date(2026, 8, 1)): (
        33042,
        "6da408180123fa6748399acb89c717e3fc32380ee818679248043daa9a60baab",
    ),
    (CbrBankForm.FORM_135, date(2026, 8, 1)): (
        33181,
        "061a00791196d660bdb070de890228c820ea7e0d8af7978309b11b4226ed4776",
    ),
}


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        for key, value in attrs:
            if key.casefold() == "href" and value is not None:
                self.hrefs.append(value)


def _validate_https_cbr_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise CbrSourceError(CbrSourceStatus.INVALID_CONTENT, "invalid CBR source URL")
    if parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise CbrSourceError(CbrSourceStatus.INVALID_CONTENT, "invalid CBR source URL")


def discover_artifacts_from_html(
    html: str,
    *,
    discovered_at: datetime,
    source_page: str = SOURCE_PAGE,
) -> tuple[CbrArtifactReference, ...]:
    _validate_https_cbr_url(source_page)
    parser = _AnchorParser()
    parser.feed(html)
    found: dict[tuple[CbrBankForm, date], CbrArtifactReference] = {}
    for href in parser.hrefs:
        resolved = urljoin(source_page, href)
        parsed = urlparse(resolved)
        filename = PurePosixPath(parsed.path).name
        match = ARTIFACT_RE.fullmatch(filename)
        if match is None:
            continue
        _validate_https_cbr_url(resolved)
        form = CbrBankForm.parse(match.group(1))
        try:
            report_date = datetime.strptime(match.group(2), "%Y%m%d").date()
        except ValueError as exc:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT, "invalid artifact date"
            ) from exc
        reference = CbrArtifactReference(
            form=form,
            source_href=href,
            source_url=resolved,
            artifact_filename=filename,
            report_date=report_date,
            discovered_at=discovered_at,
        )
        key = (form, report_date)
        previous = found.get(key)
        if previous is not None:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT,
                "duplicate or conflicting artifact references",
            )
        found[key] = reference
    return tuple(
        sorted(
            found.values(),
            key=lambda item: (item.report_date, item.form.value, item.source_url),
        )
    )


class CbrBankRegulatoryClient:
    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.http_client = http_client
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._sleep = sleep or time.sleep
        self._artifact_bytes_downloaded = 0
        self._historical_artifact_bytes_downloaded = 0

    def discover_artifacts(
        self, *, form: CbrBankForm, report_date: date
    ) -> tuple[CbrArtifactReference, ...]:
        references = self.discover_requested(forms=(form,), report_date=report_date)
        return references

    def discover_catalog(self) -> tuple[CbrArtifactReference, ...]:
        """Return every supported artifact reference exposed by the source page."""
        content, _headers = self._get_bytes(SOURCE_PAGE, MAX_SOURCE_PAGE_BYTES)
        try:
            html = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT, "invalid source page encoding"
            ) from exc
        return discover_artifacts_from_html(html, discovered_at=self._now())

    def discover_requested(
        self, *, forms: tuple[CbrBankForm, ...], report_date: date
    ) -> tuple[CbrArtifactReference, ...]:
        by_key = {
            (item.form, item.report_date): item
            for item in self.discover_catalog()
        }
        result: list[CbrArtifactReference] = []
        for form in forms:
            reference = by_key.get((form, report_date))
            if reference is None:
                raise CbrSourceError(
                    CbrSourceStatus.ARTIFACT_NOT_FOUND, "artifact link not found"
                )
            result.append(reference)
        return tuple(result)

    def fetch_artifact(self, reference: CbrArtifactReference) -> CbrBankArtifact:
        _validate_https_cbr_url(reference.source_url)
        expected = EXPECTED_CURRENT.get((reference.form, reference.report_date))
        if expected is None:
            raise CbrSourceError(
                CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
                "artifact identity is not approved for Task251 v1",
            )
        return self._fetch_artifact(
            reference,
            expected_identity=expected,
            max_artifact_bytes=MAX_ARTIFACT_BYTES,
            max_total_artifact_bytes=MAX_TOTAL_ARTIFACT_BYTES,
            historical_budget=False,
        )

    def fetch_discovered_artifact(
        self, reference: CbrArtifactReference
    ) -> CbrBankArtifact:
        """Fetch a discovered artifact without assigning it a trusted identity.

        Recurring ingestion freezes the returned hash and size in a manifest only
        after the existing archive, DBF, and approved-schema checks succeed.  The
        original ``fetch_artifact`` contract remains the approved-fixture path.
        """
        _validate_https_cbr_url(reference.source_url)
        return self._fetch_artifact(
            reference,
            expected_identity=None,
            max_artifact_bytes=MAX_ARTIFACT_BYTES,
            max_total_artifact_bytes=MAX_TOTAL_ARTIFACT_BYTES,
            historical_budget=False,
        )

    def fetch_discovered_artifact_historical(
        self, reference: CbrArtifactReference
    ) -> CbrBankArtifact:
        """Fetch one catalog artifact under the bounded historical-audit budget."""
        _validate_https_cbr_url(reference.source_url)
        return self._fetch_artifact(
            reference,
            expected_identity=None,
            max_artifact_bytes=HISTORICAL_MAX_ARTIFACT_BYTES,
            max_total_artifact_bytes=HISTORICAL_MAX_TOTAL_ARTIFACT_BYTES,
            historical_budget=True,
        )

    def _fetch_artifact(
        self,
        reference: CbrArtifactReference,
        *,
        expected_identity: tuple[int, str] | None,
        max_artifact_bytes: int,
        max_total_artifact_bytes: int,
        historical_budget: bool,
    ) -> CbrBankArtifact:
        downloaded = (
            self._historical_artifact_bytes_downloaded
            if historical_budget
            else self._artifact_bytes_downloaded
        )
        remaining = max_total_artifact_bytes - downloaded
        if remaining <= 0:
            raise CbrSourceError(
                CbrSourceStatus.ARTIFACT_TOO_LARGE, "total artifact budget exceeded"
            )
        data, headers = self._get_bytes(
            reference.source_url, min(max_artifact_bytes, remaining)
        )
        if historical_budget:
            self._historical_artifact_bytes_downloaded += len(data)
        else:
            self._artifact_bytes_downloaded += len(data)
        digest = hashlib.sha256(data).hexdigest()
        if expected_identity is not None and (len(data), digest) != expected_identity:
            raise CbrSourceError(
                CbrSourceStatus.ARTIFACT_MUTATED, "artifact identity changed"
            )
        content_type = headers.get("content-type")
        if content_type and content_type.casefold().startswith("text/"):
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT, "unexpected artifact content type"
            )
        return CbrBankArtifact(
            reference=reference,
            content=data,
            content_sha256=digest,
            compressed_size=len(data),
            content_type=content_type,
            retrieved_at=self._now(),
        )

    def _get_bytes(self, url: str, byte_limit: int) -> tuple[bytes, dict[str, str]]:
        _validate_https_cbr_url(url)
        client = self.http_client or httpx.Client(
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={"User-Agent": "BondRadar-Task251/1.0 read-only-source"},
            follow_redirects=False,
        )
        close_client = self.http_client is None
        try:
            for attempt in range(3):
                current = url
                redirects = 0
                try:
                    while True:
                        with client.stream("GET", current) as response:
                            if 300 <= response.status_code < 400:
                                location = response.headers.get("location")
                                if location is None or redirects >= 3:
                                    raise CbrSourceError(
                                        CbrSourceStatus.SOURCE_ERROR,
                                        "invalid CBR redirect",
                                    )
                                current = urljoin(current, location)
                                _validate_https_cbr_url(current)
                                redirects += 1
                                continue
                            if response.status_code == 404:
                                raise CbrSourceError(
                                    CbrSourceStatus.ARTIFACT_NOT_FOUND,
                                    "CBR artifact not found",
                                )
                            if response.status_code == 429:
                                if attempt == 2:
                                    raise CbrSourceError(
                                        CbrSourceStatus.RATE_LIMITED,
                                        "CBR rate limit reached",
                                    )
                                break
                            if response.status_code in {500, 502, 503, 504}:
                                if attempt == 2:
                                    raise CbrSourceError(
                                        CbrSourceStatus.SOURCE_ERROR,
                                        "CBR source unavailable",
                                    )
                                break
                            try:
                                response.raise_for_status()
                            except httpx.HTTPError as exc:
                                raise CbrSourceError(
                                    CbrSourceStatus.SOURCE_ERROR,
                                    "CBR request failed",
                                ) from exc
                            content_length = response.headers.get("content-length")
                            if content_length is not None:
                                try:
                                    if int(content_length) > byte_limit:
                                        raise CbrSourceError(
                                            CbrSourceStatus.ARTIFACT_TOO_LARGE,
                                            "response exceeds size limit",
                                        )
                                except ValueError as exc:
                                    raise CbrSourceError(
                                        CbrSourceStatus.INVALID_CONTENT,
                                        "invalid content length",
                                    ) from exc
                            payload = bytearray()
                            for chunk in response.iter_bytes():
                                payload.extend(chunk)
                                if len(payload) > byte_limit:
                                    raise CbrSourceError(
                                        CbrSourceStatus.ARTIFACT_TOO_LARGE,
                                        "response exceeds size limit",
                                    )
                            return bytes(payload), dict(response.headers)
                except httpx.TimeoutException as exc:
                    if attempt == 2:
                        raise CbrSourceError(
                            CbrSourceStatus.TIMEOUT, "CBR request timed out"
                        ) from exc
                except httpx.HTTPError as exc:
                    if attempt == 2:
                        raise CbrSourceError(
                            CbrSourceStatus.SOURCE_ERROR, "CBR request failed"
                        ) from exc
                self._sleep(0.25 * (2**attempt))
            raise CbrSourceError(CbrSourceStatus.SOURCE_ERROR, "CBR request failed")
        finally:
            if close_client:
                client.close()
