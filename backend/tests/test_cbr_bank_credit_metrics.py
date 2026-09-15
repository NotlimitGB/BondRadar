from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Numeric, create_engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_CREDIT_METRIC_CONTRACT_VERSION,
    CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
    CbrBankCreditMetric,
    CbrBankNormalizedObservation,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence.credit_metrics import (
    FORM_123_METRIC_KEYS,
    FORM_135_METRIC_KEYS,
    CbrBankCreditMetricStore,
    CreditMetricFamily,
    CreditMetricSemanticCollision,
    UnsupportedMetricSource,
    project_credit_metric,
)


REPORT_DATE = date(2026, 8, 1)
OBSERVED_AT = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


def _normalized(
    *,
    normalized_id: int = 1,
    form: str = "0409123",
    source_code: str = "000",
    value: Decimal | None = Decimal("123000"),
    value_state: str = "VALUE",
) -> CbrBankNormalizedObservation:
    monetary = form != "0409135"
    return CbrBankNormalizedObservation(
        id=normalized_id,
        contract_version=CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
        raw_observation_id=normalized_id,
        form=form,
        report_date=REPORT_DATE,
        subject_regn="1",
        source_date=None,
        source_code=source_code,
        source_subcode=None,
        source_dimensions=[],
        source_disclosure_state=(
            "PUBLIC_VALUE" if value_state == "VALUE" else "PUBLIC_VALUE_BLANK"
        ),
        value_state=value_state,
        normalized_value=value,
        normalized_unit="RUB" if monetary else "PERCENT",
        normalized_currency="RUB" if monetary else None,
        transformation_kind=(
            "SCALE_BY_SOURCE_MULTIPLIER" if monetary else "IDENTITY"
        ),
        applied_multiplier=1000 if monetary else None,
        item_fingerprint=f"{normalized_id % 10}" * 64,
        normalization_fingerprint=f"{(normalized_id + 1) % 10}" * 64,
    )


@pytest.mark.parametrize(
    ("form", "source_code", "metric_key", "family", "unit"),
    [
        *[
            ("0409123", code, key, CreditMetricFamily.REGULATORY_CAPITAL, "RUB")
            for code, key in FORM_123_METRIC_KEYS.items()
        ],
        *[
            ("0409135", code, key, CreditMetricFamily.REGULATORY_RATIO, "PERCENT")
            for code, key in FORM_135_METRIC_KEYS.items()
        ],
    ],
)
def test_reviewed_mapping_copies_exact_decimal_without_recalculation(
    form: str,
    source_code: str,
    metric_key: str,
    family: CreditMetricFamily,
    unit: str,
) -> None:
    value = Decimal("-12.340") if form == "0409135" else Decimal("123000.00")
    draft = project_credit_metric(
        _normalized(form=form, source_code=source_code, value=value)
    )
    assert draft is not None
    assert draft.metric_key == metric_key
    assert draft.metric_family == family
    assert draft.metric_value == value
    assert draft.metric_value.as_tuple().exponent == value.as_tuple().exponent
    assert draft.metric_unit == unit
    assert isinstance(draft.metric_value, Decimal)
    assert len(draft.metric_fingerprint) == 64


def test_unavailable_is_not_zero_and_irrelevant_forms_are_not_projected() -> None:
    unavailable = _normalized(
        source_code="102",
        value=None,
        value_state="SOURCE_VALUE_UNAVAILABLE",
    )
    assert project_credit_metric(unavailable) is None
    assert project_credit_metric(_normalized(form="0409101", source_code="1")) is None
    assert project_credit_metric(_normalized(form="0409102", source_code="1")) is None


def test_n18_percent_decimal_is_copied_without_rescaling() -> None:
    draft = project_credit_metric(
        _normalized(
            form="0409135",
            source_code="N18",
            value=Decimal("123.456"),
        )
    )
    assert draft is not None
    assert draft.metric_key == "CBR_135_N18"
    assert draft.metric_family is CreditMetricFamily.REGULATORY_RATIO
    assert draft.metric_unit == "PERCENT"
    assert draft.metric_value == Decimal("123.456")
    assert draft.metric_value.as_tuple().exponent == -3


def test_unknown_public_code_and_invalid_semantics_fail_closed() -> None:
    with pytest.raises(UnsupportedMetricSource):
        project_credit_metric(_normalized(source_code="999"))
    with pytest.raises(UnsupportedMetricSource):
        project_credit_metric(
            _normalized(form="0409135", source_code="N999", value=Decimal("1"))
        )

    malformed = _normalized(source_code="000")
    malformed.normalized_value = 1.0
    with pytest.raises(UnsupportedMetricSource):
        project_credit_metric(malformed)


def _seed_lineage(session: Session) -> tuple[CbrBankNormalizedObservation, ...]:
    subject = CbrBankReportingSubject(
        subject_regn="1", first_observed_at=OBSERVED_AT, last_observed_at=OBSERVED_AT
    )
    artifact = CbrBankSourceArtifact(
        source_url="https://www.cbr.ru/task262.rar",
        artifact_filename="task262.rar",
        form="0409123",
        report_date=REPORT_DATE,
        content_bytes=b"x",
        content_sha256="a" * 64,
        compressed_size=1,
        content_type="application/octet-stream",
        first_discovered_at=OBSERVED_AT,
        first_retrieved_at=OBSERVED_AT,
        ingested_at=OBSERVED_AT,
        parser_contract_version="task251-test",
        archive_runtime_contract="test",
        artifact_fingerprint="b" * 64,
    )
    session.add_all((subject, artifact))
    session.flush()
    snapshot = CbrBankReportSnapshot(
        artifact_id=artifact.id,
        form="0409123",
        report_date=REPORT_DATE,
        value_member_name="VALUE.DBF",
        member_schema_inventory=[],
        form_schema_fingerprint="c" * 64,
        parser_contract_version="task251-test",
        observed_at=OBSERVED_AT,
        retrieved_at=OBSERVED_AT,
        ingested_at=OBSERVED_AT,
        publication_status="UNKNOWN",
        publication_at=None,
        record_count=2,
        subject_count=1,
        subject_set_sha256="d" * 64,
        observation_set_sha256="e" * 64,
        snapshot_fingerprint="f" * 64,
    )
    session.add(snapshot)
    session.flush()
    result = []
    for index, code in enumerate(("000", "102"), start=1):
        raw = CbrBankRawObservation(
            snapshot_id=snapshot.id,
            reporting_subject_id=subject.id,
            form="0409123",
            report_date=REPORT_DATE,
            subject_regn="1",
            archive_member_name="VALUE.DBF",
            source_row_number=index,
            source_row_fingerprint=str(index) * 64,
            source_value_field="C3",
            source_code=code,
            source_subcode=None,
            source_dimensions=[["C1", code]],
            source_fields_sha256=str(index + 2) * 64,
            raw_value_text=f"{index}.000",
            parsed_decimal_value=Decimal(f"{index}.000"),
            disclosure_state="PUBLIC_VALUE",
            source_unit="RUB_THOUSANDS",
            source_currency="RUB",
            source_multiplier=1000,
            source_date=None,
            parser_contract_version="task251-test",
            ingested_at=OBSERVED_AT,
            observation_fingerprint=str(index + 4) * 64,
        )
        session.add(raw)
        session.flush()
        normalized = _normalized(
            normalized_id=raw.id,
            source_code=code,
            value=Decimal(index * 1000),
        )
        normalized.id = None
        normalized.raw_observation_id = raw.id
        session.add(normalized)
        session.flush()
        result.append(normalized)
    return tuple(result)


def test_model_constraints_relationship_store_idempotency_and_collision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    table = CbrBankCreditMetric.__table__
    assert isinstance(table.c.metric_value.type, Numeric)
    assert table.c.metric_value.type.asdecimal is True
    assert table.c.normalized_observation_id.unique is None
    inspector = inspect(engine)
    assert inspector.get_foreign_keys(table.name)[0]["options"]["ondelete"] == "RESTRICT"
    assert {item["name"] for item in inspector.get_indexes(table.name)} == {
        "ix_cbr_bank_credit_metrics_key_report",
        "ix_cbr_bank_credit_metrics_subject_report",
    }
    checks = {
        item["name"]: item["sqltext"]
        for item in inspector.get_check_constraints(table.name)
    }
    expected_check_suffixes = {
        "contract_valid",
        "family_valid",
        "fingerprint_valid",
        "identity_present",
        "mapping_valid",
        "regn_canonical",
    }
    assert all(
        any(name.endswith(suffix) for name in checks)
        for suffix in expected_check_suffixes
    )
    mapping_sql = next(
        sqltext for name, sqltext in checks.items() if name.endswith("mapping_valid")
    )
    for source_code, metric_key in {
        **FORM_123_METRIC_KEYS,
        **FORM_135_METRIC_KEYS,
    }.items():
        assert source_code in mapping_sql
        assert metric_key in mapping_sql

    with Session(engine) as session, session.begin():
        normalized = _seed_lineage(session)
        drafts = tuple(project_credit_metric(row) for row in normalized)
        assert all(draft is not None for draft in drafts)
        store = CbrBankCreditMetricStore(session)
        first = store.persist(drafts)
        assert (first.inserted, first.reused) == (2, 0)
        second = store.persist(drafts)
        assert (second.inserted, second.reused) == (0, 2)
        assert normalized[0].credit_metric.normalized_observation is normalized[0]
        with pytest.raises(CreditMetricSemanticCollision):
            store.persist((replace(drafts[0], metric_value=Decimal("999")),))
        assert session.scalar(select(CbrBankCreditMetric.metric_value)) == Decimal(
            "1000"
        )
    engine.dispose()


def test_store_flushes_but_caller_rollback_owns_transaction() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    transaction = session.begin()
    normalized = _seed_lineage(session)
    CbrBankCreditMetricStore(session).persist(
        tuple(project_credit_metric(row) for row in normalized)
    )
    assert session.query(CbrBankCreditMetric).count() == 2
    transaction.rollback()
    session.close()
    with Session(engine) as verify:
        assert verify.query(CbrBankCreditMetric).count() == 0
    engine.dispose()


def test_distinct_normalized_lineage_versions_remain_distinct_metrics() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        versions = (
            _normalized(normalized_id=1, source_code="000", value=Decimal("1000")),
            _normalized(normalized_id=2, source_code="000", value=Decimal("1000")),
        )
        session.add_all(versions)
        session.flush()
        drafts = tuple(project_credit_metric(row) for row in versions)
        result = CbrBankCreditMetricStore(session).persist(drafts)
        assert (result.inserted, result.reused) == (2, 0)
        metrics = session.scalars(
            select(CbrBankCreditMetric).order_by(
                CbrBankCreditMetric.normalized_observation_id
            )
        ).all()
        assert [row.normalized_observation_id for row in metrics] == [1, 2]
        assert {row.metric_key for row in metrics} == {"CBR_123_000"}
        assert len({row.metric_fingerprint for row in metrics}) == 2
    engine.dispose()


def test_concurrent_identical_insert_is_reloaded_and_reused(monkeypatch) -> None:
    normalized = _normalized(normalized_id=7, source_code="000")
    draft = project_credit_metric(normalized)
    assert draft is not None
    raced = CbrBankCreditMetric(
        **{
            "contract_version": CBR_BANK_CREDIT_METRIC_CONTRACT_VERSION,
            "normalized_observation_id": draft.normalized_observation_id,
            "subject_regn": draft.subject_regn,
            "report_date": draft.report_date,
            "metric_key": draft.metric_key,
            "metric_family": draft.metric_family.value,
            "source_form": draft.source_form,
            "source_code": draft.source_code,
            "metric_value": draft.metric_value,
            "metric_unit": draft.metric_unit,
            "metric_fingerprint": draft.metric_fingerprint,
        }
    )

    class Result:
        def __init__(self, rows) -> None:
            self.rows = rows

        def scalars(self):
            return iter(self.rows)

    class Binding:
        class Dialect:
            name = "postgresql"

        dialect = Dialect()

    class FakeSession:
        def __init__(self) -> None:
            self.calls = 0
            self.no_autoflush = nullcontext()

        def get_bind(self):
            return Binding()

        def execute(self, statement):
            del statement
            self.calls += 1
            return Result([] if self.calls == 1 else [raced])

        def add_all(self, rows) -> None:
            assert len(rows) == 1

        def flush(self) -> None:
            pass

    class RacingSavepoint:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback
            raise IntegrityError("insert", {}, RuntimeError("race"))

    session = FakeSession()
    store = CbrBankCreditMetricStore(session)  # type: ignore[arg-type]
    monkeypatch.setattr(store, "_savepoint", lambda: RacingSavepoint())
    result = store.persist((draft,))
    assert (result.inserted, result.reused) == (0, 1)


def test_contract_constant_is_exact() -> None:
    assert CBR_BANK_CREDIT_METRIC_CONTRACT_VERSION == "cbr-bank-credit-metric-v1"
