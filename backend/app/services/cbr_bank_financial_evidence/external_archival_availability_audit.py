"""Task259B read-only external archival availability audit.

Archive capture time is a third-party observation, never publication time.  A
capture is usable only when the archived HTTP payload hashes to the exact target
artifact bytes.  The module deliberately contains no database or filesystem
write surface.
"""
from __future__ import annotations

import argparse
from email.utils import parsedate_to_datetime
import gzip
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote, urljoin, urlsplit

import httpx

from app.services.cbr_bank_financial_evidence.publication_availability_audit import (
    FORMS,
    REPORT_DATES,
)
from app.services.cbr_bank_financial_evidence.fingerprints import (
    canonical_value,
    ordered_fingerprints_sha256,
    sha256_canonical,
)
from app.services.cbr_bank_financial_evidence.lexical import extract_exact_form_evidence
from app.services.cbr_bank_reporting.archive import (
    HISTORICAL_MAX_MEMBER_BYTES,
    HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES,
    extract_archive_members,
)
from app.services.cbr_bank_reporting.bundle import (
    CbrBankRegulatoryBundleService,
    subject_set_sha256,
)
from app.services.cbr_bank_reporting.client import discover_artifacts_from_html
from app.services.cbr_bank_reporting.contracts import (
    SOURCE_PAGE,
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
)
from app.services.cbr_bank_reporting.parsers import (
    compute_structural_schema_fingerprint,
)


AUDIT_SCHEMA = "bondradar.cbr_bank_external_archival_availability_audit.v1"
INVENTORY_SCHEMA = "bondradar.cbr_bank_archival_target_inventory.v1"
CHECKPOINT_SCHEMA = "bondradar.cbr_bank_external_archival_checkpoint.v1"
AUDIT_CODE_CONTRACT_VERSION = "cbr-external-archival-audit-v1"
MISMATCH_AUDIT_SCHEMA = "bondradar.cbr_bank_wayback_version_mismatch_semantic_audit.v1"
CURRENT_BINDING = "CURRENT_OFFICIAL_REPRESENTATION"
FROZEN_BINDING = "FROZEN_TASK258B"
WAYBACK = "WAYBACK_MACHINE"
COMMON_CRAWL = "COMMON_CRAWL"

MAX_REDIRECTS = 3
MAX_ATTEMPTS = 3
MAX_INDEX_BYTES = 1024 * 1024
MAX_CBR_CATALOG_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_CURRENT_INVENTORY_BYTES = 512 * 1024 * 1024
MAX_WARC_RANGE_BYTES = 40 * 1024 * 1024
MAX_ARCHIVAL_PAYLOAD_BYTES = 1024 * 1024 * 1024
MAX_WAYBACK_CANDIDATES = 64
MAX_COMMONCRAWL_CANDIDATES = 128
MAX_COMMONCRAWL_COLLECTIONS = 64
MAX_PAYLOAD_ATTEMPTS_PER_SOURCE = 3
MAX_HEADER_BYTES = 64 * 1024
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_BASE_SECONDS = 2.0
RETRY_CAP_SECONDS = 30.0
COMMON_CRAWL_INDEX_SPACING_SECONDS = 2.0

PENDING = "PENDING"
INDEX_CONFIRMED_NO_CAPTURE = "INDEX_CONFIRMED_NO_CAPTURE"
PAYLOAD_PENDING = "PAYLOAD_PENDING"
EXACT_VERSION_VERIFIED = "EXACT_VERSION_VERIFIED"
VERSION_MISMATCH = "VERSION_MISMATCH"
VERSION_UNBOUND_COMPLETE = "VERSION_UNBOUND_COMPLETE"
TECHNICAL_FAILURE_RETRYABLE = "TECHNICAL_FAILURE_RETRYABLE"
TECHNICAL_FAILURE_FINAL_FOR_RUN = "TECHNICAL_FAILURE_FINAL_FOR_RUN"
SOURCE_STATES = frozenset(
    {
        PENDING,
        INDEX_CONFIRMED_NO_CAPTURE,
        PAYLOAD_PENDING,
        EXACT_VERSION_VERIFIED,
        VERSION_MISMATCH,
        VERSION_UNBOUND_COMPLETE,
        TECHNICAL_FAILURE_RETRYABLE,
        TECHNICAL_FAILURE_FINAL_FOR_RUN,
    }
)
TERMINAL_SOURCE_STATES = frozenset(
    {
        INDEX_CONFIRMED_NO_CAPTURE,
        EXACT_VERSION_VERIFIED,
        VERSION_MISMATCH,
        VERSION_UNBOUND_COMPLETE,
    }
)

CBR_HOSTS = frozenset({"cbr.ru", "www.cbr.ru"})
WAYBACK_HOSTS = frozenset({"web.archive.org"})
CC_INDEX_HOSTS = frozenset({"index.commoncrawl.org"})
CC_DATA_HOSTS = frozenset({"data.commoncrawl.org"})
SHA256_RE = re.compile(r"[0-9a-f]{64}")
TIMESTAMP_RE = re.compile(r"\d{14}")
FILENAME_RE = re.compile(r"(101|102|123|135)-(\d{8})\.rar")
FORM_SHORT = {form.value: form.short_code for form in FORMS}
FORM_VALUES = tuple(form.value for form in FORMS)


class AuditError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical_sha256(value: Mapping[str, Any]) -> str:
    body = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AuditError("INVALID_OBSERVATION_TIME")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _capture_time(value: str) -> datetime:
    if TIMESTAMP_RE.fullmatch(value) is None:
        raise AuditError("ARCHIVE_INDEX_INVALID")
    try:
        return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        raise AuditError("ARCHIVE_INDEX_INVALID") from None


def _validate_url(url: str, hosts: frozenset[str]) -> str:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or host not in hosts
            or parsed.username
            or parsed.password
            or parsed.port not in (None, 443)
            or parsed.fragment
            or not parsed.path.startswith("/")
            or any(ord(char) < 33 for char in url)
        ):
            raise ValueError
        try:
            if host and ipaddress.ip_address(host).is_private:
                raise ValueError
        except ValueError as exc:
            if host and re.fullmatch(r"[0-9a-fA-F:.]+", host):
                raise AuditError("UNSAFE_ARCHIVE_URL") from exc
    except (TypeError, ValueError):
        raise AuditError("UNSAFE_ARCHIVE_URL") from None
    return url


def _validate_target_url(url: str, report_date: str, form: str, filename: str) -> None:
    _validate_url(url, CBR_HOSTS)
    match = FILENAME_RE.fullmatch(filename)
    expected = f"{FORM_SHORT.get(form, '')}-{report_date.replace('-', '')}.rar"
    if (
        match is None
        or filename != expected
        or urlsplit(url).path != f"/vfs/credit/forms/{filename}"
        or urlsplit(url).query
    ):
        raise AuditError("TARGET_INVENTORY_INVALID")


@dataclass(frozen=True, slots=True)
class TargetArtifact:
    report_date: str
    form: str
    source_url: str
    artifact_filename: str
    target_content_sha256: str
    target_compressed_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date,
            "form": self.form,
            "source_url": self.source_url,
            "artifact_filename": self.artifact_filename,
            "target_content_sha256": self.target_content_sha256,
            "target_compressed_size": self.target_compressed_size,
        }


@dataclass(frozen=True, slots=True)
class TargetInventory:
    binding_target: str
    upstream_task258b_batch_sha256: str | None
    artifacts: tuple[TargetArtifact, ...]
    target_inventory_sha256: str

    def body(self) -> dict[str, Any]:
        return {
            "schema": INVENTORY_SCHEMA,
            "binding_target": self.binding_target,
            "upstream_task258b_batch_sha256": self.upstream_task258b_batch_sha256,
            "target_report_dates": [item.isoformat() for item in REPORT_DATES],
            "target_forms": list(FORM_VALUES),
            "target_artifacts": [item.to_dict() for item in self.artifacts],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "target_inventory_sha256": self.target_inventory_sha256}

    @classmethod
    def create(
        cls,
        *,
        binding_target: str,
        upstream_task258b_batch_sha256: str | None,
        artifacts: Iterable[TargetArtifact],
    ) -> "TargetInventory":
        ordered = tuple(sorted(artifacts, key=lambda item: (item.report_date, item.form)))
        expected_pairs = tuple(
            (day.isoformat(), form.value) for day in REPORT_DATES for form in FORMS
        )
        if binding_target not in {CURRENT_BINDING, FROZEN_BINDING}:
            raise AuditError("TARGET_INVENTORY_INVALID")
        if binding_target == FROZEN_BINDING:
            if not isinstance(upstream_task258b_batch_sha256, str) or SHA256_RE.fullmatch(
                upstream_task258b_batch_sha256
            ) is None:
                raise AuditError("TARGET_INVENTORY_INVALID")
        elif upstream_task258b_batch_sha256 is not None:
            raise AuditError("TARGET_INVENTORY_INVALID")
        if tuple((item.report_date, item.form) for item in ordered) != expected_pairs:
            raise AuditError("TARGET_INVENTORY_SCOPE_INVALID")
        total = 0
        for item in ordered:
            try:
                parsed_date = date.fromisoformat(item.report_date)
            except ValueError:
                raise AuditError("TARGET_INVENTORY_INVALID") from None
            if (
                parsed_date not in REPORT_DATES
                or item.form not in FORM_VALUES
                or SHA256_RE.fullmatch(item.target_content_sha256) is None
                or not isinstance(item.target_compressed_size, int)
                or item.target_compressed_size <= 0
                or item.target_compressed_size > MAX_ARTIFACT_BYTES
            ):
                raise AuditError("TARGET_INVENTORY_INVALID")
            _validate_target_url(
                item.source_url, item.report_date, item.form, item.artifact_filename
            )
            total += item.target_compressed_size
        if total > MAX_CURRENT_INVENTORY_BYTES:
            raise AuditError("TARGET_INVENTORY_OVERSIZE")
        draft = cls(binding_target, upstream_task258b_batch_sha256, ordered, "")
        return cls(
            binding_target,
            upstream_task258b_batch_sha256,
            ordered,
            _canonical_sha256(draft.body()),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TargetInventory":
        expected = {
            "schema",
            "binding_target",
            "upstream_task258b_batch_sha256",
            "target_report_dates",
            "target_forms",
            "target_artifacts",
            "target_inventory_sha256",
        }
        if set(payload) != expected or payload.get("schema") != INVENTORY_SCHEMA:
            raise AuditError("TARGET_INVENTORY_INVALID")
        if payload.get("target_report_dates") != [item.isoformat() for item in REPORT_DATES]:
            raise AuditError("TARGET_INVENTORY_SCOPE_INVALID")
        if payload.get("target_forms") != list(FORM_VALUES):
            raise AuditError("TARGET_INVENTORY_SCOPE_INVALID")
        raw = payload.get("target_artifacts")
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise AuditError("TARGET_INVENTORY_INVALID")
        artifact_fields = set(TargetArtifact.__dataclass_fields__)
        if any(set(item) != artifact_fields for item in raw):
            raise AuditError("TARGET_INVENTORY_INVALID")
        try:
            artifacts = tuple(TargetArtifact(**item) for item in raw)
        except TypeError:
            raise AuditError("TARGET_INVENTORY_INVALID") from None
        rebuilt = cls.create(
            binding_target=payload.get("binding_target"),
            upstream_task258b_batch_sha256=payload.get(
                "upstream_task258b_batch_sha256"
            ),
            artifacts=artifacts,
        )
        supplied = payload.get("target_inventory_sha256")
        if not isinstance(supplied, str) or not hmac.compare_digest(
            rebuilt.target_inventory_sha256, supplied
        ):
            raise AuditError("TARGET_INVENTORY_HASH_MISMATCH")
        if dict(payload) != rebuilt.to_dict():
            raise AuditError("TARGET_INVENTORY_INVALID")
        return rebuilt


def _empty_source_checkpoint() -> dict[str, Any]:
    return {
        "state": PENDING,
        "candidates": [],
        "index_rows": [],
        "index_cursor": 0,
        "payload_cursor": 0,
        "source_error_code": None,
    }


def _checkpoint_evidence_body(checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": checkpoint["schema"],
        "audit_code_contract_version": checkpoint["audit_code_contract_version"],
        "audit_observed_at": checkpoint["audit_observed_at"],
        "target_inventory_sha256": checkpoint["target_inventory_sha256"],
        "target_inventory": checkpoint["target_inventory"],
        "source_scope": checkpoint["source_scope"],
        "artifacts": checkpoint["artifacts"],
    }


def _seal_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    checkpoint["checkpoint_evidence_sha256"] = _canonical_sha256(
        _checkpoint_evidence_body(checkpoint)
    )
    return checkpoint


def create_checkpoint(
    inventory: TargetInventory, *, observed_at: datetime, created_at: datetime
) -> dict[str, Any]:
    observed = _iso(observed_at)
    created = _iso(created_at)
    checkpoint = {
        "schema": CHECKPOINT_SCHEMA,
        "audit_code_contract_version": AUDIT_CODE_CONTRACT_VERSION,
        "created_at": created,
        "updated_at": created,
        "audit_observed_at": observed,
        "target_inventory_sha256": inventory.target_inventory_sha256,
        "target_inventory": inventory.to_dict(),
        "source_scope": {"commoncrawl_collections": []},
        "artifacts": [
            {
                "report_date": item.report_date,
                "form": item.form,
                "sources": {
                    WAYBACK: _empty_source_checkpoint(),
                    COMMON_CRAWL: _empty_source_checkpoint(),
                },
            }
            for item in inventory.artifacts
        ],
        "checkpoint_evidence_sha256": "",
    }
    return _seal_checkpoint(checkpoint)


def validate_checkpoint(payload: Mapping[str, Any]) -> tuple[dict[str, Any], TargetInventory]:
    expected = {
        "schema",
        "audit_code_contract_version",
        "created_at",
        "updated_at",
        "audit_observed_at",
        "target_inventory_sha256",
        "target_inventory",
        "source_scope",
        "artifacts",
        "checkpoint_evidence_sha256",
    }
    if (
        set(payload) != expected
        or payload.get("schema") != CHECKPOINT_SCHEMA
        or payload.get("audit_code_contract_version") != AUDIT_CODE_CONTRACT_VERSION
    ):
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    for field in ("created_at", "updated_at", "audit_observed_at"):
        try:
            parsed = datetime.fromisoformat(str(payload[field]).replace("Z", "+00:00"))
            _iso(parsed)
        except (ValueError, TypeError, AuditError):
            raise AuditError("CHECKPOINT_INCOMPATIBLE") from None
    raw_inventory = payload.get("target_inventory")
    if not isinstance(raw_inventory, dict):
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    try:
        inventory = TargetInventory.from_dict(raw_inventory)
    except AuditError:
        raise AuditError("CHECKPOINT_INCOMPATIBLE") from None
    if payload.get("target_inventory_sha256") != inventory.target_inventory_sha256:
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    source_scope = payload.get("source_scope")
    if not isinstance(source_scope, dict) or set(source_scope) != {"commoncrawl_collections"}:
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    collections = source_scope["commoncrawl_collections"]
    if not isinstance(collections, list):
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(inventory.artifacts):
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    source_fields = {
        "state",
        "candidates",
        "index_rows",
        "index_cursor",
        "payload_cursor",
        "source_error_code",
    }
    for expected_artifact, raw in zip(inventory.artifacts, artifacts, strict=True):
        if (
            not isinstance(raw, dict)
            or set(raw) != {"report_date", "form", "sources"}
            or raw.get("report_date") != expected_artifact.report_date
            or raw.get("form") != expected_artifact.form
            or not isinstance(raw.get("sources"), dict)
            or set(raw["sources"]) != {WAYBACK, COMMON_CRAWL}
        ):
            raise AuditError("CHECKPOINT_INCOMPATIBLE")
        for source in (WAYBACK, COMMON_CRAWL):
            entry = raw["sources"][source]
            if (
                not isinstance(entry, dict)
                or set(entry) != source_fields
                or entry.get("state") not in SOURCE_STATES
                or not isinstance(entry.get("candidates"), list)
                or not isinstance(entry.get("index_rows"), list)
                or not isinstance(entry.get("index_cursor"), int)
                or not isinstance(entry.get("payload_cursor"), int)
                or entry["index_cursor"] < 0
                or entry["payload_cursor"] < 0
                or (
                    entry.get("source_error_code") is not None
                    and not isinstance(entry.get("source_error_code"), str)
                )
            ):
                raise AuditError("CHECKPOINT_INCOMPATIBLE")
            if entry["state"] == EXACT_VERSION_VERIFIED and not any(
                isinstance(candidate, dict) and candidate.get("payload_exact_match") is True
                for candidate in entry["candidates"]
            ):
                raise AuditError("CHECKPOINT_INCOMPATIBLE")
            if entry["state"] == INDEX_CONFIRMED_NO_CAPTURE and entry["candidates"]:
                raise AuditError("CHECKPOINT_INCOMPATIBLE")
    supplied = payload.get("checkpoint_evidence_sha256")
    if not isinstance(supplied, str) or SHA256_RE.fullmatch(supplied) is None:
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    rebuilt = dict(payload)
    if not hmac.compare_digest(
        supplied, _canonical_sha256(_checkpoint_evidence_body(rebuilt))
    ):
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    return rebuilt, inventory


def load_checkpoint(path: Path) -> tuple[dict[str, Any], TargetInventory]:
    try:
        if path.is_symlink() or not path.is_file():
            raise AuditError("CHECKPOINT_UNREADABLE")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except AuditError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise AuditError("CHECKPOINT_UNREADABLE") from None
    if not isinstance(payload, dict):
        raise AuditError("CHECKPOINT_INCOMPATIBLE")
    return validate_checkpoint(payload)


def write_checkpoint(path: Path, checkpoint: dict[str, Any], *, updated_at: datetime) -> None:
    if path.exists() and path.is_symlink():
        raise AuditError("CHECKPOINT_WRITE_FAILED")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent.is_symlink():
            raise AuditError("CHECKPOINT_WRITE_FAILED")
        checkpoint["updated_at"] = _iso(updated_at)
        _seal_checkpoint(checkpoint)
        body = json.dumps(
            checkpoint, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
        loaded, _ = load_checkpoint(path)
        if loaded["checkpoint_evidence_sha256"] != checkpoint["checkpoint_evidence_sha256"]:
            raise AuditError("CHECKPOINT_WRITE_FAILED")
    except AuditError:
        raise
    except OSError:
        raise AuditError("CHECKPOINT_WRITE_FAILED") from None


@dataclass(frozen=True, slots=True)
class HttpResult:
    status_code: int
    final_url: str
    body: bytes
    headers: Mapping[str, str]
    redirects: tuple[str, ...]


class ReadOnlyTransport:
    def __init__(
        self,
        client: httpx.Client,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.client = client
        self.sleep = sleep
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.logical_requests = 0
        self.request_attempts = 0
        self.network_accessed = False
        self.archive_payload_bytes = 0
        self.current_inventory_bytes = 0

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        delay = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2**attempt))
        if response is None:
            return delay
        retry_after = response.headers.get("retry-after")
        if not retry_after:
            return delay
        parsed: float | None = None
        if retry_after.isdigit():
            parsed = float(retry_after)
        else:
            try:
                parsed_date = parsedate_to_datetime(retry_after)
                if parsed_date.tzinfo is None:
                    parsed_date = parsed_date.replace(tzinfo=timezone.utc)
                parsed = max(
                    0.0,
                    (parsed_date.astimezone(timezone.utc) - self.clock()).total_seconds(),
                )
            except (TypeError, ValueError, OverflowError):
                parsed = None
        return min(RETRY_CAP_SECONDS, max(delay, parsed or 0.0))

    def get(
        self,
        url: str,
        *,
        hosts: frozenset[str],
        max_bytes: int,
        headers: Mapping[str, str] | None = None,
        allowed_statuses: frozenset[int] = frozenset({200}),
        budget: str | None = None,
    ) -> HttpResult:
        self.logical_requests += 1
        current = _validate_url(url, hosts)
        redirects: list[str] = []
        for redirect_index in range(MAX_REDIRECTS + 1):
            response: httpx.Response | None = None
            for attempt in range(MAX_ATTEMPTS):
                self.request_attempts += 1
                self.network_accessed = True
                try:
                    request = self.client.build_request("GET", current, headers=headers)
                    response = self.client.send(request, stream=True)
                except httpx.TimeoutException:
                    if attempt + 1 == MAX_ATTEMPTS:
                        raise AuditError("ARCHIVE_TIMEOUT") from None
                    self.sleep(self._retry_delay(None, attempt))
                    continue
                except httpx.TransportError:
                    if attempt + 1 == MAX_ATTEMPTS:
                        raise AuditError("ARCHIVE_CONNECTION_ERROR") from None
                    self.sleep(self._retry_delay(None, attempt))
                    continue
                except httpx.HTTPError:
                    raise AuditError("ARCHIVE_SOURCE_ERROR") from None
                if response.status_code in RETRYABLE_STATUS_CODES:
                    status = response.status_code
                    delay = self._retry_delay(response, attempt)
                    response.close()
                    if attempt + 1 == MAX_ATTEMPTS:
                        raise AuditError(
                            "ARCHIVE_RATE_LIMITED"
                            if status == 429
                            else "ARCHIVE_RETRY_EXHAUSTED_503"
                            if status == 503
                            else "ARCHIVE_SOURCE_ERROR"
                        )
                    self.sleep(delay)
                    continue
                break
            assert response is not None
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                response.close()
                if not location or redirect_index == MAX_REDIRECTS:
                    raise AuditError("ARCHIVE_REDIRECT_INVALID")
                current = _validate_url(urljoin(current, location), hosts)
                redirects.append(current)
                continue
            if response.status_code not in allowed_statuses:
                status = response.status_code
                response.close()
                return HttpResult(status, current, b"", {}, tuple(redirects))
            length = response.headers.get("content-length")
            if length and (not length.isdigit() or int(length) > max_bytes):
                response.close()
                raise AuditError("ARCHIVE_RESPONSE_OVERSIZE")
            chunks: list[bytes] = []
            size = 0
            try:
                stream = (response.content,) if response.is_stream_consumed else response.iter_raw()
                for chunk in stream:
                    size += len(chunk)
                    if size > max_bytes:
                        raise AuditError("ARCHIVE_RESPONSE_OVERSIZE")
                    chunks.append(chunk)
            finally:
                response.close()
            if budget == "archive":
                self.archive_payload_bytes += size
                if self.archive_payload_bytes > MAX_ARCHIVAL_PAYLOAD_BYTES:
                    raise AuditError("ARCHIVE_TOTAL_BUDGET_EXCEEDED")
            elif budget == "current":
                self.current_inventory_bytes += size
                if self.current_inventory_bytes > MAX_CURRENT_INVENTORY_BYTES:
                    raise AuditError("CURRENT_INVENTORY_BUDGET_EXCEEDED")
            return HttpResult(
                response.status_code,
                current,
                b"".join(chunks),
                {key.lower(): value for key, value in response.headers.items()},
                tuple(redirects),
            )
        raise AuditError("ARCHIVE_REDIRECT_INVALID")


def build_current_inventory(
    transport: ReadOnlyTransport, *, observed_at: datetime
) -> TargetInventory:
    page = transport.get(
        SOURCE_PAGE, hosts=CBR_HOSTS, max_bytes=MAX_CBR_CATALOG_BYTES
    )
    try:
        html = page.body.decode("utf-8", errors="strict")
        references = discover_artifacts_from_html(
            html, discovered_at=observed_at, source_page=page.final_url
        )
    except Exception:
        raise AuditError("CBR_CATALOG_INVALID") from None
    index = {(item.report_date, item.form.value): item for item in references}
    artifacts: list[TargetArtifact] = []
    for report_date in REPORT_DATES:
        for form in FORMS:
            ref = index.get((report_date, form.value))
            if ref is None:
                raise AuditError("CBR_TARGET_ARTIFACT_MISSING")
            result = transport.get(
                ref.source_url,
                hosts=CBR_HOSTS,
                max_bytes=MAX_ARTIFACT_BYTES,
                budget="current",
            )
            if result.status_code != 200 or not result.body:
                raise AuditError("CBR_TARGET_ARTIFACT_UNAVAILABLE")
            artifacts.append(
                TargetArtifact(
                    report_date.isoformat(),
                    form.value,
                    ref.source_url,
                    ref.artifact_filename,
                    hashlib.sha256(result.body).hexdigest(),
                    len(result.body),
                )
            )
    return TargetInventory.create(
        binding_target=CURRENT_BINDING,
        upstream_task258b_batch_sha256=None,
        artifacts=artifacts,
    )


def _wayback_rows(body: bytes, expected_url: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AuditError("WAYBACK_INDEX_INVALID") from None
    fields = ["timestamp", "original", "mimetype", "statuscode", "digest", "length"]
    if not isinstance(payload, list) or not payload:
        return []
    if payload[0] != fields or any(not isinstance(row, list) or len(row) != len(fields) for row in payload[1:]):
        raise AuditError("WAYBACK_INDEX_INVALID")
    if len(payload) - 1 > MAX_WAYBACK_CANDIDATES:
        raise AuditError("WAYBACK_CANDIDATE_LIMIT_EXCEEDED")
    rows = [dict(zip(fields, row, strict=True)) for row in payload[1:]]
    exact_rows: list[dict[str, Any]] = []
    for row in rows:
        _capture_time(str(row["timestamp"]))
        if row["original"] == expected_url:
            exact_rows.append(row)
    return sorted(exact_rows, key=lambda row: str(row["timestamp"]))


def _parse_cdxj(body: bytes, expected_url: str) -> list[dict[str, Any]]:
    try:
        lines = body.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError:
        raise AuditError("COMMONCRAWL_INDEX_INVALID") from None
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        raw = line.split(" ", 2)[-1] if not line.lstrip().startswith("{") else line
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            raise AuditError("COMMONCRAWL_INDEX_INVALID") from None
        if not isinstance(item, dict):
            raise AuditError("COMMONCRAWL_INDEX_INVALID")
        required = {"timestamp", "url", "status", "digest", "length", "offset", "filename"}
        if not required.issubset(item) or item["url"] != expected_url:
            raise AuditError("COMMONCRAWL_EXACT_URL_MISMATCH")
        _capture_time(str(item["timestamp"]))
        rows.append(item)
    return rows


def _header_block(data: bytes, *, code: str) -> tuple[dict[str, str], bytes]:
    boundary = data.find(b"\r\n\r\n")
    if boundary < 0 or boundary > MAX_HEADER_BYTES:
        raise AuditError(code)
    try:
        lines = data[:boundary].decode("ascii", errors="strict").split("\r\n")
    except UnicodeDecodeError:
        raise AuditError(code) from None
    headers: dict[str, str] = {":first": lines[0]}
    for line in lines[1:]:
        if ":" not in line:
            raise AuditError(code)
        key, value = line.split(":", 1)
        lowered = key.strip().lower()
        if not lowered or lowered in headers:
            raise AuditError(code)
        headers[lowered] = value.strip()
    return headers, data[boundary + 4 :]


def parse_warc_range(data: bytes, *, expected_url: str) -> dict[str, Any]:
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as source:
            decoded = source.read(MAX_ARTIFACT_BYTES + 2 * MAX_HEADER_BYTES + 1)
    except (OSError, EOFError):
        raise AuditError("COMMONCRAWL_WARC_INVALID") from None
    if len(decoded) > MAX_ARTIFACT_BYTES + 2 * MAX_HEADER_BYTES:
        raise AuditError("COMMONCRAWL_WARC_OVERSIZE")
    warc, content = _header_block(decoded, code="COMMONCRAWL_WARC_INVALID")
    if not warc[":first"].startswith("WARC/"):
        raise AuditError("COMMONCRAWL_WARC_INVALID")
    if warc.get("warc-target-uri") != expected_url:
        raise AuditError("COMMONCRAWL_WARC_TARGET_MISMATCH")
    try:
        warc_date = _iso(datetime.fromisoformat(warc["warc-date"].replace("Z", "+00:00")))
    except (KeyError, ValueError, AuditError):
        raise AuditError("COMMONCRAWL_WARC_INVALID") from None
    record_type = warc.get("warc-type")
    if record_type == "revisit":
        return {
            "record_type": "revisit",
            "warc_date": warc_date,
            "digest": warc.get("warc-payload-digest"),
            "payload": None,
        }
    if record_type != "response":
        raise AuditError("COMMONCRAWL_WARC_TYPE_UNSUPPORTED")
    try:
        content_length = int(warc["content-length"])
    except (KeyError, ValueError):
        raise AuditError("COMMONCRAWL_WARC_INVALID") from None
    if content_length < 0 or len(content) < content_length:
        raise AuditError("COMMONCRAWL_WARC_INVALID")
    http, payload = _header_block(
        content[:content_length], code="COMMONCRAWL_HTTP_INVALID"
    )
    match = re.fullmatch(r"HTTP/1\.[01] (\d{3})(?: .*)?", http[":first"])
    if match is None:
        raise AuditError("COMMONCRAWL_HTTP_INVALID")
    if http.get("content-encoding", "identity").lower() not in {"", "identity"}:
        raise AuditError("COMMONCRAWL_HTTP_ENCODING_UNSUPPORTED")
    if http.get("transfer-encoding"):
        raise AuditError("COMMONCRAWL_HTTP_ENCODING_UNSUPPORTED")
    return {
        "record_type": "response",
        "warc_date": warc_date,
        "digest": warc.get("warc-payload-digest"),
        "http_status": int(match.group(1)),
        "payload": payload,
    }


def _candidate_projection(source: str, row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "archive_source": source,
        "capture_at": _iso(_capture_time(str(row["timestamp"]))),
        "original_url": row.get("original", row.get("url")),
        "archived_status": str(row.get("statuscode", row.get("status", ""))),
        "archive_index_digest": row.get("digest"),
        "archive_payload_sha256": None,
        "target_sha256": None,
        "payload_exact_match": False,
        "capture_record_locator": None,
        "evidence_class": "ARCHIVE_INDEX_CAPTURE",
        "error_code": None,
    }


class WaybackClient:
    def __init__(self, transport: ReadOnlyTransport) -> None:
        self.transport = transport

    def inspect(
        self,
        target: TargetArtifact,
        *,
        prior: Mapping[str, Any] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        prior = prior or _empty_source_checkpoint()
        rows = list(prior.get("index_rows") or [])
        candidates = [dict(item) for item in prior.get("candidates") or []]
        index_cursor = int(prior.get("index_cursor") or 0)
        payload_cursor = int(prior.get("payload_cursor") or 0)
        if index_cursor == 0:
            params = {
                "url": target.source_url,
                "matchType": "exact",
                "output": "json",
                "fl": "timestamp,original,mimetype,statuscode,digest,length",
                "limit": str(MAX_WAYBACK_CANDIDATES + 1),
                "gzip": "false",
            }
            url = str(httpx.URL("https://web.archive.org/cdx/search/cdx", params=params))
            response = self.transport.get(
                url,
                hosts=WAYBACK_HOSTS,
                max_bytes=MAX_INDEX_BYTES,
                headers={"Connection": "close"},
                allowed_statuses=frozenset({200, 404}),
            )
            rows = (
                []
                if response.status_code == 404
                else _wayback_rows(response.body, target.source_url)
            )
            candidates = [_candidate_projection(WAYBACK, row) for row in rows]
            index_cursor = 1
            payload_cursor = 0
            if progress is not None:
                progress(
                    {
                        "candidates": candidates,
                        "index_rows": rows,
                        "index_cursor": index_cursor,
                        "payload_cursor": payload_cursor,
                        "payload_pending": bool(rows),
                        "source_error_code": None,
                    }
                )
        if len(candidates) != len(rows) or payload_cursor > len(rows):
            raise AuditError("CHECKPOINT_INCOMPATIBLE")
        payload_attempts = 0
        while payload_cursor < len(rows):
            row = rows[payload_cursor]
            candidate = candidates[payload_cursor]
            if str(row.get("statuscode")) != "200":
                if str(row.get("statuscode", "")).startswith("3"):
                    candidate["evidence_class"] = "ARCHIVE_REDIRECT_CAPTURE"
                payload_cursor += 1
                if progress is not None:
                    progress(
                        {
                            "candidates": candidates,
                            "index_rows": rows,
                            "index_cursor": index_cursor,
                            "payload_cursor": payload_cursor,
                            "payload_pending": payload_cursor < len(rows),
                            "source_error_code": None,
                        }
                    )
                continue
            if payload_attempts >= MAX_PAYLOAD_ATTEMPTS_PER_SOURCE:
                break
            payload_attempts += 1
            replay = (
                f"https://web.archive.org/web/{row['timestamp']}id_/"
                f"{quote(target.source_url, safe=':/?=&%')}"
            )
            try:
                result = self.transport.get(
                    replay,
                    hosts=WAYBACK_HOSTS,
                    max_bytes=MAX_ARTIFACT_BYTES,
                    headers={"Connection": "close"},
                    allowed_statuses=frozenset({200, 404}),
                    budget="archive",
                )
            except AuditError as exc:
                return {
                    "candidates": candidates,
                    "index_rows": rows,
                    "index_cursor": index_cursor,
                    "payload_cursor": payload_cursor,
                    "payload_pending": True,
                    "source_error_code": exc.code,
                }
            candidate["capture_record_locator"] = replay
            if result.status_code != 200 or not result.body:
                candidate.update(
                    evidence_class="ARCHIVE_CAPTURE_UNAVAILABLE",
                    error_code="WAYBACK_REPLAY_UNAVAILABLE",
                )
                payload_cursor += 1
                continue
            digest = hashlib.sha256(result.body).hexdigest()
            exact = hmac.compare_digest(digest, target.target_content_sha256)
            candidate.update(
                archive_payload_sha256=digest,
                target_sha256=target.target_content_sha256,
                payload_exact_match=exact,
                evidence_class=(
                    "ARCHIVE_PAYLOAD_EXACT_VERSION_MATCH"
                    if exact
                    else "ARCHIVE_PAYLOAD_VERSION_MISMATCH"
                ),
            )
            payload_cursor += 1
            if progress is not None:
                progress(
                    {
                        "candidates": candidates,
                        "index_rows": rows,
                        "index_cursor": index_cursor,
                        "payload_cursor": payload_cursor,
                        "payload_pending": payload_cursor < len(rows),
                        "source_error_code": None,
                    }
                )
            if exact:
                break
        return {
            "candidates": candidates,
            "index_rows": rows,
            "index_cursor": index_cursor,
            "payload_cursor": payload_cursor,
            "payload_pending": payload_cursor < len(rows),
            "source_error_code": None,
        }


def _collection_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, AttributeError):
        raise AuditError("COMMONCRAWL_COLLECTION_INDEX_INVALID") from None


class CommonCrawlClient:
    def __init__(
        self,
        transport: ReadOnlyTransport,
        *,
        observed_at: datetime,
    ) -> None:
        self.transport = transport
        self.observed_at = observed_at
        self._collections: tuple[tuple[str, datetime, datetime], ...] | None = None
        self._index_request_started = False
        self.rate_limited_for_run = False

    def collections(self) -> tuple[tuple[str, datetime, datetime], ...]:
        if self._collections is not None:
            return self._collections
        result = self.transport.get(
            "https://index.commoncrawl.org/collinfo.json",
            hosts=CC_INDEX_HOSTS,
            max_bytes=MAX_INDEX_BYTES,
        )
        try:
            payload = json.loads(result.body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AuditError("COMMONCRAWL_COLLECTION_INDEX_INVALID") from None
        if not isinstance(payload, list):
            raise AuditError("COMMONCRAWL_COLLECTION_INDEX_INVALID")
        earliest = datetime(2023, 7, 1, tzinfo=timezone.utc)
        selected: list[tuple[str, datetime, datetime]] = []
        for item in payload:
            if not isinstance(item, dict) or not {"id", "cdx-api", "from", "to"}.issubset(item):
                raise AuditError("COMMONCRAWL_COLLECTION_INDEX_INVALID")
            api = _validate_url(item["cdx-api"], CC_INDEX_HOSTS)
            if not api.endswith("-index"):
                raise AuditError("COMMONCRAWL_COLLECTION_INDEX_INVALID")
            collection_from = _collection_time(item["from"])
            collection_to = _collection_time(item["to"])
            if collection_from > collection_to:
                raise AuditError("COMMONCRAWL_COLLECTION_INDEX_INVALID")
            if collection_to >= earliest and collection_from <= self.observed_at:
                selected.append((api, collection_from, collection_to))
        selected = sorted(set(selected), key=lambda item: (item[1], item[2], item[0]))
        if not selected or len(selected) > MAX_COMMONCRAWL_COLLECTIONS:
            raise AuditError("COMMONCRAWL_COLLECTION_LIMIT_EXCEEDED")
        self._collections = tuple(selected)
        return self._collections

    def inspect(
        self,
        target: TargetArtifact,
        *,
        prior: Mapping[str, Any] | None = None,
        progress: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if self.rate_limited_for_run:
            raise AuditError("COMMON_CRAWL_RATE_LIMITED_FOR_RUN")
        prior = prior or _empty_source_checkpoint()
        report_day = date.fromisoformat(target.report_date)
        collections = tuple(
            endpoint
            for endpoint, _collection_from, collection_to in self.collections()
            if collection_to.date() >= report_day
        )
        rows = [dict(item) for item in prior.get("index_rows") or []]
        candidates = [dict(item) for item in prior.get("candidates") or []]
        index_cursor = int(prior.get("index_cursor") or 0)
        payload_cursor = int(prior.get("payload_cursor") or 0)
        if index_cursor > len(collections) or len(rows) != len(candidates):
            raise AuditError("CHECKPOINT_INCOMPATIBLE")
        for row in rows:
            if row.get("url") != target.source_url:
                raise AuditError("CHECKPOINT_INCOMPATIBLE")

        def fetch_collection(endpoint: str) -> list[dict[str, Any]]:
            if self._index_request_started:
                self.transport.sleep(COMMON_CRAWL_INDEX_SPACING_SECONDS)
            self._index_request_started = True
            url = str(
                httpx.URL(
                    endpoint,
                    params={
                        "url": target.source_url,
                        "matchType": "exact",
                        "output": "json",
                    },
                )
            )
            try:
                result = self.transport.get(
                    url,
                    hosts=CC_INDEX_HOSTS,
                    max_bytes=MAX_INDEX_BYTES,
                    headers={"Connection": "close"},
                    allowed_statuses=frozenset({200, 404}),
                )
            except AuditError as exc:
                if exc.code in {"ARCHIVE_RATE_LIMITED", "ARCHIVE_RETRY_EXHAUSTED_503"}:
                    self.rate_limited_for_run = True
                raise
            return (
                _parse_cdxj(result.body, target.source_url)
                if result.status_code == 200
                else []
            )

        while index_cursor < len(collections):
            endpoint = collections[index_cursor]
            try:
                discovered = fetch_collection(endpoint)
            except AuditError as exc:
                return {
                    "candidates": candidates,
                    "index_rows": rows,
                    "index_cursor": index_cursor,
                    "payload_cursor": payload_cursor,
                    "payload_pending": True,
                    "source_error_code": exc.code,
                }
            discovered.sort(key=lambda item: str(item["timestamp"]))
            rows.extend(discovered)
            candidates.extend(
                _candidate_projection(COMMON_CRAWL, row) for row in discovered
            )
            index_cursor += 1
            if len(rows) > MAX_COMMONCRAWL_CANDIDATES:
                raise AuditError("COMMONCRAWL_CANDIDATE_LIMIT_EXCEEDED")
            if progress is not None:
                progress(
                    {
                        "candidates": candidates,
                        "index_rows": rows,
                        "index_cursor": index_cursor,
                        "payload_cursor": payload_cursor,
                        "payload_pending": True,
                        "source_error_code": None,
                    }
                )
        if payload_cursor > len(rows):
            raise AuditError("CHECKPOINT_INCOMPATIBLE")
        attempts = 0
        processing_order = sorted(
            range(len(rows)),
            key=lambda index: (
                str(rows[index].get("mime", "")).lower() == "warc/revisit",
                str(rows[index]["timestamp"]),
            ),
        )
        while payload_cursor < len(processing_order):
            index = processing_order[payload_cursor]
            row, candidate = rows[index], candidates[index]
            if str(row.get("status")) != "200":
                if str(row.get("status", "")).startswith("3"):
                    candidate["evidence_class"] = "ARCHIVE_REDIRECT_CAPTURE"
                payload_cursor += 1
                if progress is not None:
                    progress(
                        {
                            "candidates": candidates,
                            "index_rows": rows,
                            "index_cursor": index_cursor,
                            "payload_cursor": payload_cursor,
                            "payload_pending": payload_cursor < len(processing_order),
                            "source_error_code": None,
                        }
                    )
                continue
            if attempts >= MAX_PAYLOAD_ATTEMPTS_PER_SOURCE:
                break
            try:
                offset = int(row["offset"])
                length = int(row["length"])
            except (TypeError, ValueError):
                raise AuditError("COMMONCRAWL_RANGE_INVALID") from None
            if offset < 0 or length <= 0 or length > MAX_WARC_RANGE_BYTES:
                raise AuditError("COMMONCRAWL_RANGE_INVALID")
            filename = str(row["filename"])
            if (
                not filename.startswith("crawl-data/")
                or ".." in filename.split("/")
                or not filename.endswith(".warc.gz")
            ):
                raise AuditError("COMMONCRAWL_RANGE_INVALID")
            attempts += 1
            locator = f"https://data.commoncrawl.org/{filename}#bytes={offset}-{offset + length - 1}"
            try:
                result = self.transport.get(
                    f"https://data.commoncrawl.org/{filename}",
                    hosts=CC_DATA_HOSTS,
                    max_bytes=MAX_WARC_RANGE_BYTES,
                    headers={
                        "Range": f"bytes={offset}-{offset + length - 1}",
                        "Connection": "close",
                    },
                    allowed_statuses=frozenset({206}),
                    budget="archive",
                )
            except AuditError as exc:
                if exc.code in {"ARCHIVE_RATE_LIMITED", "ARCHIVE_RETRY_EXHAUSTED_503"}:
                    self.rate_limited_for_run = True
                return {
                    "candidates": candidates,
                    "index_rows": rows,
                    "index_cursor": index_cursor,
                    "payload_cursor": payload_cursor,
                    "payload_pending": True,
                    "source_error_code": exc.code,
                }
            candidate["capture_record_locator"] = locator
            if result.status_code != 206:
                candidate.update(
                    evidence_class="ARCHIVE_CAPTURE_UNAVAILABLE",
                    error_code="COMMONCRAWL_RANGE_UNAVAILABLE",
                )
                payload_cursor += 1
                if progress is not None:
                    progress(
                        {
                            "candidates": candidates,
                            "index_rows": rows,
                            "index_cursor": index_cursor,
                            "payload_cursor": payload_cursor,
                            "payload_pending": payload_cursor < len(processing_order),
                            "source_error_code": None,
                        }
                    )
                continue
            parsed = parse_warc_range(result.body, expected_url=target.source_url)
            if parsed["record_type"] == "revisit":
                digest_key = str(row.get("digest"))
                linked = next(
                    (
                        item
                        for item in candidates
                        if item is not candidate
                        and item.get("original_url") == target.source_url
                        and item.get("archive_index_digest") == digest_key
                        and item.get("archive_payload_sha256") is not None
                    ),
                    None,
                )
                if linked is None:
                    candidate["evidence_class"] = "ARCHIVE_REVISIT_RECORD"
                    candidate["error_code"] = "ARCHIVAL_VERSION_UNBOUND"
                    payload_cursor += 1
                    if progress is not None:
                        progress(
                            {
                                "candidates": candidates,
                                "index_rows": rows,
                                "index_cursor": index_cursor,
                                "payload_cursor": payload_cursor,
                                "payload_pending": payload_cursor < len(processing_order),
                                "source_error_code": None,
                            }
                        )
                    continue
                digest = str(linked["archive_payload_sha256"])
            else:
                if parsed["http_status"] != 200:
                    candidate["evidence_class"] = "ARCHIVE_METADATA_ONLY"
                    payload_cursor += 1
                    if progress is not None:
                        progress(
                            {
                                "candidates": candidates,
                                "index_rows": rows,
                                "index_cursor": index_cursor,
                                "payload_cursor": payload_cursor,
                                "payload_pending": payload_cursor < len(processing_order),
                                "source_error_code": None,
                            }
                        )
                    continue
                payload = parsed["payload"]
                digest = hashlib.sha256(payload).hexdigest()
            exact = hmac.compare_digest(digest, target.target_content_sha256)
            candidate.update(
                archive_payload_sha256=digest,
                target_sha256=target.target_content_sha256,
                payload_exact_match=exact,
                evidence_class=(
                    "ARCHIVE_PAYLOAD_EXACT_VERSION_MATCH"
                    if exact
                    else "ARCHIVE_PAYLOAD_VERSION_MISMATCH"
                ),
                error_code=None,
            )
            payload_cursor += 1
            if progress is not None:
                progress(
                    {
                        "candidates": candidates,
                        "index_rows": rows,
                        "index_cursor": index_cursor,
                        "payload_cursor": payload_cursor,
                        "payload_pending": payload_cursor < len(processing_order),
                        "source_error_code": None,
                    }
                )
            if exact:
                break
        for index, row in enumerate(rows):
            if str(row.get("mime", "")).lower() != "warc/revisit":
                continue
            candidate = candidates[index]
            if candidate.get("payload_exact_match") is True:
                continue
            linked = next(
                (
                    item
                    for item in candidates
                    if item is not candidate
                    and item.get("original_url") == target.source_url
                    and item.get("archive_index_digest") == row.get("digest")
                    and item.get("archive_payload_sha256") is not None
                ),
                None,
            )
            if linked is None:
                continue
            digest = str(linked["archive_payload_sha256"])
            exact = hmac.compare_digest(digest, target.target_content_sha256)
            candidate.update(
                archive_payload_sha256=digest,
                target_sha256=target.target_content_sha256,
                payload_exact_match=exact,
                evidence_class=(
                    "ARCHIVE_PAYLOAD_EXACT_VERSION_MATCH"
                    if exact
                    else "ARCHIVE_PAYLOAD_VERSION_MISMATCH"
                ),
                error_code=None,
            )
        return {
            "candidates": candidates,
            "index_rows": rows,
            "index_cursor": index_cursor,
            "payload_cursor": payload_cursor,
            "payload_pending": payload_cursor < len(processing_order),
            "source_error_code": None,
        }


def _artifact_result(target: TargetArtifact) -> dict[str, Any]:
    return {
        **target.to_dict(),
        "wayback_candidates": [],
        "commoncrawl_candidates": [],
        "wayback_source_error_code": None,
        "commoncrawl_source_error_code": None,
        "archive_index_hit": False,
        "archived_payload_available": False,
        "verified_captures": [],
        "earliest_verified_exact_capture_at": None,
        "historical_availability_proven_by_archive": False,
        "pit_evidence_class": "UNKNOWN",
        "temporal_anomaly": False,
        "notes": [],
    }


def _source_state_from_result(result: Mapping[str, Any]) -> str:
    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        raise AuditError("ARCHIVE_SOURCE_RESULT_INVALID")
    if any(
        isinstance(candidate, dict) and candidate.get("payload_exact_match") is True
        for candidate in candidates
    ):
        return EXACT_VERSION_VERIFIED
    if result.get("payload_pending") is True:
        return PAYLOAD_PENDING
    if not candidates:
        return INDEX_CONFIRMED_NO_CAPTURE
    if any(
        isinstance(candidate, dict)
        and candidate.get("evidence_class") == "ARCHIVE_PAYLOAD_VERSION_MISMATCH"
        for candidate in candidates
    ):
        return VERSION_MISMATCH
    return VERSION_UNBOUND_COMPLETE


def _checkpoint_artifact_index(
    checkpoint: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    return {
        (item["report_date"], item["form"]): item
        for item in checkpoint["artifacts"]
    }


class _CheckpointArchive:
    def __init__(self, checkpoint: Mapping[str, Any], source: str) -> None:
        self.index = _checkpoint_artifact_index(checkpoint)
        self.source = source

    def inspect(self, target: TargetArtifact) -> dict[str, Any]:
        entry = self.index[(target.report_date, target.form)]["sources"][self.source]
        error = None
        if entry["state"] not in TERMINAL_SOURCE_STATES:
            error = entry["source_error_code"] or "SOURCE_CHECK_PENDING"
        return {"candidates": entry["candidates"], "source_error_code": error}


def _source_completion(checkpoint: Mapping[str, Any], source: str) -> dict[str, Any]:
    entries = [item["sources"][source] for item in checkpoint["artifacts"]]
    return {
        "source": source,
        "target_artifacts": len(entries),
        "complete_artifact_checks": sum(
            entry["state"] in TERMINAL_SOURCE_STATES for entry in entries
        ),
        "confirmed_no_capture": sum(
            entry["state"] == INDEX_CONFIRMED_NO_CAPTURE for entry in entries
        ),
        "capture_found": sum(bool(entry["candidates"]) for entry in entries),
        "exact_version_matches": sum(
            entry["state"] == EXACT_VERSION_VERIFIED for entry in entries
        ),
        "version_mismatches": sum(
            entry["state"] == VERSION_MISMATCH for entry in entries
        ),
        "technical_failures": sum(
            entry["state"]
            in {TECHNICAL_FAILURE_RETRYABLE, TECHNICAL_FAILURE_FINAL_FOR_RUN}
            for entry in entries
        ),
        "unresolved_checks": sum(
            entry["state"] not in TERMINAL_SOURCE_STATES for entry in entries
        ),
        "source_audit_complete": all(
            entry["state"] in TERMINAL_SOURCE_STATES for entry in entries
        ),
    }


def _collections_to_checkpoint(
    collections: Sequence[tuple[str, datetime, datetime]],
) -> list[dict[str, str]]:
    return [
        {"endpoint": endpoint, "from": _iso(start), "to": _iso(end)}
        for endpoint, start, end in collections
    ]


def _collections_from_checkpoint(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[tuple[str, datetime, datetime], ...]:
    collections: list[tuple[str, datetime, datetime]] = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"endpoint", "from", "to"}:
            raise AuditError("CHECKPOINT_INCOMPATIBLE")
        endpoint = _validate_url(row["endpoint"], CC_INDEX_HOSTS)
        start = _collection_time(row["from"])
        end = _collection_time(row["to"])
        if start > end:
            raise AuditError("CHECKPOINT_INCOMPATIBLE")
        collections.append((endpoint, start, end))
    return tuple(collections)


def report_from_checkpoint(
    checkpoint: Mapping[str, Any],
    inventory: TargetInventory,
    *,
    reused_completed_checks: int,
    newly_completed_checks: int,
    resumed_from_checkpoint: bool,
    filesystem_write: bool,
) -> dict[str, Any]:
    observed_at = datetime.fromisoformat(
        checkpoint["audit_observed_at"].replace("Z", "+00:00")
    )
    report = run_audit(
        inventory,
        wayback=_CheckpointArchive(checkpoint, WAYBACK),
        commoncrawl=_CheckpointArchive(checkpoint, COMMON_CRAWL),
        observed_at=observed_at,
    )
    source_completion = {
        WAYBACK: _source_completion(checkpoint, WAYBACK),
        COMMON_CRAWL: _source_completion(checkpoint, COMMON_CRAWL),
    }
    unresolved = sum(
        value["unresolved_checks"] for value in source_completion.values()
    )
    report.update(
        checkpoint_enabled=True,
        checkpoint_schema=CHECKPOINT_SCHEMA,
        checkpoint_evidence_sha256=checkpoint["checkpoint_evidence_sha256"],
        resumed_from_checkpoint=resumed_from_checkpoint,
        reused_completed_checks=reused_completed_checks,
        newly_completed_checks=newly_completed_checks,
        source_completion=source_completion,
        wayback_source_audit_complete=source_completion[WAYBACK]["source_audit_complete"],
        commoncrawl_source_audit_complete=source_completion[COMMON_CRAWL][
            "source_audit_complete"
        ],
        unresolved_technical_findings=unresolved,
        filesystem_write=filesystem_write,
        filesystem_write_scope=(
            "LOCAL_OPERATIONAL_CHECKPOINT_ONLY" if filesystem_write else "NONE"
        ),
    )
    semantic_body = {
        "target_inventory_sha256": inventory.target_inventory_sha256,
        "artifacts": report["artifacts"],
        "source_completion": source_completion,
    }
    report["semantic_evidence_sha256"] = _canonical_sha256(semantic_body)
    if unresolved:
        report.update(
            status="incomplete",
            error_code="ARCHIVAL_AUDIT_INCOMPLETE",
            overall_archival_status="UNKNOWN",
            recommendation="RESUME_INCOMPLETE_ARCHIVE_CHECKS",
        )
    return report


def execute_resumable_audit(
    checkpoint: dict[str, Any],
    inventory: TargetInventory,
    *,
    wayback: Any,
    commoncrawl: Any,
    selected_sources: Sequence[str],
    checkpoint_path: Path,
    clock: Callable[[], datetime],
    resumed_from_checkpoint: bool,
) -> dict[str, Any]:
    index = _checkpoint_artifact_index(checkpoint)
    reused = 0
    newly_completed = 0
    if COMMON_CRAWL in selected_sources and isinstance(commoncrawl, CommonCrawlClient):
        frozen = checkpoint["source_scope"]["commoncrawl_collections"]
        if frozen:
            commoncrawl._collections = _collections_from_checkpoint(frozen)
        else:
            commoncrawl._collections = commoncrawl.collections()
            checkpoint["source_scope"]["commoncrawl_collections"] = (
                _collections_to_checkpoint(commoncrawl._collections)
            )
            write_checkpoint(checkpoint_path, checkpoint, updated_at=clock())
    stop_commoncrawl = False
    for target in inventory.artifacts:
        artifact = index[(target.report_date, target.form)]
        for source, client in ((WAYBACK, wayback), (COMMON_CRAWL, commoncrawl)):
            if source not in selected_sources or (source == COMMON_CRAWL and stop_commoncrawl):
                continue
            entry = artifact["sources"][source]
            if entry["state"] in TERMINAL_SOURCE_STATES:
                reused += 1
                continue
            previous_terminal = entry["state"] in TERMINAL_SOURCE_STATES
            entry["state"] = TECHNICAL_FAILURE_RETRYABLE
            entry["source_error_code"] = None
            def persist_progress(partial: Mapping[str, Any]) -> None:
                entry["candidates"] = list(partial["candidates"])
                for cursor_field in ("index_rows", "index_cursor", "payload_cursor"):
                    if cursor_field in partial:
                        entry[cursor_field] = partial[cursor_field]
                entry["source_error_code"] = partial.get("source_error_code")
                entry["state"] = (
                    TECHNICAL_FAILURE_FINAL_FOR_RUN
                    if entry["source_error_code"]
                    else _source_state_from_result(partial)
                )
                write_checkpoint(checkpoint_path, checkpoint, updated_at=clock())
            try:
                if isinstance(client, (WaybackClient, CommonCrawlClient)):
                    result = client.inspect(
                        target, prior=entry, progress=persist_progress
                    )
                else:
                    result = client.inspect(target)
                entry["candidates"] = result["candidates"]
                for cursor_field in ("index_rows", "index_cursor", "payload_cursor"):
                    if cursor_field in result:
                        entry[cursor_field] = result[cursor_field]
                entry["source_error_code"] = result.get("source_error_code")
                entry["state"] = (
                    TECHNICAL_FAILURE_FINAL_FOR_RUN
                    if entry["source_error_code"]
                    else _source_state_from_result(result)
                )
            except AuditError as exc:
                entry["state"] = TECHNICAL_FAILURE_FINAL_FOR_RUN
                entry["source_error_code"] = exc.code
                if source == COMMON_CRAWL and exc.code in {
                    "ARCHIVE_RATE_LIMITED",
                    "ARCHIVE_RETRY_EXHAUSTED_503",
                    "COMMON_CRAWL_RATE_LIMITED_FOR_RUN",
                }:
                    commoncrawl.rate_limited_for_run = True
                    stop_commoncrawl = True
            if source == COMMON_CRAWL and getattr(
                commoncrawl, "rate_limited_for_run", False
            ):
                stop_commoncrawl = True
            if not previous_terminal and entry["state"] in TERMINAL_SOURCE_STATES:
                newly_completed += 1
            write_checkpoint(checkpoint_path, checkpoint, updated_at=clock())
    return report_from_checkpoint(
        checkpoint,
        inventory,
        reused_completed_checks=reused,
        newly_completed_checks=newly_completed,
        resumed_from_checkpoint=resumed_from_checkpoint,
        filesystem_write=True,
    )


def _source_counts(rows: Sequence[dict[str, Any]], source: str) -> tuple[int, int, int]:
    field = "wayback_candidates" if source == WAYBACK else "commoncrawl_candidates"
    return (
        sum(bool(row[field]) for row in rows),
        sum(any(candidate["archive_payload_sha256"] is not None for candidate in row[field]) for row in rows),
        sum(any(candidate["payload_exact_match"] is True for candidate in row[field]) for row in rows),
    )


def _lag_stats(values: Sequence[int]) -> dict[str, int | float | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "p75": None, "p90": None, "max": None}
    ordered = sorted(values)
    percentile = lambda p: ordered[min(len(ordered) - 1, max(0, int((len(ordered) - 1) * p + 0.5)))]
    return {
        "count": len(ordered),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p75": percentile(0.75),
        "p90": percentile(0.90),
        "max": ordered[-1],
    }


def _empty_report(observed_at: datetime) -> dict[str, Any]:
    return {
        "schema": AUDIT_SCHEMA,
        "status": "incomplete",
        "error_code": None,
        "observed_at": _iso(observed_at),
        "version_binding_target": None,
        "target_report_dates": [item.isoformat() for item in REPORT_DATES],
        "target_forms": list(FORM_VALUES),
        "target_artifacts": 104,
        "target_inventory_sha256": None,
        "wayback_index_hits": None,
        "wayback_payload_available": None,
        "wayback_exact_version_matches": None,
        "commoncrawl_index_hits": None,
        "commoncrawl_payload_available": None,
        "commoncrawl_exact_version_matches": None,
        "any_archive_index_hits": None,
        "any_archive_exact_version_matches": None,
        "artifacts_with_conservative_bound": None,
        "artifacts_without_conservative_bound": None,
        "coverage_ratio": None,
        "historical_availability_proven_by_archive": False,
        "publication_time_proven": False,
        "publication_at": None,
        "pit_ready": False,
        "frozen_task258b_binding": False,
        "frozen_batch_vds_run_required": True,
        "database_accessed": False,
        "database_mutation_executed": False,
        "database_persistence": False,
        "filesystem_write": False,
        "archive_write_executed": False,
        "publication_backfill_executed": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
        "checkpoint_enabled": False,
        "checkpoint_schema": None,
        "checkpoint_evidence_sha256": None,
        "resumed_from_checkpoint": False,
        "reused_completed_checks": 0,
        "newly_completed_checks": 0,
        "source_completion": {},
        "wayback_source_audit_complete": False,
        "commoncrawl_source_audit_complete": False,
        "unresolved_technical_findings": None,
        "semantic_evidence_sha256": None,
        "filesystem_write_scope": "NONE",
        "network_accessed": False,
        "logical_requests": 0,
        "request_attempts": 0,
        "artifacts": [],
        "findings": [],
        "lag_diagnostic": {"overall": _lag_stats([]), "by_form": {}, "by_year": {}, "by_archive_source": {}},
        "coverage_by_form": {},
        "coverage_by_year": {},
        "coverage_by_report_date": {},
        "coverage_by_archive_source": {},
        "earliest_verified_capture": None,
        "latest_earliest_verified_capture": None,
        "temporal_anomalies": None,
        "version_mismatches": None,
        "overall_archival_status": "UNKNOWN",
        "recommendation": "COMPLETE_EXTERNAL_ARCHIVAL_AUDIT",
    }


def run_audit(
    inventory: TargetInventory,
    *,
    wayback: Any,
    commoncrawl: Any,
    observed_at: datetime,
) -> dict[str, Any]:
    report = _empty_report(observed_at)
    report.update(
        version_binding_target=inventory.binding_target,
        target_inventory_sha256=inventory.target_inventory_sha256,
        frozen_task258b_binding=inventory.binding_target == FROZEN_BINDING,
        frozen_batch_vds_run_required=inventory.binding_target != FROZEN_BINDING,
    )
    technical_errors: list[str] = []
    rows: list[dict[str, Any]] = []
    for target in inventory.artifacts:
        row = _artifact_result(target)
        for key, client, field, error_field in (
            (WAYBACK, wayback, "wayback_candidates", "wayback_source_error_code"),
            (COMMON_CRAWL, commoncrawl, "commoncrawl_candidates", "commoncrawl_source_error_code"),
        ):
            try:
                source = client.inspect(target)
                row[field] = source["candidates"]
                row[error_field] = source.get("source_error_code")
                if row[error_field]:
                    technical_errors.append(
                        f"{key}:{target.report_date}:{target.form}:{row[error_field]}"
                    )
            except AuditError as exc:
                row[error_field] = exc.code
                technical_errors.append(f"{key}:{target.report_date}:{target.form}:{exc.code}")
        candidates = row["wayback_candidates"] + row["commoncrawl_candidates"]
        row["archive_index_hit"] = bool(candidates)
        row["archived_payload_available"] = any(
            candidate["archive_payload_sha256"] is not None for candidate in candidates
        )
        verified = [candidate for candidate in candidates if candidate["payload_exact_match"] is True]
        verified.sort(key=lambda item: (item["capture_at"], item["archive_source"]))
        valid: list[dict[str, Any]] = []
        report_day = date.fromisoformat(target.report_date)
        for candidate in verified:
            if datetime.fromisoformat(candidate["capture_at"].replace("Z", "+00:00")).date() < report_day:
                row["temporal_anomaly"] = True
                row["notes"].append("CAPTURE_BEFORE_REPORT_DATE_NOT_USED")
            else:
                valid.append(candidate)
        row["verified_captures"] = verified
        if valid:
            row["earliest_verified_exact_capture_at"] = valid[0]["capture_at"]
            row["historical_availability_proven_by_archive"] = True
            row["pit_evidence_class"] = "ARCHIVAL_PROVEN_CONSERVATIVE_BOUND"
        elif any(candidate["evidence_class"] == "ARCHIVE_PAYLOAD_VERSION_MISMATCH" for candidate in candidates):
            row["pit_evidence_class"] = "ARCHIVAL_VERSION_MISMATCH"
        elif any(candidate["evidence_class"] == "ARCHIVE_REVISIT_RECORD" for candidate in candidates):
            row["pit_evidence_class"] = "ARCHIVAL_VERSION_UNBOUND"
        elif candidates:
            row["pit_evidence_class"] = "ARCHIVAL_METADATA_ONLY"
        elif row["wayback_source_error_code"] or row["commoncrawl_source_error_code"]:
            row["pit_evidence_class"] = "UNKNOWN"
            row["notes"].append("TECHNICAL_FAILURE_IS_NOT_NO_CAPTURE")
        else:
            row["pit_evidence_class"] = "NOT_USABLE_FOR_PIT"
            row["notes"].append("NO_CAPTURE_DOES_NOT_PROVE_NON_PUBLICATION")
        rows.append(row)

    report["artifacts"] = rows
    wb = _source_counts(rows, WAYBACK)
    cc = _source_counts(rows, COMMON_CRAWL)
    proven = [row for row in rows if row["historical_availability_proven_by_archive"]]
    index_hit_artifacts = sum(row["archive_index_hit"] for row in rows)
    exact_artifacts = len(proven)
    lags = [
        (
            datetime.fromisoformat(row["earliest_verified_exact_capture_at"].replace("Z", "+00:00")).date()
            - date.fromisoformat(row["report_date"])
        ).days
        for row in proven
    ]
    by_form = {form: sum(row["form"] == form and row["historical_availability_proven_by_archive"] for row in rows) for form in FORM_VALUES}
    years = sorted({row["report_date"][:4] for row in rows})
    by_year = {year: sum(row["report_date"].startswith(year) and row["historical_availability_proven_by_archive"] for row in rows) for year in years}
    by_date = {day.isoformat(): sum(row["report_date"] == day.isoformat() and row["historical_availability_proven_by_archive"] for row in rows) for day in REPORT_DATES}
    source_coverage = {
        WAYBACK: sum(any(c["payload_exact_match"] for c in row["wayback_candidates"]) for row in rows),
        COMMON_CRAWL: sum(any(c["payload_exact_match"] for c in row["commoncrawl_candidates"]) for row in rows),
    }
    status = (
        "ARCHIVAL_EVIDENCE_SUFFICIENT_FOR_NEXT_PIT_CONTRACT"
        if exact_artifacts == 104
        else "ARCHIVAL_EVIDENCE_PARTIAL"
        if exact_artifacts
        else "ARCHIVAL_EVIDENCE_INSUFFICIENT"
    )
    report.update(
        status="incomplete" if technical_errors else "complete",
        error_code="ARCHIVAL_AUDIT_INCOMPLETE" if technical_errors else None,
        wayback_index_hits=wb[0],
        wayback_payload_available=wb[1],
        wayback_exact_version_matches=wb[2],
        commoncrawl_index_hits=cc[0],
        commoncrawl_payload_available=cc[1],
        commoncrawl_exact_version_matches=cc[2],
        any_archive_index_hits=index_hit_artifacts,
        any_archive_exact_version_matches=exact_artifacts,
        artifacts_with_conservative_bound=exact_artifacts,
        artifacts_without_conservative_bound=104 - exact_artifacts,
        coverage_ratio=f"{exact_artifacts / 104:.6f}",
        historical_availability_proven_by_archive=bool(exact_artifacts),
        coverage_by_form=by_form,
        coverage_by_year=by_year,
        coverage_by_report_date=by_date,
        coverage_by_archive_source=source_coverage,
        earliest_verified_capture=min((row["earliest_verified_exact_capture_at"] for row in proven), default=None),
        latest_earliest_verified_capture=max((row["earliest_verified_exact_capture_at"] for row in proven), default=None),
        temporal_anomalies=sum(row["temporal_anomaly"] for row in rows),
        version_mismatches=sum(candidate["evidence_class"] == "ARCHIVE_PAYLOAD_VERSION_MISMATCH" for row in rows for candidate in row["wayback_candidates"] + row["commoncrawl_candidates"]),
        overall_archival_status=status if not technical_errors else "UNKNOWN",
        recommendation=(
            "DEFINE_SEPARATE_PIT_EVIDENCE_CONTRACT"
            if exact_artifacts == 104 and not technical_errors
            else "FROZEN_TASK258B_READ_ONLY_BINDING_REQUIRED"
            if not technical_errors
            else "COMPLETE_EXTERNAL_ARCHIVAL_AUDIT"
        ),
        findings=technical_errors,
    )
    report["lag_diagnostic"] = {
        "overall": _lag_stats(lags),
        "by_form": {
            form: _lag_stats([
                (datetime.fromisoformat(row["earliest_verified_exact_capture_at"].replace("Z", "+00:00")).date() - date.fromisoformat(row["report_date"])).days
                for row in proven if row["form"] == form
            ])
            for form in FORM_VALUES
        },
        "by_year": {
            year: _lag_stats([
                (datetime.fromisoformat(row["earliest_verified_exact_capture_at"].replace("Z", "+00:00")).date() - date.fromisoformat(row["report_date"])).days
                for row in proven if row["report_date"].startswith(year)
            ])
            for year in years
        },
        "by_archive_source": {
            source: _lag_stats([
                (datetime.fromisoformat(candidate["capture_at"].replace("Z", "+00:00")).date() - date.fromisoformat(row["report_date"])).days
                for row in rows
                for candidate in row["verified_captures"]
                if candidate["archive_source"] == source and datetime.fromisoformat(candidate["capture_at"].replace("Z", "+00:00")).date() >= date.fromisoformat(row["report_date"])
            ])
            for source in (WAYBACK, COMMON_CRAWL)
        },
    }
    return report


def classify_semantic_difference(
    archived: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    archived_rows = Counter(archived["semantic_observations"])
    current_rows = Counter(current["semantic_observations"])
    raw_equal = archived_rows == current_rows
    added = sum((current_rows - archived_rows).values())
    removed = sum((archived_rows - current_rows).values())
    schema_difference = (
        archived["form_structural_schema_fingerprint"]
        != current["form_structural_schema_fingerprint"]
        or archived["member_schema_fingerprints"]
        != current["member_schema_fingerprints"]
    )
    value_schema_difference = (
        archived["value_member_schema_fingerprint"]
        != current["value_member_schema_fingerprint"]
    )
    member_bytes_equal = (
        archived["member_content_sha256"] == current["member_content_sha256"]
    )
    if not raw_equal:
        classification = "RAW_DATA_DIFFERENCE"
    elif schema_difference:
        classification = "STRUCTURAL_SCHEMA_DIFFERENCE"
    elif member_bytes_equal:
        classification = "CONTAINER_ONLY_DIFFERENCE"
    else:
        classification = "METADATA_ONLY_DIFFERENCE"
    return {
        "semantic_classification": classification,
        "added_rows": added,
        "removed_rows": removed,
        "changed_rows": min(added, removed),
        "changed_raw_values": min(added, removed),
        "schema_difference": schema_difference,
        "value_member_schema_difference": value_schema_difference,
        "raw_data_difference": not raw_equal,
        "raw_lexical_equal": raw_equal,
    }


def _semantic_projection(
    artifact: CbrBankArtifact, *, archive_executable: str | None = None
) -> dict[str, Any]:
    bundle = CbrBankRegulatoryBundleService(
        archive_executable=archive_executable
    ).build_snapshot(
        report_date=artifact.reference.report_date,
        artifacts=(artifact,),
        enforce_approved_schema=False,
        allow_dynamic_value_member=True,
        max_archive_member_bytes=HISTORICAL_MAX_MEMBER_BYTES,
        max_archive_total_uncompressed_bytes=HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES,
    )
    form_result = bundle.forms[0]
    exact = extract_exact_form_evidence(
        form_result,
        archive_executable=archive_executable,
        allow_dynamic_value_member=True,
        max_archive_member_bytes=HISTORICAL_MAX_MEMBER_BYTES,
        max_archive_total_uncompressed_bytes=HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES,
    )
    extracted = extract_archive_members(
        artifact,
        executable=archive_executable,
        max_member_bytes=HISTORICAL_MAX_MEMBER_BYTES,
        max_total_uncompressed_bytes=HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES,
    )
    member_content = tuple(
        (member.name.upper(), hashlib.sha256(content).hexdigest())
        for member, content in sorted(extracted, key=lambda item: item[0].name.upper())
    )
    schemas = tuple(form_result.member_schema_fingerprints)
    schema_by_name = dict(schemas)
    value_schema = schema_by_name[exact.value_member_name.upper()]
    semantic_rows: list[str] = []
    for observation in exact.observations:
        record = observation.record
        semantic_rows.append(
            json.dumps(
                canonical_value(
                    {
                        "regn": record.regn,
                        "source_code": record.source_code,
                        "source_date": record.source_date,
                        "source_value_field": observation.source_value_field,
                        "source_dimensions": observation.source_dimensions,
                        "raw_value_text": observation.raw_value_text,
                        "parsed_decimal_value": observation.parsed_decimal_value,
                        "disclosure_state": record.disclosure_state,
                        "raw_unit": record.raw_unit,
                        "raw_currency": record.raw_currency,
                        "raw_multiplier": record.raw_multiplier,
                    }
                ),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    semantic_rows.sort()
    return {
        "payload_sha256": artifact.content_sha256,
        "compressed_size": artifact.compressed_size,
        "member_names": [name for name, _digest in member_content],
        "member_content_sha256": list(member_content),
        "member_schema_fingerprints": list(schemas),
        "form_schema_fingerprint": form_result.form_schema_fingerprint,
        "form_structural_schema_fingerprint": compute_structural_schema_fingerprint(
            fingerprint for _name, fingerprint in schemas
        ),
        "value_member_schema_fingerprint": value_schema,
        "record_count": len(form_result.records),
        "subject_count": len(form_result.subjects),
        "subject_set_sha256": subject_set_sha256(set(form_result.subjects)),
        "source_row_fingerprint_set_sha256": ordered_fingerprints_sha256(
            [item.source_row_fingerprint for item in exact.observations]
        ),
        "semantic_observations": semantic_rows,
        "semantic_observation_set_sha256": sha256_canonical(semantic_rows),
    }


def _artifact_from_payload(
    target: TargetArtifact, payload: bytes, *, observed_at: datetime
) -> CbrBankArtifact:
    form = CbrBankForm(target.form)
    reference = CbrArtifactReference(
        form=form,
        source_href=target.source_url,
        source_url=target.source_url,
        artifact_filename=target.artifact_filename,
        report_date=date.fromisoformat(target.report_date),
        discovered_at=observed_at,
    )
    return CbrBankArtifact(
        reference=reference,
        content=payload,
        content_sha256=hashlib.sha256(payload).hexdigest(),
        compressed_size=len(payload),
        content_type="application/vnd.rar",
        retrieved_at=observed_at,
    )


def run_wayback_mismatch_semantic_audit(
    checkpoint: Mapping[str, Any],
    inventory: TargetInventory,
    *,
    transport: ReadOnlyTransport,
    observed_at: datetime,
    archive_executable: str | None = None,
) -> dict[str, Any]:
    targets = _checkpoint_artifact_index(checkpoint)
    mismatches: list[tuple[TargetArtifact, Mapping[str, Any]]] = []
    for target in inventory.artifacts:
        source = targets[(target.report_date, target.form)]["sources"][WAYBACK]
        if source["state"] != VERSION_MISMATCH:
            continue
        candidates = [
            item
            for item in source["candidates"]
            if item.get("evidence_class") == "ARCHIVE_PAYLOAD_VERSION_MISMATCH"
        ]
        if len(candidates) != 1:
            raise AuditError("WAYBACK_MISMATCH_CHECKPOINT_INVALID")
        mismatches.append((target, candidates[0]))
    if len(mismatches) != 3:
        raise AuditError("WAYBACK_MISMATCH_COUNT_INVALID")
    rows: list[dict[str, Any]] = []
    for target, candidate in mismatches:
        row = {
            "report_date": target.report_date,
            "form": target.form,
            "capture_at": candidate["capture_at"],
            "archive_sha256": candidate["archive_payload_sha256"],
            "current_sha256": target.target_content_sha256,
            "semantic_classification": "UNKNOWN",
            "error_code": None,
            "archive": None,
            "current": None,
            "added_rows": None,
            "removed_rows": None,
            "changed_rows": None,
            "changed_raw_values": None,
            "schema_difference": None,
            "value_member_schema_difference": None,
            "raw_data_difference": None,
            "raw_lexical_equal": None,
        }
        try:
            stamp = datetime.fromisoformat(
                str(candidate["capture_at"]).replace("Z", "+00:00")
            ).strftime("%Y%m%d%H%M%S")
            expected_locator = (
                f"https://web.archive.org/web/{stamp}id_/"
                f"{quote(target.source_url, safe=':/?=&%')}"
            )
            if candidate.get("capture_record_locator") != expected_locator:
                raise AuditError("WAYBACK_MISMATCH_LOCATOR_INVALID")
            archived_response = transport.get(
                expected_locator,
                hosts=WAYBACK_HOSTS,
                max_bytes=MAX_ARTIFACT_BYTES,
                headers={"Connection": "close"},
                budget="archive",
            )
            current_response = transport.get(
                target.source_url,
                hosts=CBR_HOSTS,
                max_bytes=MAX_ARTIFACT_BYTES,
                headers={"Connection": "close"},
            )
            archived_hash = hashlib.sha256(archived_response.body).hexdigest()
            current_hash = hashlib.sha256(current_response.body).hexdigest()
            if archived_hash != candidate["archive_payload_sha256"]:
                raise AuditError("WAYBACK_ARCHIVED_PAYLOAD_CHANGED")
            if (
                current_hash != target.target_content_sha256
                or len(current_response.body) != target.target_compressed_size
            ):
                raise AuditError("CURRENT_CBR_PAYLOAD_CHANGED")
            archived = _semantic_projection(
                _artifact_from_payload(target, archived_response.body, observed_at=observed_at),
                archive_executable=archive_executable,
            )
            current = _semantic_projection(
                _artifact_from_payload(target, current_response.body, observed_at=observed_at),
                archive_executable=archive_executable,
            )
            comparison = classify_semantic_difference(archived, current)
            row.update(comparison)
            row["archive"] = {
                key: value for key, value in archived.items() if key != "semantic_observations"
            }
            row["current"] = {
                key: value for key, value in current.items() if key != "semantic_observations"
            }
        except AuditError as exc:
            row["error_code"] = exc.code
        except Exception as exc:
            code = getattr(getattr(exc, "code", None), "value", None)
            row["error_code"] = code or "SEMANTIC_COMPARISON_FAILED"
        rows.append(row)
    counts = {
        name: sum(row["semantic_classification"] == name for row in rows)
        for name in (
            "CONTAINER_ONLY_DIFFERENCE",
            "METADATA_ONLY_DIFFERENCE",
            "STRUCTURAL_SCHEMA_DIFFERENCE",
            "RAW_DATA_DIFFERENCE",
            "UNKNOWN",
        )
    }
    revision_risk = any(
        row["semantic_classification"] == "RAW_DATA_DIFFERENCE"
        or row["value_member_schema_difference"] is True
        for row in rows
    )
    unknown = counts["UNKNOWN"] > 0
    return {
        "schema": MISMATCH_AUDIT_SCHEMA,
        "status": "incomplete" if unknown else "complete",
        "error_code": "SEMANTIC_AUDIT_INCOMPLETE" if unknown else None,
        "observed_at": _iso(observed_at),
        "checkpoint_evidence_sha256": checkpoint["checkpoint_evidence_sha256"],
        "target_inventory_sha256": inventory.target_inventory_sha256,
        "version_binding_target": inventory.binding_target,
        "mismatch_artifacts": len(rows),
        "classification_counts": counts,
        "historical_revision_risk_proven": revision_risk,
        "current_cbr_historical_artifacts_point_in_time_faithful": (
            "UNKNOWN" if unknown else "DISPROVEN" if revision_risk else "PROVEN"
        ),
        "artifacts": rows,
        "network_accessed": transport.network_accessed,
        "logical_requests": transport.logical_requests,
        "request_attempts": transport.request_attempts,
        "database_accessed": False,
        "database_mutation_executed": False,
        "database_persistence": False,
        "checkpoint_mutated": False,
        "common_crawl_continued": False,
        "archive_write_executed": False,
        "publication_backfill_executed": False,
        "pit_ready": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
        "frozen_task258b_binding": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=True)
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--target-inventory")
    group.add_argument("--current-official-representation", action="store_true")
    parser.add_argument("--checkpoint")
    parser.add_argument("--wayback-mismatch-semantic-audit", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-incomplete-only", action="store_true")
    parser.add_argument(
        "--source", choices=("wayback", "commoncrawl", "all"), default="all"
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    client_factory: Callable[..., httpx.Client] = httpx.Client,
) -> int:
    clock = clock or (lambda: datetime.now(timezone.utc))
    observed_at = clock()
    report = _empty_report(observed_at)
    try:
        args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit:
        report.update(status="invalid_arguments", error_code="INVALID_ARGUMENTS")
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return 2
    transport: ReadOnlyTransport | None = None
    checkpoint_written = False
    try:
        checkpoint_path = Path(args.checkpoint) if args.checkpoint else None
        if args.wayback_mismatch_semantic_audit:
            if (
                checkpoint_path is None
                or args.target_inventory
                or args.current_official_representation
                or args.resume
                or args.retry_incomplete_only
                or args.source != "all"
            ):
                raise AuditError("INVALID_ARGUMENTS")
            checkpoint, inventory = load_checkpoint(checkpoint_path)
            with client_factory(
                timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5),
                trust_env=False,
                follow_redirects=False,
                headers={
                    "User-Agent": "BondRadar-Task259B-ReadOnly/1.0",
                    "Accept-Encoding": "identity",
                },
            ) as client:
                transport = ReadOnlyTransport(client)
                report = run_wayback_mismatch_semantic_audit(
                    checkpoint,
                    inventory,
                    transport=transport,
                    observed_at=clock(),
                )
            print(
                json.dumps(
                    report,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0 if report["status"] == "complete" else 1
        has_inventory_selector = bool(
            args.target_inventory or args.current_official_representation
        )
        if args.resume:
            if (
                checkpoint_path is None
                or has_inventory_selector
            ):
                raise AuditError("INVALID_ARGUMENTS")
            checkpoint, inventory = load_checkpoint(checkpoint_path)
            observed_at = datetime.fromisoformat(
                checkpoint["audit_observed_at"].replace("Z", "+00:00")
            )
        else:
            if (
                not has_inventory_selector
                or args.retry_incomplete_only
                or (args.source != "all" and checkpoint_path is None)
            ):
                raise AuditError("INVALID_ARGUMENTS")
            checkpoint = None
            if checkpoint_path is not None and checkpoint_path.exists():
                raise AuditError("CHECKPOINT_ALREADY_EXISTS")
        if args.target_inventory and not args.resume:
            try:
                raw = Path(args.target_inventory).read_text(encoding="utf-8")
                payload = json.loads(raw)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                raise AuditError("TARGET_INVENTORY_UNREADABLE") from None
            if not isinstance(payload, dict):
                raise AuditError("TARGET_INVENTORY_INVALID")
            inventory = TargetInventory.from_dict(payload)
        with client_factory(
            timeout=httpx.Timeout(connect=5, read=30, write=10, pool=5),
            trust_env=False,
            follow_redirects=False,
            headers={
                "User-Agent": "BondRadar-Task259B-ReadOnly/1.0",
                "Accept-Encoding": "identity",
            },
        ) as client:
            transport = ReadOnlyTransport(client)
            if args.current_official_representation and not args.resume:
                inventory = build_current_inventory(transport, observed_at=observed_at)
            wayback = WaybackClient(transport)
            commoncrawl = CommonCrawlClient(transport, observed_at=observed_at)
            if checkpoint_path is not None:
                if checkpoint is None:
                    checkpoint = create_checkpoint(
                        inventory, observed_at=observed_at, created_at=clock()
                    )
                    write_checkpoint(checkpoint_path, checkpoint, updated_at=clock())
                    checkpoint_written = True
                selected = {
                    "wayback": (WAYBACK,),
                    "commoncrawl": (COMMON_CRAWL,),
                    "all": (WAYBACK, COMMON_CRAWL),
                }[args.source]
                report = execute_resumable_audit(
                    checkpoint,
                    inventory,
                    wayback=wayback,
                    commoncrawl=commoncrawl,
                    selected_sources=selected,
                    checkpoint_path=checkpoint_path,
                    clock=clock,
                    resumed_from_checkpoint=args.resume,
                )
                checkpoint_written = True
            else:
                report = run_audit(
                    inventory,
                    wayback=wayback,
                    commoncrawl=commoncrawl,
                    observed_at=observed_at,
                )
            report.update(
                network_accessed=transport.network_accessed,
                logical_requests=transport.logical_requests,
                request_attempts=transport.request_attempts,
            )
    except AuditError as exc:
        report.update(
            status=("invalid_arguments" if exc.code == "INVALID_ARGUMENTS" else "incomplete"),
            error_code=exc.code,
            filesystem_write=checkpoint_written,
            filesystem_write_scope=(
                "LOCAL_OPERATIONAL_CHECKPOINT_ONLY" if checkpoint_written else "NONE"
            ),
        )
    except Exception:
        report.update(status="incomplete", error_code="AUDIT_RUNTIME_FAILURE")
    if transport is not None:
        report.update(
            network_accessed=transport.network_accessed,
            logical_requests=transport.logical_requests,
            request_attempts=transport.request_attempts,
        )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 2 if report["status"] == "invalid_arguments" else 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
