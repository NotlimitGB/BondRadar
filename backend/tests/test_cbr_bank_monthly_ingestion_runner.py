from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.cbr_bank_financial_evidence import (
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerEvidence,
    CbrBankSubjectLegalIssuerProfile,
)
from app.services.cbr_bank_financial_evidence import monthly_runner as runner
from app.services.cbr_bank_financial_evidence import production_runner
from app.services.cbr_bank_financial_evidence.store import (
    CbrBankRawFinancialEvidenceStore,
)
from app.services.cbr_bank_reporting.client import CbrBankRegulatoryClient
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
)


REPORT_DATE = date(2026, 8, 1)
T1 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(seconds=1)
FIXTURES = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"
EXPECTED_ROWS = {
    "0409101": 25_654,
    "0409102": 10_079,
    "0409123": 1_400,
    "0409135": 1_709,
}


@pytest.fixture(scope="module")
def prepared():
    initial = production_runner._prepare_evidence(
        report_date=REPORT_DATE, evidence_observed_at=T1
    )
    return runner._prepare_artifacts(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
        artifacts=initial.artifacts,
    )


def _full_state(revision: str = runner.EXPECTED_ALEMBIC_REVISION):
    return production_runner._DatabaseState(
        revisions=(revision,),
        tables=frozenset(
            {*production_runner._TASK255_TABLES, *production_runner._LEGACY_GUARD_TABLES}
        ),
        counts={},
    )


def _schema_reader(revision: str = runner.EXPECTED_ALEMBIC_REVISION):
    return lambda session: _full_state(revision)


def _sqlite_engine(tmp_path: Path, name: str):
    engine = create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _counts(engine):
    models = (
        CbrBankReportingSubject,
        CbrBankSourceArtifact,
        CbrBankReportSnapshot,
        CbrBankRawObservation,
        CbrBankSubjectLegalIssuerEvidence,
        CbrBankSubjectLegalIssuerProfile,
    )
    with engine.connect() as connection:
        return tuple(
            int(connection.scalar(select(func.count()).select_from(model)) or 0)
            for model in models
        )


def _write_manifest(tmp_path: Path, prepared) -> tuple[Path, Path]:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(prepared.manifest.to_dict(), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return manifest, FIXTURES


def test_known_month_plan_manifest_and_lexical_contract(prepared) -> None:
    report = prepared.report
    manifest = prepared.manifest
    assert report["subjects"] == 353
    assert report["artifacts"] == report["snapshots"] == 4
    assert report["observations"] == 38_842
    assert report["records_by_form"] == EXPECTED_ROWS
    assert report["raw_lexical_mismatch_count"] == 0
    assert report["database_accessed"] is report["network_accessed"] is False
    assert manifest.publication_status == "UNKNOWN"
    assert manifest.publication_at is None
    assert len(manifest.ingestion_manifest_sha256) == 64
    assert [item.form for item in manifest.artifacts] == [item.value for item in CbrBankForm]
    assert json.loads(runner._manifest_bytes(manifest))["forms"] == [
        item.to_dict() for item in manifest.artifacts
    ]
    serialized = json.dumps(manifest.to_dict())
    assert "password" not in serialized.casefold()
    assert "database_url" not in serialized.casefold()


def test_manifest_hash_binds_time_bytes_and_source_reference(prepared) -> None:
    repeated = runner.MonthlyIngestionManifest.create(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
        artifacts=prepared.manifest.artifacts,
    )
    changed_time = runner.MonthlyIngestionManifest.create(
        report_date=REPORT_DATE,
        evidence_observed_at=T2,
        artifacts=prepared.manifest.artifacts,
    )
    first = prepared.manifest.artifacts[0]
    changed_bytes = replace(first, artifact_sha256="0" * 64)
    changed_source = replace(
        first,
        source_href=first.source_href + "?revision=2",
        source_url=first.source_url + "?revision=2",
    )
    byte_manifest = runner.MonthlyIngestionManifest.create(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
        artifacts=(changed_bytes, *prepared.manifest.artifacts[1:]),
    )
    source_manifest = runner.MonthlyIngestionManifest.create(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
        artifacts=(changed_source, *prepared.manifest.artifacts[1:]),
    )
    assert repeated.ingestion_manifest_sha256 == prepared.manifest.ingestion_manifest_sha256
    assert len(
        {
            repeated.ingestion_manifest_sha256,
            changed_time.ingestion_manifest_sha256,
            byte_manifest.ingestion_manifest_sha256,
            source_manifest.ingestion_manifest_sha256,
        }
    ) == 4


def test_manifest_validation_is_strict_and_plan_detects_artifact_mutation(
    tmp_path: Path, prepared
) -> None:
    payload = prepared.manifest.to_dict()
    assert runner.MonthlyIngestionManifest.from_dict(payload) == prepared.manifest
    with pytest.raises(runner.RunnerError, match="MANIFEST_HASH_MISMATCH"):
        runner.MonthlyIngestionManifest.from_dict(
            {**payload, "ingestion_manifest_sha256": "0" * 64}
        )
    with pytest.raises(runner.RunnerError, match="MANIFEST_INVALID"):
        runner.MonthlyIngestionManifest.from_dict({**payload, "extra": True})

    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    for artifact in prepared.artifacts:
        (artifact_dir / artifact.reference.artifact_filename).write_bytes(artifact.content)
    changed = prepared.artifacts[0]
    path = artifact_dir / changed.reference.artifact_filename
    path.write_bytes(changed.content[:-1] + bytes([changed.content[-1] ^ 1]))
    with pytest.raises(runner.RunnerError, match="ARTIFACT_IDENTITY_MISMATCH"):
        runner.prepare_from_manifest(
            manifest=prepared.manifest, artifact_dir=artifact_dir
        )


def test_unknown_schema_and_partial_bundle_fail_closed(prepared) -> None:
    with pytest.raises(runner.RunnerError, match="FOUR_FORM_BUNDLE_REQUIRED"):
        runner._prepare_artifacts(
            report_date=REPORT_DATE,
            evidence_observed_at=T1,
            artifacts=prepared.artifacts[:3],
        )

    class _Unsupported:
        def build_snapshot(self, **kwargs):
            raise CbrSourceError(
                CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION, "synthetic unknown schema"
            )

    with pytest.raises(runner.RunnerError, match="UNSUPPORTED_SCHEMA_VERSION"):
        runner._prepare_artifacts(
            report_date=REPORT_DATE,
            evidence_observed_at=T1,
            artifacts=prepared.artifacts,
            bundle_service=_Unsupported(),
        )


def test_unapproved_discovered_fetch_is_bounded_but_approved_path_stays_locked() -> None:
    content = b"synthetic future RAR transport bytes"
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=content,
            headers={"content-type": "application/vnd.rar"},
            request=request,
        )
    )
    reference = CbrArtifactReference(
        form=CbrBankForm.FORM_101,
        source_href="/vfs/credit/forms/101-20260901.rar",
        source_url="https://www.cbr.ru/vfs/credit/forms/101-20260901.rar",
        artifact_filename="101-20260901.rar",
        report_date=date(2026, 9, 1),
        discovered_at=T1,
    )
    client = CbrBankRegulatoryClient(http_client=httpx.Client(transport=transport), now=lambda: T1)
    artifact = client.fetch_discovered_artifact(reference)
    assert artifact.content == content
    assert artifact.content_sha256 == hashlib.sha256(content).hexdigest()
    with pytest.raises(CbrSourceError) as error:
        client.fetch_artifact(reference)
    assert error.value.code == CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION
    client.http_client.close()


def test_discover_uses_injected_source_only_and_freezes_without_database(
    tmp_path: Path, prepared
) -> None:
    class _Source:
        def discover_requested(self, *, forms, report_date):
            assert forms == tuple(CbrBankForm)
            assert report_date == REPORT_DATE
            return tuple(item.reference for item in prepared.artifacts)

        def fetch_discovered_artifact(self, reference):
            return next(
                item for item in prepared.artifacts if item.reference.form == reference.form
            )

    discovered = runner.discover_month(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
        client=_Source(),
    )
    assert discovered.report["mode"] == "discover"
    assert discovered.report["network_accessed"] is True
    assert discovered.report["database_accessed"] is False
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"
    runner.freeze_discovery(
        discovered, artifact_dir=artifact_dir, manifest_output=manifest_path
    )
    assert runner._read_manifest(manifest_path) == discovered.manifest
    assert sorted(path.name for path in artifact_dir.iterdir()) == sorted(
        item.reference.artifact_filename for item in prepared.artifacts
    )
    runner.freeze_discovery(
        discovered, artifact_dir=artifact_dir, manifest_output=manifest_path
    )
    first = discovered.artifacts[0]
    (artifact_dir / first.reference.artifact_filename).write_bytes(b"changed")
    with pytest.raises(runner.RunnerError, match="ARTIFACT_CACHE_CONFLICT"):
        runner.freeze_discovery(
            discovered, artifact_dir=artifact_dir, manifest_output=manifest_path
        )


@pytest.mark.parametrize("references", ("missing", "duplicate"))
def test_discover_requires_four_distinct_forms(prepared, references: str) -> None:
    selected = tuple(item.reference for item in prepared.artifacts)
    invalid = selected[:3] if references == "missing" else (*selected[:3], selected[0])

    class _Source:
        def discover_requested(self, **kwargs):
            return invalid

        def fetch_discovered_artifact(self, reference):
            raise AssertionError("invalid references must fail before fetch")

    with pytest.raises(runner.RunnerError, match="FOUR_FORM_BUNDLE_REQUIRED"):
        runner.discover_month(
            report_date=REPORT_DATE, evidence_observed_at=T1, client=_Source()
        )


def test_discover_cli_emits_runner_wrapper_and_manifest_identity(
    tmp_path: Path,
    prepared,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir()
    manifest_output = tmp_path / "manifest.json"
    monkeypatch.setattr(runner, "discover_month", lambda **kwargs: prepared)
    assert runner.main(
        [
            "--mode",
            "discover",
            "--report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
            "--manifest-output",
            str(manifest_output),
            "--artifact-dir",
            str(artifact_dir),
        ],
        client_factory=lambda **kwargs: SimpleNamespace(),
        engine_factory=lambda *args, **kwargs: pytest.fail("DB engine created"),
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["schema"] == runner.SCHEMA_VERSION
    assert output["manifest_schema"] == runner.MANIFEST_SCHEMA_VERSION
    assert output["ingestion_manifest_sha256"] == prepared.manifest.ingestion_manifest_sha256
    assert output["network_accessed"] is True
    assert output["database_accessed"] is False
    assert runner._read_manifest(manifest_output) == prepared.manifest


def test_manifest_rejects_foreign_source(prepared) -> None:
    payload = prepared.manifest.to_dict()
    payload["forms"][0]["source_href"] = "https://evil.invalid/101-20260801.rar"
    payload["forms"][0]["source_url"] = "https://evil.invalid/101-20260801.rar"
    with pytest.raises(runner.RunnerError, match="MANIFEST_INVALID"):
        runner.MonthlyIngestionManifest.from_dict(payload)


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "plan"],
        ["--mode", "discover", "--report-date", "2026-08-01"],
        [
            "--mode",
            "discover",
            "--report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T15:00:00+03:00",
            "--manifest-output",
            "manifest.json",
            "--artifact-dir",
            "artifacts",
        ],
        [
            "--mode",
            "preflight",
            "--report-date",
            "2026-08-01",
            "--manifest",
            "manifest.json",
            "--database-url-env",
            "DATABASE_URL",
        ],
        [
            "--mode",
            "apply",
            "--report-date",
            "2026-08-01",
            "--manifest",
            "manifest.json",
            "--artifact-dir",
            "artifacts",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-write",
            "--expected-manifest-sha256",
            "bad",
        ],
        [
            "--mode",
            "preflight",
            "--report-date",
            "2026-08-01",
            "--manifest",
            "manifest.json",
            "--database-url",
            "postgresql://user:secret@host/db",
            "--confirm-read-only",
        ],
    ],
)
def test_cli_arguments_fail_before_source_or_database(argv, capsys) -> None:
    source_calls = []
    engine_calls = []
    assert runner.main(
        argv,
        environ={},
        client_factory=lambda **kwargs: source_calls.append(kwargs),
        engine_factory=lambda *args, **kwargs: engine_calls.append(args),
    ) == 2
    assert source_calls == engine_calls == []
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENTS"


def test_plan_cli_uses_no_network_database_or_environment(
    tmp_path: Path, prepared, capsys
) -> None:
    manifest_path, artifact_dir = _write_manifest(tmp_path, prepared)

    class _NoEnvironment(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("plan must not read environment")

    assert runner.main(
        [
            "--mode",
            "plan",
            "--report-date",
            "2026-08-01",
            "--manifest",
            str(manifest_path),
            "--artifact-dir",
            str(artifact_dir),
        ],
        environ=_NoEnvironment(),
        client_factory=lambda **kwargs: pytest.fail("network client created"),
        engine_factory=lambda *args, **kwargs: pytest.fail("DB engine created"),
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["observations"] == 38_842
    assert output["network_accessed"] is output["database_accessed"] is False


def test_preflight_read_only_order_and_empty_state(tmp_path: Path, prepared) -> None:
    engine = _sqlite_engine(tmp_path, "preflight.db")
    events = []

    def read_only(session):
        events.extend(("SET TRANSACTION READ ONLY", "SHOW transaction_read_only"))

    def schema_reader(session):
        events.append("SELECT schema")
        return _full_state()

    report = runner.execute_preflight(
        engine,
        manifest=prepared.manifest,
        schema_reader=schema_reader,
        allow_non_postgresql=True,
        read_only_enforcer=read_only,
    )
    assert events[:3] == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
        "SELECT schema",
    ]
    assert report["monthly_state"] == "EMPTY"
    assert report["source_observation"] == "FIRST_OBSERVATION"
    assert report["transaction_read_only"] is True
    assert report["database_mutation_executed"] is False
    assert _counts(engine) == (0, 0, 0, 0, 0, 0)
    engine.dispose()


def test_month_state_partial_conflict_exact_and_reobservation(
    tmp_path: Path, prepared
) -> None:
    partial_engine = _sqlite_engine(tmp_path, "partial.db")
    with Session(partial_engine) as session:
        store = CbrBankRawFinancialEvidenceStore(session)
        store._persist_artifact(prepared.bundle.forms[0], ingested_at=T1)
        session.commit()
        state = runner._classify_month_state(session, prepared.manifest)
        assert state.state == "PARTIAL_STATE"
    partial_engine.dispose()

    conflict_engine = _sqlite_engine(tmp_path, "conflict.db")
    with Session(conflict_engine) as session:
        original = prepared.bundle.forms[0]
        changed_content = original.artifact.content + b"revision"
        changed_artifact = replace(
            original.artifact,
            content=changed_content,
            content_sha256=hashlib.sha256(changed_content).hexdigest(),
            compressed_size=len(changed_content),
        )
        CbrBankRawFinancialEvidenceStore(session)._persist_artifact(
            replace(original, artifact=changed_artifact), ingested_at=T1
        )
        session.commit()
        state = runner._classify_month_state(session, prepared.manifest)
        assert state.state == "CONFLICTING_ARTIFACT"
        assert state.source_observation == "CHANGED_SOURCE_BYTES"
    conflict_engine.dispose()

    exact_engine = _sqlite_engine(tmp_path, "exact.db")
    first = runner.execute_apply(
        exact_engine,
        prepared=prepared,
        ingested_at=T1,
        schema_reader=_schema_reader(),
        allow_non_postgresql=True,
    )
    assert first["monthly_state_before"] == "EMPTY"
    assert first["monthly_state_after"] == "EXACT_ALREADY_PRESENT"
    assert first["artifacts_inserted"] == 4
    assert first["snapshots_inserted"] == 4
    assert first["observations_inserted"] == 38_842
    with Session(exact_engine) as session:
        state = runner._classify_month_state(session, prepared.manifest)
        assert state.state == "EXACT_ALREADY_PRESENT"
        assert state.matching_observations == 38_842
    second = runner.execute_apply(
        exact_engine,
        prepared=prepared,
        ingested_at=T2,
        schema_reader=_schema_reader(),
        allow_non_postgresql=True,
    )
    assert second["artifacts_inserted"] == 0
    assert second["snapshots_inserted"] == 0
    assert second["observations_inserted"] == 0
    assert second["artifacts_reused"] == 4
    assert second["snapshots_reused"] == 4
    assert second["observations_reused"] == 38_842
    assert _counts(exact_engine) == (353, 4, 4, 38_842, 0, 0)
    exact_engine.dispose()


def test_apply_readback_failure_rolls_back_and_commit_failure_is_unknown(
    tmp_path: Path, prepared, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _sqlite_engine(tmp_path, "rollback.db")
    with pytest.raises(RuntimeError, match="readback failure"):
        runner.execute_apply(
            engine,
            prepared=prepared,
            ingested_at=T1,
            schema_reader=_schema_reader(),
            allow_non_postgresql=True,
            readback_validator=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("readback failure")
            ),
        )
    assert _counts(engine) == (0, 0, 0, 0, 0, 0)
    monkeypatch.setattr(
        runner,
        "_commit_transaction",
        lambda transaction: (_ for _ in ()).throw(RuntimeError("lost ack")),
    )
    with pytest.raises(production_runner.CommitOutcomeUnknown):
        runner.execute_apply(
            engine,
            prepared=prepared,
            ingested_at=T1,
            schema_reader=_schema_reader(),
            allow_non_postgresql=True,
        )
    engine.dispose()


def test_apply_wrong_manifest_hash_blocks_before_engine(
    tmp_path: Path, prepared, capsys
) -> None:
    manifest_path, artifact_dir = _write_manifest(tmp_path, prepared)
    engine_calls = []
    result = runner.main(
        [
            "--mode",
            "apply",
            "--report-date",
            "2026-08-01",
            "--manifest",
            str(manifest_path),
            "--artifact-dir",
            str(artifact_dir),
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-write",
            "--expected-manifest-sha256",
            "0" * 64,
        ],
        environ={"DATABASE_URL": "postgresql://user:secret@host/db"},
        client_factory=lambda **kwargs: pytest.fail("network client created"),
        engine_factory=lambda *args, **kwargs: engine_calls.append(args),
    )
    assert result == 1
    assert engine_calls == []
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "MANIFEST_HASH_MISMATCH"
    assert "secret" not in output


def test_runtime_and_scope_have_no_self_install_second_store_or_migration() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "pip install",
        "apt-get install",
        "subprocess",
        "alembic.command",
        "upgrade(",
        "downgrade(",
        "FinancialReport",
        "CompanyScore",
    ):
        assert forbidden not in source
    assert "CbrBankRegulatoryBundleService" in source
    assert "extract_exact_form_evidence" in source
    assert "CbrBankRawFinancialEvidenceStore" in source
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text()
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
    assert "rarfile==4.5" in requirements
    assert "dbfread==2.0.7" in requirements
    assert "libarchive-tools" in dockerfile


def test_sanitized_preflight_configuration_failure(
    tmp_path: Path, prepared, capsys
) -> None:
    manifest_path, _ = _write_manifest(tmp_path, prepared)
    args = [
        "--mode",
        "preflight",
        "--report-date",
        "2026-08-01",
        "--manifest",
        str(manifest_path),
        "--database-url-env",
        "DATABASE_URL",
        "--confirm-read-only",
    ]
    assert runner.main(args, environ={}) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "DATABASE_CONFIGURATION_UNAVAILABLE"
    assert runner.main(args, environ={"DATABASE_URL": "sqlite:///secret.db"}) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "DATABASE_CONFIGURATION_INVALID"
    assert "secret" not in output


def test_read_only_guard_exact_statements_remains_shared() -> None:
    calls = []

    class _Result:
        def scalar_one(self):
            return "on"

    production_runner._enforce_read_only(
        SimpleNamespace(
            execute=lambda statement: calls.append(str(statement).strip()) or _Result()
        )
    )
    assert calls == ["SET TRANSACTION READ ONLY", "SHOW transaction_read_only"]
