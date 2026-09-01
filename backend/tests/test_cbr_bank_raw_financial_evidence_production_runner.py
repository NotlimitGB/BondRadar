from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, func, select

from app.db.base import Base
from app.models.cbr_bank_financial_evidence import (
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerEvidence,
    CbrBankSubjectLegalIssuerProfile,
)
from app.services.cbr_bank_financial_evidence import production_runner as runner


REPORT_DATE = date(2026, 8, 1)
T1 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(seconds=1)
EXPECTED_ROWS = {
    "0409101": 25_654,
    "0409102": 10_079,
    "0409123": 1_400,
    "0409135": 1_709,
}
EXPECTED_SUBJECTS = {
    "0409101": 353,
    "0409102": 212,
    "0409123": 352,
    "0409135": 345,
}
EXPECTED_SUBJECT_HASHES = {
    "0409101": "692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd",
    "0409102": "90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8",
    "0409123": "5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc",
    "0409135": "660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619",
}


@pytest.fixture(scope="module")
def prepared():
    return runner._prepare_evidence(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
    )


def _full_state(revision: str = runner.EXPECTED_ALEMBIC_REVISION):
    return runner._DatabaseState(
        revisions=(revision,),
        tables=frozenset(
            {*runner._TASK255_TABLES, *runner._LEGACY_GUARD_TABLES}
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


def test_plan_proves_exact_fixtures_lexical_counts_and_no_side_effects(
    prepared,
) -> None:
    report = prepared.report
    assert report["status"] == "ready"
    assert report["mode"] == "plan"
    assert report["subjects"] == 353
    assert report["artifacts"] == report["snapshots"] == 4
    assert report["observations"] == 38_842
    assert report["records_by_form"] == EXPECTED_ROWS
    assert report["subjects_by_form"] == EXPECTED_SUBJECTS
    assert report["subject_set_sha256_by_form"] == EXPECTED_SUBJECT_HASHES
    assert report["raw_lexical_mismatch_count"] == 0
    assert report["publication_status"] == "UNKNOWN"
    assert report["publication_at"] is None
    assert report["database_accessed"] is False
    assert report["network_accessed"] is False
    assert report["production_actions"] == "NONE"
    assert len(report["evidence_envelope_sha256"]) == 64
    assert all(
        len(value) == 64 for value in report["artifact_sha256_by_form"].values()
    )


def test_envelope_is_repeatable_and_frozen_timestamp_is_semantic(prepared) -> None:
    repeated = runner._prepare_evidence(
        report_date=REPORT_DATE,
        evidence_observed_at=T1,
    )
    changed_time = runner._prepare_evidence(
        report_date=REPORT_DATE,
        evidence_observed_at=T2,
    )
    assert repeated.report == prepared.report
    assert (
        changed_time.report["evidence_envelope_sha256"]
        != prepared.report["evidence_envelope_sha256"]
    )


def test_plan_cli_never_reads_database_or_environment(
    prepared, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(runner, "_prepare_evidence", lambda **kwargs: prepared)

    class _NoEnvironment(dict):
        def get(self, *args, **kwargs):
            raise AssertionError("plan must not read environment")

    result = runner.main(
        [
            "--mode",
            "plan",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
        ],
        environ=_NoEnvironment(),
        engine_factory=lambda *args, **kwargs: pytest.fail("DB engine created"),
    )
    assert result == 0
    assert json.loads(capsys.readouterr().out) == prepared.report


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["--mode", "unknown"],
        ["--mode", "plan"],
        [
            "--mode",
            "plan",
            "--task251-fixture-report-date",
            "2026-07-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
        ],
        [
            "--mode",
            "plan",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00",
        ],
        [
            "--mode",
            "plan",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T15:00:00+03:00",
        ],
        [
            "--mode",
            "preflight",
            "--database-url-env",
            "DATABASE_URL",
        ],
        [
            "--mode",
            "apply",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-write",
            "--expected-envelope-sha256",
            "BAD",
        ],
        [
            "--mode",
            "apply",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
            "--database-url-env",
            "DATABASE_URL",
            "--expected-envelope-sha256",
            "0" * 64,
        ],
        [
            "--mode",
            "preflight",
            "--database-url",
            "postgresql://user:secret@host/db",
            "--confirm-read-only",
        ],
    ],
)
def test_cli_arguments_fail_closed_before_engine_creation(argv, capsys) -> None:
    calls = []
    assert runner.main(
        argv,
        environ={},
        engine_factory=lambda *args, **kwargs: calls.append(args),
    ) == 2
    assert calls == []
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENTS"


def test_apply_wrong_envelope_blocks_before_database(
    prepared, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(runner, "_prepare_evidence", lambda **kwargs: prepared)
    engine_calls = []
    result = runner.main(
        [
            "--mode",
            "apply",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-write",
            "--expected-envelope-sha256",
            "0" * 64,
        ],
        environ={"DATABASE_URL": "postgresql://user:secret@host/db"},
        engine_factory=lambda *args, **kwargs: engine_calls.append(args),
    )
    assert result == 1
    assert engine_calls == []
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "EVIDENCE_ENVELOPE_MISMATCH"
    assert "secret" not in output


def test_read_only_guard_has_exact_first_statements() -> None:
    calls = []

    class _Result:
        def __init__(self, value=None):
            self.value = value

        def scalar_one(self):
            return self.value

    class _Session:
        def execute(self, statement):
            sql = str(statement).strip()
            calls.append(sql)
            return _Result("on" if sql == "SHOW transaction_read_only" else None)

    runner._enforce_read_only(_Session())
    assert calls == ["SET TRANSACTION READ ONLY", "SHOW transaction_read_only"]
    with pytest.raises(runner.RunnerError, match="READ_ONLY_VERIFICATION_FAILED"):
        runner._enforce_read_only(
            SimpleNamespace(
                execute=lambda statement: _Result(
                    "off" if str(statement).startswith("SHOW") else None
                )
            )
        )


@pytest.mark.parametrize(
    ("state", "code"),
    [
        (
            runner._DatabaseState(
                revisions=(runner.EXPECTED_ALEMBIC_REVISION,),
                tables=frozenset({runner._TASK255_TABLES[0]}),
                counts={},
            ),
            "TASK255_SCHEMA_PARTIAL",
        ),
        (
            runner._DatabaseState(
                revisions=(runner.EXPECTED_ALEMBIC_REVISION,),
                tables=frozenset(runner._LEGACY_GUARD_TABLES),
                counts={},
            ),
            "TASK255_SCHEMA_MISSING",
        ),
        (_full_state("wrong"), "ALEMBIC_REVISION_MISMATCH"),
        (
            runner._DatabaseState(
                revisions=("202608280002", runner.EXPECTED_ALEMBIC_REVISION),
                tables=_full_state().tables,
                counts={},
            ),
            "ALEMBIC_REVISION_MISMATCH",
        ),
        (
            runner._DatabaseState(
                revisions=(runner.EXPECTED_ALEMBIC_REVISION,),
                tables=frozenset(runner._TASK255_TABLES),
                counts={},
            ),
            "LEGACY_SCHEMA_MISSING",
        ),
    ],
)
def test_schema_contract_fails_closed(state, code) -> None:
    with pytest.raises(runner.RunnerError, match=code):
        runner._validate_schema_state(state)


def test_preflight_is_read_only_and_reports_all_task255_counts(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path, "preflight.db")
    before = _counts(engine)
    report = runner._execute_preflight(
        engine,
        schema_reader=_schema_reader(),
        allow_non_postgresql=True,
        read_only_enforcer=lambda session: None,
    )
    assert report["status"] == "ready"
    assert report["transaction_read_only"] is True
    assert report["database_mutation_executed"] is False
    assert report["transaction_rolled_back"] is True
    assert report["current_task255_counts"] == {
        table: 0 for table in runner._TASK255_TABLES
    }
    assert _counts(engine) == before
    engine.dispose()


def test_production_database_boundary_rejects_non_postgresql(tmp_path: Path) -> None:
    engine = _sqlite_engine(tmp_path, "dialect-guard.db")
    read_only_calls = []
    with pytest.raises(runner.RunnerError, match="POSTGRESQL_REQUIRED"):
        runner._execute_preflight(
            engine,
            schema_reader=_schema_reader(),
            read_only_enforcer=lambda session: read_only_calls.append(True),
        )
    assert read_only_calls == []
    assert _counts(engine) == (0, 0, 0, 0, 0, 0)
    engine.dispose()


def test_database_configuration_and_failures_are_sanitized(capsys) -> None:
    args = [
        "--mode",
        "preflight",
        "--database-url-env",
        "DATABASE_URL",
        "--confirm-read-only",
    ]
    engine_calls = []
    assert runner.main(args, environ={}) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "DATABASE_CONFIGURATION_UNAVAILABLE"
    assert runner.main(
        args,
        environ={"DATABASE_URL": "sqlite:///secret.db"},
        engine_factory=lambda *a, **k: engine_calls.append(a),
    ) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "DATABASE_CONFIGURATION_INVALID"
    assert "secret" not in output
    assert engine_calls == []
    assert runner.main(
        args,
        environ={"DATABASE_URL": "postgresql://user:secret@host/db"},
        engine_factory=lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("password=secret")
        ),
    ) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "PREFLIGHT_FAILED"
    assert "secret" not in output


def test_apply_first_run_readback_commit_and_exact_retry(
    tmp_path: Path,
    prepared,
) -> None:
    engine = _sqlite_engine(tmp_path, "apply.db")
    events = []
    event.listen(engine, "commit", lambda connection: events.append("commit"))

    def readback(*args, **kwargs):
        events.append("readback")
        return runner._validate_apply_readback(*args, **kwargs)

    first = runner._execute_apply(
        engine,
        prepared=prepared,
        ingested_at=T1,
        schema_reader=_schema_reader(),
        allow_non_postgresql=True,
        readback_validator=readback,
    )
    assert first["subjects_inserted"] == 353
    assert first["artifacts_inserted"] == first["snapshots_inserted"] == 4
    assert first["observations_inserted"] == 38_842
    assert first["post_subject_count"] == 353
    assert first["post_artifact_count"] == first["post_snapshot_count"] == 4
    assert first["post_observation_count"] == 38_842
    assert first["transaction_committed"] is True
    assert first["database_mutation_executed"] is True
    assert events[-2:] == ["readback", "commit"]
    assert _counts(engine) == (353, 4, 4, 38_842, 0, 0)

    second = runner._execute_apply(
        engine,
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
    assert second["database_mutation_executed"] is False
    assert _counts(engine) == (353, 4, 4, 38_842, 0, 0)
    with engine.connect() as connection:
        snapshots = list(connection.execute(select(CbrBankReportSnapshot)))
        assert len(snapshots) == 4
        assert all(row.publication_status == "UNKNOWN" for row in snapshots)
        assert all(row.publication_at is None for row in snapshots)
    engine.dispose()


def test_wrong_revision_and_readback_failure_roll_back_everything(
    tmp_path: Path,
    prepared,
) -> None:
    wrong_revision_engine = _sqlite_engine(tmp_path, "wrong-revision.db")
    with pytest.raises(runner.RunnerError, match="ALEMBIC_REVISION_MISMATCH"):
        runner._execute_apply(
            wrong_revision_engine,
            prepared=prepared,
            ingested_at=T1,
            schema_reader=_schema_reader("wrong"),
            allow_non_postgresql=True,
        )
    assert _counts(wrong_revision_engine) == (0, 0, 0, 0, 0, 0)
    wrong_revision_engine.dispose()

    failed_engine = _sqlite_engine(tmp_path, "readback-failure.db")
    with pytest.raises(RuntimeError, match="injected readback failure"):
        runner._execute_apply(
            failed_engine,
            prepared=prepared,
            ingested_at=T1,
            schema_reader=_schema_reader(),
            allow_non_postgresql=True,
            readback_validator=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("injected readback failure")
            ),
        )
    assert _counts(failed_engine) == (0, 0, 0, 0, 0, 0)
    failed_engine.dispose()


def test_commit_failure_is_unknown_and_connection_close_rolls_back(
    tmp_path: Path,
    prepared,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _sqlite_engine(tmp_path, "commit-failure.db")
    monkeypatch.setattr(
        runner,
        "_commit_transaction",
        lambda transaction: (_ for _ in ()).throw(RuntimeError("lost ack")),
    )
    with pytest.raises(runner.CommitOutcomeUnknown):
        runner._execute_apply(
            engine,
            prepared=prepared,
            ingested_at=T1,
            schema_reader=_schema_reader(),
            allow_non_postgresql=True,
        )
    assert _counts(engine) == (0, 0, 0, 0, 0, 0)
    engine.dispose()


def test_commit_unknown_cli_contract_requires_reconciliation(
    prepared, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(runner, "_prepare_evidence", lambda **kwargs: prepared)
    monkeypatch.setattr(
        runner,
        "_execute_apply",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            runner.CommitOutcomeUnknown()
        ),
    )

    class _Engine:
        def dispose(self):
            self.disposed = True

    engine = _Engine()
    result = runner.main(
        [
            "--mode",
            "apply",
            "--task251-fixture-report-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-08-30T12:00:00Z",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-write",
            "--expected-envelope-sha256",
            prepared.report["evidence_envelope_sha256"],
        ],
        environ={"DATABASE_URL": "postgresql://user:secret@host/db"},
        engine_factory=lambda *args, **kwargs: engine,
    )
    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output["error_code"] == "COMMIT_OUTCOME_UNKNOWN"
    assert output["reconciliation_required"] is True
    assert "secret" not in json.dumps(output)
    assert engine.disposed is True


def test_runner_scope_has_no_network_migration_task252_or_raw_output_surface() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "httpx",
        "requests",
        "alembic.command",
        "upgrade(",
        "downgrade(",
        "cbr_legal_issuer_bridge",
        "identity_snapshot=identity",
        "raw_value_text\":",
    ):
        assert forbidden not in source
    assert "identity_snapshot=None" in source
    assert "hmac.compare_digest" in source
    assert "SET TRANSACTION READ ONLY" in source
    assert "SHOW transaction_read_only" in source
