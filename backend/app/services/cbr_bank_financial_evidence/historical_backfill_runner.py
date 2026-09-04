from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence.fingerprints import (
    ordered_fingerprints_sha256,
    sha256_canonical,
    utc_datetime,
)
from app.services.cbr_bank_financial_evidence.historical_audit import build_catalog
from app.services.cbr_bank_financial_evidence.monthly_runner import (
    MANIFEST_CONTRACT_VERSION,
    MonthlyIngestionManifest,
    _validate_apply_readback,
    classify_month_database_state,
    prepare_artifacts,
)
from app.services.cbr_bank_financial_evidence.production_runner import (
    RunnerError,
    CommitOutcomeUnknown,
    _ArgumentParser,
    _DatabaseState,
    _TASK255_TABLES,
    _commit_transaction,
    _database_url,
    _enforce_postgresql,
    _enforce_read_only,
    _read_postgresql_schema_state,
    _task255_counts,
    _validate_schema_state,
)
from app.services.cbr_bank_financial_evidence.store import CbrBankRawFinancialEvidenceStore
from app.services.cbr_bank_reporting.archive import (
    HISTORICAL_MAX_MEMBER_BYTES,
    HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES,
)
from app.services.cbr_bank_reporting.bundle import CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.client import (
    HISTORICAL_MAX_ARTIFACT_BYTES,
    HISTORICAL_MAX_TOTAL_ARTIFACT_BYTES,
    CbrBankRegulatoryClient,
)
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
)
from app.services.cbr_bank_reporting.parsers import (
    compute_structural_schema_fingerprint,
)


SCHEMA_VERSION = "bondradar.cbr_bank_historical_backfill_runner.v1"
APPLY_SCOPE_SCHEMA_VERSION = "bondradar.cbr_bank_historical_backfill_apply_scope.v1"
BATCH_MANIFEST_SCHEMA_VERSION = (
    "bondradar.cbr_bank_historical_backfill_batch_manifest.v1"
)
BATCH_MANIFEST_CONTRACT_VERSION = (
    "cbr-bank-historical-backfill-batch-manifest-v1"
)
EXPECTED_ALEMBIC_REVISION = "202609010001"
PUBLICATION_STATUS = "UNKNOWN"
PRODUCTION_ACTION_NONE = "NONE"
PRODUCTION_ACTION_HISTORICAL_APPLY = "CBR_HISTORICAL_BACKFILL_APPLY"
PRODUCTION_ACTION_HISTORICAL_APPLY_OUTCOME_UNKNOWN = "CBR_HISTORICAL_BACKFILL_APPLY_OUTCOME_UNKNOWN"
HISTORICAL_BACKFILL_MIN_REPORT_DATE = date(2023, 7, 1)
MAX_BACKFILL_COMPLETE_DATES = 32
MAX_BACKFILL_ARTIFACTS = 128
MAX_BATCH_MANIFEST_BYTES = 2 * 1024 * 1024
REQUIRED_FORMS = tuple(CbrBankForm)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_READY_ACTIONS = frozenset(
    {"INSERT_CANDIDATE", "SKIP_EXACT_MONTH", "SKIP_EXACT_SOURCE"}
)
_BLOCK_ACTIONS = frozenset(
    {"BLOCK_PARTIAL_STATE", "BLOCK_CONFLICTING_SOURCE"}
)
_APPLY_ERROR_CODES = frozenset({
    "INVALID_ARGUMENTS", "BATCH_MANIFEST_HASH_MISMATCH", "APPLY_SCOPE_HASH_MISMATCH",
    "UNKNOWN_DATABASE_STATE", "BATCH_STATE_BLOCKED", "CANDIDATE_STATE_CHANGED",
    "POSTGRESQL_REQUIRED", "READ_ONLY_VERIFICATION_FAILED", "TASK255_SCHEMA_PARTIAL",
    "TASK255_SCHEMA_MISSING", "ALEMBIC_REVISION_MISMATCH", "LEGACY_SCHEMA_MISSING",
    "ARTIFACT_CACHE_UNAVAILABLE", "ARTIFACT_IDENTITY_MISMATCH",
    "MONTHLY_MANIFEST_PLAN_MISMATCH", "STRUCTURAL_SCHEMA_PLAN_MISMATCH",
    "MONTHLY_SOURCE_VALIDATION_FAILED", "POST_WRITE_COUNT_MISMATCH",
    "POST_WRITE_READBACK_MISMATCH", "IDENTITY_TABLE_MUTATION_DETECTED",
    "TRANSACTION_CLEANUP_FAILED", "POST_COMMIT_READBACK_MISMATCH",
    "FINAL_BATCH_READBACK_MISMATCH", "FINAL_BATCH_COUNT_MISMATCH",
    "COMMIT_OUTCOME_UNKNOWN", "DATABASE_LOCK_TIMEOUT", "NETWORK_FORBIDDEN",
}) | frozenset(item.value for item in CbrSourceStatus)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RunnerError("INVALID_ARGUMENTS")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise RunnerError("INVALID_ARGUMENTS")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc
    return _strict_utc(parsed)


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _fingerprint_map(payload: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(payload, dict) or set(payload) != {
        form.value for form in REQUIRED_FORMS
    }:
        raise RunnerError("BATCH_MANIFEST_INVALID")
    if any(not _valid_digest(value) for value in payload.values()):
        raise RunnerError("BATCH_MANIFEST_INVALID")
    return tuple(sorted((str(key), str(value)) for key, value in payload.items()))


@dataclass(frozen=True, slots=True)
class IncompleteReportDate:
    report_date: date
    missing_forms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date.isoformat(),
            "missing_forms": list(self.missing_forms),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "IncompleteReportDate":
        if set(payload) != {"report_date", "missing_forms"} or not isinstance(
            payload["missing_forms"], list
        ):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        missing = tuple(str(item) for item in payload["missing_forms"])
        canonical = tuple(
            form.value for form in REQUIRED_FORMS if form.value in set(missing)
        )
        if not missing or missing != canonical or len(set(missing)) != len(missing):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        return cls(report_date=_parse_date(payload["report_date"]), missing_forms=missing)


@dataclass(frozen=True, slots=True)
class HistoricalMonthManifest:
    report_date: date
    monthly_manifest: MonthlyIngestionManifest
    form_structural_schema_fingerprint_by_form: tuple[tuple[str, str], ...]
    value_member_schema_fingerprint_by_form: tuple[tuple[str, str], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date.isoformat(),
            "monthly_manifest": self.monthly_manifest.to_dict(),
            "form_structural_schema_fingerprint_by_form": dict(
                self.form_structural_schema_fingerprint_by_form
            ),
            "value_member_schema_fingerprint_by_form": dict(
                self.value_member_schema_fingerprint_by_form
            ),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HistoricalMonthManifest":
        expected = {
            "report_date",
            "monthly_manifest",
            "form_structural_schema_fingerprint_by_form",
            "value_member_schema_fingerprint_by_form",
        }
        if set(payload) != expected or not isinstance(payload["monthly_manifest"], dict):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        report_date = _parse_date(payload["report_date"])
        monthly = MonthlyIngestionManifest.from_dict(payload["monthly_manifest"])
        if monthly.report_date != report_date or any(
            type(value) is not int
            for item in monthly.artifacts
            for value in (item.artifact_size, item.record_count, item.subject_count)
        ):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        return cls(
            report_date=report_date,
            monthly_manifest=monthly,
            form_structural_schema_fingerprint_by_form=_fingerprint_map(
                payload["form_structural_schema_fingerprint_by_form"]
            ),
            value_member_schema_fingerprint_by_form=_fingerprint_map(
                payload["value_member_schema_fingerprint_by_form"]
            ),
        )


@dataclass(frozen=True, slots=True)
class HistoricalBackfillBatchManifest:
    requested_from_date: date
    requested_to_date: date
    evidence_observed_at: datetime
    complete_report_dates: tuple[date, ...]
    incomplete_report_dates: tuple[IncompleteReportDate, ...]
    months: tuple[HistoricalMonthManifest, ...]
    artifact_count: int
    batch_manifest_sha256: str
    publication_status: str = PUBLICATION_STATUS
    publication_at: None = None

    def body(self) -> dict[str, Any]:
        return {
            "schema": BATCH_MANIFEST_SCHEMA_VERSION,
            "contract_version": BATCH_MANIFEST_CONTRACT_VERSION,
            "task255_contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
            "monthly_manifest_contract_version": MANIFEST_CONTRACT_VERSION,
            "requested_from_date": self.requested_from_date.isoformat(),
            "requested_to_date": self.requested_to_date.isoformat(),
            "evidence_observed_at": _iso(self.evidence_observed_at),
            "publication_status": self.publication_status,
            "publication_at": self.publication_at,
            "complete_report_dates": [item.isoformat() for item in self.complete_report_dates],
            "incomplete_report_dates": [
                item.to_dict() for item in self.incomplete_report_dates
            ],
            "months": [item.to_dict() for item in self.months],
            "artifact_count": self.artifact_count,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "batch_manifest_sha256": self.batch_manifest_sha256}

    @classmethod
    def create(
        cls,
        *,
        requested_from_date: date,
        requested_to_date: date,
        evidence_observed_at: datetime,
        incomplete_report_dates: Sequence[IncompleteReportDate],
        months: Sequence[HistoricalMonthManifest],
    ) -> "HistoricalBackfillBatchManifest":
        observed = _strict_utc(evidence_observed_at)
        if (
            requested_from_date < HISTORICAL_BACKFILL_MIN_REPORT_DATE
            or requested_to_date < requested_from_date
        ):
            raise RunnerError("INVALID_ARGUMENTS")
        ordered_months = tuple(sorted(months, key=lambda item: item.report_date))
        complete = tuple(item.report_date for item in ordered_months)
        ordered_incomplete = tuple(
            sorted(incomplete_report_dates, key=lambda item: item.report_date)
        )
        incomplete_dates = tuple(item.report_date for item in ordered_incomplete)
        artifact_count = sum(len(item.monthly_manifest.artifacts) for item in ordered_months)
        artifact_sizes = tuple(
            artifact.artifact_size
            for month in ordered_months
            for artifact in month.monthly_manifest.artifacts
        )
        if (
            not ordered_months
            or len(set(complete)) != len(complete)
            or len(set(incomplete_dates)) != len(incomplete_dates)
            or set(complete).intersection(incomplete_dates)
            or len(complete) > MAX_BACKFILL_COMPLETE_DATES
            or artifact_count > MAX_BACKFILL_ARTIFACTS
            or any(size <= 0 or size > HISTORICAL_MAX_ARTIFACT_BYTES for size in artifact_sizes)
            or sum(artifact_sizes) > HISTORICAL_MAX_TOTAL_ARTIFACT_BYTES
            or any(
                item < requested_from_date or item > requested_to_date
                for item in (*complete, *incomplete_dates)
            )
            or any(
                item.monthly_manifest.evidence_observed_at != observed
                for item in ordered_months
            )
            or any(
                len(item.monthly_manifest.artifacts) != len(REQUIRED_FORMS)
                for item in ordered_months
            )
        ):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        draft = cls(
            requested_from_date=requested_from_date,
            requested_to_date=requested_to_date,
            evidence_observed_at=observed,
            complete_report_dates=complete,
            incomplete_report_dates=ordered_incomplete,
            months=ordered_months,
            artifact_count=artifact_count,
            batch_manifest_sha256="",
        )
        return cls(
            requested_from_date=requested_from_date,
            requested_to_date=requested_to_date,
            evidence_observed_at=observed,
            complete_report_dates=complete,
            incomplete_report_dates=ordered_incomplete,
            months=ordered_months,
            artifact_count=artifact_count,
            batch_manifest_sha256=sha256_canonical(draft.body()),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "HistoricalBackfillBatchManifest":
        expected = {
            "schema",
            "contract_version",
            "task255_contract_version",
            "monthly_manifest_contract_version",
            "requested_from_date",
            "requested_to_date",
            "evidence_observed_at",
            "publication_status",
            "publication_at",
            "complete_report_dates",
            "incomplete_report_dates",
            "months",
            "artifact_count",
            "batch_manifest_sha256",
        }
        if (
            set(payload) != expected
            or payload["schema"] != BATCH_MANIFEST_SCHEMA_VERSION
            or payload["contract_version"] != BATCH_MANIFEST_CONTRACT_VERSION
            or payload["task255_contract_version"]
            != CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION
            or payload["monthly_manifest_contract_version"]
            != MANIFEST_CONTRACT_VERSION
            or payload["publication_status"] != PUBLICATION_STATUS
            or payload["publication_at"] is not None
            or not isinstance(payload["complete_report_dates"], list)
            or not isinstance(payload["incomplete_report_dates"], list)
            or not isinstance(payload["months"], list)
            or not isinstance(payload["artifact_count"], int)
            or not _valid_digest(payload["batch_manifest_sha256"])
        ):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        if any(not isinstance(item, dict) for item in payload["months"]):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        if any(not isinstance(item, dict) for item in payload["incomplete_report_dates"]):
            raise RunnerError("BATCH_MANIFEST_INVALID")
        months = tuple(HistoricalMonthManifest.from_dict(item) for item in payload["months"])
        incomplete = tuple(
            IncompleteReportDate.from_dict(item)
            for item in payload["incomplete_report_dates"]
        )
        rebuilt = cls.create(
            requested_from_date=_parse_date(payload["requested_from_date"]),
            requested_to_date=_parse_date(payload["requested_to_date"]),
            evidence_observed_at=_parse_utc(payload["evidence_observed_at"]),
            incomplete_report_dates=incomplete,
            months=months,
        )
        complete_payload = tuple(
            _parse_date(item) for item in payload["complete_report_dates"]
        )
        if (
            complete_payload != rebuilt.complete_report_dates
            or payload["artifact_count"] != rebuilt.artifact_count
            or not hmac.compare_digest(
                rebuilt.batch_manifest_sha256, payload["batch_manifest_sha256"]
            )
        ):
            raise RunnerError("BATCH_MANIFEST_HASH_MISMATCH")
        if dict(payload) != rebuilt.to_dict():
            raise RunnerError("BATCH_MANIFEST_INVALID")
        return rebuilt


@dataclass(frozen=True, slots=True)
class PreparedHistoricalBatch:
    manifest: HistoricalBackfillBatchManifest
    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HistoricalMonthDecision:
    report_date: date
    monthly_state: str
    source_observation: str
    backfill_action: str
    matching_artifacts: int
    matching_snapshots: int
    matching_observations: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_date": self.report_date.isoformat(),
            "monthly_state": self.monthly_state,
            "source_observation": self.source_observation,
            "backfill_action": self.backfill_action,
            "matching_artifacts": self.matching_artifacts,
            "matching_snapshots": self.matching_snapshots,
            "matching_observations": self.matching_observations,
        }


class _OfflineSource:
    """Prevent the reusable bundle service from constructing a network transport."""

    def __getattr__(self, name: str) -> Any:
        raise RunnerError("NETWORK_FORBIDDEN")


def _historical_prepare(
    *,
    report_date: date,
    evidence_observed_at: datetime,
    artifacts: tuple[CbrBankArtifact, ...],
    archive_executable: str | None,
) -> Any:
    return prepare_artifacts(
        report_date=report_date,
        evidence_observed_at=evidence_observed_at,
        artifacts=artifacts,
        archive_executable=archive_executable,
        bundle_service=CbrBankRegulatoryBundleService(
            client=_OfflineSource(), archive_executable=archive_executable
        ),
        enforce_approved_schema=False,
        allow_dynamic_value_member=True,
        max_archive_member_bytes=HISTORICAL_MAX_MEMBER_BYTES,
        max_archive_total_uncompressed_bytes=(
            HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
        ),
    )


def _structural_projection(prepared: Any) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    exact_by_form = {item.form: item for item in prepared.exact_forms}
    form_structural: list[tuple[str, str]] = []
    value_member: list[tuple[str, str]] = []
    for result in sorted(prepared.bundle.forms, key=lambda item: item.form.value):
        form = result.form.value
        exact = exact_by_form.get(form)
        if exact is None:
            raise RunnerError("HISTORICAL_MONTH_INCOMPLETE")
        inventory = dict(result.member_schema_fingerprints)
        selected = inventory.get(exact.value_member_name.upper())
        if selected is None:
            raise RunnerError("HISTORICAL_SCHEMA_LINEAGE_MISMATCH")
        form_structural.append(
            (
                form,
                compute_structural_schema_fingerprint(
                    fingerprint
                    for _member_name, fingerprint in result.member_schema_fingerprints
                ),
            )
        )
        value_member.append((form, selected))
    if len(form_structural) != len(REQUIRED_FORMS):
        raise RunnerError("HISTORICAL_MONTH_INCOMPLETE")
    return tuple(form_structural), tuple(value_member)


def _normalized_artifacts(
    artifacts: Sequence[CbrBankArtifact], *, observed_at: datetime
) -> tuple[CbrBankArtifact, ...]:
    for item in artifacts:
        if (
            len(item.content) != item.compressed_size
            or hashlib.sha256(item.content).hexdigest() != item.content_sha256
        ):
            raise RunnerError("ARTIFACT_IDENTITY_MISMATCH")
    return tuple(
        CbrBankArtifact(
            reference=CbrArtifactReference(
                form=item.reference.form,
                source_href=item.reference.source_href,
                source_url=item.reference.source_url,
                artifact_filename=item.reference.artifact_filename,
                report_date=item.reference.report_date,
                discovered_at=observed_at,
            ),
            content=item.content,
            content_sha256=item.content_sha256,
            compressed_size=item.compressed_size,
            content_type=item.content_type or "application/octet-stream",
            retrieved_at=observed_at,
        )
        for item in artifacts
    )


def _batch_report(
    manifest: HistoricalBackfillBatchManifest, *, mode: str
) -> dict[str, Any]:
    observations = sum(
        item.record_count
        for month in manifest.months
        for item in month.monthly_manifest.artifacts
    )
    return {
        "schema": SCHEMA_VERSION,
        "status": "ready",
        "mode": mode,
        "requested_from_date": manifest.requested_from_date.isoformat(),
        "requested_to_date": manifest.requested_to_date.isoformat(),
        "evidence_observed_at": _iso(manifest.evidence_observed_at),
        "complete_dates": len(manifest.complete_report_dates),
        "incomplete_dates": len(manifest.incomplete_report_dates),
        "complete_report_dates": [item.isoformat() for item in manifest.complete_report_dates],
        "incomplete_report_dates": [
            item.to_dict() for item in manifest.incomplete_report_dates
        ],
        "artifacts": manifest.artifact_count,
        "snapshots": manifest.artifact_count,
        "observations": observations,
        "batch_manifest_sha256": manifest.batch_manifest_sha256,
        "raw_lexical_mismatch_count": 0,
        "publication_status": PUBLICATION_STATUS,
        "publication_at": None,
        "historical_availability_proven": False,
        "pit_ready": False,
        "database_accessed": False,
        "database_mutation_executed": False,
        "database_persistence": False,
        "network_accessed": False,
        "filesystem_read": mode in {"plan", "preflight"},
        "filesystem_write": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
    }


def _manifest_bytes(manifest: HistoricalBackfillBatchManifest) -> bytes:
    return (
        json.dumps(
            manifest.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("ascii")


def _read_batch_manifest(path: Path) -> HistoricalBackfillBatchManifest:
    try:
        if path.stat().st_size > MAX_BATCH_MANIFEST_BYTES:
            raise RunnerError("BATCH_MANIFEST_INVALID")
        with path.open("rb") as handle:
            content = handle.read(MAX_BATCH_MANIFEST_BYTES + 1)
        if len(content) > MAX_BATCH_MANIFEST_BYTES:
            raise RunnerError("BATCH_MANIFEST_INVALID")
        payload = json.loads(content.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("BATCH_MANIFEST_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise RunnerError("BATCH_MANIFEST_INVALID")
    return HistoricalBackfillBatchManifest.from_dict(payload)


def _write_frozen_atomic(path: Path, content: bytes, *, conflict_code: str) -> None:
    if not path.parent.is_dir():
        raise RunnerError("OUTPUT_DIRECTORY_UNAVAILABLE")
    if path.exists():
        try:
            if path.read_bytes() != content:
                raise RunnerError(conflict_code)
        except OSError as exc:
            raise RunnerError("OUTPUT_UNAVAILABLE") from exc
        return
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != content:
                raise RunnerError(conflict_code)
        except OSError as exc:
            raise RunnerError("OUTPUT_UNAVAILABLE") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _artifact_path(root: Path, report_date: date, filename: str) -> Path:
    if Path(filename).name != filename or filename in {"", ".", ".."}:
        raise RunnerError("BATCH_MANIFEST_INVALID")
    month_root = root / report_date.isoformat()
    candidate = month_root / filename
    try:
        if (
            month_root.is_symlink()
            or candidate.is_symlink()
            or month_root.resolve().parent != root.resolve()
            or candidate.resolve().parent != month_root.resolve()
        ):
            raise RunnerError("BATCH_MANIFEST_INVALID")
    except OSError as exc:
        raise RunnerError("ARTIFACT_CACHE_UNAVAILABLE") from exc
    return candidate


def discover_batch(
    *,
    requested_from_date: date,
    requested_to_date: date,
    evidence_observed_at: datetime,
    artifact_dir: Path,
    batch_manifest_output: Path,
    client: CbrBankRegulatoryClient,
    archive_executable: str | None = None,
) -> PreparedHistoricalBatch:
    observed = _strict_utc(evidence_observed_at)
    if (
        requested_from_date < HISTORICAL_BACKFILL_MIN_REPORT_DATE
        or requested_to_date < requested_from_date
        or not artifact_dir.is_dir()
    ):
        raise RunnerError("INVALID_ARGUMENTS")
    try:
        catalog = build_catalog(
            client.discover_catalog(),
            from_date=requested_from_date,
            to_date=requested_to_date,
        )
    except CbrSourceError as exc:
        raise RunnerError(exc.code.value) from exc
    complete_groups = tuple(
        (report_date, references)
        for report_date, references in catalog.references_by_date
        if len(references) == len(REQUIRED_FORMS)
        and {item.form for item in references} == set(REQUIRED_FORMS)
    )
    if (
        not complete_groups
        or len(complete_groups) > MAX_BACKFILL_COMPLETE_DATES
        or len(complete_groups) * len(REQUIRED_FORMS) > MAX_BACKFILL_ARTIFACTS
    ):
        raise RunnerError("BACKFILL_BATCH_LIMIT_EXCEEDED")
    incomplete = tuple(
        IncompleteReportDate.from_dict(item)
        for item in catalog.report["incomplete_report_dates"]
    )
    months: list[HistoricalMonthManifest] = []
    for report_date, references in complete_groups:
        try:
            fetched = tuple(
                client.fetch_discovered_artifact_historical(reference)
                for reference in references
            )
            artifacts = _normalized_artifacts(fetched, observed_at=observed)
            prepared = _historical_prepare(
                report_date=report_date,
                evidence_observed_at=observed,
                artifacts=artifacts,
                archive_executable=archive_executable,
            )
        except CbrSourceError as exc:
            raise RunnerError(exc.code.value) from exc
        form_structural, value_member = _structural_projection(prepared)
        month = HistoricalMonthManifest(
            report_date=report_date,
            monthly_manifest=prepared.manifest,
            form_structural_schema_fingerprint_by_form=form_structural,
            value_member_schema_fingerprint_by_form=value_member,
        )
        month_root = artifact_dir / report_date.isoformat()
        try:
            month_root.mkdir(exist_ok=True)
        except OSError as exc:
            raise RunnerError("OUTPUT_DIRECTORY_UNAVAILABLE") from exc
        if not month_root.is_dir():
            raise RunnerError("OUTPUT_DIRECTORY_UNAVAILABLE")
        for artifact in artifacts:
            _write_frozen_atomic(
                _artifact_path(
                    artifact_dir,
                    report_date,
                    artifact.reference.artifact_filename,
                ),
                artifact.content,
                conflict_code="ARTIFACT_CACHE_CONFLICT",
            )
        months.append(month)
    manifest = HistoricalBackfillBatchManifest.create(
        requested_from_date=requested_from_date,
        requested_to_date=requested_to_date,
        evidence_observed_at=observed,
        incomplete_report_dates=incomplete,
        months=months,
    )
    _write_frozen_atomic(
        batch_manifest_output,
        _manifest_bytes(manifest),
        conflict_code="BATCH_MANIFEST_OUTPUT_CONFLICT",
    )
    report = _batch_report(manifest, mode="discover")
    report.update(network_accessed=True, filesystem_write=True)
    return PreparedHistoricalBatch(manifest=manifest, report=report)


def prepare_batch_from_manifest(
    *,
    manifest: HistoricalBackfillBatchManifest,
    artifact_dir: Path,
    archive_executable: str | None = None,
) -> PreparedHistoricalBatch:
    if not artifact_dir.is_dir():
        raise RunnerError("ARTIFACT_CACHE_UNAVAILABLE")
    for month in manifest.months:
        _prepare_cached_month(month, artifact_dir, archive_executable=archive_executable)
    return PreparedHistoricalBatch(
        manifest=manifest, report=_batch_report(manifest, mode="plan")
    )


def _prepare_cached_month(
    month: HistoricalMonthManifest,
    artifact_dir: Path,
    *,
    archive_executable: str | None = None,
) -> Any:
    artifacts: list[CbrBankArtifact] = []
    observed = month.monthly_manifest.evidence_observed_at
    for item in month.monthly_manifest.artifacts:
        path = _artifact_path(artifact_dir, month.report_date, item.artifact_filename)
        try:
            if path.stat().st_size != item.artifact_size:
                raise RunnerError("ARTIFACT_IDENTITY_MISMATCH")
            with path.open("rb") as handle:
                content = handle.read(item.artifact_size + 1)
        except OSError as exc:
            raise RunnerError("ARTIFACT_CACHE_UNAVAILABLE") from exc
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != item.artifact_size or digest != item.artifact_sha256:
            raise RunnerError("ARTIFACT_IDENTITY_MISMATCH")
        artifacts.append(CbrBankArtifact(
            reference=CbrArtifactReference(
                form=CbrBankForm.parse(item.form),
                source_href=item.source_href,
                source_url=item.source_url,
                artifact_filename=item.artifact_filename,
                report_date=month.report_date,
                discovered_at=observed,
            ),
            content=content,
            content_sha256=digest,
            compressed_size=len(content),
            content_type=item.content_type,
            retrieved_at=observed,
        ))
    try:
        prepared = _historical_prepare(
            report_date=month.report_date,
            evidence_observed_at=observed,
            artifacts=tuple(artifacts),
            archive_executable=archive_executable,
        )
    except CbrSourceError as exc:
        raise RunnerError(exc.code.value) from exc
    if prepared.manifest.to_dict() != month.monthly_manifest.to_dict():
        raise RunnerError("MONTHLY_MANIFEST_PLAN_MISMATCH")
    form_structural, value_member = _structural_projection(prepared)
    if (
        form_structural != month.form_structural_schema_fingerprint_by_form
        or value_member != month.value_member_schema_fingerprint_by_form
    ):
        raise RunnerError("STRUCTURAL_SCHEMA_PLAN_MISMATCH")
    return prepared


def _complete_source_reobservation(
    session: Session, month: HistoricalMonthManifest
) -> HistoricalMonthDecision:
    manifest = month.monthly_manifest
    expected = {item.form: item for item in manifest.artifacts}
    artifacts = list(
        session.execute(
            select(CbrBankSourceArtifact).where(
                CbrBankSourceArtifact.report_date == manifest.report_date,
                CbrBankSourceArtifact.form.in_(tuple(expected)),
            )
        ).scalars()
    )
    exact = {
        row.form: row
        for row in artifacts
        if row.form in expected
        and row.content_sha256 == expected[row.form].artifact_sha256
    }
    if len(exact) != len(expected):
        return HistoricalMonthDecision(
            manifest.report_date,
            "PARTIAL_STATE",
            "EXACT_REOBSERVATION",
            "BLOCK_PARTIAL_STATE",
            len(exact),
            0,
            0,
        )
    snapshots = list(
        session.execute(
            select(CbrBankReportSnapshot).where(
                CbrBankReportSnapshot.report_date == manifest.report_date,
                CbrBankReportSnapshot.artifact_id.in_(
                    tuple(row.id for row in exact.values())
                ),
            )
        ).scalars()
    )
    if not snapshots:
        return HistoricalMonthDecision(
            manifest.report_date,
            "PARTIAL_STATE",
            "EXACT_REOBSERVATION",
            "BLOCK_PARTIAL_STATE",
            len(exact),
            0,
            0,
        )
    grouped: dict[datetime, list[CbrBankReportSnapshot]] = {}
    for snapshot in snapshots:
        item = expected.get(snapshot.form)
        if item is None or (
            snapshot.publication_status != PUBLICATION_STATUS
            or snapshot.publication_at is not None
            or snapshot.record_count != item.record_count
            or snapshot.subject_count != item.subject_count
            or snapshot.subject_set_sha256 != item.subject_set_sha256
            or snapshot.form_schema_fingerprint != item.form_schema_fingerprint
            # Exact artifact identity is already proven; compare case only,
            # without rewriting either stored or manifest provenance.
            or snapshot.value_member_name.casefold() != item.value_member_name.casefold()
        ):
            return HistoricalMonthDecision(
                manifest.report_date,
                "PARTIAL_STATE",
                "EXACT_REOBSERVATION",
                "BLOCK_PARTIAL_STATE",
                len(exact),
                len(snapshots),
                0,
            )
        observed = utc_datetime(snapshot.observed_at, field_name="observed_at")
        grouped.setdefault(observed, []).append(snapshot)
    observation_total = 0
    for rows in grouped.values():
        if len(rows) != len(expected) or {row.form for row in rows} != set(expected):
            return HistoricalMonthDecision(
                manifest.report_date,
                "PARTIAL_STATE",
                "EXACT_REOBSERVATION",
                "BLOCK_PARTIAL_STATE",
                len(exact),
                len(snapshots),
                observation_total,
            )
        for snapshot in rows:
            observations = list(
                session.execute(
                    select(CbrBankRawObservation.source_row_fingerprint)
                    .where(CbrBankRawObservation.snapshot_id == snapshot.id)
                    .order_by(CbrBankRawObservation.source_row_number)
                ).scalars()
            )
            expected_item = expected[snapshot.form]
            observation_total += len(observations)
            if (
                len(observations) != expected_item.record_count
                or ordered_fingerprints_sha256(
                    [str(value) for value in observations]
                )
                != expected_item.source_row_fingerprint_set_sha256
            ):
                return HistoricalMonthDecision(
                    manifest.report_date,
                    "PARTIAL_STATE",
                    "EXACT_REOBSERVATION",
                    "BLOCK_PARTIAL_STATE",
                    len(exact),
                    len(snapshots),
                    observation_total,
                )
    return HistoricalMonthDecision(
        manifest.report_date,
        "EXACT_SOURCE_ALREADY_PRESENT",
        "EXACT_REOBSERVATION",
        "SKIP_EXACT_SOURCE",
        len(exact),
        len(snapshots),
        observation_total,
    )


def classify_backfill_month(
    session: Session, month: HistoricalMonthManifest
) -> HistoricalMonthDecision:
    inspection = classify_month_database_state(
        session, month.monthly_manifest, verify_observation_checksums=True
    )
    if inspection.state == "EXACT_ALREADY_PRESENT":
        history = _complete_source_reobservation(session, month)
        if history.backfill_action != "SKIP_EXACT_SOURCE":
            return history
        action = "SKIP_EXACT_MONTH"
    elif inspection.state == "CONFLICTING_ARTIFACT":
        action = "BLOCK_CONFLICTING_SOURCE"
    elif inspection.state == "PARTIAL_STATE":
        action = "BLOCK_PARTIAL_STATE"
    elif inspection.state == "EMPTY" and inspection.source_observation == "FIRST_OBSERVATION":
        action = "INSERT_CANDIDATE"
    elif inspection.state == "EMPTY" and inspection.source_observation == "EXACT_REOBSERVATION":
        return _complete_source_reobservation(session, month)
    else:
        action = "BLOCK_PARTIAL_STATE"
    return HistoricalMonthDecision(
        report_date=month.report_date,
        monthly_state=inspection.state,
        source_observation=inspection.source_observation,
        backfill_action=action,
        matching_artifacts=inspection.matching_artifacts,
        matching_snapshots=inspection.matching_snapshots,
        matching_observations=inspection.matching_observations,
    )


def build_apply_scope(
    manifest: HistoricalBackfillBatchManifest,
    decisions: Sequence[HistoricalMonthDecision],
    counts: Mapping[str, int],
) -> dict[str, Any]:
    ordered = sorted(decisions, key=lambda item: item.report_date)
    if (
        [item.report_date for item in ordered] != sorted(item.report_date for item in manifest.months)
        or len({item.report_date for item in ordered}) != len(ordered)
        or set(counts) != set(_TASK255_TABLES)
        or any(type(value) is not int or value < 0 for value in counts.values())
    ):
        raise RunnerError("UNKNOWN_DATABASE_STATE")
    ready_states = {
        "INSERT_CANDIDATE": ("EMPTY", "FIRST_OBSERVATION"),
        "SKIP_EXACT_MONTH": ("EXACT_ALREADY_PRESENT", "EXACT_REOBSERVATION"),
        "SKIP_EXACT_SOURCE": ("EXACT_SOURCE_ALREADY_PRESENT", "EXACT_REOBSERVATION"),
    }
    for item in ordered:
        if (
            item.backfill_action not in _READY_ACTIONS | _BLOCK_ACTIONS
            or item.monthly_state not in {
                "EMPTY", "EXACT_ALREADY_PRESENT", "EXACT_SOURCE_ALREADY_PRESENT",
                "PARTIAL_STATE", "CONFLICTING_ARTIFACT",
            }
            or item.source_observation not in {"FIRST_OBSERVATION", "EXACT_REOBSERVATION", "CHANGED_SOURCE_BYTES"}
            or (item.backfill_action in ready_states and
                (item.monthly_state, item.source_observation) != ready_states[item.backfill_action])
            or any(type(value) is not int or value < 0 for value in (
                item.matching_artifacts, item.matching_snapshots, item.matching_observations
            ))
        ):
            raise RunnerError("UNKNOWN_DATABASE_STATE")
    candidate_dates = {item.report_date for item in ordered if item.backfill_action == "INSERT_CANDIDATE"}
    candidates = [item for item in manifest.months if item.report_date in candidate_dates]
    return {
        "schema": APPLY_SCOPE_SCHEMA_VERSION,
        "batch_manifest_sha256": manifest.batch_manifest_sha256,
        "current_task255_counts": dict(sorted(counts.items())),
        "months": [item.to_dict() for item in ordered],
        "insert_candidate_months": len(candidates),
        "skip_exact_months": sum(item.backfill_action == "SKIP_EXACT_MONTH" for item in ordered),
        "skip_exact_source_months": sum(item.backfill_action == "SKIP_EXACT_SOURCE" for item in ordered),
        "blocked_months": sum(item.backfill_action in _BLOCK_ACTIONS for item in ordered),
        "candidate_artifacts": sum(len(item.monthly_manifest.artifacts) for item in candidates),
        "candidate_snapshots": sum(len(item.monthly_manifest.artifacts) for item in candidates),
        "candidate_observations": sum(
            artifact.record_count for item in candidates for artifact in item.monthly_manifest.artifacts
        ),
    }


def _read_apply_scope(session: Session, manifest: HistoricalBackfillBatchManifest) -> dict[str, Any]:
    return build_apply_scope(
        manifest,
        tuple(classify_backfill_month(session, month) for month in manifest.months),
        _task255_counts(session),
    )


def _lock_task255_tables(session: Session) -> None:
    # Fixed internal table identifiers, never CLI/input-derived SQL.
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    session.execute(text(
        "LOCK TABLE " + ", ".join(sorted(_TASK255_TABLES)) + " IN SHARE ROW EXCLUSIVE MODE"
    ))


def _readonly_apply_scope(
    engine: Engine,
    manifest: HistoricalBackfillBatchManifest,
    *,
    schema_reader: Callable,
    allow_non_postgresql: bool,
    read_only_enforcer: Callable,
) -> dict[str, Any]:
    with engine.connect() as connection:
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        if connection.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin() as transaction:
            with Session(bind=connection, autoflush=False) as session:
                read_only_enforcer(session)
                _validate_schema_state(schema_reader(session))
                scope = _read_apply_scope(session, manifest)
                transaction.rollback()
                return scope


def _apply_progress() -> dict[str, Any]:
    return {
        "before_task255_counts": None,
        "after_task255_counts": None,
        "task255_count_deltas": None,
        "apply_scope_sha256": None,
        "batch_manifest_sha256": None,
        "batch_ingested_at": None,
        "attempted_report_dates": [],
        "committed_report_dates": [],
        "skipped_exact_month_report_dates": [],
        "skipped_exact_source_report_dates": [],
        "failed_report_date": None,
        "last_attempted_report_date": None,
        "partial_batch_committed": False,
        "commit_outcome_unknown": False,
        "reconciliation_required": False,
        "database_accessed": False,
        "database_mutation_executed": False,
        "database_persistence": False,
        "network_accessed": False,
    }


def execute_apply(
    engine: Engine,
    *,
    prepared: PreparedHistoricalBatch,
    artifact_dir: Path,
    expected_batch_manifest_sha256: str,
    expected_apply_scope_sha256: str,
    ingested_at: datetime,
    schema_reader: Callable = _read_postgresql_schema_state,
    allow_non_postgresql: bool = False,
    read_only_enforcer: Callable = _enforce_read_only,
    lock_tables: Callable = _lock_task255_tables,
    store_factory: Callable = CbrBankRawFinancialEvidenceStore,
    readback_validator: Callable = _validate_apply_readback,
    archive_executable: str | None = None,
) -> dict[str, Any]:
    report = _failure("APPLY_FAILED", mode="apply")
    manifest = prepared.manifest
    report["batch_manifest_sha256"] = manifest.batch_manifest_sha256
    expected_scope = None
    authorized_scope = None
    candidates: list[HistoricalMonthManifest] = []
    next_index = 0
    uncertain = False
    final_verification_started = False
    current_date = None
    try:
        if not _valid_digest(expected_batch_manifest_sha256) or not _valid_digest(expected_apply_scope_sha256):
            raise RunnerError("INVALID_ARGUMENTS")
        if not hmac.compare_digest(manifest.batch_manifest_sha256, expected_batch_manifest_sha256):
            raise RunnerError("BATCH_MANIFEST_HASH_MISMATCH")
        report["batch_ingested_at"] = _iso(_strict_utc(ingested_at))
        while True:
            connection = transaction = session = None
            commit_attempted = False
            try:
                report["database_accessed"] = True
                connection = engine.connect()
                _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
                # Fresh snapshots after table locks, even for engines with a different default.
                if connection.dialect.name == "postgresql":
                    connection = connection.execution_options(isolation_level="READ COMMITTED")
                transaction = connection.begin()
                session = Session(bind=connection, autoflush=False, expire_on_commit=False)
                lock_tables(session)
                _validate_schema_state(schema_reader(session))
                scope = _read_apply_scope(session, manifest)
                if expected_scope is None:
                    report["apply_scope_sha256"] = sha256_canonical(scope)
                    if not hmac.compare_digest(report["apply_scope_sha256"], expected_apply_scope_sha256):
                        raise RunnerError("APPLY_SCOPE_HASH_MISMATCH")
                    if scope["blocked_months"]:
                        raise RunnerError("BATCH_STATE_BLOCKED")
                    authorized_scope = expected_scope = scope
                    report["before_task255_counts"] = dict(scope["current_task255_counts"])
                    actions = {item["report_date"]: item["backfill_action"] for item in scope["months"]}
                    candidates = sorted(
                        (month for month in manifest.months if actions[month.report_date.isoformat()] == "INSERT_CANDIDATE"),
                        key=lambda month: month.report_date,
                    )
                    for action, field in (
                        ("SKIP_EXACT_MONTH", "skipped_exact_month_report_dates"),
                        ("SKIP_EXACT_SOURCE", "skipped_exact_source_report_dates"),
                    ):
                        report[field] = sorted(day for day, value in actions.items() if value == action)
                    if not candidates:
                        transaction.rollback()
                        break
                elif not hmac.compare_digest(sha256_canonical(scope), sha256_canonical(expected_scope)):
                    raise RunnerError("APPLY_SCOPE_HASH_MISMATCH")

                month = candidates[next_index]
                current_date = month.report_date.isoformat()
                if current_date not in report["attempted_report_dates"]:
                    report["attempted_report_dates"].append(current_date)
                report["last_attempted_report_date"] = current_date
                current = next(item for item in scope["months"] if item["report_date"] == current_date)
                if current["backfill_action"] != "INSERT_CANDIDATE":
                    raise RunnerError("CANDIDATE_STATE_CHANGED")
                month_prepared = _prepare_cached_month(
                    month, artifact_dir, archive_executable=archive_executable
                )
                result = store_factory(
                    session,
                    archive_executable=archive_executable,
                    allow_dynamic_value_member=True,
                    max_archive_member_bytes=HISTORICAL_MAX_MEMBER_BYTES,
                    max_archive_total_uncompressed_bytes=HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES,
                ).persist_bundle(
                    month_prepared.bundle,
                    observed_at=manifest.evidence_observed_at,
                    ingested_at=ingested_at,
                    publication_status=PUBLICATION_STATUS,
                    publication_at=None,
                    identity_snapshot=None,
                )
                after, inspection = readback_validator(
                    session, prepared=month_prepared,
                    before_counts=scope["current_task255_counts"], result=result,
                )
                row_count = sum(item.record_count for item in month.monthly_manifest.artifacts)
                if (
                    inspection.state != "EXACT_ALREADY_PRESENT"
                    or result.artifacts.inserted != 4 or result.snapshots.inserted != 4
                    or result.observations.inserted != row_count
                    or result.identity_evidence.inserted or result.identity_evidence.updated
                    or result.identity_profiles.inserted or result.identity_profiles.updated
                ):
                    raise RunnerError("POST_WRITE_READBACK_MISMATCH")
                terminal_decisions = []
                for item in scope["months"]:
                    values = dict(item)
                    values["report_date"] = date.fromisoformat(values["report_date"])
                    decision = HistoricalMonthDecision(**values)
                    if decision.report_date == month.report_date:
                        decision = replace(
                            decision, monthly_state="EXACT_ALREADY_PRESENT",
                            source_observation="EXACT_REOBSERVATION", backfill_action="SKIP_EXACT_MONTH",
                            matching_artifacts=4, matching_snapshots=4, matching_observations=row_count,
                        )
                    terminal_decisions.append(decision)
                next_scope = build_apply_scope(manifest, terminal_decisions, after)
                if sha256_canonical(_read_apply_scope(session, manifest)) != sha256_canonical(next_scope):
                    raise RunnerError("POST_WRITE_READBACK_MISMATCH")
                commit_attempted = True
                try:
                    _commit_transaction(transaction)
                except Exception as exc:
                    uncertain = True
                    raise CommitOutcomeUnknown() from exc
                # Record success before cleanup/readback can fail.
                report["committed_report_dates"].append(current_date)
                expected_scope = next_scope
                del month_prepared
            finally:
                # Never replace the primary failure or infer a commit outcome from cleanup.
                active_error = sys.exc_info()[0] is not None
                cleanup_failed = False
                for cleanup in (
                    lambda: transaction.rollback() if transaction is not None and transaction.is_active and not commit_attempted else None,
                    lambda: session.close() if session is not None else None,
                    lambda: connection.close() if connection is not None else None,
                ):
                    try:
                        cleanup()
                    except Exception:
                        cleanup_failed = True
                if cleanup_failed and not active_error:
                    raise RunnerError("TRANSACTION_CLEANUP_FAILED")
            verified = _readonly_apply_scope(
                engine, manifest, schema_reader=schema_reader,
                allow_non_postgresql=allow_non_postgresql, read_only_enforcer=read_only_enforcer,
            )
            if sha256_canonical(verified) != sha256_canonical(expected_scope):
                raise RunnerError("POST_COMMIT_READBACK_MISMATCH")
            next_index += 1
            if next_index == len(candidates):
                break
            current_date = candidates[next_index].report_date.isoformat()
            report["last_attempted_report_date"] = current_date
            report["attempted_report_dates"].append(current_date)

        final_verification_started = True
        final = _readonly_apply_scope(
            engine, manifest, schema_reader=schema_reader,
            allow_non_postgresql=allow_non_postgresql, read_only_enforcer=read_only_enforcer,
        )
        if (sha256_canonical(final) != sha256_canonical(expected_scope)
                or final["blocked_months"] or final["insert_candidate_months"]):
            raise RunnerError("FINAL_BATCH_READBACK_MISMATCH")
        before = report["before_task255_counts"]
        after = final["current_task255_counts"]
        deltas = {table: after[table] - before[table] for table in sorted(before)}
        for table, field in (
            ("cbr_bank_source_artifacts", "candidate_artifacts"),
            ("cbr_bank_report_snapshots", "candidate_snapshots"),
            ("cbr_bank_raw_observations", "candidate_observations"),
        ):
            if deltas[table] != authorized_scope[field]:
                raise RunnerError("FINAL_BATCH_COUNT_MISMATCH")
        if any(deltas[table] != 0 for table in (
            "cbr_bank_subject_legal_issuer_evidence", "cbr_bank_subject_legal_issuer_profiles"
        )):
            raise RunnerError("IDENTITY_TABLE_MUTATION_DETECTED")
        report.update(status="complete", error_code=None, after_task255_counts=after,
                      task255_count_deltas=deltas)
    except Exception as exc:
        code = "APPLY_FAILED"
        if isinstance(exc, RunnerError) and exc.code in _APPLY_ERROR_CODES:
            code = exc.code
        elif isinstance(exc, CbrSourceError):
            code = exc.code.value
        elif getattr(getattr(exc, "orig", None), "sqlstate", None) == "55P03" or getattr(getattr(exc, "orig", None), "pgcode", None) == "55P03":
            code = "DATABASE_LOCK_TIMEOUT"
        report.update(status="failed", error_code=code, failed_report_date=current_date)
    committed = bool(report["committed_report_dates"])
    failed = report["status"] == "failed"
    report.update(
        commit_outcome_unknown=uncertain,
        production_actions=(
            PRODUCTION_ACTION_HISTORICAL_APPLY_OUTCOME_UNKNOWN if uncertain
            else PRODUCTION_ACTION_HISTORICAL_APPLY if committed
            else PRODUCTION_ACTION_NONE
        ),
        database_mutation_executed=True if committed else (None if uncertain else False),
        database_persistence=True if committed else (None if uncertain else False),
        partial_batch_committed=failed and committed,
        reconciliation_required=failed and (committed or uncertain or final_verification_started),
    )
    return report


def execute_preflight(
    engine: Engine,
    *,
    prepared: PreparedHistoricalBatch,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql: bool = False,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
) -> dict[str, Any]:
    connection = transaction = session = None
    rolled_back = False
    try:
        connection = engine.connect()
        if connection.dialect.name == "postgresql":
            connection = connection.execution_options(isolation_level="REPEATABLE READ")
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        read_only_enforcer(session)
        state = schema_reader(session)
        _validate_schema_state(state)
        decisions = tuple(
            classify_backfill_month(session, month)
            for month in prepared.manifest.months
        )
        unknown = [
            item for item in decisions if item.backfill_action not in _READY_ACTIONS | _BLOCK_ACTIONS
        ]
        if unknown:
            raise RunnerError("UNKNOWN_DATABASE_STATE")
        counts = _task255_counts(session)
        scope = build_apply_scope(prepared.manifest, decisions, counts)
        transaction.rollback()
        rolled_back = True
        blocked = [item for item in decisions if item.backfill_action in _BLOCK_ACTIONS]
        candidates = [
            month
            for month, decision in zip(prepared.manifest.months, decisions, strict=True)
            if decision.backfill_action == "INSERT_CANDIDATE"
        ]
        report = dict(prepared.report)
        report.update(
            status="blocked" if blocked else "ready",
            mode="preflight",
            months=[item.to_dict() for item in decisions],
            insert_candidate_months=sum(
                item.backfill_action == "INSERT_CANDIDATE" for item in decisions
            ),
            skip_exact_months=sum(
                item.backfill_action == "SKIP_EXACT_MONTH" for item in decisions
            ),
            skip_exact_source_months=sum(
                item.backfill_action == "SKIP_EXACT_SOURCE" for item in decisions
            ),
            blocked_months=len(blocked),
            candidate_artifacts=sum(
                len(item.monthly_manifest.artifacts) for item in candidates
            ),
            candidate_snapshots=sum(
                len(item.monthly_manifest.artifacts) for item in candidates
            ),
            candidate_observations=sum(
                artifact.record_count
                for item in candidates
                for artifact in item.monthly_manifest.artifacts
            ),
            current_task255_counts=counts,
            apply_scope_sha256=sha256_canonical(scope),
            transaction_read_only=True,
            database_read_only=True,
            transaction_rolled_back=True,
            database_accessed=True,
            database_mutation_executed=False,
            network_accessed=False,
        )
        return report
    finally:
        if transaction is not None and not rolled_back and transaction.is_active:
            transaction.rollback()
        if session is not None:
            session.close()
        if connection is not None:
            connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="cbr-bank-historical-backfill-runner")
    parser.add_argument("--mode", choices=("discover", "plan", "preflight", "apply"), required=True)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--evidence-observed-at")
    parser.add_argument("--batch-manifest-output")
    parser.add_argument("--batch-manifest")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--database-url-env")
    parser.add_argument("--confirm-read-only", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expected-batch-manifest-sha256")
    parser.add_argument("--expected-apply-scope-sha256")
    return parser


def _validate_args(args: argparse.Namespace) -> tuple[date | None, date | None]:
    if not args.artifact_dir:
        raise RunnerError("INVALID_ARGUMENTS")
    if args.mode != "apply" and (
        args.confirm_write or args.expected_batch_manifest_sha256 or args.expected_apply_scope_sha256
    ):
        raise RunnerError("INVALID_ARGUMENTS")
    if args.mode == "discover":
        if (
            not args.from_date
            or not args.to_date
            or not args.evidence_observed_at
            or not args.batch_manifest_output
            or args.batch_manifest
            or args.database_url_env
            or args.confirm_read_only
        ):
            raise RunnerError("INVALID_ARGUMENTS")
        from_date = _parse_date(args.from_date)
        to_date = _parse_date(args.to_date)
        _parse_utc(args.evidence_observed_at)
        if from_date < HISTORICAL_BACKFILL_MIN_REPORT_DATE or to_date < from_date:
            raise RunnerError("INVALID_ARGUMENTS")
        return from_date, to_date
    if (
        not args.batch_manifest
        or args.from_date
        or args.to_date
        or args.evidence_observed_at
        or args.batch_manifest_output
    ):
        raise RunnerError("INVALID_ARGUMENTS")
    if args.mode == "plan" and (args.database_url_env or args.confirm_read_only):
        raise RunnerError("INVALID_ARGUMENTS")
    if args.mode == "preflight" and (
        not args.database_url_env
        or _ENV_NAME.fullmatch(args.database_url_env) is None
        or not args.confirm_read_only
    ):
        raise RunnerError("INVALID_ARGUMENTS")
    if args.mode == "apply" and (
        not args.database_url_env or _ENV_NAME.fullmatch(args.database_url_env) is None
        or not args.confirm_write or args.confirm_read_only
        or not _valid_digest(args.expected_batch_manifest_sha256)
        or not _valid_digest(args.expected_apply_scope_sha256)
    ):
        raise RunnerError("INVALID_ARGUMENTS")
    return None, None


def _failure(code: str, *, mode: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "error_code": code,
        "database_mutation_executed": False,
        "database_persistence": False,
        "publication_status": PUBLICATION_STATUS,
        "publication_at": None,
        "historical_availability_proven": False,
        "pit_ready": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
    }
    if mode == "apply":
        result.update(_apply_progress())
    if mode in {"discover", "plan", "preflight", "apply"}:
        result["mode"] = mode
    return result


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[..., Engine] | None = None,
    client_factory: Callable[..., CbrBankRegulatoryClient] | None = None,
    archive_executable: str | None = None,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql_test_adapter: bool = False,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
    clock: Callable[[], datetime] | None = None,
) -> int:
    # Preserve the APPLY failure shape even when argparse rejects an unknown flag.
    raw_args = list(sys.argv[1:] if argv is None else argv)
    mode_hint = None
    for index, value in enumerate(raw_args):
        candidate = (
            raw_args[index + 1] if value == "--mode" and index + 1 < len(raw_args)
            else value.removeprefix("--mode=") if value.startswith("--mode=") else None
        )
        if candidate in {"discover", "plan", "preflight", "apply"}:
            mode_hint = candidate
    try:
        args = _parser().parse_args(raw_args)
        from_date, to_date = _validate_args(args)
    except (RunnerError, SystemExit):
        _emit(_failure("INVALID_ARGUMENTS", mode=mode_hint))
        return 2
    try:
        if args.mode == "discover":
            observed = _parse_utc(args.evidence_observed_at)
            source = (
                client_factory(now=lambda: observed)
                if client_factory is not None
                else CbrBankRegulatoryClient(now=lambda: observed)
            )
            prepared = discover_batch(
                requested_from_date=from_date,
                requested_to_date=to_date,
                evidence_observed_at=observed,
                artifact_dir=Path(args.artifact_dir),
                batch_manifest_output=Path(args.batch_manifest_output),
                client=source,
                archive_executable=archive_executable,
            )
            _emit(prepared.report)
            return 0
        manifest = _read_batch_manifest(Path(args.batch_manifest))
        if args.mode == "apply" and not hmac.compare_digest(
            manifest.batch_manifest_sha256, args.expected_batch_manifest_sha256
        ):
            raise RunnerError("BATCH_MANIFEST_HASH_MISMATCH")
        prepared = prepare_batch_from_manifest(
            manifest=manifest,
            artifact_dir=Path(args.artifact_dir),
            archive_executable=archive_executable,
        )
        if args.mode == "plan":
            _emit(prepared.report)
            return 0
        environment = os.environ if environ is None else environ
        database_url = _database_url(args.database_url_env, environment)
        engine: Engine | None = None
        try:
            engine = (engine_factory or create_engine)(database_url, pool_pre_ping=True)
            if args.mode == "apply":
                report = execute_apply(
                    engine, prepared=prepared, artifact_dir=Path(args.artifact_dir),
                    expected_batch_manifest_sha256=args.expected_batch_manifest_sha256,
                    expected_apply_scope_sha256=args.expected_apply_scope_sha256,
                    ingested_at=(clock or (lambda: datetime.now(timezone.utc)))(),
                    schema_reader=schema_reader,
                    allow_non_postgresql=allow_non_postgresql_test_adapter,
                    read_only_enforcer=read_only_enforcer,
                    archive_executable=archive_executable,
                )
            else:
                report = execute_preflight(
                    engine,
                    prepared=prepared,
                    schema_reader=schema_reader,
                    allow_non_postgresql=allow_non_postgresql_test_adapter,
                    read_only_enforcer=read_only_enforcer,
                )
            _emit(report)
            return 0 if report["status"] in {"ready", "complete"} else 1
        finally:
            if engine is not None:
                try:
                    engine.dispose()
                except Exception:
                    pass
    except RunnerError as exc:
        _emit(_failure(exc.code, mode=args.mode))
        return 1
    except CbrSourceError as exc:
        _emit(_failure(exc.code.value, mode=args.mode))
        return 1
    except Exception:
        _emit(_failure(f"{args.mode.upper()}_FAILED", mode=args.mode))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
