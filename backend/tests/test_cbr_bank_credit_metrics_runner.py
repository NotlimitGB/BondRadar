from __future__ import annotations

from datetime import date
from decimal import Decimal
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.cbr_bank_financial_evidence import (
    CbrBankCreditMetric,
    CbrBankNormalizedObservation,
)
from app.services.cbr_bank_financial_evidence import credit_metrics_runner as runner
from app.services.cbr_bank_financial_evidence.credit_metrics import (
    CbrBankCreditMetricStore,
)


REPORT_DATE = date(2026, 8, 1)


@pytest.fixture()
def metrics_engine():
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


def _common() -> dict:
    return {
        "schema_reader": _schema_reader(),
        "read_only_enforcer": _no_read_only,
        "allow_non_postgresql": True,
    }


def _normalized(
    index: int,
    *,
    form: str,
    source_code: str,
    value: Decimal | None,
) -> CbrBankNormalizedObservation:
    monetary = form != "0409135"
    available = value is not None
    return CbrBankNormalizedObservation(
        raw_observation_id=index,
        form=form,
        report_date=REPORT_DATE,
        subject_regn="1",
        source_date=None,
        source_code=source_code,
        source_subcode=None,
        source_dimensions=[],
        source_disclosure_state=("PUBLIC_VALUE" if available else "PUBLIC_VALUE_BLANK"),
        value_state="VALUE" if available else "SOURCE_VALUE_UNAVAILABLE",
        normalized_value=value,
        normalized_unit="RUB" if monetary else "PERCENT",
        normalized_currency="RUB" if monetary else None,
        transformation_kind=(
            "SCALE_BY_SOURCE_MULTIPLIER" if monetary else "IDENTITY"
        ),
        applied_multiplier=1000 if monetary else None,
        item_fingerprint=f"{index % 10}" * 64,
        normalization_fingerprint=f"{(index + 1) % 10}" * 64,
    )


def _seed(engine, *, include_unavailable: bool = True, include_irrelevant: bool = True):
    rows = [
        _normalized(1, form="0409123", source_code="000", value=Decimal("1000")),
        _normalized(2, form="0409135", source_code="N15.1", value=Decimal("12.345")),
    ]
    if include_unavailable:
        rows.append(_normalized(3, form="0409123", source_code="102", value=None))
    if include_irrelevant:
        rows.append(_normalized(4, form="0409101", source_code="1", value=Decimal("1")))
    with Session(engine) as session, session.begin():
        session.add_all(rows)


def test_plan_preflight_apply_and_replanned_retry(metrics_engine) -> None:
    _seed(metrics_engine)
    plan = runner.execute_plan(metrics_engine, **_common())
    assert plan["status"] == "complete"
    assert plan["ready"] is True
    assert plan["normalized_rows_in_scope"] == 3
    assert plan["supported_value_rows"] == 2
    assert plan["unavailable_supported_rows"] == 1
    assert plan["insert_candidate_rows"] == 2
    assert plan["rows_by_form"] == {"0409123": 2, "0409135": 1}
    assert plan["rows_by_metric_key"]["CBR_123_000"] == 1
    assert plan["rows_by_metric_key"]["CBR_135_N15_1"] == 1
    assert set(plan["rows_by_metric_key"]) == set(runner.ALL_METRIC_KEYS)
    assert runner.execute_plan(metrics_engine, **_common())["plan_hash"] == plan[
        "plan_hash"
    ]

    preflight = runner.execute_preflight(
        metrics_engine,
        through_id=plan["through_normalized_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        **_common(),
    )
    assert preflight["transaction_read_only"] is True
    with pytest.raises(runner.CreditMetricsRunnerError, match="PLAN_HASH_MISMATCH"):
        runner.execute_preflight(
            metrics_engine,
            through_id=plan["through_normalized_observation_id"],
            expected_plan_hash="f" * 64,
            **_common(),
        )
    result = runner.execute_apply(
        metrics_engine,
        through_id=plan["through_normalized_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        **_common(),
    )
    assert result["inserted_rows"] == 2
    assert result["post_credit_metric_rows"] == 2
    assert result["production_actions"] == "CBR_BANK_CREDIT_METRICS_APPLY"

    continuation = runner.execute_plan(metrics_engine, **_common())
    assert continuation["plan_hash"] != plan["plan_hash"]
    retry = runner.execute_apply(
        metrics_engine,
        through_id=continuation["through_normalized_observation_id"],
        expected_plan_hash=continuation["plan_hash"],
        **_common(),
    )
    assert retry["inserted_rows"] == 0
    assert retry["reused_rows"] == 2
    assert retry["production_actions"] == "NONE"


def test_unknown_public_code_blocks_preflight_without_mutation(metrics_engine) -> None:
    with Session(metrics_engine) as session, session.begin():
        session.add(
            _normalized(1, form="0409123", source_code="999", value=Decimal("1"))
        )
    plan = runner.execute_plan(metrics_engine, **_common())
    assert plan["ready"] is False
    assert plan["unsupported_metric_source_rows"] == 1
    assert plan["unsupported_metric_sources"] == [
        {"form": "0409123", "source_code": "999", "count": 1}
    ]
    with pytest.raises(runner.CreditMetricsRunnerError, match="UNSUPPORTED_METRIC_SOURCE"):
        runner.execute_preflight(
            metrics_engine,
            through_id=plan["through_normalized_observation_id"],
            expected_plan_hash=plan["plan_hash"],
            **_common(),
        )
    with Session(metrics_engine) as session:
        assert session.scalar(select(func.count()).select_from(CbrBankCreditMetric)) == 0


def test_schema_guards_and_read_only_order_fail_closed(metrics_engine) -> None:
    _seed(metrics_engine, include_unavailable=False, include_irrelevant=False)
    events: list[str] = []

    def read_only(session: Session) -> None:
        del session
        events.extend(("SET TRANSACTION READ ONLY", "SHOW transaction_read_only"))

    def schema(session: Session):
        del session
        events.append("SELECT schema")
        return _schema_state()

    runner.execute_plan(
        metrics_engine,
        schema_reader=schema,
        read_only_enforcer=read_only,
        allow_non_postgresql=True,
    )
    assert events[:3] == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
        "SELECT schema",
    ]
    for revisions in (
        ("202609140001",),
        ("209901010001",),
        (),
        (runner.EXPECTED_ALEMBIC_REVISION, "209901010001"),
    ):
        with pytest.raises(runner.CreditMetricsRunnerError, match="ALEMBIC_REVISION_MISMATCH"):
            runner.execute_plan(
                metrics_engine,
                schema_reader=lambda session, values=revisions: runner._SchemaState(
                    revisions=values, tables=frozenset(runner._REQUIRED_TABLES)
                ),
                read_only_enforcer=_no_read_only,
                allow_non_postgresql=True,
            )
    with pytest.raises(runner.CreditMetricsRunnerError, match="CREDIT_METRIC_SCHEMA_MISSING"):
        runner.execute_plan(
            metrics_engine,
            schema_reader=lambda session: runner._SchemaState(
                revisions=(runner.EXPECTED_ALEMBIC_REVISION,),
                tables=frozenset({"alembic_version"}),
            ),
            read_only_enforcer=_no_read_only,
            allow_non_postgresql=True,
        )
    def unreadable(session: Session):
        del session
        raise RuntimeError("unreadable")

    with pytest.raises(RuntimeError, match="unreadable"):
        runner.execute_plan(
            metrics_engine,
            schema_reader=unreadable,
            read_only_enforcer=_no_read_only,
            allow_non_postgresql=True,
        )


def test_partial_and_uncertain_commit_truth(metrics_engine, monkeypatch) -> None:
    _seed(metrics_engine, include_unavailable=False, include_irrelevant=False)
    plan = runner.execute_plan(metrics_engine, **_common())
    monkeypatch.setattr(runner, "BATCH_SIZE", 1)
    calls = 0

    class FailingStore:
        def __init__(self, session: Session) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("secret")
            self.delegate = CbrBankCreditMetricStore(session)

        def persist(self, drafts):
            return self.delegate.persist(drafts)

    partial = runner.execute_apply(
        metrics_engine,
        through_id=plan["through_normalized_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        store_factory=FailingStore,
        **_common(),
    )
    assert partial["partial_apply"] is True
    assert partial["inserted_rows"] == 1
    assert partial["reused_rows"] == 0
    assert partial["post_credit_metric_rows"] is None
    assert partial["database_mutation_executed"] is True
    assert partial["reconciliation_required"] is True
    assert partial["production_actions"] == "CBR_BANK_CREDIT_METRICS_APPLY"

    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    _seed(engine, include_unavailable=False, include_irrelevant=False)
    plan = runner.execute_plan(engine, **_common())

    def uncertain(transaction) -> None:
        del transaction
        raise RuntimeError("uncertain")

    unknown = runner.execute_apply(
        engine,
        through_id=plan["through_normalized_observation_id"],
        expected_plan_hash=plan["plan_hash"],
        commit_transaction=uncertain,
        **_common(),
    )
    assert unknown["commit_outcome_unknown"] is True
    assert unknown["inserted_rows"] == 0
    assert unknown["reused_rows"] == 0
    assert unknown["post_credit_metric_rows"] is None
    assert unknown["database_mutation_executed"] is None
    assert unknown["production_actions"] == (
        "CBR_BANK_CREDIT_METRICS_APPLY_OUTCOME_UNKNOWN"
    )
    engine.dispose()


def test_cli_failures_are_sanitized_and_common_fields_are_stable(capsys) -> None:
    created = False

    def forbidden_engine(*args, **kwargs):
        nonlocal created
        created = True
        raise AssertionError

    assert runner.main([], engine_factory=forbidden_engine) == 2
    invalid = json.loads(capsys.readouterr().out)
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
    failed_text = capsys.readouterr().out
    assert "SECRET_DB" not in failed_text
    assert "secret-not-a-url" not in failed_text
    failed = json.loads(failed_text)
    assert failed["error_code"] == "DATABASE_CONFIGURATION_INVALID"
    assert failed["mode"] == "plan"
    assert failed["production_actions"] == "NONE"
    assert created is False


def test_runner_has_no_network_artifact_pit_or_scoring_surface() -> None:
    text_value = open(runner.__file__, encoding="utf-8").read()
    for forbidden in ("httpx", "requests", "cbr.ru", "rarfile", "dbfread", "safe_known_from"):
        assert forbidden not in text_value
    assert runner.BATCH_SIZE == 2_000
    assert runner.EXPECTED_ALEMBIC_REVISION == "202609150001"
