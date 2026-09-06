"""Task259A: official-source capability audit, never a publication promotion.

No artifact bodies, database, output files, or financial parsers are used here.
Only reviewed, artifact/version-specific assertions can prove publication.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from html.parser import HTMLParser
from typing import Callable, Sequence
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.cbr_bank_reporting.client import discover_artifacts_from_html
from app.services.cbr_bank_reporting.contracts import CbrBankForm, SOURCE_PAGE

SCHEMA = "bondradar.cbr_bank_publication_availability_audit.v1"
REPORT_DATES = tuple(date.fromisoformat(value) for value in (
    "2023-07-01", "2023-10-01", "2024-01-01", "2024-04-01", "2024-07-01", "2024-10-01",
    "2025-01-01", "2025-02-01", "2025-03-01", "2025-04-01", "2025-05-01", "2025-06-01",
    "2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01", "2025-11-01", "2025-12-01",
    "2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01",
    "2026-07-01", "2026-08-01",
))
FORMS = tuple(CbrBankForm)
MAX_PAGE_BYTES = 2 * 1024 * 1024
MAX_HEADER_BYTES = 32 * 1024
MAX_REDIRECTS = 3
MAX_ATTEMPTS = 3
MAX_ALTERNATIVE_SOURCES = 12
ALLOWED_HOSTS = frozenset({"cbr.ru", "www.cbr.ru"})
ARCHIVE_MEDIA_TYPES = frozenset({"application/rar", "application/vnd.rar", "application/x-rar-compressed", "application/octet-stream", "application/x-rar"})

# Discovered through the official catalog/development navigation or prior CBR
# source-contract research. These are capability pages, not financial datasets.
RESEARCH_SOURCES = (
    ("https://www.cbr.ru/calendar/", "GENERAL_PUBLICATION_CALENDAR"),
    ("https://www.cbr.ru/statistics/indcalendar/", "OFFICIAL_STATISTICS_PUBLICATION_CALENDAR"),
    ("https://www.cbr.ru/development/", "OFFICIAL_API_NAVIGATION"),
    ("https://www.cbr.ru/development/WSCO/", "BANK_FORMS_API_DATE_SEMANTICS"),
    ("https://www.cbr.ru/development/RSS/", "OFFICIAL_PUBLICATION_FEED_DISCOVERY"),
    ("https://www.cbr.ru/rss/RssNews", "CURRENT_OFFICIAL_FEED_NOT_HISTORICAL_ARCHIVE"),
    ("https://www.cbr.ru/statistics/data-service/APIdocumentation/", "STATISTICAL_API_CAPABILITY"),
    ("https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/transparent/", "DISCLOSURE_CONSENT_NOT_PUBLICATION_HISTORY"),
    ("https://www.cbr.ru/explan/mery-podderzhki-fin-sektora/", "DISCLOSURE_POLICY_2023_RESUMPTION"),
    ("https://www.cbr.ru/about_br/dir/rsd_2022-12-29_23_03/", "DISCLOSURE_POLICY_2023_DECISION"),
    ("https://www.cbr.ru/rbr/dir_decisions/rsd_2025-12-19_23_02/", "DISCLOSURE_RULE_NOT_ACTUAL_RELEASE_EVENT"),
)

R1_RETRY_FILENAMES = frozenset({"101-20241001.rar", "123-20250701.rar"})
CALENDAR_PURPOSES = frozenset({"GENERAL_PUBLICATION_CALENDAR", "OFFICIAL_STATISTICS_PUBLICATION_CALENDAR"})
POLICY_PURPOSES = frozenset({"DISCLOSURE_POLICY_2023_RESUMPTION", "DISCLOSURE_POLICY_2023_DECISION",
                             "DISCLOSURE_RULE_NOT_ACTUAL_RELEASE_EVENT"})


class EvidenceType(StrEnum):
    TIMESTAMP = "EXPLICIT_OFFICIAL_PUBLICATION_TIMESTAMP"
    DATE = "EXPLICIT_OFFICIAL_PUBLICATION_DATE"
    LAST_MODIFIED = "OFFICIAL_ARTIFACT_LAST_MODIFIED"
    VERSION = "OFFICIAL_ARTIFACT_VERSION_METADATA"
    CATALOG = "OFFICIAL_CURRENT_CATALOG_PRESENCE"
    CURRENT = "OBSERVED_CURRENTLY_AVAILABLE"
    PERIOD = "REPORT_PERIOD_ONLY"
    NONE = "NO_HISTORICAL_AVAILABILITY_EVIDENCE"


class PitClass(StrEnum):
    EXACT = "PROVEN_EXACT"
    DATE = "PROVEN_DATE_ONLY"
    BOUND = "POTENTIAL_CONSERVATIVE_BOUND"
    CURRENT = "CURRENT_EXISTENCE_ONLY"
    NOT_USABLE = "NOT_USABLE_FOR_HISTORICAL_PIT"
    UNKNOWN = "UNKNOWN"


class CalendarRelation(StrEnum):
    EXACT_ARTIFACT = "EXACT_ARTIFACT_MATCH"
    EXACT_FORM = "EXACT_FORM_DATASET_MATCH"
    BROADER = "BROADER_BANKING_STATISTICS_ONLY"
    UNRELATED = "UNRELATED_PUBLICATION"
    AMBIGUOUS = "AMBIGUOUS"


class AuditError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _utc(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuditError("INVALID_OBSERVATION_TIME")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or parsed.fragment or any(ord(c) < 33 for c in url)):
            raise ValueError
    except (ValueError, TypeError):
        raise AuditError("UNSAFE_SOURCE_URL") from None
    return url


def _http_date(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return _utc(parsedate_to_datetime(raw))
    except (ValueError, TypeError, OverflowError, AuditError):
        return None


@dataclass(frozen=True)
class PublicationAssertion:
    """Reviewed source fact, not a CLI/user boolean granting PIT readiness.

    The live adapter currently supplies none: no exact-version official
    publication structure has been established. Tests exercise this boundary.
    """
    evidence_type: str
    source_url: str
    artifact_url: str
    observed_at: datetime
    raw_value: str
    semantics_url: str
    semantics_excerpt: str
    version_identity: str
    semantics_documented: bool
    version_binding_proven: bool


def classify_publication(assertion: PublicationAssertion | None, *, artifact_url: str | None) -> dict:
    result = {"explicit_publication_at": None, "explicit_publication_date": None,
              "historical_availability_proven": False, "pit_evidence_class": PitClass.UNKNOWN.value}
    if assertion is None:
        return result
    try:
        _safe_url(assertion.source_url)
        _safe_url(assertion.semantics_url)
        _safe_url(assertion.artifact_url)
        _utc(assertion.observed_at)
        if (assertion.artifact_url != artifact_url or assertion.semantics_documented is not True
                or assertion.version_binding_proven is not True or not assertion.semantics_excerpt.strip()
                or not assertion.version_identity.strip()):
            return result
        if assertion.evidence_type == EvidenceType.TIMESTAMP:
            value = _utc(datetime.fromisoformat(assertion.raw_value.replace("Z", "+00:00")))
            if datetime.fromisoformat(value.replace("Z", "+00:00")) > assertion.observed_at:
                return result
            result.update(explicit_publication_at=value, historical_availability_proven=True,
                          pit_evidence_class=PitClass.EXACT.value)
        elif assertion.evidence_type == EvidenceType.DATE:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", assertion.raw_value):
                return result
            value = date.fromisoformat(assertion.raw_value)
            if value > assertion.observed_at.date():
                return result
            result.update(explicit_publication_date=value.isoformat(), historical_availability_proven=True,
                          pit_evidence_class=PitClass.DATE.value)
    except (ValueError, TypeError, AttributeError, AuditError):
        return result
    return result


@dataclass(frozen=True)
class ResponseMetadata:
    status: int
    final_url: str
    redirects: tuple[str, ...]
    headers: dict[str, str]
    observed_at: str
    body: bytes | None
    duplicate_headers: tuple[str, ...]
    method: str = "GET"


class OfficialTransport:
    def __init__(self, client: httpx.Client, *, clock: Callable[[], datetime], sleep: Callable[[float], None] = time.sleep):
        self.client, self.clock, self.sleep = client, clock, sleep
        self.network_accessed = False
        self.logical_requests = 0
        self.request_attempts = 0

    def request(self, method: str, url: str, *, read_page: bool = False) -> ResponseMetadata:
        if method not in {"GET", "HEAD"} or (method == "HEAD" and read_page):
            raise AuditError("UNSUPPORTED_REQUEST_METHOD")
        _safe_url(url)
        self.logical_requests += 1
        for attempt in range(MAX_ATTEMPTS):
            current, redirects = url, []
            retry_code = "SOURCE_ERROR"
            try:
                while True:
                    self.network_accessed = True
                    self.request_attempts += 1
                    with self.client.stream(method, current, follow_redirects=False) as response:
                        raw_headers = response.headers.raw
                        if sum(len(k) + len(v) for k, v in raw_headers) > MAX_HEADER_BYTES:
                            raise AuditError("HEADERS_TOO_LARGE")
                        names = Counter(k.decode("ascii").lower() for k, _ in raw_headers)
                        duplicates = tuple(sorted(k for k, n in names.items() if n > 1))
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location or "location" in duplicates or len(redirects) >= MAX_REDIRECTS:
                                raise AuditError("INVALID_REDIRECT")
                            current = _safe_url(urljoin(current, location))
                            redirects.append(current)
                            continue
                        if response.status_code == 429 or 500 <= response.status_code <= 599:
                            retry_code = "RATE_LIMITED" if response.status_code == 429 else "SOURCE_ERROR"
                            break
                        body = None
                        if read_page and response.status_code == 200:
                            raw_length = response.headers.get("content-length")
                            if raw_length is not None and (not raw_length.isascii() or not raw_length.isdigit()):
                                raise AuditError("INVALID_CONTENT_LENGTH")
                            if raw_length is not None and int(raw_length) > MAX_PAGE_BYTES:
                                raise AuditError("PAGE_TOO_LARGE")
                            payload = bytearray()
                            for chunk in response.iter_bytes(chunk_size=16384):
                                if len(payload) + len(chunk) > MAX_PAGE_BYTES:
                                    raise AuditError("PAGE_TOO_LARGE")
                                payload.extend(chunk)
                            body = bytes(payload)
                        # Artifact response.body/read/iter_bytes are never accessed.
                        return ResponseMetadata(response.status_code, current, tuple(redirects),
                            dict(response.headers), _utc(self.clock()), body, duplicates, method)
            except httpx.TimeoutException:
                retry_code = "TIMEOUT"
            except httpx.HTTPError:
                raise AuditError("SOURCE_ERROR") from None
            if attempt == MAX_ATTEMPTS - 1:
                raise AuditError(retry_code)
            self.sleep(0.25 * 2**attempt)
        raise AuditError("SOURCE_ERROR")

    def get(self, url: str, *, read_page: bool = False) -> ResponseMetadata:
        return self.request("GET", url, read_page=read_page)

    def head(self, url: str) -> ResponseMetadata:
        return self.request("HEAD", url)


class PageContext(HTMLParser):
    """Capture link attributes/text and adjacent text, never infer a date."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[dict] = []
        self.active: dict | None = None
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag == "a":
            self.active = {"attributes": dict(attrs), "text": "", "text_offset": len(self.parts)}
            self.links.append(self.active)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag == "a":
            self.active = None

    def handle_data(self, data):
        if self.hidden:
            return
        value = " ".join(data.split())
        if value:
            self.parts.append(value)
            if self.active is not None:
                self.active["text"] += value + " "

    def context(self, href: str) -> dict:
        matches = [item for item in self.links if item["attributes"].get("href") == href]
        if len(matches) != 1:
            return {"anchor_text": None, "anchor_attributes": {}, "adjacent_text": None}
        item = matches[0]
        offset = item["text_offset"]
        return {"anchor_text": item["text"].strip(), "anchor_attributes": dict(sorted(item["attributes"].items())),
                "adjacent_text": " | ".join(self.parts[max(0, offset - 2):offset + 4])[:1500]}


def _page(response: ResponseMetadata) -> tuple[str, PageContext]:
    if response.status != 200 or response.body is None:
        raise AuditError("SOURCE_PAGE_UNAVAILABLE")
    media = response.headers.get("content-type", "").split(";")[0].lower()
    if media not in {"text/html", "application/xhtml+xml", "application/xml", "text/xml", "application/rss+xml"}:
        raise AuditError("INVALID_PAGE_CONTENT_TYPE")
    try:
        html = response.body.decode("utf-8-sig", errors="strict")
        if re.search(r"<!ENTITY\b|<!DOCTYPE\s+[^>]*\[", html, re.I):
            raise AuditError("UNSAFE_MARKUP")
        parser = PageContext()
        parser.feed(html)
        parser.close()
        return html, parser
    except (UnicodeError, ValueError):
        raise AuditError("INVALID_PAGE_CONTENT") from None


def _slot(day: date, form: CbrBankForm, observed_at: str) -> dict:
    return {
        "report_date": day.isoformat(), "form": form.value,
        "artifact_filename": f"{form.short_code}-{day:%Y%m%d}.rar",
        "source_href": None, "source_url": None, "catalog_present": None,
        "observed_at": observed_at, "metadata_observed_at": None,
        "metadata_state": "UNASSESSED", "error_code": None, "failure_classification": None,
        "http_status": None, "final_url": None, "redirect_count": None, "redirect_urls": [],
        "content_type": None, "content_length": None,
        "http_date": None, "last_modified": None, "etag": None,
        "raw_headers": {}, "explicit_publication_at": None, "explicit_publication_date": None,
        "evidence_types": [EvidenceType.PERIOD.value, EvidenceType.NONE.value],
        "pit_evidence_class": PitClass.UNKNOWN.value, "historical_availability_proven": False,
        "version_binding_proven": False, "catalog_context": None, "evidence": [], "notes": [],
    }


def _record_metadata(row: dict, response: ResponseMetadata) -> None:
    headers = response.headers
    selected = ("date", "last-modified", "etag", "content-type", "content-length")
    row.update(http_status=response.status, final_url=response.final_url,
               redirect_count=len(response.redirects), redirect_urls=list(response.redirects),
               metadata_observed_at=response.observed_at,
               raw_headers={key: headers.get(key) for key in selected})
    row["content_type"] = headers.get("content-type")
    row["http_date"] = _http_date(headers.get("date"))
    row["last_modified"] = _http_date(headers.get("last-modified"))
    row["etag"] = headers.get("etag")
    raw_length = headers.get("content-length")
    malformed = bool(set(response.duplicate_headers) & set(selected))
    if raw_length is not None:
        if raw_length.isascii() and raw_length.isdigit():
            row["content_length"] = int(raw_length)
        else:
            malformed = True
    for key, field in (("date", "http_date"), ("last-modified", "last_modified")):
        if headers.get(key) is not None and row[field] is None:
            malformed = True
    if row["last_modified"] and row["http_date"] and row["last_modified"] > row["http_date"]:
        malformed = True
    if response.status == 404:
        row.update(metadata_state="NOT_FOUND", error_code="ARTIFACT_NOT_FOUND")
    elif response.status != 200:
        row.update(metadata_state="FAILED", error_code="HTTP_STATUS_UNSUPPORTED")
    elif malformed:
        row.update(metadata_state="FAILED", error_code="MALFORMED_METADATA", pit_evidence_class=PitClass.UNKNOWN.value)
    elif row["content_type"] and row["content_type"].split(";")[0].lower() not in ARCHIVE_MEDIA_TYPES:
        row.update(metadata_state="FAILED", error_code="INVALID_ARTIFACT_CONTENT_TYPE")
    else:
        row.update(metadata_state="CHECKED", pit_evidence_class=PitClass.CURRENT.value)
        row["evidence_types"].append(EvidenceType.CURRENT.value)
    for key, kind in (("last-modified", EvidenceType.LAST_MODIFIED), ("etag", EvidenceType.VERSION)):
        raw = headers.get(key)
        if raw is not None and response.status == 200:
            row["evidence_types"].append(kind.value)
            row["evidence"].append({"official_source_url": response.final_url,
                "observed_at": response.observed_at, "evidence_type": kind.value, "raw_value": raw,
                "interpretation": "SOURCE_METADATA_NOT_PROVEN_PUBLICATION", "pit_classification": PitClass.NOT_USABLE.value,
                "semantics_documented": False, "version_binding_proven": False})
    row["notes"].extend(["HTTP_DATE_IS_RESPONSE_TIME", "NO_HISTORICAL_BYTE_IDENTITY_PROOF",
                         "LAST_MODIFIED_AND_ETAG_NOT_PUBLICATION", "NO_ARTIFACT_BODY_READ"])
    row["evidence_types"] = sorted(set(row["evidence_types"]))


def retry_artifact_metadata(transport: OfficialTransport, *, source_url: str, artifact_filename: str) -> dict:
    """Isolated HEAD-then-streamed-GET retry; it never reads artifact bytes."""
    result = {"artifact_filename": artifact_filename, "source_url": _safe_url(source_url),
              "status": "FAILED", "classification": None, "selected_method": None,
              "attempts": [], "metadata": None}
    selected = None
    head_unsupported = False
    for method in ("HEAD", "GET"):
        if method == "GET" and selected is not None:
            break
        try:
            response = transport.head(source_url) if method == "HEAD" else transport.get(source_url)
            attempt = {"method": method, "http_status": response.status, "final_url": response.final_url,
                       "redirect_count": len(response.redirects), "error_code": None}
            result["attempts"].append(attempt)
            media = response.headers.get("content-type", "").split(";", 1)[0].lower()
            head_incomplete = method == "HEAD" and response.status == 200 and media not in ARCHIVE_MEDIA_TYPES
            if method == "HEAD" and response.status in {405, 501}:
                head_unsupported = True
            if method == "HEAD" and (head_incomplete or head_unsupported or response.status == 404):
                continue
            selected = response
        except AuditError as exc:
            result["attempts"].append({"method": method, "http_status": None, "final_url": None,
                                       "redirect_count": None, "error_code": exc.code})
    if selected is not None:
        row = _slot(REPORT_DATES[0], FORMS[0], selected.observed_at)
        _record_metadata(row, selected)
        result["metadata"] = {key: row[key] for key in (
            "http_status", "final_url", "redirect_count", "redirect_urls", "raw_headers", "content_type",
            "content_length", "http_date", "last_modified", "etag", "metadata_observed_at",
            "metadata_state", "error_code")}
        if row["metadata_state"] in {"CHECKED", "NOT_FOUND"}:
            result.update(status="CHECKED", selected_method=selected.method,
                          classification=("UNSUPPORTED_REQUEST_METHOD" if head_unsupported else
                                          f"RECOVERED_BY_{selected.method}"))
            return result
    errors = {item["error_code"] for item in result["attempts"] if item["error_code"]}
    statuses = {item["http_status"] for item in result["attempts"] if item["http_status"] is not None}
    if head_unsupported and len(result["attempts"]) > 1:
        classification = "SOURCE_BEHAVIOR_INCONSISTENT"
    elif errors <= {"TIMEOUT", "RATE_LIMITED", "SOURCE_ERROR"} and errors:
        classification = "PERSISTENT_SOURCE_FAILURE"
    elif statuses == {404}:
        classification = "PERSISTENT_SOURCE_FAILURE"
    else:
        classification = "OTHER_EXPLICIT_STATE"
    result["classification"] = classification
    return result


def classify_calendar_candidate(*, title: str, href: str = "", reference_period: str | None = None,
                                publication_date: str | None = None, publication_time: str | None = None,
                                planned_vs_actual: str = "UNKNOWN") -> dict:
    """Classify an official calendar item without fuzzy dataset matching."""
    combined = " ".join(filter(None, (title, href, reference_period))).strip()
    artifact_matches = sorted(set(re.findall(r"(?:101|102|123|135)-\d{8}\.rar", combined, re.I)))
    form_matches = sorted(set(re.findall(r"(?<!\d)0409(?:101|102|123|135)(?!\d)", combined)))
    ambiguous_short = bool(re.search(r"(?:^|\W)(?:форма|формы|form)\s*(?:101|102|123|135)(?:\W|$)", combined, re.I))
    if len(artifact_matches) == 1:
        relation = CalendarRelation.EXACT_ARTIFACT
    elif len(artifact_matches) > 1:
        relation = CalendarRelation.AMBIGUOUS
    elif form_matches:
        relation = CalendarRelation.EXACT_FORM
    elif ambiguous_short:
        relation = CalendarRelation.AMBIGUOUS
    elif re.search(r"кредитн\w*\s+организац|банковск\w*\s+(?:сектор|систем|статист)", combined, re.I):
        relation = CalendarRelation.BROADER
    elif re.search(r"публикац|отчетност|статист", combined, re.I):
        relation = CalendarRelation.AMBIGUOUS
    else:
        relation = CalendarRelation.UNRELATED
    exact = relation in {CalendarRelation.EXACT_ARTIFACT, CalendarRelation.EXACT_FORM}
    actual = planned_vs_actual == "ACTUAL"
    return {"title": title.strip(), "href": href, "publication_date": publication_date,
            "publication_time": publication_time, "reference_period": reference_period,
            "planned_vs_actual": planned_vs_actual, "relation": relation.value,
            "exact_artifact_matches": artifact_matches, "exact_form_matches": form_matches,
            "relationship_proof": "EXPLICIT_IDENTIFIER_IN_OFFICIAL_ITEM" if exact else "NONE",
            "pit_classification": ("POTENTIAL_PUBLICATION_EVIDENCE_REQUIRES_VERSION_BINDING"
                                   if exact and actual else PitClass.NOT_USABLE.value),
            "pit_usable": False}


def _calendar_review(context: PageContext, *, planned_vs_actual: str) -> dict:
    counts = Counter()
    candidates = []
    for link in context.links:
        href = link["attributes"].get("href", "")
        title = link["text"].strip()
        offset = link["text_offset"]
        adjacent = " | ".join(context.parts[max(0, offset - 2):offset + 4])
        dates = re.findall(r"(?<!\d)\d{2}\.\d{2}\.\d{4}(?!\d)", adjacent)
        times = re.findall(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", adjacent)
        item = classify_calendar_candidate(title=title, href=href, reference_period=adjacent[:500] or None,
                                           publication_date=dates[0] if dates else None,
                                           publication_time=times[0] if times else None,
                                           planned_vs_actual=planned_vs_actual)
        counts[item["relation"]] += 1
        if item["relation"] != CalendarRelation.UNRELATED.value:
            candidates.append(item)
    candidates.sort(key=lambda row: (row["relation"], row["title"], row["href"]))
    return {"planned_vs_actual": planned_vs_actual,
            "candidate_counts": {kind.value: counts[kind.value] for kind in CalendarRelation},
            "exact_artifact_matches": counts[CalendarRelation.EXACT_ARTIFACT.value],
            "exact_form_dataset_matches": counts[CalendarRelation.EXACT_FORM.value],
            "ambiguous_matches": counts[CalendarRelation.AMBIGUOUS.value],
            "pit_usable_matches": 0, "candidate_samples": candidates[:24],
            "conclusion": "NO_EXACT_TARGET_DATASET_PUBLICATION_ENTRY"}


def _policy_review(html: str, purpose: str) -> dict:
    normalized = " ".join(html.split())
    all_forms = all(code in normalized for code in ("0409101", "0409102", "0409123", "0409135"))
    june_boundary = bool(re.search(r"(?:01\.06\.2023|1\s+июня\s+2023)", normalized, re.I))
    conclusions = []
    if purpose in POLICY_PURPOSES and all_forms:
        conclusions.append("PROVES_DISCLOSURE_POLICY_ONLY")
    if purpose in {"DISCLOSURE_POLICY_2023_RESUMPTION", "DISCLOSURE_POLICY_2023_DECISION"} and june_boundary:
        conclusions.append("PROVES_EARLIEST_ELIGIBLE_REPORT_DATE")
    if conclusions:
        conclusions.append("PROVES_NOTHING_ARTIFACT_SPECIFIC")
    return {"conclusions": conclusions, "all_target_forms_named": all_forms,
            "reporting_boundary_2023_06_01_named": june_boundary,
            "artifact_release_date_proven": False, "publication_at_proven": False,
            "pit_usable": False}


def last_modified_diagnostic(rows: Sequence[dict]) -> dict:
    usable = [row for row in rows if row.get("last_modified")]
    values = [datetime.fromisoformat(row["last_modified"].replace("Z", "+00:00")) for row in usable]
    counter = Counter(row["last_modified"] for row in usable)
    before = close = long_after = 0
    for row, value in zip(usable, values):
        delta = (value.date() - date.fromisoformat(row["report_date"])).days
        before += delta < 0
        close += 0 <= delta <= 31
        long_after += delta > 90
    non_monotonic = []
    for form in sorted({row["form"] for row in usable}):
        sequence = [datetime.fromisoformat(row["last_modified"].replace("Z", "+00:00"))
                    for row in sorted((item for item in usable if item["form"] == form),
                                      key=lambda item: item["report_date"])]
        if any(left > right for left, right in zip(sequence, sequence[1:])):
            non_monotonic.append(form)
    by_form = {form: sum(row["form"] == form for row in usable) for form in sorted({row["form"] for row in usable})}
    return {"count": len(usable), "min": _utc(min(values)) if values else None,
            "max": _utc(max(values)) if values else None, "distinct_timestamp_count": len(counter),
            "distribution_by_form": by_form,
            "distribution_by_report_month": dict(sorted(Counter(row["report_date"][5:7] for row in usable).items())),
            "same_timestamp_clusters": [{"timestamp": stamp, "count": count} for stamp, count in sorted(counter.items())
                                        if count > 1],
            "timestamps_before_report_date": before, "timestamps_within_31_days_after_report_date": close,
            "timestamps_more_than_90_days_after_report_date": long_after,
            "non_monotonic_forms": non_monotonic,
            "bulk_migration_or_republication_signal": any(count >= 4 for count in counter.values()) or bool(long_after),
            "semantics": "UNDOCUMENTED_HTTP_RESOURCE_METADATA_NOT_PUBLICATION",
            "pit_usable": False}


def _research(transport: OfficialTransport) -> list[dict]:
    assert len(RESEARCH_SOURCES) <= MAX_ALTERNATIVE_SOURCES
    result = []
    for url, purpose in RESEARCH_SOURCES:
        item = {"url": url, "official_host": urlsplit(url).hostname, "purpose": purpose,
                "status": "UNASSESSED", "error_code": None, "observed_at": None,
                "final_url": None, "http_status": None, "raw_metadata": {}, "content_sha256": None,
                "raw_excerpts": [], "artifact_specific": False, "report_date_specific": False,
                "candidate_artifact_references": [], "publication_marker_present": False,
                "immutable_versioned": False, "semantics_documented_for_artifact_publication": False,
                "interpretation": "CAPABILITY_CONTEXT_ONLY_NOT_EXACT_ARTIFACT_VERSION_PUBLICATION",
                "pit_usefulness": PitClass.NOT_USABLE.value, "calendar_review": None, "policy_review": None}
        try:
            response = transport.get(url, read_page=True)
            item.update(observed_at=response.observed_at, final_url=response.final_url, http_status=response.status,
                        raw_metadata={k: response.headers.get(k) for k in ("date", "last-modified", "etag")})
            if response.status == 404:
                item.update(status="NOT_FOUND", error_code="OFFICIAL_RESOURCE_NOT_FOUND")
            else:
                html, context = _page(response)
                excerpts = [part for part in context.parts if re.search(
                    r"публик|раскры|архив|LastUpdate|GetFormsMaxDate|GetDatesFor|RSS|API|график|календар", part, re.I)]
                candidates = sorted({link["attributes"].get("href", "") for link in context.links
                    if re.search(r"(?:101|102|123|135)-\d{8}\.rar(?:$|[?#])", link["attributes"].get("href") or "", re.I)})
                item.update(status="CHECKED", content_sha256=hashlib.sha256(response.body).hexdigest(),
                            candidate_artifact_references=candidates,
                            artifact_specific=bool(candidates), report_date_specific=bool(candidates),
                            publication_marker_present=any(re.search(r"публикац|опублик|pubDate|datePublished", part, re.I) for part in context.parts),
                            raw_excerpts=[part[:800] for part in excerpts[:12]])
                if purpose in CALENDAR_PURPOSES:
                    item["calendar_review"] = _calendar_review(context, planned_vs_actual="PLANNED")
                    item["interpretation"] = "PLANNED_STATISTICAL_SCHEDULE_NOT_TARGET_ARCHIVE_RELEASE_HISTORY"
                elif purpose in {"OFFICIAL_PUBLICATION_FEED_DISCOVERY", "CURRENT_OFFICIAL_FEED_NOT_HISTORICAL_ARCHIVE"}:
                    item["calendar_review"] = _calendar_review(context, planned_vs_actual="ACTUAL")
                    item["interpretation"] = "CURRENT_FEED_HAS_NO_EXACT_TARGET_ARCHIVE_VERSION_HISTORY"
                if purpose in POLICY_PURPOSES:
                    item["policy_review"] = _policy_review(html, purpose)
                    if item["policy_review"]["conclusions"]:
                        item["interpretation"] = ";".join(item["policy_review"]["conclusions"])
        except AuditError as exc:
            item.update(status="FAILED", error_code=exc.code)
        result.append(item)
    return result


def _aggregate(report: dict) -> dict:
    rows = report["artifacts"]
    catalog_known = report["catalog_status"] == "CHECKED"
    if catalog_known:
        report["catalog_artifacts_found"] = sum(r["catalog_present"] is True for r in rows)
        report["catalog_artifacts_missing"] = sum(r["catalog_present"] is False for r in rows)
    report["artifact_metadata_checked"] = sum(r["metadata_state"] in {"CHECKED", "NOT_FOUND"} for r in rows)
    report["artifact_metadata_failures"] = sum(r["metadata_state"] == "FAILED" for r in rows)
    report["artifact_metadata_unassessed"] = sum(r["metadata_state"] == "UNASSESSED" for r in rows)
    report["explicit_publication_timestamp_count"] = sum(r["explicit_publication_at"] is not None for r in rows)
    report["explicit_publication_date_count"] = sum(r["explicit_publication_date"] is not None for r in rows)
    report["last_modified_count"] = sum(r["http_status"] == 200 and r["last_modified"] is not None for r in rows)
    report["etag_count"] = sum(r["http_status"] == 200 and r["etag"] is not None for r in rows)
    proven = sum(r["historical_availability_proven"] for r in rows)
    report["historical_availability_proven_artifacts"] = proven
    report["historical_availability_unproven_artifacts"] = len(rows) - proven
    for field, kind in (("pit_exact_artifacts", PitClass.EXACT), ("pit_date_only_artifacts", PitClass.DATE),
                        ("pit_conservative_bound_artifacts", PitClass.BOUND)):
        report[field] = sum(r["pit_evidence_class"] == kind.value for r in rows)
    report["pit_not_usable_artifacts"] = sum(r["pit_evidence_class"] in {
        PitClass.UNKNOWN.value, PitClass.NOT_USABLE.value, PitClass.CURRENT.value} for r in rows)
    report["coverage_by_form"] = dict(sorted(Counter(r["form"] for r in rows).items()))
    report["coverage_by_year"] = dict(sorted(Counter(r["report_date"][:4] for r in rows).items()))
    report["coverage_by_evidence_class"] = dict(sorted(Counter(r["pit_evidence_class"] for r in rows).items()))
    report["last_modified_diagnostic"] = last_modified_diagnostic(rows)
    calendar_reviews = [source["calendar_review"] for source in report["findings"] if source.get("calendar_review")]
    report["calendar_exact_artifact_matches"] = sum(item["exact_artifact_matches"] for item in calendar_reviews)
    report["calendar_exact_form_dataset_matches"] = sum(item["exact_form_dataset_matches"] for item in calendar_reviews)
    report["calendar_ambiguous_matches"] = sum(item["ambiguous_matches"] for item in calendar_reviews)
    report["calendar_pit_usable_matches"] = sum(item["pit_usable_matches"] for item in calendar_reviews)
    report["disclosure_policy_proves"] = sorted({conclusion for source in report["findings"]
        for conclusion in ((source.get("policy_review") or {}).get("conclusions") or [])})
    measured_source_failures = {"PERSISTENT_SOURCE_FAILURE", "TRANSIENT_SOURCE_FAILURE"}
    unresolved_failures = [row for row in rows if row["metadata_state"] == "FAILED"
                           and row.get("failure_classification") not in measured_source_failures]
    no_metadata_observed = report["artifact_metadata_checked"] == 0 and report["artifact_metadata_failures"] > 0
    incomplete = bool(report["error_code"]) or not catalog_known or bool(unresolved_failures) or no_metadata_observed or any(
        source["status"] == "FAILED" for source in report["findings"])
    report["status"] = "incomplete" if incomplete else "complete"
    report["overall_historical_availability_status"] = (
        "UNKNOWN_AUDIT_INCOMPLETE" if incomplete else
        "HISTORICAL_AVAILABILITY_PROVEN" if proven == len(rows) else
        "HISTORICAL_AVAILABILITY_PARTIALLY_PROVEN" if proven else "HISTORICAL_AVAILABILITY_NOT_PROVEN")
    report["recommendation"] = (
        "SOURCE_CAPABILITY_INSUFFICIENT" if incomplete else
        "GO_OFFICIAL_PUBLICATION_EVIDENCE_IMPLEMENTATION" if proven else "REQUIRES_EXTERNAL_ARCHIVAL_EVIDENCE")
    report["next_recommended_task"] = (
        "259B_EXTERNAL_ARCHIVAL_AVAILABILITY_EVIDENCE_AUDIT" if not incomplete and not proven else
        "Task259B — Official publication-evidence implementation" if proven else
        "Complete Task259A official-source investigation before selecting Task259B")
    report["official_source_only_pit_reconstruction"] = (
        "PARTIAL" if incomplete else "SUPPORTED" if proven == len(rows) else "UNSUPPORTED")
    report["source_audit_complete"] = not incomplete
    return report


def _empty_report(observed_at: str) -> dict:
    return {"schema": SCHEMA, "status": "incomplete", "error_code": None, "observed_at": observed_at,
        "source_page": SOURCE_PAGE, "requested_from_date": REPORT_DATES[0].isoformat(),
        "requested_to_date": REPORT_DATES[-1].isoformat(), "expected_report_dates": 26,
        "expected_artifacts": 104, "expected_forms": [f.value for f in FORMS],
        "report_dates": [d.isoformat() for d in REPORT_DATES],
        "catalog_status": "UNASSESSED", "catalog_artifacts_found": None, "catalog_artifacts_missing": None,
        "catalog_content_sha256": None, "catalog_observed_at": None, "catalog_page_timestamp_context": [],
        "network_accessed": False, "database_accessed": False, "database_mutation_executed": False,
        "database_persistence": False, "filesystem_write": False, "normalization": False, "scoring": False,
        "production_actions": "NONE", "pit_ready": False, "publication_backfill_executed": False,
        "publication_status": "UNKNOWN", "publication_at": None, "historical_availability_proven": False,
        "artifact_bodies_read": 0, "logical_requests": 0, "request_attempts": 0,
        "calendar_exact_artifact_matches": 0, "calendar_exact_form_dataset_matches": 0,
        "calendar_ambiguous_matches": 0, "calendar_pit_usable_matches": 0,
        "disclosure_policy_proves": [], "last_modified_diagnostic": last_modified_diagnostic([]),
        "official_source_only_pit_reconstruction": "PARTIAL", "source_audit_complete": False,
        "artifact_metadata_retry_results": [],
        "artifacts": [_slot(d, f, observed_at) for d in REPORT_DATES for f in FORMS], "findings": []}


def run_audit(transport: OfficialTransport, *, observed_at: datetime) -> dict:
    report = _empty_report(_utc(observed_at))
    try:
        response = transport.get(SOURCE_PAGE, read_page=True)
        html, context = _page(response)
        try:
            references = discover_artifacts_from_html(html, discovered_at=observed_at, source_page=response.final_url)
            for ref in references:
                _safe_url(ref.source_url)
        except Exception:
            raise AuditError("INVALID_CATALOG_REFERENCES") from None
        index = {(ref.report_date.isoformat(), ref.form.value): ref for ref in references}
        report.update(catalog_status="CHECKED", catalog_observed_at=response.observed_at,
                      catalog_content_sha256=hashlib.sha256(response.body).hexdigest(),
                      catalog_page_timestamp_context=[part[:800] for part in context.parts
                          if re.search(r"обновлен|публикац", part, re.I)][:12])
        for row in report["artifacts"]:
            ref = index.get((row["report_date"], row["form"]))
            row["catalog_present"] = ref is not None
            if ref is None:
                row.update(metadata_state="NOT_REQUESTED", error_code="CATALOG_ARTIFACT_MISSING",
                           pit_evidence_class=PitClass.NOT_USABLE.value)
                continue
            row.update(source_href=ref.source_href, source_url=ref.source_url, artifact_filename=ref.artifact_filename,
                       catalog_context=context.context(ref.source_href), pit_evidence_class=PitClass.CURRENT.value)
            row["evidence_types"].append(EvidenceType.CATALOG.value)
            row["evidence"].append({"official_source_url": response.final_url, "observed_at": response.observed_at,
                "evidence_type": EvidenceType.CATALOG.value, "raw_value": ref.source_href,
                "interpretation": "CURRENT_LINK_ONLY_NOT_HISTORICAL_PUBLICATION", "pit_classification": PitClass.CURRENT.value,
                "semantics_documented": False, "version_binding_proven": False})
            try:
                _record_metadata(row, transport.get(ref.source_url))
            except AuditError as exc:
                row.update(metadata_state="FAILED", error_code=exc.code, pit_evidence_class=PitClass.UNKNOWN.value,
                           failure_classification=("TRANSIENT_SOURCE_FAILURE" if exc.code in {
                               "TIMEOUT", "RATE_LIMITED", "SOURCE_ERROR"} else "OTHER_EXPLICIT_STATE"))
                if row["artifact_filename"] in R1_RETRY_FILENAMES:
                    retry = retry_artifact_metadata(transport, source_url=ref.source_url,
                                                     artifact_filename=row["artifact_filename"])
                    report["artifact_metadata_retry_results"].append(retry)
                    if retry["metadata"] is not None and retry["status"] == "CHECKED":
                        metadata = retry["metadata"]
                        response = ResponseMetadata(metadata["http_status"], metadata["final_url"],
                            tuple(metadata["redirect_urls"]), metadata["raw_headers"],
                            metadata["metadata_observed_at"], None, (), retry["selected_method"])
                        _record_metadata(row, response)
                        row["notes"].append("R1_ISOLATED_METADATA_RETRY")
                    else:
                        row["failure_classification"] = retry["classification"]
        report["findings"] = _research(transport)
    except AuditError as exc:
        report.update(error_code=exc.code, catalog_status="FAILED")
    report.update(network_accessed=transport.network_accessed, logical_requests=transport.logical_requests,
                  request_attempts=transport.request_attempts)
    return _aggregate(report)


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], datetime] | None = None,
         client_factory: Callable[..., httpx.Client] = httpx.Client) -> int:
    clock = clock or (lambda: datetime.now(timezone.utc))
    report = _empty_report(_utc(clock()))
    transport = None
    if list(sys.argv[1:] if argv is None else argv):
        report = _aggregate(report)
        report.update(status="invalid_arguments", error_code="INVALID_ARGUMENTS")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 2
    try:
        with client_factory(timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5), trust_env=False,
                            follow_redirects=False, headers={"User-Agent": "BondRadar-Task259A-ReadOnly/1.0",
                                                          "Accept-Encoding": "identity"}) as client:
            transport = OfficialTransport(client, clock=clock)
            report = run_audit(transport, observed_at=datetime.fromisoformat(report["observed_at"].replace("Z", "+00:00")))
    except Exception:
        # No exception text, connection information, or credentials in stdout.
        report["error_code"] = "AUDIT_RUNTIME_FAILURE"
        report["network_accessed"] = transport.network_accessed if transport is not None else False
        report = _aggregate(report)
        report["status"] = "incomplete"
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
