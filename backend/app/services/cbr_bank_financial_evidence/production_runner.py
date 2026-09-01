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
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerEvidence,
    CbrBankSubjectLegalIssuerProfile,
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
from app.services.cbr_bank_financial_evidence.store import (
    CbrBankRawFinancialEvidenceStore,
)
from app.services.cbr_bank_reporting.bundle import CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.client import EXPECTED_CURRENT
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrBankRegulatoryBundleSnapshot,
)


SCHEMA_VERSION = "bondradar.cbr_raw_financial_evidence_production_runner.v1"
EXPECTED_ALEMBIC_REVISION = "202609010001"
APPROVED_REPORT_DATE = date(2026, 8, 1)
PUBLICATION_STATUS = "UNKNOWN"
RUNNER_CONTRACT_VERSION = "cbr-controlled-production-ingestion-runner-v1"

_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_FIXTURE_ROOT = (
    Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "cbr_bank_reporting"
)
_TASK255_TABLES = (
    "cbr_bank_reporting_subjects",
    "cbr_bank_source_artifacts",
    "cbr_bank_report_snapshots",
    "cbr_bank_raw_observations",
    "cbr_bank_subject_legal_issuer_evidence",
    "cbr_bank_subject_legal_issuer_profiles",
)
_LEGACY_GUARD_TABLES = ("bonds", "companies", "financial_reports", "legal_issuers")
_TABLE_MODELS = {
    "cbr_bank_reporting_subjects": CbrBankReportingSubject,
    "cbr_bank_source_artifacts": CbrBankSourceArtifact,
    "cbr_bank_report_snapshots": CbrBankReportSnapshot,
    "cbr_bank_raw_observations": CbrBankRawObservation,
    "cbr_bank_subject_legal_issuer_evidence": CbrBankSubjectLegalIssuerEvidence,
    "cbr_bank_subject_legal_issuer_profiles": CbrBankSubjectLegalIssuerProfile,
}
_OFFICIAL_ARTIFACT_URLS = {
    CbrBankForm.FORM_101: "https://www.cbr.ru/vfs/credit/forms/101-20260801.rar",
    CbrBankForm.FORM_102: "https://www.cbr.ru/vfs/credit/forms/102-20260801.rar",
    CbrBankForm.FORM_123: "https://www.cbr.ru/vfs/credit/forms/123-20260801.rar",
    CbrBankForm.FORM_135: "https://www.cbr.ru/vfs/credit/forms/135-20260801.rar",
}


class RunnerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CommitOutcomeUnknown(RunnerError):
    def __init__(self) -> None:
        super().__init__("COMMIT_OUTCOME_UNKNOWN")


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        raise RunnerError("INVALID_ARGUMENTS")


@dataclass(frozen=True, slots=True)
class _PreparedEvidence:
    bundle: CbrBankRegulatoryBundleSnapshot
    exact_forms: tuple[ExactFormEvidence, ...]
    report: dict[str, Any]
    form_projection: tuple[dict[str, Any], ...]
    artifacts: tuple[CbrBankArtifact, ...]


@dataclass(frozen=True, slots=True)
class _DatabaseState:
    revisions: tuple[str, ...]
    tables: frozenset[str]
    counts: dict[str, int]


def _iso(value: datetime) -> str:
    return utc_datetime(value, field_name="timestamp").isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as exc:
        raise RunnerError("INVALID_ARGUMENTS") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise RunnerError("INVALID_ARGUMENTS")
    return parsed.astimezone(timezone.utc)


def _approved_filename(form: CbrBankForm) -> str:
    return f"{form.short_code}-20260801.rar"


def _load_approved_artifacts(
    *,
    report_date: date,
    evidence_observed_at: datetime,
    fixture_root: Path,
) -> tuple[CbrBankArtifact, ...]:
    if report_date != APPROVED_REPORT_DATE:
        raise RunnerError("REPORT_DATE_NOT_APPROVED")
    artifacts: list[CbrBankArtifact] = []
    for form in CbrBankForm:
        expected = EXPECTED_CURRENT.get((form, report_date))
        if expected is None:
            raise RunnerError("FIXTURE_IDENTITY_INVALID")
        filename = _approved_filename(form)
        try:
            content = (fixture_root / filename).read_bytes()
        except OSError as exc:
            raise RunnerError("FIXTURE_UNAVAILABLE") from exc
        expected_size, expected_hash = expected
        content_hash = hashlib.sha256(content).hexdigest()
        if len(content) != expected_size or content_hash != expected_hash:
            raise RunnerError("FIXTURE_IDENTITY_INVALID")
        reference = CbrArtifactReference(
            form=form,
            source_href=f"/vfs/credit/forms/{filename}",
            source_url=_OFFICIAL_ARTIFACT_URLS[form],
            artifact_filename=filename,
            report_date=report_date,
            discovered_at=evidence_observed_at,
        )
        artifacts.append(
            CbrBankArtifact(
                reference=reference,
                content=content,
                content_sha256=content_hash,
                compressed_size=len(content),
                content_type="application/vnd.rar",
                retrieved_at=evidence_observed_at,
            )
        )
    return tuple(artifacts)


def _prepare_evidence(
    *,
    report_date: date,
    evidence_observed_at: datetime,
    fixture_root: Path = _FIXTURE_ROOT,
    archive_executable: str | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
) -> _PreparedEvidence:
    observed = utc_datetime(evidence_observed_at, field_name="evidence_observed_at")
    artifacts = _load_approved_artifacts(
        report_date=report_date,
        evidence_observed_at=observed,
        fixture_root=fixture_root,
    )
    service = bundle_service or CbrBankRegulatoryBundleService(
        archive_executable=archive_executable
    )
    try:
        bundle = service.build_snapshot(report_date=report_date, artifacts=artifacts)
        exact_forms = tuple(
            extract_exact_form_evidence(
                form_result, archive_executable=archive_executable
            )
            for form_result in sorted(bundle.forms, key=lambda item: item.form.value)
        )
    except RunnerError:
        raise
    except Exception as exc:
        raise RunnerError("FIXTURE_VALIDATION_FAILED") from exc

    exact_by_form = {item.form: item for item in exact_forms}
    artifact_by_form = {item.reference.form.value: item for item in artifacts}
    records_by_form = dict(bundle.records_by_form)
    subjects_by_form = dict(bundle.subjects_by_form)
    subject_hashes = dict(bundle.subject_set_hashes)
    form_results = {item.form.value: item for item in bundle.forms}
    form_projection: list[dict[str, Any]] = []
    for form in sorted(form_results):
        result = form_results[form]
        artifact = artifact_by_form[form]
        exact = exact_by_form[form]
        form_projection.append(
            {
                "form": form,
                "artifact_filename": artifact.reference.artifact_filename,
                "artifact_size": artifact.compressed_size,
                "artifact_sha256": artifact.content_sha256,
                "record_count": records_by_form[form],
                "subject_count": subjects_by_form[form],
                "subject_set_sha256": subject_hashes[form],
                "form_schema_fingerprint": result.form_schema_fingerprint,
                "value_member_name": exact.value_member_name,
                "source_row_fingerprint_set_sha256": ordered_fingerprints_sha256(
                    [item.source_row_fingerprint for item in exact.observations]
                ),
            }
        )
    envelope = {
        "runner_contract_version": RUNNER_CONTRACT_VERSION,
        "task255_contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        "report_date": report_date,
        "evidence_observed_at": observed,
        "publication_status": PUBLICATION_STATUS,
        "publication_at": None,
        "forms": form_projection,
    }
    evidence_envelope_sha256 = sha256_canonical(envelope)
    subject_union = {
        regn for form_result in bundle.forms for regn in form_result.subjects
    }
    observation_count = sum(len(item.observations) for item in exact_forms)
    report = {
        "schema": SCHEMA_VERSION,
        "status": "ready",
        "mode": "plan",
        "report_date": report_date.isoformat(),
        "evidence_observed_at": _iso(observed),
        "evidence_envelope_sha256": evidence_envelope_sha256,
        "publication_status": PUBLICATION_STATUS,
        "publication_at": None,
        "subjects": len(subject_union),
        "artifacts": len(artifacts),
        "snapshots": len(exact_forms),
        "observations": observation_count,
        "records_by_form": records_by_form,
        "subjects_by_form": subjects_by_form,
        "artifact_sha256_by_form": {
            item["form"]: item["artifact_sha256"] for item in form_projection
        },
        "subject_set_sha256_by_form": subject_hashes,
        "raw_lexical_mismatch_count": 0,
        "database_accessed": False,
        "network_accessed": False,
        "production_actions": "NONE",
        "normalization": False,
        "scoring": False,
    }
    return _PreparedEvidence(
        bundle=bundle,
        exact_forms=exact_forms,
        report=report,
        form_projection=tuple(form_projection),
        artifacts=artifacts,
    )


def _enforce_postgresql(connection: Any, *, allow_non_postgresql: bool) -> None:
    if not allow_non_postgresql and connection.dialect.name != "postgresql":
        raise RunnerError("POSTGRESQL_REQUIRED")


def _enforce_read_only(session: Session) -> None:
    session.execute(text("SET TRANSACTION READ ONLY"))
    value = session.execute(text("SHOW transaction_read_only")).scalar_one()
    if value != "on":
        raise RunnerError("READ_ONLY_VERIFICATION_FAILED")


def _read_postgresql_schema_state(session: Session) -> _DatabaseState:
    revisions = tuple(
        str(value)
        for value in session.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()
    )
    requested_tables = tuple(sorted({*_TASK255_TABLES, *_LEGACY_GUARD_TABLES}))
    table_statement = text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name IN :table_names "
        "ORDER BY table_name"
    ).bindparams(bindparam("table_names", expanding=True))
    tables = frozenset(
        str(value)
        for value in session.execute(
            table_statement, {"table_names": requested_tables}
        ).scalars()
    )
    return _DatabaseState(revisions=revisions, tables=tables, counts={})


def _validate_schema_state(state: _DatabaseState) -> None:
    task_tables = set(_TASK255_TABLES)
    present_task_tables = task_tables.intersection(state.tables)
    if present_task_tables and present_task_tables != task_tables:
        raise RunnerError("TASK255_SCHEMA_PARTIAL")
    if present_task_tables != task_tables:
        raise RunnerError("TASK255_SCHEMA_MISSING")
    if state.revisions != (EXPECTED_ALEMBIC_REVISION,):
        raise RunnerError("ALEMBIC_REVISION_MISMATCH")
    if not set(_LEGACY_GUARD_TABLES).issubset(state.tables):
        raise RunnerError("LEGACY_SCHEMA_MISSING")


def _task255_counts(session: Session) -> dict[str, int]:
    return {
        table: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for table, model in _TABLE_MODELS.items()
    }


def _execute_preflight(
    engine: Engine,
    *,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql: bool = False,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
) -> dict[str, Any]:
    connection = None
    transaction = None
    session = None
    rolled_back = False
    try:
        connection = engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        read_only_enforcer(session)
        state = schema_reader(session)
        _validate_schema_state(state)
        counts = _task255_counts(session)
        transaction.rollback()
        rolled_back = True
        return {
            "schema": SCHEMA_VERSION,
            "status": "ready",
            "mode": "preflight",
            "current_alembic_revision": EXPECTED_ALEMBIC_REVISION,
            "task255_tables_verified": list(_TASK255_TABLES),
            "legacy_tables_verified": list(_LEGACY_GUARD_TABLES),
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


def _assert_count_delta(
    before: dict[str, int], after: dict[str, int], result: Any
) -> None:
    expected = {
        "cbr_bank_reporting_subjects": result.subjects.inserted,
        "cbr_bank_source_artifacts": result.artifacts.inserted,
        "cbr_bank_report_snapshots": result.snapshots.inserted,
        "cbr_bank_raw_observations": result.observations.inserted,
        "cbr_bank_subject_legal_issuer_evidence": result.identity_evidence.inserted,
        "cbr_bank_subject_legal_issuer_profiles": result.identity_profiles.inserted,
    }
    for table, inserted in expected.items():
        if after[table] != before[table] + inserted:
            raise RunnerError("POST_WRITE_COUNT_MISMATCH")


def _validate_apply_readback(
    session: Session,
    *,
    prepared: _PreparedEvidence,
    before_counts: dict[str, int],
    result: Any,
) -> dict[str, int]:
    after_counts = _task255_counts(session)
    _assert_count_delta(before_counts, after_counts, result)
    if (
        after_counts["cbr_bank_subject_legal_issuer_evidence"]
        != before_counts["cbr_bank_subject_legal_issuer_evidence"]
        or after_counts["cbr_bank_subject_legal_issuer_profiles"]
        != before_counts["cbr_bank_subject_legal_issuer_profiles"]
    ):
        raise RunnerError("IDENTITY_TABLE_MUTATION_DETECTED")

    planned_artifacts = {item.content_sha256: item for item in prepared.artifacts}
    artifact_rows = list(
        session.execute(
            select(CbrBankSourceArtifact).where(
                CbrBankSourceArtifact.content_sha256.in_(tuple(planned_artifacts))
            )
        ).scalars()
    )
    if len(artifact_rows) != len(planned_artifacts):
        raise RunnerError("POST_WRITE_ARTIFACT_MISMATCH")
    artifact_ids: list[int] = []
    for row in artifact_rows:
        planned = planned_artifacts.get(row.content_sha256)
        if (
            planned is None
            or row.form != planned.reference.form.value
            or row.report_date != planned.reference.report_date
            or row.compressed_size != planned.compressed_size
            or row.content_bytes != planned.content
        ):
            raise RunnerError("POST_WRITE_ARTIFACT_MISMATCH")
        artifact_ids.append(row.id)

    snapshot_candidates = list(
        session.execute(
            select(CbrBankReportSnapshot).where(
                CbrBankReportSnapshot.artifact_id.in_(artifact_ids),
                CbrBankReportSnapshot.report_date == APPROVED_REPORT_DATE,
            )
        ).scalars()
    )
    observed = _parse_utc(prepared.report["evidence_observed_at"])
    snapshot_rows = [
        row
        for row in snapshot_candidates
        if utc_datetime(row.observed_at, field_name="snapshot observed_at") == observed
    ]
    if len(snapshot_rows) != len(prepared.form_projection):
        raise RunnerError("POST_WRITE_SNAPSHOT_MISMATCH")
    projection_by_form = {item["form"]: item for item in prepared.form_projection}
    observation_total = 0
    for snapshot in snapshot_rows:
        projection = projection_by_form.get(snapshot.form)
        if (
            projection is None
            or snapshot.publication_status != PUBLICATION_STATUS
            or snapshot.publication_at is not None
            or snapshot.record_count != projection["record_count"]
            or snapshot.subject_count != projection["subject_count"]
            or snapshot.subject_set_sha256 != projection["subject_set_sha256"]
            or snapshot.form_schema_fingerprint
            != projection["form_schema_fingerprint"]
            or snapshot.value_member_name != projection["value_member_name"]
        ):
            raise RunnerError("POST_WRITE_SNAPSHOT_MISMATCH")
        fingerprint_rows = list(
            session.execute(
                select(
                    CbrBankRawObservation.source_row_number,
                    CbrBankRawObservation.observation_fingerprint,
                )
                .where(CbrBankRawObservation.snapshot_id == snapshot.id)
                .order_by(CbrBankRawObservation.source_row_number)
            )
        )
        if len(fingerprint_rows) != snapshot.record_count:
            raise RunnerError("POST_WRITE_OBSERVATION_MISMATCH")
        checksum = ordered_fingerprints_sha256(
            [str(row.observation_fingerprint) for row in fingerprint_rows]
        )
        if checksum != snapshot.observation_set_sha256:
            raise RunnerError("POST_WRITE_OBSERVATION_MISMATCH")
        observation_total += len(fingerprint_rows)
    if observation_total != prepared.report["observations"]:
        raise RunnerError("POST_WRITE_OBSERVATION_MISMATCH")
    return after_counts


def _commit_transaction(transaction: Any) -> None:
    transaction.commit()


def _execute_apply(
    engine: Engine,
    *,
    prepared: _PreparedEvidence,
    ingested_at: datetime,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql: bool = False,
    store_factory: Callable[..., CbrBankRawFinancialEvidenceStore] = (
        CbrBankRawFinancialEvidenceStore
    ),
    readback_validator: Callable[..., dict[str, int]] = _validate_apply_readback,
    archive_executable: str | None = None,
) -> dict[str, Any]:
    connection = None
    transaction = None
    session = None
    commit_attempted = False
    committed = False
    try:
        connection = engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        state = schema_reader(session)
        _validate_schema_state(state)
        before_counts = _task255_counts(session)
        result = store_factory(
            session, archive_executable=archive_executable
        ).persist_bundle(
            prepared.bundle,
            observed_at=_parse_utc(prepared.report["evidence_observed_at"]),
            ingested_at=utc_datetime(ingested_at, field_name="ingested_at"),
            publication_status=PUBLICATION_STATUS,
            publication_at=None,
            identity_snapshot=None,
        )
        after_counts = readback_validator(
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
            "report_date": prepared.report["report_date"],
            "evidence_observed_at": prepared.report["evidence_observed_at"],
            "evidence_envelope_sha256": prepared.report[
                "evidence_envelope_sha256"
            ],
            "publication_status": PUBLICATION_STATUS,
            "publication_at": None,
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
    parser = _ArgumentParser(prog="cbr-raw-financial-evidence-production-runner")
    parser.add_argument("--mode", choices=("plan", "preflight", "apply"), required=True)
    parser.add_argument("--task251-fixture-report-date")
    parser.add_argument("--evidence-observed-at")
    parser.add_argument("--database-url-env")
    parser.add_argument("--confirm-read-only", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--expected-envelope-sha256")
    return parser


def _validate_args(args: argparse.Namespace) -> tuple[date | None, datetime | None]:
    if args.mode in {"plan", "apply"}:
        if not args.task251_fixture_report_date or not args.evidence_observed_at:
            raise RunnerError("INVALID_ARGUMENTS")
        try:
            report_date = date.fromisoformat(args.task251_fixture_report_date)
        except (TypeError, ValueError) as exc:
            raise RunnerError("INVALID_ARGUMENTS") from exc
        observed_at = _parse_utc(args.evidence_observed_at)
        if report_date != APPROVED_REPORT_DATE:
            raise RunnerError("INVALID_ARGUMENTS")
    else:
        report_date = None
        observed_at = None

    if args.mode == "plan":
        if any(
            (
                args.database_url_env,
                args.confirm_read_only,
                args.confirm_write,
                args.expected_envelope_sha256,
            )
        ):
            raise RunnerError("INVALID_ARGUMENTS")
    elif args.mode == "preflight":
        if (
            args.task251_fixture_report_date is not None
            or args.evidence_observed_at is not None
            or not args.database_url_env
            or not args.confirm_read_only
            or args.confirm_write
            or args.expected_envelope_sha256
        ):
            raise RunnerError("INVALID_ARGUMENTS")
    else:
        if (
            not args.database_url_env
            or args.confirm_read_only
            or not args.confirm_write
            or not args.expected_envelope_sha256
            or not _SHA256.fullmatch(args.expected_envelope_sha256)
        ):
            raise RunnerError("INVALID_ARGUMENTS")
    if args.database_url_env and not _ENV_NAME.fullmatch(args.database_url_env):
        raise RunnerError("INVALID_ARGUMENTS")
    return report_date, observed_at


def _failure(
    code: str,
    *,
    mode: str | None = None,
    reconciliation_required: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "error_code": code,
        "reconciliation_required": reconciliation_required,
    }
    if mode in {"plan", "preflight", "apply"}:
        result["mode"] = mode
    return result


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _database_url(
    env_name: str, environment: Mapping[str, str]
) -> str:
    value = environment.get(env_name)
    if not value:
        raise RunnerError("DATABASE_CONFIGURATION_UNAVAILABLE")
    try:
        if make_url(value).get_backend_name() != "postgresql":
            raise RunnerError("DATABASE_CONFIGURATION_INVALID")
    except RunnerError:
        raise
    except Exception as exc:
        raise RunnerError("DATABASE_CONFIGURATION_INVALID") from exc
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[..., Engine] | None = None,
    fixture_root: Path = _FIXTURE_ROOT,
    archive_executable: str | None = None,
    clock: Callable[[], datetime] | None = None,
    schema_reader: Callable[[Session], _DatabaseState] = _read_postgresql_schema_state,
    allow_non_postgresql_test_adapter: bool = False,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
) -> int:
    try:
        args = _parser().parse_args(argv)
        report_date, observed_at = _validate_args(args)
    except (RunnerError, SystemExit):
        _emit(_failure("INVALID_ARGUMENTS"))
        return 2

    if args.mode == "plan":
        try:
            prepared = _prepare_evidence(
                report_date=report_date,
                evidence_observed_at=observed_at,
                fixture_root=fixture_root,
                archive_executable=archive_executable,
            )
            _emit(prepared.report)
            return 0
        except RunnerError as exc:
            _emit(_failure(exc.code, mode="plan"))
            return 1
        except Exception:
            _emit(_failure("PLAN_FAILED", mode="plan"))
            return 1

    prepared: _PreparedEvidence | None = None
    if args.mode == "apply":
        try:
            prepared = _prepare_evidence(
                report_date=report_date,
                evidence_observed_at=observed_at,
                fixture_root=fixture_root,
                archive_executable=archive_executable,
            )
            if not hmac.compare_digest(
                prepared.report["evidence_envelope_sha256"],
                args.expected_envelope_sha256,
            ):
                raise RunnerError("EVIDENCE_ENVELOPE_MISMATCH")
        except RunnerError as exc:
            _emit(_failure(exc.code, mode="apply"))
            return 1
        except Exception:
            _emit(_failure("PLAN_FAILED", mode="apply"))
            return 1

    environment = os.environ if environ is None else environ
    try:
        database_url = _database_url(args.database_url_env, environment)
    except RunnerError as exc:
        _emit(_failure(exc.code, mode=args.mode))
        return 1

    engine: Engine | None = None
    try:
        engine = (engine_factory or create_engine)(database_url, pool_pre_ping=True)
        if args.mode == "preflight":
            report = _execute_preflight(
                engine,
                schema_reader=schema_reader,
                allow_non_postgresql=allow_non_postgresql_test_adapter,
                read_only_enforcer=read_only_enforcer,
            )
        else:
            report = _execute_apply(
                engine,
                prepared=prepared,
                ingested_at=(clock or (lambda: datetime.now(timezone.utc)))(),
                schema_reader=schema_reader,
                allow_non_postgresql=allow_non_postgresql_test_adapter,
                archive_executable=archive_executable,
            )
        _emit(report)
        return 0
    except CommitOutcomeUnknown as exc:
        _emit(
            _failure(
                exc.code,
                mode=args.mode,
                reconciliation_required=True,
            )
        )
        return 1
    except RunnerError as exc:
        _emit(_failure(exc.code, mode=args.mode))
        return 1
    except Exception:
        _emit(_failure(f"{args.mode.upper()}_FAILED", mode=args.mode))
        return 1
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
