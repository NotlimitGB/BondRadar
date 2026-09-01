from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin, urlparse

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence.contracts import ExactFormEvidence
from app.services.cbr_bank_financial_evidence.fingerprints import (
    ordered_fingerprints_sha256,
    sha256_canonical,
    utc_datetime,
)
from app.services.cbr_bank_financial_evidence.lexical import (
    extract_exact_form_evidence,
)
from app.services.cbr_bank_financial_evidence.production_runner import (
    CommitOutcomeUnknown,
    RunnerError,
    _ArgumentParser,
    _DatabaseState,
    _assert_count_delta,
    _commit_transaction,
    _database_url,
    _enforce_postgresql,
    _enforce_read_only,
    _read_postgresql_schema_state,
    _task255_counts,
    _validate_schema_state,
)
from app.services.cbr_bank_financial_evidence.store import (
    CbrBankRawFinancialEvidenceStore,
)
from app.services.cbr_bank_reporting.bundle import CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.client import (
    ALLOWED_HOSTS,
    ARTIFACT_RE,
    CbrBankRegulatoryClient,
)
from app.services.cbr_bank_reporting.contracts import (
    SOURCE_PAGE,
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrBankRegulatoryBundleSnapshot,
    CbrSourceError,
)


SCHEMA_VERSION = "bondradar.cbr_bank_monthly_ingestion_runner.v1"
MANIFEST_SCHEMA_VERSION = "bondradar.cbr_bank_monthly_ingestion_manifest.v1"
MANIFEST_CONTRACT_VERSION = "cbr-bank-monthly-ingestion-manifest-v1"
EXPECTED_ALEMBIC_REVISION = "202609010001"
PUBLICATION_STATUS = "UNKNOWN"
REQUIRED_FORMS = tuple(CbrBankForm)

_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _strict_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise RunnerError("INVALID_ARGUMENTS")
    if value.utcoffset() != timezone.utc.utcoffset(value):
        raise RunnerError("INVALID_ARGUMENTS")
    try:
        return value.astimezone(timezone.utc)
    except (OverflowError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc


def _parse_utc_text(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc
    return _strict_utc(parsed, field_name="evidence_observed_at")


def _parse_report_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc


def _validate_source_reference(
    *, form: str, report_date: date, source_href: str, source_url: str, filename: str
) -> None:
    parsed = urlparse(source_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.query
        or parsed.fragment
    ):
        raise RunnerError("MANIFEST_INVALID")
    match = ARTIFACT_RE.fullmatch(PurePosixPath(parsed.path).name)
    if match is None or filename != PurePosixPath(parsed.path).name:
        raise RunnerError("MANIFEST_INVALID")
    try:
        filename_form = CbrBankForm.parse(match.group(1)).value
        filename_date = datetime.strptime(match.group(2), "%Y%m%d").date()
    except (ValueError, TypeError) as exc:
        raise RunnerError("MANIFEST_INVALID") from exc
    if filename_form != form or filename_date != report_date:
        raise RunnerError("MANIFEST_INVALID")
    if urljoin(SOURCE_PAGE, source_href) != source_url:
        raise RunnerError("MANIFEST_INVALID")


@dataclass(frozen=True, slots=True)
class MonthlyArtifactManifest:
    form: str
    source_href: str
    source_url: str
    artifact_filename: str
    artifact_size: int
    artifact_sha256: str
    content_type: str
    record_count: int
    subject_count: int
    subject_set_sha256: str
    form_schema_fingerprint: str
    value_member_name: str
    source_row_fingerprint_set_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "form": self.form,
            "source_href": self.source_href,
            "source_url": self.source_url,
            "artifact_filename": self.artifact_filename,
            "artifact_size": self.artifact_size,
            "artifact_sha256": self.artifact_sha256,
            "content_type": self.content_type,
            "record_count": self.record_count,
            "subject_count": self.subject_count,
            "subject_set_sha256": self.subject_set_sha256,
            "form_schema_fingerprint": self.form_schema_fingerprint,
            "value_member_name": self.value_member_name,
            "source_row_fingerprint_set_sha256": (
                self.source_row_fingerprint_set_sha256
            ),
        }

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, report_date: date
    ) -> "MonthlyArtifactManifest":
        expected = set(cls.__dataclass_fields__)
        if set(payload) != expected:
            raise RunnerError("MANIFEST_INVALID")
        try:
            item = cls(**payload)
        except TypeError as exc:
            raise RunnerError("MANIFEST_INVALID") from exc
        if (
            item.form not in {value.value for value in REQUIRED_FORMS}
            or not isinstance(item.artifact_size, int)
            or item.artifact_size <= 0
            or not isinstance(item.record_count, int)
            or item.record_count < 0
            or not isinstance(item.subject_count, int)
            or item.subject_count < 0
            or not item.content_type
        ):
            raise RunnerError("MANIFEST_INVALID")
        for digest in (
            item.artifact_sha256,
            item.subject_set_sha256,
            item.form_schema_fingerprint,
            item.source_row_fingerprint_set_sha256,
        ):
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise RunnerError("MANIFEST_INVALID")
        _validate_source_reference(
            form=item.form,
            report_date=report_date,
            source_href=item.source_href,
            source_url=item.source_url,
            filename=item.artifact_filename,
        )
        return item


@dataclass(frozen=True, slots=True)
class MonthlyIngestionManifest:
    report_date: date
    evidence_observed_at: datetime
    artifacts: tuple[MonthlyArtifactManifest, ...]
    ingestion_manifest_sha256: str
    publication_status: str = PUBLICATION_STATUS
    publication_at: None = None

    def body(self) -> dict[str, Any]:
        return {
            "schema": MANIFEST_SCHEMA_VERSION,
            "contract_version": MANIFEST_CONTRACT_VERSION,
            "task255_contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
            "report_date": self.report_date.isoformat(),
            "evidence_observed_at": _iso(self.evidence_observed_at),
            "publication_status": self.publication_status,
            "publication_at": self.publication_at,
            "forms": [item.to_dict() for item in self.artifacts],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.body(), "ingestion_manifest_sha256": self.ingestion_manifest_sha256}

    @classmethod
    def create(
        cls,
        *,
        report_date: date,
        evidence_observed_at: datetime,
        artifacts: tuple[MonthlyArtifactManifest, ...],
    ) -> "MonthlyIngestionManifest":
        observed = _strict_utc(
            evidence_observed_at, field_name="evidence_observed_at"
        )
        ordered = tuple(sorted(artifacts, key=lambda item: item.form))
        if tuple(item.form for item in ordered) != tuple(
            item.value for item in REQUIRED_FORMS
        ):
            raise RunnerError("FOUR_FORM_BUNDLE_REQUIRED")
        draft = cls(
            report_date=report_date,
            evidence_observed_at=observed,
            artifacts=ordered,
            ingestion_manifest_sha256="",
        )
        return cls(
            report_date=report_date,
            evidence_observed_at=observed,
            artifacts=ordered,
            ingestion_manifest_sha256=sha256_canonical(draft.body()),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "MonthlyIngestionManifest":
        expected = {
            "schema",
            "contract_version",
            "task255_contract_version",
            "report_date",
            "evidence_observed_at",
            "publication_status",
            "publication_at",
            "forms",
            "ingestion_manifest_sha256",
        }
        if set(payload) != expected:
            raise RunnerError("MANIFEST_INVALID")
        if (
            payload["schema"] != MANIFEST_SCHEMA_VERSION
            or payload["contract_version"] != MANIFEST_CONTRACT_VERSION
            or payload["task255_contract_version"]
            != CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION
            or payload["publication_status"] != PUBLICATION_STATUS
            or payload["publication_at"] is not None
            or not isinstance(payload["forms"], list)
            or not isinstance(payload["ingestion_manifest_sha256"], str)
            or _SHA256.fullmatch(payload["ingestion_manifest_sha256"]) is None
        ):
            raise RunnerError("MANIFEST_INVALID")
        if any(not isinstance(item, dict) for item in payload["forms"]):
            raise RunnerError("MANIFEST_INVALID")
        report_date = _parse_report_date(payload["report_date"])
        observed = _parse_utc_text(payload["evidence_observed_at"])
        artifacts = tuple(
            MonthlyArtifactManifest.from_dict(item, report_date=report_date)
            for item in payload["forms"]
        )
        manifest = cls.create(
            report_date=report_date,
            evidence_observed_at=observed,
            artifacts=artifacts,
        )
        if not hmac.compare_digest(
            manifest.ingestion_manifest_sha256,
            payload["ingestion_manifest_sha256"],
        ):
            raise RunnerError("MANIFEST_HASH_MISMATCH")
        return manifest


@dataclass(frozen=True, slots=True)
class PreparedMonthlyEvidence:
    manifest: MonthlyIngestionManifest
    bundle: CbrBankRegulatoryBundleSnapshot
    exact_forms: tuple[ExactFormEvidence, ...]
    artifacts: tuple[CbrBankArtifact, ...]
    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class MonthlyDatabaseInspection:
    state: str
    source_observation: str
    matching_artifacts: int
    matching_snapshots: int
    matching_observations: int


def _artifact_manifest_projection(
    *,
    bundle: CbrBankRegulatoryBundleSnapshot,
    exact_forms: tuple[ExactFormEvidence, ...],
) -> tuple[MonthlyArtifactManifest, ...]:
    exact_by_form = {item.form: item for item in exact_forms}
    records_by_form = dict(bundle.records_by_form)
    subjects_by_form = dict(bundle.subjects_by_form)
    subject_hashes = dict(bundle.subject_set_hashes)
    projection = []
    for result in sorted(bundle.forms, key=lambda item: item.form.value):
        artifact = result.artifact
        exact = exact_by_form[result.form.value]
        projection.append(
            MonthlyArtifactManifest(
                form=result.form.value,
                source_href=artifact.reference.source_href,
                source_url=artifact.reference.source_url,
                artifact_filename=artifact.reference.artifact_filename,
                artifact_size=artifact.compressed_size,
                artifact_sha256=artifact.content_sha256,
                content_type=artifact.content_type or "application/octet-stream",
                record_count=records_by_form[result.form.value],
                subject_count=subjects_by_form[result.form.value],
                subject_set_sha256=subject_hashes[result.form.value],
                form_schema_fingerprint=result.form_schema_fingerprint,
                value_member_name=exact.value_member_name,
                source_row_fingerprint_set_sha256=ordered_fingerprints_sha256(
                    [item.source_row_fingerprint for item in exact.observations]
                ),
            )
        )
    return tuple(projection)


def _prepare_artifacts(
    *,
    report_date: date,
    evidence_observed_at: datetime,
    artifacts: tuple[CbrBankArtifact, ...],
    archive_executable: str | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
) -> PreparedMonthlyEvidence:
    if tuple(sorted((item.reference.form for item in artifacts), key=lambda item: item.value)) != REQUIRED_FORMS:
        raise RunnerError("FOUR_FORM_BUNDLE_REQUIRED")
    service = bundle_service or CbrBankRegulatoryBundleService(
        archive_executable=archive_executable
    )
    try:
        bundle = service.build_snapshot(report_date=report_date, artifacts=artifacts)
        exact_forms = tuple(
            extract_exact_form_evidence(item, archive_executable=archive_executable)
            for item in sorted(bundle.forms, key=lambda result: result.form.value)
        )
    except CbrSourceError as exc:
        raise RunnerError(exc.code.value) from exc
    except RunnerError:
        raise
    except Exception as exc:
        raise RunnerError("MONTHLY_SOURCE_VALIDATION_FAILED") from exc
    projection = _artifact_manifest_projection(bundle=bundle, exact_forms=exact_forms)
    manifest = MonthlyIngestionManifest.create(
        report_date=report_date,
        evidence_observed_at=evidence_observed_at,
        artifacts=projection,
    )
    subject_union = {regn for result in bundle.forms for regn in result.subjects}
    report = {
        "schema": SCHEMA_VERSION,
        "status": "ready",
        "mode": "plan",
        "report_date": report_date.isoformat(),
        "evidence_observed_at": _iso(evidence_observed_at),
        "ingestion_manifest_sha256": manifest.ingestion_manifest_sha256,
        "publication_status": PUBLICATION_STATUS,
        "publication_at": None,
        "subjects": len(subject_union),
        "artifacts": len(artifacts),
        "snapshots": len(exact_forms),
        "observations": sum(len(item.observations) for item in exact_forms),
        "records_by_form": dict(bundle.records_by_form),
        "subjects_by_form": dict(bundle.subjects_by_form),
        "artifact_sha256_by_form": {
            item.form: item.artifact_sha256 for item in projection
        },
        "subject_set_sha256_by_form": dict(bundle.subject_set_hashes),
        "schema_fingerprint_by_form": {
            item.form: item.form_schema_fingerprint for item in projection
        },
        "raw_lexical_mismatch_count": 0,
        "database_accessed": False,
        "network_accessed": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
    }
    return PreparedMonthlyEvidence(
        manifest=manifest,
        bundle=bundle,
        exact_forms=exact_forms,
        artifacts=artifacts,
        report=report,
    )


def discover_month(
    *,
    report_date: date,
    evidence_observed_at: datetime,
    client: CbrBankRegulatoryClient | None = None,
    archive_executable: str | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
) -> PreparedMonthlyEvidence:
    observed = _strict_utc(evidence_observed_at, field_name="evidence_observed_at")
    source = client or CbrBankRegulatoryClient(now=lambda: observed)
    try:
        references = source.discover_requested(
            forms=REQUIRED_FORMS, report_date=report_date
        )
        if tuple(item.form for item in references) != REQUIRED_FORMS:
            raise RunnerError("FOUR_FORM_BUNDLE_REQUIRED")
        fetched = tuple(source.fetch_discovered_artifact(item) for item in references)
    except CbrSourceError as exc:
        raise RunnerError(exc.code.value) from exc
    normalized = tuple(
        CbrBankArtifact(
            reference=CbrArtifactReference(
                form=item.reference.form,
                source_href=item.reference.source_href,
                source_url=item.reference.source_url,
                artifact_filename=item.reference.artifact_filename,
                report_date=item.reference.report_date,
                discovered_at=observed,
            ),
            content=item.content,
            content_sha256=item.content_sha256,
            compressed_size=item.compressed_size,
            content_type=item.content_type or "application/octet-stream",
            retrieved_at=observed,
        )
        for item in fetched
    )
    prepared = _prepare_artifacts(
        report_date=report_date,
        evidence_observed_at=observed,
        artifacts=normalized,
        archive_executable=archive_executable,
        bundle_service=bundle_service,
    )
    prepared.report.update(mode="discover", network_accessed=True)
    return prepared


def _read_manifest(path: Path) -> MonthlyIngestionManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RunnerError("MANIFEST_UNAVAILABLE") from exc
    if not isinstance(payload, dict):
        raise RunnerError("MANIFEST_INVALID")
    return MonthlyIngestionManifest.from_dict(payload)


def _manifest_bytes(manifest: MonthlyIngestionManifest) -> bytes:
    return (
        json.dumps(
            manifest.to_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        + "\n"
    ).encode("ascii")


def _write_frozen(path: Path, content: bytes, *, conflict_code: str) -> None:
    if not path.parent.is_dir():
        raise RunnerError("OUTPUT_DIRECTORY_UNAVAILABLE")
    if path.exists():
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise RunnerError("OUTPUT_UNAVAILABLE") from exc
        if existing != content:
            raise RunnerError(conflict_code)
        return
    try:
        with path.open("xb") as handle:
            handle.write(content)
    except FileExistsError:
        _write_frozen(path, content, conflict_code=conflict_code)
    except OSError as exc:
        raise RunnerError("OUTPUT_UNAVAILABLE") from exc


def freeze_discovery(
    prepared: PreparedMonthlyEvidence, *, artifact_dir: Path, manifest_output: Path
) -> None:
    if not artifact_dir.is_dir():
        raise RunnerError("OUTPUT_DIRECTORY_UNAVAILABLE")
    for artifact in prepared.artifacts:
        _write_frozen(
            artifact_dir / artifact.reference.artifact_filename,
            artifact.content,
            conflict_code="ARTIFACT_CACHE_CONFLICT",
        )
    _write_frozen(
        manifest_output,
        _manifest_bytes(prepared.manifest),
        conflict_code="MANIFEST_OUTPUT_CONFLICT",
    )


def prepare_from_manifest(
    *,
    manifest: MonthlyIngestionManifest,
    artifact_dir: Path,
    archive_executable: str | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
) -> PreparedMonthlyEvidence:
    if not artifact_dir.is_dir():
        raise RunnerError("ARTIFACT_CACHE_UNAVAILABLE")
    artifacts = []
    for item in manifest.artifacts:
        try:
            content = (artifact_dir / item.artifact_filename).read_bytes()
        except OSError as exc:
            raise RunnerError("ARTIFACT_CACHE_UNAVAILABLE") from exc
        digest = hashlib.sha256(content).hexdigest()
        if len(content) != item.artifact_size or digest != item.artifact_sha256:
            raise RunnerError("ARTIFACT_IDENTITY_MISMATCH")
        form = CbrBankForm.parse(item.form)
        artifacts.append(
            CbrBankArtifact(
                reference=CbrArtifactReference(
                    form=form,
                    source_href=item.source_href,
                    source_url=item.source_url,
                    artifact_filename=item.artifact_filename,
                    report_date=manifest.report_date,
                    discovered_at=manifest.evidence_observed_at,
                ),
                content=content,
                content_sha256=digest,
                compressed_size=len(content),
                content_type=item.content_type,
                retrieved_at=manifest.evidence_observed_at,
            )
        )
    prepared = _prepare_artifacts(
        report_date=manifest.report_date,
        evidence_observed_at=manifest.evidence_observed_at,
        artifacts=tuple(artifacts),
        archive_executable=archive_executable,
        bundle_service=bundle_service,
    )
    if prepared.manifest.to_dict() != manifest.to_dict():
        raise RunnerError("MANIFEST_PLAN_MISMATCH")
    return prepared


def _classify_month_state(
    session: Session,
    manifest: MonthlyIngestionManifest,
    *,
    verify_observation_checksums: bool = False,
) -> MonthlyDatabaseInspection:
    forms = tuple(item.form for item in manifest.artifacts)
    expected = {item.form: item for item in manifest.artifacts}
    artifact_rows = list(
        session.execute(
            select(CbrBankSourceArtifact).where(
                CbrBankSourceArtifact.report_date == manifest.report_date,
                CbrBankSourceArtifact.form.in_(forms),
            )
        ).scalars()
    )
    if any(
        row.content_sha256 != expected[row.form].artifact_sha256
        for row in artifact_rows
        if row.form in expected
    ):
        return MonthlyDatabaseInspection(
            "CONFLICTING_ARTIFACT", "CHANGED_SOURCE_BYTES", 0, 0, 0
        )
    exact_artifacts = {
        row.form: row
        for row in artifact_rows
        if row.content_sha256 == expected[row.form].artifact_sha256
    }
    if not artifact_rows:
        return MonthlyDatabaseInspection("EMPTY", "FIRST_OBSERVATION", 0, 0, 0)
    if len(exact_artifacts) != len(expected):
        return MonthlyDatabaseInspection(
            "PARTIAL_STATE", "FIRST_OBSERVATION", len(exact_artifacts), 0, 0
        )
    snapshot_rows = list(
        session.execute(
            select(CbrBankReportSnapshot).where(
                CbrBankReportSnapshot.report_date == manifest.report_date,
                CbrBankReportSnapshot.artifact_id.in_(
                    tuple(row.id for row in exact_artifacts.values())
                ),
            )
        ).scalars()
    )
    target = []
    for row in snapshot_rows:
        item = expected[row.form]
        if utc_datetime(row.observed_at, field_name="observed_at") != manifest.evidence_observed_at:
            continue
        if (
            row.publication_status != PUBLICATION_STATUS
            or row.publication_at is not None
            or row.record_count != item.record_count
            or row.subject_count != item.subject_count
            or row.subject_set_sha256 != item.subject_set_sha256
            or row.form_schema_fingerprint != item.form_schema_fingerprint
            or row.value_member_name != item.value_member_name
        ):
            return MonthlyDatabaseInspection(
                "PARTIAL_STATE",
                "EXACT_REOBSERVATION",
                len(exact_artifacts),
                len(target),
                0,
            )
        target.append(row)
    if not target:
        return MonthlyDatabaseInspection(
            "EMPTY", "EXACT_REOBSERVATION", len(exact_artifacts), 0, 0
        )
    if len(target) != len(expected) or len({row.form for row in target}) != len(expected):
        return MonthlyDatabaseInspection(
            "PARTIAL_STATE",
            "EXACT_REOBSERVATION",
            len(exact_artifacts),
            len(target),
            0,
        )
    observation_total = 0
    for snapshot in target:
        if not verify_observation_checksums:
            row_count = int(
                session.scalar(
                    select(func.count()).select_from(CbrBankRawObservation).where(
                        CbrBankRawObservation.snapshot_id == snapshot.id
                    )
                )
                or 0
            )
            if row_count != snapshot.record_count:
                return MonthlyDatabaseInspection(
                    "PARTIAL_STATE",
                    "EXACT_REOBSERVATION",
                    len(exact_artifacts),
                    len(target),
                    observation_total + row_count,
                )
            observation_total += row_count
            continue
        rows = list(
            session.execute(
                select(
                    CbrBankRawObservation.source_row_number,
                    CbrBankRawObservation.observation_fingerprint,
                )
                .where(CbrBankRawObservation.snapshot_id == snapshot.id)
                .order_by(CbrBankRawObservation.source_row_number)
            )
        )
        if len(rows) != snapshot.record_count:
            return MonthlyDatabaseInspection(
                "PARTIAL_STATE",
                "EXACT_REOBSERVATION",
                len(exact_artifacts),
                len(target),
                observation_total + len(rows),
            )
        if ordered_fingerprints_sha256(
            [str(row.observation_fingerprint) for row in rows]
        ) != snapshot.observation_set_sha256:
            return MonthlyDatabaseInspection(
                "PARTIAL_STATE",
                "EXACT_REOBSERVATION",
                len(exact_artifacts),
                len(target),
                observation_total + len(rows),
            )
        observation_total += len(rows)
    return MonthlyDatabaseInspection(
        "EXACT_ALREADY_PRESENT",
        "EXACT_REOBSERVATION",
        len(exact_artifacts),
        len(target),
        observation_total,
    )


def execute_preflight(
    engine: Engine,
    *,
    manifest: MonthlyIngestionManifest,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql: bool = False,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
) -> dict[str, Any]:
    connection = transaction = session = None
    rolled_back = False
    try:
        connection = engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        read_only_enforcer(session)
        schema_state = schema_reader(session)
        _validate_schema_state(schema_state)
        inspection = _classify_month_state(session, manifest)
        counts = _task255_counts(session)
        transaction.rollback()
        rolled_back = True
        return {
            "schema": SCHEMA_VERSION,
            "status": (
                "blocked"
                if inspection.state in {"PARTIAL_STATE", "CONFLICTING_ARTIFACT"}
                else "ready"
            ),
            "mode": "preflight",
            "report_date": manifest.report_date.isoformat(),
            "ingestion_manifest_sha256": manifest.ingestion_manifest_sha256,
            "monthly_state": inspection.state,
            "source_observation": inspection.source_observation,
            "current_task255_counts": counts,
            "transaction_read_only": True,
            "database_read_only": True,
            "transaction_rolled_back": True,
            "database_accessed": True,
            "database_mutation_executed": False,
            "network_accessed": False,
            "production_actions": "NONE",
        }
    finally:
        if transaction is not None and not rolled_back and transaction.is_active:
            transaction.rollback()
        if session is not None:
            session.close()
        if connection is not None:
            connection.close()


def _validate_apply_readback(
    session: Session,
    *,
    prepared: PreparedMonthlyEvidence,
    before_counts: dict[str, int],
    result: Any,
) -> tuple[dict[str, int], MonthlyDatabaseInspection]:
    after_counts = _task255_counts(session)
    _assert_count_delta(before_counts, after_counts, result)
    if (
        after_counts["cbr_bank_subject_legal_issuer_evidence"]
        != before_counts["cbr_bank_subject_legal_issuer_evidence"]
        or after_counts["cbr_bank_subject_legal_issuer_profiles"]
        != before_counts["cbr_bank_subject_legal_issuer_profiles"]
    ):
        raise RunnerError("IDENTITY_TABLE_MUTATION_DETECTED")
    inspection = _classify_month_state(
        session, prepared.manifest, verify_observation_checksums=True
    )
    if inspection.state != "EXACT_ALREADY_PRESENT":
        raise RunnerError("POST_WRITE_READBACK_MISMATCH")
    return after_counts, inspection


def execute_apply(
    engine: Engine,
    *,
    prepared: PreparedMonthlyEvidence,
    ingested_at: datetime,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql: bool = False,
    store_factory: Callable[..., CbrBankRawFinancialEvidenceStore] = CbrBankRawFinancialEvidenceStore,
    readback_validator: Callable[..., tuple[dict[str, int], MonthlyDatabaseInspection]] = _validate_apply_readback,
    archive_executable: str | None = None,
) -> dict[str, Any]:
    connection = transaction = session = None
    commit_attempted = committed = False
    try:
        connection = engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        schema_state = schema_reader(session)
        _validate_schema_state(schema_state)
        inspection = _classify_month_state(session, prepared.manifest)
        if inspection.state == "CONFLICTING_ARTIFACT":
            raise RunnerError("CONFLICTING_ARTIFACT")
        if inspection.state == "PARTIAL_STATE":
            raise RunnerError("PARTIAL_STATE")
        before_counts = _task255_counts(session)
        result = store_factory(
            session, archive_executable=archive_executable
        ).persist_bundle(
            prepared.bundle,
            observed_at=prepared.manifest.evidence_observed_at,
            ingested_at=utc_datetime(ingested_at, field_name="ingested_at"),
            publication_status=PUBLICATION_STATUS,
            publication_at=None,
            identity_snapshot=None,
        )
        after_counts, readback = readback_validator(
            session,
            prepared=prepared,
            before_counts=before_counts,
            result=result,
        )
        commit_attempted = True
        try:
            _commit_transaction(transaction)
        except Exception as exc:
            raise CommitOutcomeUnknown() from exc
        committed = True
        mutation_count = (
            result.subjects.inserted
            + result.subjects.updated
            + result.artifacts.inserted
            + result.snapshots.inserted
            + result.observations.inserted
        )
        return {
            "schema": SCHEMA_VERSION,
            "status": "complete",
            "mode": "apply",
            "report_date": prepared.manifest.report_date.isoformat(),
            "ingestion_manifest_sha256": prepared.manifest.ingestion_manifest_sha256,
            "monthly_state_before": inspection.state,
            "monthly_state_after": readback.state,
            "source_observation": inspection.source_observation,
            "subjects_inserted": result.subjects.inserted,
            "subjects_reused": result.subjects.reused,
            "subjects_updated": result.subjects.updated,
            "artifacts_inserted": result.artifacts.inserted,
            "artifacts_reused": result.artifacts.reused,
            "snapshots_inserted": result.snapshots.inserted,
            "snapshots_reused": result.snapshots.reused,
            "observations_inserted": result.observations.inserted,
            "observations_reused": result.observations.reused,
            "post_subject_count": after_counts["cbr_bank_reporting_subjects"],
            "post_artifact_count": after_counts["cbr_bank_source_artifacts"],
            "post_snapshot_count": after_counts["cbr_bank_report_snapshots"],
            "post_observation_count": after_counts["cbr_bank_raw_observations"],
            "transaction_committed": True,
            "transaction_rolled_back": False,
            "reconciliation_required": False,
            "database_accessed": True,
            "database_mutation_executed": mutation_count > 0,
            "network_accessed": False,
            "normalization": False,
            "scoring": False,
        }
    except CommitOutcomeUnknown:
        raise
    except Exception:
        if transaction is not None and transaction.is_active:
            transaction.rollback()
        raise
    finally:
        if (
            transaction is not None
            and not committed
            and not commit_attempted
            and transaction.is_active
        ):
            transaction.rollback()
        if session is not None:
            session.close()
        if connection is not None:
            connection.close()


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="cbr-bank-monthly-ingestion-runner")
    parser.add_argument(
        "--mode", choices=("discover", "plan", "preflight", "apply"), required=True
    )
    parser.add_argument("--report-date")
    parser.add_argument("--evidence-observed-at")
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-output")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--database-url-env")
    parser.add_argument("--confirm-read-only", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expected-manifest-sha256")
    return parser


def _validate_args(args: argparse.Namespace) -> date:
    if not args.report_date:
        raise RunnerError("INVALID_ARGUMENTS")
    report_date = _parse_report_date(args.report_date)
    common_db = (args.database_url_env, args.confirm_read_only, args.confirm_write)
    if args.mode == "discover":
        if (
            not args.evidence_observed_at
            or not args.manifest_output
            or not args.artifact_dir
            or args.manifest
            or any(common_db)
            or args.expected_manifest_sha256
        ):
            raise RunnerError("INVALID_ARGUMENTS")
        _parse_utc_text(args.evidence_observed_at)
    elif args.mode == "plan":
        if (
            not args.manifest
            or not args.artifact_dir
            or args.evidence_observed_at
            or args.manifest_output
            or any(common_db)
            or args.expected_manifest_sha256
        ):
            raise RunnerError("INVALID_ARGUMENTS")
    elif args.mode == "preflight":
        if (
            not args.manifest
            or not args.database_url_env
            or not args.confirm_read_only
            or args.evidence_observed_at
            or args.manifest_output
            or args.artifact_dir
            or args.confirm_write
            or args.expected_manifest_sha256
        ):
            raise RunnerError("INVALID_ARGUMENTS")
    else:
        if (
            not args.manifest
            or not args.artifact_dir
            or not args.database_url_env
            or not args.confirm_write
            or args.confirm_read_only
            or args.evidence_observed_at
            or args.manifest_output
            or not args.expected_manifest_sha256
            or _SHA256.fullmatch(args.expected_manifest_sha256) is None
        ):
            raise RunnerError("INVALID_ARGUMENTS")
    if args.database_url_env and _ENV_NAME.fullmatch(args.database_url_env) is None:
        raise RunnerError("INVALID_ARGUMENTS")
    return report_date


def _failure(
    code: str, *, mode: str | None = None, reconciliation_required: bool = False
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "error_code": code,
        "reconciliation_required": reconciliation_required,
    }
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
    clock: Callable[[], datetime] | None = None,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql_test_adapter: bool = False,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
) -> int:
    try:
        args = _parser().parse_args(argv)
        report_date = _validate_args(args)
    except (RunnerError, SystemExit):
        _emit(_failure("INVALID_ARGUMENTS"))
        return 2

    try:
        if args.mode == "discover":
            observed = _parse_utc_text(args.evidence_observed_at)
            source = (
                client_factory(now=lambda: observed)
                if client_factory is not None
                else CbrBankRegulatoryClient(now=lambda: observed)
            )
            prepared = discover_month(
                report_date=report_date,
                evidence_observed_at=observed,
                client=source,
                archive_executable=archive_executable,
            )
            freeze_discovery(
                prepared,
                artifact_dir=Path(args.artifact_dir),
                manifest_output=Path(args.manifest_output),
            )
            discovery_report = prepared.manifest.to_dict()
            discovery_report["manifest_schema"] = discovery_report.pop("schema")
            _emit(
                {
                    **discovery_report,
                    "schema": SCHEMA_VERSION,
                    "mode": "discover",
                    "status": "ready",
                    "database_accessed": False,
                    "network_accessed": True,
                    "production_actions": "NONE",
                }
            )
            return 0

        manifest = _read_manifest(Path(args.manifest))
        if manifest.report_date != report_date:
            raise RunnerError("REPORT_DATE_MISMATCH")
        if args.mode == "plan":
            prepared = prepare_from_manifest(
                manifest=manifest,
                artifact_dir=Path(args.artifact_dir),
                archive_executable=archive_executable,
            )
            _emit(prepared.report)
            return 0

        prepared = None
        if args.mode == "apply":
            if not hmac.compare_digest(
                manifest.ingestion_manifest_sha256,
                args.expected_manifest_sha256,
            ):
                raise RunnerError("MANIFEST_HASH_MISMATCH")
            prepared = prepare_from_manifest(
                manifest=manifest,
                artifact_dir=Path(args.artifact_dir),
                archive_executable=archive_executable,
            )

        environment = os.environ if environ is None else environ
        database_url = _database_url(args.database_url_env, environment)
        engine: Engine | None = None
        try:
            engine = (engine_factory or create_engine)(database_url, pool_pre_ping=True)
            if args.mode == "preflight":
                report = execute_preflight(
                    engine,
                    manifest=manifest,
                    schema_reader=schema_reader,
                    allow_non_postgresql=allow_non_postgresql_test_adapter,
                    read_only_enforcer=read_only_enforcer,
                )
            else:
                report = execute_apply(
                    engine,
                    prepared=prepared,
                    ingested_at=(clock or (lambda: datetime.now(timezone.utc)))(),
                    schema_reader=schema_reader,
                    allow_non_postgresql=allow_non_postgresql_test_adapter,
                    archive_executable=archive_executable,
                )
            _emit(report)
            return 0 if report["status"] != "blocked" else 1
        finally:
            if engine is not None:
                try:
                    engine.dispose()
                except Exception:
                    pass
    except CommitOutcomeUnknown as exc:
        _emit(
            _failure(
                exc.code, mode=args.mode, reconciliation_required=True
            )
        )
        return 1
    except RunnerError as exc:
        _emit(_failure(exc.code, mode=args.mode))
        return 1
    except Exception:
        _emit(_failure(f"{args.mode.upper()}_FAILED", mode=args.mode))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
