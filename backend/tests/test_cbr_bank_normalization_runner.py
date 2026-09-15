from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.cbr_bank_financial_evidence import (
    CbrBankNormalizedObservation,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence import normalization_runner as runner
from app.services.cbr_bank_financial_evidence.normalization import (
    CbrBankNormalizedObservationStore,
)


T1 = datetime(2026, 9, 14, 12, tzinfo=timezone.utc)


@pytest.fixture()
def normalization_engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def _schema_state(revision: str = runner.EXPECTED_ALEMBIC_REVISION):
    return runner._SchemaState(
        revisions=(revision,), tables=frozenset(runner._REQUIRED_TABLES)
    )


def _schema_reader(revision: str = runner.EXPECTED_ALEMBIC_REVISION):
    return lambda session: _schema_state(revision)


def _no_read_only(session: Session) -> None:
    del session


def _seed(engine, *, rows: int = 2) -> None:
    with Session(engine) as session, session.begin():
        subject = CbrBankReportingSubject(
            subject_regn="1", first_observed_at=T1, last_observed_at=T1
        )
        artifact = CbrBankSourceArtifact(
            source_url="https://www.cbr.ru/test.rar",
            artifact_filename="test.rar",
            form="0409102",
            report_date=date(2026, 8, 1),
            content_bytes=b"x",
            content_sha256="a" * 64,
            compressed_size=1,
            content_type="application/octet-stream",
            first_discovered_at=T1,
            first_retrieved_at=T1,
            ingested_at=T1,
            parser_contract_version="task251-test",
            archive_runtime_contract="test",
            artifact_fingerprint="b" * 64,
        )
        session.add_all((subject, artifact))
        session.flush()
        snapshot = CbrBankReportSnapshot(
            artifact_id=artifact.id,
            form="0409102",
            report_date=date(2026, 8, 1),
            value_member_name="VALUE.DBF",
            member_schema_inventory=[],
            form_schema_fingerprint="c" * 64,
            parser_contract_version="task251-test",
            observed_at=T1,
            retrieved_at=T1,
            ingested_at=T1,
            publication_status="UNKNOWN",
            publication_at=None,
            record_count=rows,
            subject_count=1,
            subject_set_sha256="d" * 64,
            observation_set_sha256="e" * 64,
            snapshot_fingerprint="f" * 64,
        )
        session.add(snapshot)
        session.flush()
        for index in range(1, rows + 1):
            session.add(
                CbrBankRawObservation(
                    snapshot_id=snapshot.id,
                    reporting_subject_id=subject.id,
                    form="0409102",
                    report_date=date(2026, 8, 1),
                    subject_regn="1",
                    archive_member_name="VALUE.DBF",
                    source_row_number=index,
                    source_row_fingerprint=str(index % 10) * 64,
                    source_value_field="SIM_ITOGO",
                    source_code=str(index),
                    source_subcode=None,
                    source_dimensions=[["CODE", str(index)]],
                    source_fields_sha256="1" * 64,
                    raw_value_text=f"{index}.250",
                    parsed_decimal_value=Decimal(f"{index}.250"),
                    disclosure_state="PUBLIC_VALUE",
                    source_unit="RUB_THOUSANDS",
                    source_currency="RUB",
                    source_multiplier=1000,
                    source_date=None,
                    parser_contract_version="task251-test",
                    ingested_at=T1,
                    observation_fingerprint=f"{index + 1:x}" * 64,
                )
            )


def _common() -> dict:
    return {
        "schema_reader": _schema_reader(),
        "read_only_enforcer": _no_read_only,
        "allow_non_postgresql": True,
    }


def test_plan_preflight_apply_and_replanned_retry_are_deterministic(
    normalization_engine,
) -> None:
    _seed(normalization_engine)
    plan = runner.execute_plan(normalization_engine, **_common())
    assert plan["status"] == "complete"
    assert plan["ready"] is True
    assert plan["raw_rows_in_scope"] == 2
    assert plan["insert_candidate_rows"] == 2
    assert plan["already_normalized_rows"] == 0
    assert plan["rows_by_form"]["0409102"] == 2
    assert plan["database_mutation_executed"] is False
    assert runner.execute_plan(normalization_engine, **_common())["plan_hash"] == plan[
        "plan_hash"
    ]

    preflight = runner.execute_preflight(
        normalization_engine,
        through_id=plan["through_raw_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        **_common(),
    )
    assert preflight["transaction_read_only"] is True
    result = runner.execute_apply(
        normalization_engine,
        through_id=plan["through_raw_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        **_common(),
    )
    assert result["status"] == "complete"
    assert result["inserted_rows"] == 2
    assert result["production_actions"] == "CBR_BANK_NORMALIZATION_APPLY"

    continuation = runner.execute_plan(normalization_engine, **_common())
    assert continuation["plan_hash"] != plan["plan_hash"]
    assert continuation["insert_candidate_rows"] == 0
    retry = runner.execute_apply(
        normalization_engine,
        through_id=continuation["through_raw_observation_id"],
        expected_plan_hash=continuation["plan_hash"],
        **_common(),
    )
    assert retry["inserted_rows"] == 0
    assert retry["reused_rows"] == 2
    assert retry["production_actions"] == "NONE"


def test_stale_hash_and_schema_states_fail_before_mutation(normalization_engine) -> None:
    _seed(normalization_engine)
    plan = runner.execute_plan(normalization_engine, **_common())
    with pytest.raises(runner.NormalizationRunnerError, match="PLAN_HASH_MISMATCH"):
        runner.execute_preflight(
            normalization_engine,
            through_id=plan["through_raw_observation_id"],
            expected_plan_hash="0" * 64,
            **_common(),
        )
    for revisions in (
        ("202609110001",),
        ("209901010001",),
        (),
        (runner.EXPECTED_ALEMBIC_REVISION, "209901010001"),
    ):
        with pytest.raises(
            runner.NormalizationRunnerError, match="ALEMBIC_REVISION_MISMATCH"
        ):
            runner.execute_plan(
                normalization_engine,
                schema_reader=lambda session, values=revisions: runner._SchemaState(
                    revisions=values, tables=frozenset(runner._REQUIRED_TABLES)
                ),
                read_only_enforcer=_no_read_only,
                allow_non_postgresql=True,
            )
    with Session(normalization_engine) as session:
        assert session.scalar(
            select(func.count()).select_from(CbrBankNormalizedObservation)
        ) == 0


def test_read_only_guard_precedes_schema_select_and_unreadable_state_is_safe(
    normalization_engine,
) -> None:
    _seed(normalization_engine, rows=1)
    events: list[str] = []

    def read_only(session: Session) -> None:
        del session
        events.extend(("SET TRANSACTION READ ONLY", "SHOW transaction_read_only"))

    def schema(session: Session):
        del session
        events.append("SELECT schema")
        return _schema_state()

    runner.execute_plan(
        normalization_engine,
        schema_reader=schema,
        read_only_enforcer=read_only,
        allow_non_postgresql=True,
    )
    assert events[:3] == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
        "SELECT schema",
    ]

    def unreadable(session: Session):
        del session
        raise RuntimeError("secret schema failure")

    with pytest.raises(RuntimeError, match="secret schema failure"):
        runner.execute_plan(
            normalization_engine,
            schema_reader=unreadable,
            read_only_enforcer=_no_read_only,
            allow_non_postgresql=True,
        )
    with Session(normalization_engine) as session:
        assert session.scalar(
            select(func.count()).select_from(CbrBankNormalizedObservation)
        ) == 0


def test_unsupported_input_blocks_preflight(normalization_engine) -> None:
    _seed(normalization_engine, rows=1)
    with Session(normalization_engine) as session, session.begin():
        raw = session.scalar(select(CbrBankRawObservation))
        raw.source_multiplier = 1
    plan = runner.execute_plan(normalization_engine, **_common())
    assert plan["ready"] is False
    assert plan["unsupported_input_rows"] == 1
    with pytest.raises(
        runner.NormalizationRunnerError, match="UNSUPPORTED_NORMALIZATION_INPUT"
    ):
        runner.execute_preflight(
            normalization_engine,
            through_id=plan["through_raw_observation_id"],
            expected_plan_hash=plan["plan_hash"],
            **_common(),
        )


def test_partial_batch_commit_stops_and_requires_replan(
    normalization_engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(normalization_engine, rows=2)
    plan = runner.execute_plan(normalization_engine, **_common())
    monkeypatch.setattr(runner, "BATCH_SIZE", 1)
    calls = 0

    class FailingStore:
        def __init__(self, session: Session) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("secret failure")
            self.delegate = CbrBankNormalizedObservationStore(session)

        def persist(self, drafts):
            return self.delegate.persist(drafts)

    result = runner.execute_apply(
        normalization_engine,
        through_id=plan["through_raw_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        store_factory=FailingStore,
        **_common(),
    )
    assert result["status"] == "failed"
    assert result["error_code"] == "NORMALIZATION_APPLY_FAILED"
    assert result["database_mutation_executed"] is True
    assert result["partial_apply"] is True
    assert result["reconciliation_required"] is True
    assert result["production_actions"] == "CBR_BANK_NORMALIZATION_APPLY"
    assert runner.execute_plan(normalization_engine, **_common())["plan_hash"] != plan[
        "plan_hash"
    ]


def test_uncertain_first_commit_is_truthful(normalization_engine) -> None:
    _seed(normalization_engine, rows=1)
    plan = runner.execute_plan(normalization_engine, **_common())

    def uncertain(transaction) -> None:
        del transaction
        raise RuntimeError("uncertain secret")

    result = runner.execute_apply(
        normalization_engine,
        through_id=plan["through_raw_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        commit_transaction=uncertain,
        **_common(),
    )
    assert result["error_code"] == "COMMIT_OUTCOME_UNKNOWN"
    assert result["commit_outcome_unknown"] is True
    assert result["database_mutation_executed"] is None
    assert result["reconciliation_required"] is True
    assert result["production_actions"] == (
        "CBR_BANK_NORMALIZATION_APPLY_OUTCOME_UNKNOWN"
    )


def test_cli_invalid_arguments_and_secret_failures_are_sanitized(
    capsys: pytest.CaptureFixture[str],
) -> None:
    created = False

    def forbidden_engine(*args, **kwargs):
        nonlocal created
        created = True
        raise AssertionError

    assert runner.main([], engine_factory=forbidden_engine) == 2
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["error_code"] == "INVALID_ARGUMENTS"
    assert invalid["mode"] == "invalid"
    assert invalid["ready"] is False
    assert created is False

    assert runner.main(
        [
            "--mode",
            "plan",
            "--database-url-env",
            "SECRET_DB",
            "--confirm-read-only",
        ],
        environ={"SECRET_DB": "secret-not-a-url"},
        engine_factory=forbidden_engine,
    ) == 1
    failed = capsys.readouterr().out
    assert "secret-not-a-url" not in failed
    assert "SECRET_DB" not in failed
    failure = json.loads(failed)
    assert failure["error_code"] == "DATABASE_CONFIGURATION_INVALID"
    assert failure["mode"] == "plan"
    assert failure["ready"] is False
    assert created is False


def test_output_contract_common_fields_are_type_stable(normalization_engine) -> None:
    _seed(normalization_engine, rows=1)
    plan = runner.execute_plan(normalization_engine, **_common())
    preflight = runner.execute_preflight(
        normalization_engine,
        through_id=plan["through_raw_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        **_common(),
    )
    apply_result = runner.execute_apply(
        normalization_engine,
        through_id=plan["through_raw_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        **_common(),
    )
    failure = runner._failure("TEST_FAILURE", mode="preflight")
    common_keys = {
        "schema",
        "status",
        "mode",
        "error_code",
        "ready",
        "database_accessed",
        "database_mutation_executed",
        "normalization_executed",
        "network_accessed",
        "pit_ready",
        "production_actions",
    }
    for payload in (plan, preflight, apply_result, failure):
        assert common_keys <= payload.keys()
        assert isinstance(payload["schema"], str)
        assert isinstance(payload["status"], str)
        assert isinstance(payload["mode"], str)
        assert payload["error_code"] is None or isinstance(payload["error_code"], str)
        assert isinstance(payload["ready"], bool)
        assert isinstance(payload["database_accessed"], bool)
        assert isinstance(payload["normalization_executed"], bool)
        assert isinstance(payload["network_accessed"], bool)
        assert isinstance(payload["pit_ready"], bool)
        assert isinstance(payload["production_actions"], str)


def test_runner_has_no_network_or_pit_selection_surface() -> None:
    source = runner.__file__
    assert source
    text_value = open(source, encoding="utf-8").read()
    for forbidden in ("httpx", "requests", "cbr.ru", "safe_known_from"):
        assert forbidden not in text_value
    assert runner.BATCH_SIZE == 2_000
    assert runner.EXPECTED_ALEMBIC_REVISION == "202609140001"
