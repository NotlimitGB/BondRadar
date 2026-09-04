from __future__ import annotations

import hashlib
import inspect
import json
import copy
import zlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.services.cbr_bank_financial_evidence import historical_backfill_runner as runner
from app.services.cbr_bank_financial_evidence import monthly_runner
from app.services.cbr_bank_financial_evidence import production_runner
from app.services.cbr_bank_financial_evidence import lexical, store as store_module
from app.services.cbr_bank_reporting import bundle as bundle_module
from app.services.cbr_bank_financial_evidence.monthly_runner import (
    MonthlyArtifactManifest,
    MonthlyIngestionManifest,
)
from app.services.cbr_bank_financial_evidence.store import (
    CbrBankRawFinancialEvidenceStore,
)
from app.services.cbr_bank_reporting import archive as reporting_archive
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
)


T1 = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(days=1)
DATE_1 = date(2026, 6, 1)
DATE_2 = date(2026, 7, 1)
DATE_3 = date(2026, 8, 1)
FIXTURES = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"


def _reference(form: CbrBankForm, report_date: date) -> CbrArtifactReference:
    filename = f"{form.short_code}-{report_date:%Y%m%d}.rar"
    href = f"/vfs/credit/forms/{filename}"
    return CbrArtifactReference(
        form=form,
        source_href=href,
        source_url=f"https://www.cbr.ru{href}",
        artifact_filename=filename,
        report_date=report_date,
        discovered_at=T1,
    )


def _artifact(form: CbrBankForm, report_date: date) -> CbrBankArtifact:
    content = f"{form.value}:{report_date.isoformat()}".encode("ascii")
    return CbrBankArtifact(
        reference=_reference(form, report_date),
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        compressed_size=len(content),
        content_type="application/vnd.rar",
        retrieved_at=T1,
    )


def _fake_prepared(
    report_date: date,
    observed_at: datetime,
    artifacts: tuple[CbrBankArtifact, ...],
):
    artifact_manifests = tuple(
        MonthlyArtifactManifest(
            form=item.reference.form.value,
            source_href=item.reference.source_href,
            source_url=item.reference.source_url,
            artifact_filename=item.reference.artifact_filename,
            artifact_size=item.compressed_size,
            artifact_sha256=item.content_sha256,
            content_type=item.content_type or "application/octet-stream",
            record_count=10,
            subject_count=2,
            subject_set_sha256=hashlib.sha256(
                f"subjects:{item.reference.form.value}".encode()
            ).hexdigest(),
            form_schema_fingerprint=hashlib.sha256(
                f"form:{item.reference.form.value}".encode()
            ).hexdigest(),
            value_member_name=f"VALUE_{item.reference.form.short_code}.DBF",
            source_row_fingerprint_set_sha256=hashlib.sha256(
                f"rows:{item.reference.form.value}".encode()
            ).hexdigest(),
        )
        for item in sorted(artifacts, key=lambda value: value.reference.form.value)
    )
    manifest = MonthlyIngestionManifest.create(
        report_date=report_date,
        evidence_observed_at=observed_at,
        artifacts=artifact_manifests,
    )
    forms = []
    exact = []
    for item in artifact_manifests:
        form = CbrBankForm.parse(item.form)
        member_hash = hashlib.sha256(f"member:{item.form}".encode()).hexdigest()
        forms.append(
            SimpleNamespace(
                form=form,
                member_schema_fingerprints=((item.value_member_name, member_hash),),
            )
        )
        exact.append(SimpleNamespace(form=item.form, value_member_name=item.value_member_name))
    return SimpleNamespace(
        manifest=manifest,
        bundle=SimpleNamespace(forms=tuple(forms)),
        exact_forms=tuple(exact),
        artifacts=artifacts,
    )


def _fake_month(report_date: date, observed_at: datetime = T1) -> runner.HistoricalMonthManifest:
    artifacts = tuple(_artifact(form, report_date) for form in CbrBankForm)
    prepared = _fake_prepared(report_date, observed_at, artifacts)
    structural, value = runner._structural_projection(prepared)
    return runner.HistoricalMonthManifest(
        report_date=report_date,
        monthly_manifest=prepared.manifest,
        form_structural_schema_fingerprint_by_form=structural,
        value_member_schema_fingerprint_by_form=value,
    )


def _batch(
    month: runner.HistoricalMonthManifest,
    *,
    incomplete=(),
) -> runner.HistoricalBackfillBatchManifest:
    return runner.HistoricalBackfillBatchManifest.create(
        requested_from_date=month.report_date,
        requested_to_date=month.report_date,
        evidence_observed_at=month.monthly_manifest.evidence_observed_at,
        incomplete_report_dates=incomplete,
        months=(month,),
    )


@pytest.fixture(scope="module")
def current_prepared():
    source = production_runner._prepare_evidence(
        report_date=DATE_3, evidence_observed_at=T1
    )
    return monthly_runner.prepare_artifacts(
        report_date=DATE_3,
        evidence_observed_at=T1,
        artifacts=source.artifacts,
    )


@pytest.fixture(scope="module")
def current_month(current_prepared):
    structural, value = runner._structural_projection(current_prepared)
    return runner.HistoricalMonthManifest(
        report_date=DATE_3,
        monthly_manifest=current_prepared.manifest,
        form_structural_schema_fingerprint_by_form=structural,
        value_member_schema_fingerprint_by_form=value,
    )


def _write_current_cache(root: Path, current_prepared) -> None:
    month_root = root / DATE_3.isoformat()
    month_root.mkdir()
    for artifact in current_prepared.artifacts:
        (month_root / artifact.reference.artifact_filename).write_bytes(artifact.content)


def _sqlite_engine(tmp_path: Path, name: str):
    engine = create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")
    Base.metadata.create_all(engine)
    return engine


def _schema_state():
    return production_runner._DatabaseState(
        revisions=(runner.EXPECTED_ALEMBIC_REVISION,),
        tables=frozenset(
            {
                *production_runner._TASK255_TABLES,
                *production_runner._LEGACY_GUARD_TABLES,
            }
        ),
        counts={},
    )


def test_request_boundaries_and_monthly_defaults_remain_strict() -> None:
    assert runner.HISTORICAL_BACKFILL_MIN_REPORT_DATE == date(2023, 7, 1)
    assert runner.MAX_BACKFILL_COMPLETE_DATES == 32
    assert runner.MAX_BACKFILL_ARTIFACTS == 128
    assert reporting_archive.MAX_MEMBER_BYTES == 16 * 1024 * 1024
    assert reporting_archive.MAX_TOTAL_UNCOMPRESSED_BYTES == 64 * 1024 * 1024
    parameters = inspect.signature(monthly_runner.prepare_artifacts).parameters
    assert parameters["enforce_approved_schema"].default is True
    assert parameters["allow_dynamic_value_member"].default is False
    assert parameters["max_archive_member_bytes"].default == 16 * 1024 * 1024
    assert (
        parameters["max_archive_total_uncompressed_bytes"].default
        == 64 * 1024 * 1024
    )


def test_date_and_batch_bounds_are_fail_closed() -> None:
    with pytest.raises(runner.RunnerError):
        runner.HistoricalBackfillBatchManifest.create(
            requested_from_date=date(2023, 6, 1),
            requested_to_date=date(2023, 7, 1),
            evidence_observed_at=T1,
            incomplete_report_dates=(),
            months=(_fake_month(date(2023, 7, 1)),),
        )
    with pytest.raises(runner.RunnerError):
        runner.HistoricalBackfillBatchManifest.create(
            requested_from_date=DATE_2,
            requested_to_date=DATE_1,
            evidence_observed_at=T1,
            incomplete_report_dates=(),
            months=(_fake_month(DATE_2),),
        )
    months_32 = tuple(
        _fake_month(date(2023 + index // 12, index % 12 + 1, 1))
        for index in range(6, 38)
    )
    accepted = runner.HistoricalBackfillBatchManifest.create(
        requested_from_date=months_32[0].report_date,
        requested_to_date=months_32[-1].report_date,
        evidence_observed_at=T1,
        incomplete_report_dates=(),
        months=months_32,
    )
    assert len(accepted.complete_report_dates) == 32
    assert accepted.artifact_count == 128
    with pytest.raises(runner.RunnerError):
        runner.HistoricalBackfillBatchManifest.create(
            requested_from_date=months_32[0].report_date,
            requested_to_date=date(2026, 3, 1),
            evidence_observed_at=T1,
            incomplete_report_dates=(),
            months=(*months_32, _fake_month(date(2026, 3, 1))),
        )


def test_batch_manifest_is_deterministic_strict_and_tamper_evident() -> None:
    month = _fake_month(DATE_3)
    manifest = _batch(month)
    assert runner.HistoricalBackfillBatchManifest.from_dict(
        manifest.to_dict()
    ) == manifest
    assert _batch(month).batch_manifest_sha256 == manifest.batch_manifest_sha256
    payload = manifest.to_dict()
    tamper_paths = (
        lambda value: value["months"][0]["monthly_manifest"]["forms"][0].__setitem__(
            "artifact_sha256", "0" * 64
        ),
        lambda value: value.__setitem__(
            "incomplete_report_dates",
            [{"report_date": DATE_2.isoformat(), "missing_forms": ["0409135"]}],
        ),
        lambda value: value["months"][0][
            "form_structural_schema_fingerprint_by_form"
        ].__setitem__("0409101", "1" * 64),
        lambda value: value.__setitem__("evidence_observed_at", "2026-09-04T10:00:00Z"),
    )
    for mutate in tamper_paths:
        changed = json.loads(json.dumps(payload))
        mutate(changed)
        with pytest.raises(runner.RunnerError):
            runner.HistoricalBackfillBatchManifest.from_dict(changed)
    two_months = runner.HistoricalBackfillBatchManifest.create(
        requested_from_date=DATE_1,
        requested_to_date=DATE_3,
        evidence_observed_at=T1,
        incomplete_report_dates=(),
        months=(_fake_month(DATE_1), month),
    ).to_dict()
    two_months["months"].reverse()
    with pytest.raises(runner.RunnerError):
        runner.HistoricalBackfillBatchManifest.from_dict(two_months)


class _CatalogClient:
    def __init__(self, references):
        self.references = tuple(references)
        self.catalog_calls = 0
        self.fetch_calls = []

    def discover_catalog(self):
        self.catalog_calls += 1
        return self.references

    def fetch_discovered_artifact_historical(self, reference):
        self.fetch_calls.append((reference.report_date, reference.form))
        return _artifact(reference.form, reference.report_date)


def test_discover_records_incomplete_month_without_download_and_freezes_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    references = (
        *(_reference(form, DATE_1) for form in CbrBankForm),
        *(_reference(form, DATE_2) for form in tuple(CbrBankForm)[:3]),
        *(_reference(form, DATE_3) for form in CbrBankForm),
    )
    source = _CatalogClient(references)
    calls = []

    def prepare(**kwargs):
        calls.append(kwargs)
        return _fake_prepared(
            kwargs["report_date"], kwargs["evidence_observed_at"], kwargs["artifacts"]
        )

    monkeypatch.setattr(runner, "prepare_artifacts", prepare)
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    manifest_path = tmp_path / "batch.json"
    result = runner.discover_batch(
        requested_from_date=DATE_1,
        requested_to_date=DATE_3,
        evidence_observed_at=T1,
        artifact_dir=artifact_root,
        batch_manifest_output=manifest_path,
        client=source,
    )
    assert source.catalog_calls == 1
    assert len(source.fetch_calls) == 8
    assert all(item[0] != DATE_2 for item in source.fetch_calls)
    assert result.manifest.complete_report_dates == (DATE_1, DATE_3)
    assert result.manifest.incomplete_report_dates[0].to_dict() == {
        "report_date": DATE_2.isoformat(),
        "missing_forms": ["0409135"],
    }
    assert manifest_path.exists()
    assert all(
        kwargs["enforce_approved_schema"] is False
        and kwargs["allow_dynamic_value_member"] is True
        and kwargs["max_archive_member_bytes"]
        == reporting_archive.HISTORICAL_MAX_MEMBER_BYTES
        and kwargs["max_archive_total_uncompressed_bytes"]
        == reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
        for kwargs in calls
    )
    first = manifest_path.read_bytes()
    repeated = runner.discover_batch(
        requested_from_date=DATE_1,
        requested_to_date=DATE_3,
        evidence_observed_at=T1,
        artifact_dir=artifact_root,
        batch_manifest_output=manifest_path,
        client=_CatalogClient(references),
    )
    assert runner._manifest_bytes(repeated.manifest) == first


def test_atomic_frozen_write_reuses_exact_and_rejects_conflict(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    runner._write_frozen_atomic(target, b"one", conflict_code="CONFLICT")
    runner._write_frozen_atomic(target, b"one", conflict_code="CONFLICT")
    with pytest.raises(runner.RunnerError, match="CONFLICT"):
        runner._write_frozen_atomic(target, b"two", conflict_code="CONFLICT")
    assert target.read_bytes() == b"one"
    with pytest.raises(runner.RunnerError):
        runner._artifact_path(tmp_path, DATE_1, "../escape.rar")
    assert not tuple(tmp_path.glob(".*.tmp"))


def test_failed_discover_never_publishes_a_partial_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _CatalogClient(
        tuple(_reference(form, day) for day in (DATE_1, DATE_3) for form in CbrBankForm)
    )

    def prepare(**kwargs):
        if kwargs["report_date"] == DATE_3:
            raise runner.RunnerError("HISTORICAL_SOURCE_INVALID")
        return _fake_prepared(
            kwargs["report_date"], kwargs["evidence_observed_at"], kwargs["artifacts"]
        )

    monkeypatch.setattr(runner, "prepare_artifacts", prepare)
    root = tmp_path / "cache"
    root.mkdir()
    output = tmp_path / "batch.json"
    with pytest.raises(runner.RunnerError, match="HISTORICAL_SOURCE_INVALID"):
        runner.discover_batch(
            requested_from_date=DATE_1,
            requested_to_date=DATE_3,
            evidence_observed_at=T1,
            artifact_dir=root,
            batch_manifest_output=output,
            client=source,
        )
    assert not output.exists()
    assert len(tuple((root / DATE_1.isoformat()).glob("*.rar"))) == 4


def test_offline_plan_reproduces_real_fixtures_without_network_or_database(
    tmp_path: Path, current_prepared, current_month
) -> None:
    artifact_root = tmp_path / "cache"
    artifact_root.mkdir()
    _write_current_cache(artifact_root, current_prepared)
    manifest = _batch(current_month)
    prepared = runner.prepare_batch_from_manifest(
        manifest=manifest,
        artifact_dir=artifact_root,
    )
    assert prepared.report["artifacts"] == prepared.report["snapshots"] == 4
    assert prepared.report["observations"] == 38_842
    assert prepared.report["raw_lexical_mismatch_count"] == 0
    assert prepared.report["network_accessed"] is False
    assert prepared.report["database_accessed"] is False
    assert prepared.report["publication_status"] == "UNKNOWN"
    assert prepared.report["publication_at"] is None
    assert prepared.report["historical_availability_proven"] is False
    assert prepared.report["pit_ready"] is False
    first = current_month.monthly_manifest.artifacts[0]
    path = artifact_root / DATE_3.isoformat() / first.artifact_filename
    path.write_bytes(path.read_bytes() + b"mutation")
    with pytest.raises(runner.RunnerError, match="ARTIFACT_IDENTITY_MISMATCH"):
        runner.prepare_batch_from_manifest(manifest=manifest, artifact_dir=artifact_root)


def test_preflight_insert_candidate_and_read_only_order(
    tmp_path: Path, current_month, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = _sqlite_engine(tmp_path, "empty.db")
    prepared = runner.PreparedHistoricalBatch(
        manifest=_batch(current_month), report=runner._batch_report(_batch(current_month), mode="plan")
    )
    events = []
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda connection, cursor, statement, parameters, context, executemany: statements.append(statement),
    )
    monkeypatch.setattr(Session, "flush", lambda *args, **kwargs: pytest.fail("flush called"))
    monkeypatch.setattr(Session, "commit", lambda *args, **kwargs: pytest.fail("commit called"))

    def read_only(_session):
        events.extend(("SET TRANSACTION READ ONLY", "SHOW transaction_read_only"))

    def schema(_session):
        events.append("SELECT schema")
        return _schema_state()

    report = runner.execute_preflight(
        engine,
        prepared=prepared,
        schema_reader=schema,
        allow_non_postgresql=True,
        read_only_enforcer=read_only,
    )
    assert events[:3] == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
        "SELECT schema",
    ]
    assert report["months"][0]["backfill_action"] == "INSERT_CANDIDATE"
    assert report["candidate_artifacts"] == report["candidate_snapshots"] == 4
    assert report["candidate_observations"] == 38_842
    assert report["transaction_rolled_back"] is True
    assert report["database_mutation_executed"] is False
    assert statements and all(item.lstrip().upper().startswith("SELECT") for item in statements)
    engine.dispose()


def _persist_current(engine, current_prepared, observed_at: datetime) -> None:
    with Session(engine) as session:
        CbrBankRawFinancialEvidenceStore(session).persist_bundle(
            current_prepared.bundle,
            observed_at=observed_at,
            ingested_at=observed_at,
            publication_status="UNKNOWN",
            publication_at=None,
            identity_snapshot=None,
        )
        session.commit()


def _month_at(current_month, observed_at: datetime):
    monthly = MonthlyIngestionManifest.create(
        report_date=current_month.report_date,
        evidence_observed_at=observed_at,
        artifacts=current_month.monthly_manifest.artifacts,
    )
    return replace(current_month, monthly_manifest=monthly)


@pytest.fixture(scope="module")
def case_only_engine(tmp_path_factory, current_prepared):
    engine = _sqlite_engine(tmp_path_factory.mktemp("case-only"), "evidence.db")
    _persist_current(engine, current_prepared, T1)
    table = production_runner.CbrBankReportSnapshot.__table__
    with engine.begin() as connection:
        for row in connection.execute(select(table.c.id, table.c.value_member_name)):
            connection.execute(
                table.update().where(table.c.id == row.id).values(
                    value_member_name=row.value_member_name.upper()
                )
            )
    yield engine
    engine.dispose()


@pytest.fixture
def case_only_session(case_only_engine):
    with case_only_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, autoflush=False) as session:
            try:
                yield session
            finally:
                transaction.rollback()


@pytest.fixture
def case_only_month(current_month):
    monthly = MonthlyIngestionManifest.create(
        report_date=current_month.report_date,
        evidence_observed_at=T2,
        artifacts=tuple(
            replace(item, value_member_name=item.value_member_name[:-4] + ".dbf")
            for item in current_month.monthly_manifest.artifacts
        ),
    )
    return replace(current_month, monthly_manifest=monthly)


def test_case_only_reobservation_preserves_provenance_and_full_lineage(
    case_only_session, case_only_month
) -> None:
    session = case_only_session
    table = production_runner.CbrBankReportSnapshot.__table__
    before = list(session.execute(select(table).order_by(table.c.id)))
    manifest_before = _batch(case_only_month).to_dict()
    expected = {
        item.form: item.value_member_name
        for item in case_only_month.monthly_manifest.artifacts
    }
    assert len(before) == 4
    for row in before:
        assert row.value_member_name.endswith(".DBF")
        assert expected[row.form].endswith(".dbf")
        assert row.value_member_name != expected[row.form]
        assert row.value_member_name.casefold() == expected[row.form].casefold()

    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    connection = session.connection()
    event.listen(connection, "before_cursor_execute", capture)
    try:
        decision = runner.classify_backfill_month(session, case_only_month)
    finally:
        event.remove(connection, "before_cursor_execute", capture)
    assert decision.backfill_action == "SKIP_EXACT_SOURCE"
    assert decision.matching_artifacts == decision.matching_snapshots == 4
    assert decision.matching_observations == 38_842
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert list(session.execute(select(table).order_by(table.c.id))) == before
    assert _batch(case_only_month).to_dict() == manifest_before
    # This task must not relax same-observation monthly comparison.
    assert runner.classify_backfill_month(
        session, _month_at(case_only_month, T1)
    ).backfill_action == "BLOCK_PARTIAL_STATE"


@pytest.mark.parametrize("stored_name", [
    "072026B2.DBF",
    "072026_DIFFERENT.dbf",
    "foo/072026B1.DBF",
    "foo\\072026B1.DBF",
    "072026B1.DBF.bak",
    " 072026B1.DBF",
    "072026B1.DBF ",
])
def test_non_case_member_difference_blocks_reobservation(
    case_only_session, case_only_month, stored_name
) -> None:
    table = production_runner.CbrBankReportSnapshot.__table__
    case_only_session.execute(
        table.update().where(table.c.form == "0409101").values(value_member_name=stored_name)
    )
    decision = runner.classify_backfill_month(case_only_session, case_only_month)
    assert decision.backfill_action == "BLOCK_PARTIAL_STATE"


@pytest.mark.parametrize("damage", ["missing_row", "checksum_same_count"])
def test_case_only_reobservation_still_checks_raw_count_and_checksum(
    case_only_session, case_only_month, damage
) -> None:
    table = production_runner.CbrBankRawObservation.__table__
    row_id = case_only_session.scalar(select(table.c.id).order_by(table.c.id).limit(1))
    ids_before = list(case_only_session.scalars(select(table.c.id).order_by(table.c.id)))
    assert len(ids_before) == 38_842
    if damage == "missing_row":
        case_only_session.execute(table.delete().where(table.c.id == row_id))
    else:
        original = case_only_session.scalar(
            select(table.c.source_row_fingerprint).where(table.c.id == row_id)
        )
        corrupted = hashlib.sha256(b"case-only-corrupt-source-row").hexdigest()
        assert corrupted != original
        case_only_session.execute(
            table.update().where(table.c.id == row_id).values(source_row_fingerprint=corrupted)
        )
        assert list(case_only_session.scalars(select(table.c.id).order_by(table.c.id))) == ids_before
    decision = runner.classify_backfill_month(case_only_session, case_only_month)
    assert decision.backfill_action == "BLOCK_PARTIAL_STATE"


def test_database_classification_exact_month_and_exact_source(
    tmp_path: Path, current_prepared, current_month
) -> None:
    engine = _sqlite_engine(tmp_path, "exact.db")
    _persist_current(engine, current_prepared, T1)
    with Session(engine) as session:
        exact = runner.classify_backfill_month(session, current_month)
        assert exact.backfill_action == "SKIP_EXACT_MONTH"
        reobserved = runner.classify_backfill_month(session, _month_at(current_month, T2))
        assert reobserved.backfill_action == "SKIP_EXACT_SOURCE"
        assert reobserved.matching_artifacts == 4
        assert reobserved.matching_snapshots == 4
        assert reobserved.matching_observations == 38_842
        model = production_runner.CbrBankReportSnapshot
        original = session.scalar(select(model).limit(1))
        partial_values = {
            column.name: getattr(original, column.name)
            for column in model.__table__.columns
            if column.name != "id"
        }
        partial_values.update(
            observed_at=T2,
            retrieved_at=T2,
            snapshot_fingerprint=hashlib.sha256(b"partial-later-observation").hexdigest(),
        )
        session.add(model(**partial_values))
        session.commit()
        assert (
            runner.classify_backfill_month(session, current_month).backfill_action
            == "BLOCK_PARTIAL_STATE"
        )
    engine.dispose()


def test_database_classification_artifact_only_partial_and_conflict(
    tmp_path: Path, current_prepared, current_month
) -> None:
    artifact_only = _sqlite_engine(tmp_path, "artifact-only.db")
    with Session(artifact_only) as session:
        store = CbrBankRawFinancialEvidenceStore(session)
        for form in current_prepared.bundle.forms:
            store._persist_artifact(form, ingested_at=T1)
        session.commit()
        decision = runner.classify_backfill_month(session, _month_at(current_month, T2))
        assert decision.backfill_action == "BLOCK_PARTIAL_STATE"
    artifact_only.dispose()

    partial = _sqlite_engine(tmp_path, "partial.db")
    with Session(partial) as session:
        CbrBankRawFinancialEvidenceStore(session)._persist_artifact(
            current_prepared.bundle.forms[0], ingested_at=T1
        )
        session.commit()
        decision = runner.classify_backfill_month(session, current_month)
        assert decision.backfill_action == "BLOCK_PARTIAL_STATE"
    partial.dispose()

    conflict = _sqlite_engine(tmp_path, "conflict.db")
    with Session(conflict) as session:
        original = current_prepared.bundle.forms[0]
        changed_bytes = original.artifact.content + b"changed"
        changed = replace(
            original,
            artifact=replace(
                original.artifact,
                content=changed_bytes,
                content_sha256=hashlib.sha256(changed_bytes).hexdigest(),
                compressed_size=len(changed_bytes),
            ),
        )
        CbrBankRawFinancialEvidenceStore(session)._persist_artifact(changed, ingested_at=T1)
        session.commit()
        decision = runner.classify_backfill_month(session, current_month)
        assert decision.backfill_action == "BLOCK_CONFLICTING_SOURCE"
    conflict.dispose()


def test_partial_reobservation_blocks_instead_of_skipping(
    tmp_path: Path, current_prepared, current_month
) -> None:
    engine = _sqlite_engine(tmp_path, "broken-reobservation.db")
    _persist_current(engine, current_prepared, T1)
    with engine.begin() as connection:
        snapshot_id = connection.scalar(
            select(production_runner.CbrBankReportSnapshot.id).limit(1)
        )
        observation_id = connection.scalar(
            select(production_runner.CbrBankRawObservation.id)
            .where(production_runner.CbrBankRawObservation.snapshot_id == snapshot_id)
            .limit(1)
        )
        connection.execute(
            production_runner.CbrBankRawObservation.__table__.delete().where(
                production_runner.CbrBankRawObservation.id == observation_id
            )
        )
    with Session(engine) as session:
        decision = runner.classify_backfill_month(session, _month_at(current_month, T2))
        assert decision.backfill_action == "BLOCK_PARTIAL_STATE"
    engine.dispose()


class _NoEnvironment(dict):
    def get(self, key, default=None):
        raise AssertionError("PLAN read a database environment variable")


def test_cli_plan_is_offline_unconfirmed_apply_is_rejected_and_failures_are_sanitized(
    tmp_path: Path, current_prepared, current_month, capsys
) -> None:
    artifact_root = tmp_path / "cli-cache"
    artifact_root.mkdir()
    _write_current_cache(artifact_root, current_prepared)
    manifest = _batch(current_month)
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_bytes(runner._manifest_bytes(manifest))
    assert runner.main(
        [
            "--mode",
            "plan",
            "--batch-manifest",
            str(manifest_path),
            "--artifact-dir",
            str(artifact_root),
        ],
        environ=_NoEnvironment(),
        engine_factory=lambda *args, **kwargs: pytest.fail("DB engine created"),
        client_factory=lambda **kwargs: pytest.fail("network client created"),
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["network_accessed"] is output["database_accessed"] is False
    assert runner.main(["--mode", "apply"]) == 2
    failure = capsys.readouterr().out
    assert json.loads(failure)["error_code"] == "INVALID_ARGUMENTS"
    assert "password" not in failure.casefold()


def test_cli_contract_preserves_discover_plan_and_read_only_preflight() -> None:
    discover = runner._parser().parse_args(
        [
            "--mode",
            "discover",
            "--from-date",
            "2023-07-01",
            "--to-date",
            "2026-08-01",
            "--evidence-observed-at",
            "2026-09-03T10:00:00Z",
            "--batch-manifest-output",
            "batch.json",
            "--artifact-dir",
            "artifacts",
        ]
    )
    assert runner._validate_args(discover) == (date(2023, 7, 1), DATE_3)
    preflight = runner._parser().parse_args(
        [
            "--mode",
            "preflight",
            "--batch-manifest",
            "batch.json",
            "--artifact-dir",
            "artifacts",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-read-only",
        ]
    )
    assert runner._validate_args(preflight) == (None, None)
    too_early = runner._parser().parse_args(
        [
            "--mode",
            "discover",
            "--from-date",
            "2023-06-01",
            "--to-date",
            "2023-07-01",
            "--evidence-observed-at",
            "2026-09-03T10:00:00Z",
            "--batch-manifest-output",
            "batch.json",
            "--artifact-dir",
            "artifacts",
        ]
    )
    with pytest.raises(runner.RunnerError):
        runner._validate_args(too_early)


def test_preflight_offline_failure_prevents_engine_creation(tmp_path: Path, capsys) -> None:
    assert runner.main(
        [
            "--mode",
            "preflight",
            "--batch-manifest",
            str(tmp_path / "missing.json"),
            "--artifact-dir",
            str(tmp_path),
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-read-only",
        ],
        environ=_NoEnvironment(),
        engine_factory=lambda *args, **kwargs: pytest.fail("engine created before offline validation"),
    ) == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error_code"] == "BATCH_MANIFEST_UNAVAILABLE"
    assert output["publication_status"] == "UNKNOWN"
    assert output["pit_ready"] is False


def test_unknown_monthly_state_is_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        runner,
        "classify_month_database_state",
        lambda *args, **kwargs: SimpleNamespace(
            state="NEW_UNKNOWN_STATE",
            source_observation="UNKNOWN",
            matching_artifacts=0,
            matching_snapshots=0,
            matching_observations=0,
        ),
    )
    decision = runner.classify_backfill_month(None, _fake_month(DATE_3))
    assert decision.backfill_action == "BLOCK_PARTIAL_STATE"


def test_apply_reuses_store_and_keeps_network_and_uncontrolled_surfaces_absent() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    forbidden = (
        ".commit(",
        "session.flush(",
        ".add(",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
    )
    assert all(item not in source for item in forbidden)
    assert "SET TRANSACTION READ ONLY" not in source
    assert "_enforce_read_only" in source
    apply_source = inspect.getsource(runner.execute_apply)
    assert "persist_bundle" in apply_source
    assert "_commit_transaction" in apply_source
    assert all(value not in apply_source for value in ("CbrBankRegulatoryClient", "discover_catalog", "fetch_"))


def _empty_counts():
    return {name: 0 for name in production_runner._TASK255_TABLES}


def _candidate(month):
    return runner.HistoricalMonthDecision(
        month.report_date, "EMPTY", "FIRST_OBSERVATION", "INSERT_CANDIDATE", 0, 0, 0
    )


def test_apply_scope_hash_is_canonical_and_semantically_sensitive():
    months = (_fake_month(DATE_1), _fake_month(DATE_3))
    batch = runner.HistoricalBackfillBatchManifest.create(
        requested_from_date=DATE_1, requested_to_date=DATE_3,
        evidence_observed_at=T1, incomplete_report_dates=(), months=months,
    )
    decisions = [_candidate(month) for month in months]
    scope = runner.build_apply_scope(batch, decisions, _empty_counts())
    digest = runner.sha256_canonical(scope)
    reordered = runner.build_apply_scope(batch, list(reversed(decisions)), dict(reversed(list(_empty_counts().items()))))
    assert digest == runner.sha256_canonical(reordered)
    assert digest == runner.sha256_canonical(json.loads(json.dumps(scope, indent=4)))
    for field in (
        "batch_manifest_sha256", "insert_candidate_months", "skip_exact_months",
        "skip_exact_source_months", "blocked_months", "candidate_artifacts",
        "candidate_snapshots", "candidate_observations",
    ):
        changed = copy.deepcopy(scope)
        changed[field] = "0" * 64 if field == "batch_manifest_sha256" else changed[field] + 1
        assert runner.sha256_canonical(changed) != digest
    for field in (
        "monthly_state", "source_observation", "backfill_action", "matching_artifacts",
        "matching_snapshots", "matching_observations", "report_date",
    ):
        changed = copy.deepcopy(scope)
        changed["months"][0][field] = "changed" if isinstance(changed["months"][0][field], str) else 1
        assert runner.sha256_canonical(changed) != digest
    changed = copy.deepcopy(scope)
    changed["current_task255_counts"]["cbr_bank_reporting_subjects"] = 1
    assert runner.sha256_canonical(changed) != digest
    with pytest.raises(runner.RunnerError, match="UNKNOWN_DATABASE_STATE"):
        runner.build_apply_scope(batch, [replace(decisions[0], monthly_state="NEW"), decisions[1]], _empty_counts())


@pytest.fixture(scope="module")
def small_dbf_templates(current_prepared):
    # Real DBF descriptors/lexical bytes; only the test archive transport is synthetic.
    result = {}
    for form in current_prepared.bundle.forms:
        members = []
        for member, content in reporting_archive.extract_archive_members(form.artifact):
            header = int.from_bytes(content[8:10], "little")
            length = int.from_bytes(content[10:12], "little")
            rows = min(1, int.from_bytes(content[4:8], "little"))
            bounded = bytearray(content[:header + rows * length])
            bounded[4:8] = rows.to_bytes(4, "little")
            bounded.append(0x1a)
            members.append((member, bytes(bounded)))
        result[form.form] = tuple(members)
    return result


@pytest.fixture
def small_history(tmp_path, monkeypatch, small_dbf_templates):
    archive_inventory = {}
    extraction_calls = []

    def extract(artifact, **kwargs):
        extraction_calls.append(kwargs)
        assert hashlib.sha256(artifact.content).hexdigest() == artifact.content_sha256
        return archive_inventory[artifact.content_sha256]

    monkeypatch.setattr(bundle_module, "extract_archive_members", extract)
    monkeypatch.setattr(lexical, "extract_archive_members", extract)
    root = tmp_path / "historical-cache"
    root.mkdir()
    months, prepared_months = [], {}
    # Input order is intentionally not chronological; authorization/execution must be.
    for report_date in (DATE_3, DATE_1, DATE_2):
        folder = root / report_date.isoformat()
        folder.mkdir()
        artifacts = []
        for form in CbrBankForm:
            members = []
            for member, content in small_dbf_templates[form]:
                # All names change, proving store historical opt-in rather than August fallback.
                name = report_date.strftime("%Y%m") + "_" + member.name
                members.append((replace(
                    member, name=name, normalized_name=name.upper(),
                    uncompressed_size=len(content), compressed_size=len(content),
                    crc32=zlib.crc32(content) & 0xffffffff,
                ), content))
            content = report_date.isoformat().encode() + b"".join(
                member.name.encode() + payload for member, payload in members
            )
            artifact = replace(
                _artifact(form, report_date), content=content,
                compressed_size=len(content), content_sha256=hashlib.sha256(content).hexdigest(),
            )
            archive_inventory[artifact.content_sha256] = tuple(members)
            artifacts.append(artifact)
            (folder / artifact.reference.artifact_filename).write_bytes(content)
        prepared = runner._historical_prepare(
            report_date=report_date, evidence_observed_at=T1,
            artifacts=tuple(artifacts), archive_executable=None,
        )
        structural, value = runner._structural_projection(prepared)
        months.append(runner.HistoricalMonthManifest(report_date, prepared.manifest, structural, value))
        prepared_months[report_date] = prepared
    manifest = runner.HistoricalBackfillBatchManifest.create(
        requested_from_date=DATE_1, requested_to_date=DATE_3, evidence_observed_at=T1,
        incomplete_report_dates=(), months=tuple(months),
    )
    batch = runner.prepare_batch_from_manifest(manifest=manifest, artifact_dir=root)
    return SimpleNamespace(batch=batch, root=root, months=prepared_months, calls=extraction_calls)


def _preflight_for_apply(engine, prepared):
    return runner.execute_preflight(
        engine, prepared=prepared, schema_reader=lambda session: _schema_state(),
        allow_non_postgresql=True, read_only_enforcer=lambda session: None,
    )


def _apply_for_test(engine, history, scope_hash=None, **kwargs):
    return runner.execute_apply(
        engine, prepared=history.batch, artifact_dir=history.root,
        expected_batch_manifest_sha256=history.batch.manifest.batch_manifest_sha256,
        expected_apply_scope_sha256=scope_hash or _preflight_for_apply(engine, history.batch)["apply_scope_sha256"],
        ingested_at=T2, schema_reader=lambda session: _schema_state(),
        allow_non_postgresql=True, read_only_enforcer=lambda session: None,
        **({"lock_tables": lambda session: None} | kwargs),
    )


def _stored_counts(engine):
    with Session(engine) as session:
        return runner._task255_counts(session)


def test_historical_opt_in_real_persistence_order_readback_and_skip(tmp_path, small_history, monkeypatch):
    engine = _sqlite_engine(tmp_path, "small-apply.db")
    events, timestamps = [], []
    real_commit = runner._commit_transaction

    def commit(transaction):
        events.append("commit")
        real_commit(transaction)

    class RecordingStore(CbrBankRawFinancialEvidenceStore):
        def persist_bundle(self, bundle, **kwargs):
            events.append(bundle.report_date)
            timestamps.append(kwargs)
            return super().persist_bundle(bundle, **kwargs)

    monkeypatch.setattr(runner, "_commit_transaction", commit)
    def locks(session):
        events.append("locks")

    # Existing current subject timestamps may expand backwards; no fixed final subject total.
    regn = small_history.months[DATE_1].bundle.forms[0].records[0].regn
    with Session(engine) as session:
        CbrBankRawFinancialEvidenceStore(session)._persist_subjects({regn, "99999999"}, observed_at=T2)
        session.commit()
    report = _apply_for_test(engine, small_history, store_factory=RecordingStore, lock_tables=locks)
    assert report["status"] == "complete", report
    assert events == ["locks", DATE_1, "commit", "locks", DATE_2, "commit", "locks", DATE_3, "commit"]
    assert report["committed_report_dates"] == [day.isoformat() for day in (DATE_1, DATE_2, DATE_3)]
    assert report["task255_count_deltas"]["cbr_bank_source_artifacts"] == 12
    assert report["task255_count_deltas"]["cbr_bank_report_snapshots"] == 12
    assert report["task255_count_deltas"]["cbr_bank_raw_observations"] == 12
    assert all(value["observed_at"] == T1 and value["ingested_at"] == T2 for value in timestamps)
    assert all(value["publication_status"] == "UNKNOWN" and value["publication_at"] is None and value["identity_snapshot"] is None for value in timestamps)
    with Session(engine) as session:
        subject = session.scalar(select(store_module.CbrBankReportingSubject).where(
            store_module.CbrBankReportingSubject.subject_regn == regn
        ))
        assert runner.utc_datetime(subject.first_observed_at, field_name="first") == T1
        assert runner.utc_datetime(subject.last_observed_at, field_name="last") == T2
        assert all(value is None for value in session.scalars(select(production_runner.CbrBankReportSnapshot.publication_at)))
    counts = _stored_counts(engine)
    no_store = lambda *a, **k: pytest.fail("SKIP called store")
    exact = _apply_for_test(engine, small_history, store_factory=no_store)
    assert exact["status"] == "complete", exact
    assert len(exact["skipped_exact_month_report_dates"]) == 3
    assert exact["committed_report_dates"] == []
    assert not exact["database_mutation_executed"]
    months = tuple(_month_at(month, T2) for month in small_history.batch.manifest.months)
    new_manifest = runner.HistoricalBackfillBatchManifest.create(
        requested_from_date=DATE_1, requested_to_date=DATE_3, evidence_observed_at=T2,
        incomplete_report_dates=(), months=months,
    )
    later = SimpleNamespace(
        root=small_history.root,
        batch=runner.prepare_batch_from_manifest(manifest=new_manifest, artifact_dir=small_history.root),
    )
    source = _apply_for_test(engine, later, store_factory=no_store)
    assert source["status"] == "complete", source
    assert len(source["skipped_exact_source_report_dates"]) == 3
    assert _stored_counts(engine) == counts
    assert all(call["max_member_bytes"] == 96 * 1024 * 1024 for call in small_history.calls)
    engine.dispose()


def test_store_defaults_reject_historical_member_and_opt_in_remains_explicit(tmp_path, small_history):
    signature = inspect.signature(CbrBankRawFinancialEvidenceStore).parameters
    assert signature["allow_dynamic_value_member"].default is False
    assert signature["max_archive_member_bytes"].default == 16 * 1024 * 1024
    assert signature["max_archive_total_uncompressed_bytes"].default == 64 * 1024 * 1024
    engine = _sqlite_engine(tmp_path, "strict-store.db")
    with Session(engine) as session:
        with pytest.raises(Exception) as caught:
            CbrBankRawFinancialEvidenceStore(session).persist_bundle(
                small_history.months[DATE_1].bundle, observed_at=T1, ingested_at=T2
            )
        assert getattr(caught.value, "code", None).value == "UNSUPPORTED_SCHEMA_VERSION"
        session.rollback()
    assert _stored_counts(engine) == _empty_counts()
    engine.dispose()


@pytest.mark.parametrize("failure", ["first_readback", "second_store", "second_readback", "second_commit", "first_commit", "changed_scope", "final_readback"])
def test_apply_failure_reconciliation_and_no_later_month(tmp_path, small_history, monkeypatch, failure):
    engine = _sqlite_engine(tmp_path, "failure.db")
    calls, commits = [], []
    real_commit = runner._commit_transaction
    real_readback = runner._validate_apply_readback
    real_scope = runner._read_apply_scope
    expected = _preflight_for_apply(engine, small_history.batch)["apply_scope_sha256"]
    unknown = failure in {"first_commit", "second_commit"}

    class FailingStore(CbrBankRawFinancialEvidenceStore):
        def persist_bundle(self, bundle, **kwargs):
            calls.append(bundle.report_date)
            if failure == "second_store" and len(calls) == 2:
                raise RuntimeError("password=DO_NOT_LEAK")
            return super().persist_bundle(bundle, **kwargs)

    def readback(*args, **kwargs):
        if (failure == "first_readback" and len(calls) == 1) or (failure == "second_readback" and len(calls) == 2):
            raise RuntimeError("postgresql://private:password@server/db")
        return real_readback(*args, **kwargs)

    def commit(transaction):
        commits.append(len(calls))
        if (failure == "first_commit" and len(commits) == 1) or (failure == "second_commit" and len(commits) == 2):
            # Simulate a lost acknowledgement after the DB really committed.
            real_commit(transaction)
            raise RuntimeError("commit password must not leak")
        real_commit(transaction)

    def locks(session):
        if failure == "changed_scope" and len(commits) == 1:
            # Concurrent count change before next candidate's gate, not a reauthorization.
            session.info["changed_scope"] = True

    def scope(session, manifest):
        result = real_scope(session, manifest)
        if session.info.get("changed_scope"):
            result["months"][1]["backfill_action"] = "SKIP_EXACT_MONTH"
        if failure == "final_readback" and len(commits) == 3:
            result["current_task255_counts"]["cbr_bank_subject_legal_issuer_profiles"] += 1
        return result

    monkeypatch.setattr(runner, "_commit_transaction", commit)
    monkeypatch.setattr(runner, "_read_apply_scope", scope)
    report = _apply_for_test(engine, small_history, scope_hash=expected, store_factory=FailingStore,
                             readback_validator=readback, lock_tables=locks)
    assert report["status"] == "failed"
    assert "password" not in json.dumps(report) and "server" not in json.dumps(report)
    first_failed = failure in {"first_readback", "first_commit"}
    known = [] if first_failed else ([DATE_1.isoformat()] if failure != "final_readback" else [day.isoformat() for day in (DATE_1, DATE_2, DATE_3)])
    assert report["committed_report_dates"] == known
    assert report["commit_outcome_unknown"] is unknown
    assert report["reconciliation_required"] is (bool(known) or unknown)
    assert report["partial_batch_committed"] is bool(known)
    assert report["database_mutation_executed"] is (True if known else (None if unknown else False))
    if failure != "final_readback":
        assert DATE_3 not in calls
    if unknown:
        assert report["error_code"] == "COMMIT_OUTCOME_UNKNOWN"
        assert report["last_attempted_report_date"] == (DATE_1 if first_failed else DATE_2).isoformat()
    if not unknown:
        assert _stored_counts(engine)["cbr_bank_raw_observations"] == len(known) * 4
    engine.dispose()


@pytest.mark.parametrize("state", ["stale_counts", "partial", "conflict", "unknown", "lock_timeout"])
def test_authorization_and_full_batch_gate_prevent_first_write(tmp_path, small_history, monkeypatch, state):
    engine = _sqlite_engine(tmp_path, "blocked.db")
    before = _preflight_for_apply(engine, small_history.batch)
    scope_hash = before["apply_scope_sha256"]
    if state == "stale_counts":
        with Session(engine) as session:
            CbrBankRawFinancialEvidenceStore(session)._persist_subjects({"99999999"}, observed_at=T1)
            session.commit()
    if state in {"partial", "conflict"}:
        with Session(engine) as session:
            source = small_history.months[DATE_2].bundle.forms[0]
            if state == "conflict":
                payload = source.artifact.content + b"different"
                source = replace(source, artifact=replace(source.artifact, content=payload,
                    compressed_size=len(payload), content_sha256=hashlib.sha256(payload).hexdigest()))
            CbrBankRawFinancialEvidenceStore(session)._persist_artifact(source, ingested_at=T1)
            session.commit()
        blocked = _preflight_for_apply(engine, small_history.batch)
        assert blocked["status"] == "blocked"
        assert blocked["blocked_months"] == 1
        scope_hash = blocked["apply_scope_sha256"]
    if state == "unknown":
        real = runner.classify_backfill_month
        monkeypatch.setattr(runner, "classify_backfill_month", lambda session, month:
            replace(real(session, month), monthly_state="NEW_UNKNOWN_STATE") if month.report_date == DATE_2 else real(session, month))
    def locks(session):
        if state == "lock_timeout":
            error = RuntimeError("postgresql://password@private")
            error.orig = SimpleNamespace(sqlstate="55P03")
            raise error
    counts = _stored_counts(engine)
    report = _apply_for_test(engine, small_history, scope_hash=scope_hash, lock_tables=locks,
        store_factory=lambda *a, **k: pytest.fail("blocked batch invoked store"))
    assert report["status"] == "failed"
    assert report["database_mutation_executed"] is False
    assert report["reconciliation_required"] is False
    assert report["committed_report_dates"] == []
    assert _stored_counts(engine) == counts
    assert report["error_code"] == {
        "stale_counts": "APPLY_SCOPE_HASH_MISMATCH", "partial": "BATCH_STATE_BLOCKED",
        "conflict": "BATCH_STATE_BLOCKED", "unknown": "UNKNOWN_DATABASE_STATE",
        "lock_timeout": "DATABASE_LOCK_TIMEOUT",
    }[state]
    assert "password" not in json.dumps(report)
    engine.dispose()


def test_lock_sql_is_fixed_order_and_bounded():
    statements = []
    runner._lock_task255_tables(SimpleNamespace(execute=lambda statement: statements.append(str(statement))))
    assert statements == [
        "SET LOCAL lock_timeout = '5s'",
        "LOCK TABLE " + ", ".join(sorted(production_runner._TASK255_TABLES)) + " IN SHARE ROW EXCLUSIVE MODE",
    ]


def _apply_args(batch_path, root, batch_hash, scope_hash="0" * 64):
    return [
        "--mode", "apply", "--batch-manifest", str(batch_path), "--artifact-dir", str(root),
        "--database-url-env", "DATABASE_URL", "--confirm-write",
        "--expected-batch-manifest-sha256", batch_hash, "--expected-apply-scope-sha256", scope_hash,
    ]


@pytest.mark.parametrize("change", [
    "no_write", "no_batch_hash", "no_scope_hash", "uppercase_hash", "read_only",
    "from_date", "to_date", "observation", "output", "raw_url", "resume",
])
def test_apply_cli_rejects_missing_or_contradictory_authorization(change, capsys):
    args = _apply_args("batch.json", "cache", "a" * 64)
    removal = {"no_write": "--confirm-write", "no_batch_hash": "--expected-batch-manifest-sha256", "no_scope_hash": "--expected-apply-scope-sha256"}
    if change in removal:
        index = args.index(removal[change])
        del args[index:index + (1 if change == "no_write" else 2)]
    elif change == "uppercase_hash":
        args[args.index("--expected-batch-manifest-sha256") + 1] = "A" * 64
    else:
        args += {
            "read_only": ["--confirm-read-only"], "from_date": ["--from-date", "2023-07-01"],
            "to_date": ["--to-date", "2026-08-01"], "observation": ["--evidence-observed-at", "2026-09-03T10:00:00Z"],
            "output": ["--batch-manifest-output", "other.json"],
            "raw_url": ["--database-url", "postgresql://password@private"], "resume": ["--resume"],
        }[change]
    assert runner.main(args, environ=_NoEnvironment(),
        engine_factory=lambda *a, **k: pytest.fail("invalid args created engine")) == 2
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["error_code"] == "INVALID_ARGUMENTS"
    assert set(runner._apply_progress()).issubset(output)
    assert output["committed_report_dates"] == []
    assert output["before_task255_counts"] is None
    assert captured.err == ""
    assert "password" not in captured.out


@pytest.mark.parametrize("mode", ["discover", "plan", "preflight"])
def test_non_apply_modes_reject_write_authorization(mode):
    args = runner._parser().parse_args([
        "--mode", mode, "--artifact-dir", "cache", "--confirm-write",
        "--expected-batch-manifest-sha256", "a" * 64,
    ])
    with pytest.raises(runner.RunnerError, match="INVALID_ARGUMENTS"):
        runner._validate_args(args)


@pytest.mark.parametrize("damage", ["batch_hash", "missing_artifact", "changed_artifact"])
def test_apply_offline_failures_precede_environment_engine_and_network(tmp_path, small_history, capsys, monkeypatch, damage):
    manifest = small_history.batch.manifest
    path = tmp_path / "batch.json"
    path.write_bytes(runner._manifest_bytes(manifest))
    supplied = manifest.batch_manifest_sha256
    if damage == "batch_hash":
        supplied = "0" * 64
    else:
        item = manifest.months[0].monthly_manifest.artifacts[0]
        artifact = small_history.root / DATE_1.isoformat() / item.artifact_filename
        if damage == "missing_artifact":
            artifact.unlink()
        else:
            content = bytearray(artifact.read_bytes())
            content[-1] ^= 1
            artifact.write_bytes(content)
    monkeypatch.setattr(bundle_module, "CbrBankRegulatoryClient", lambda *a, **k: pytest.fail("HTTP transport created"))
    assert runner.main(_apply_args(path, small_history.root, supplied), environ=_NoEnvironment(),
        client_factory=lambda *a, **k: pytest.fail("network client created"),
        engine_factory=lambda *a, **k: pytest.fail("offline failure created engine")) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["error_code"] == {
        "batch_hash": "BATCH_MANIFEST_HASH_MISMATCH",
        "missing_artifact": "ARTIFACT_CACHE_UNAVAILABLE",
        "changed_artifact": "ARTIFACT_IDENTITY_MISMATCH",
    }[damage]
    assert report["database_accessed"] is False
    assert report["database_mutation_executed"] is False
    assert report["reconciliation_required"] is False
    assert report["publication_status"] == "UNKNOWN" and report["publication_at"] is None
    assert report["historical_availability_proven"] is report["pit_ready"] is False


def test_valid_apply_cli_has_no_transport_and_disposes_engine(tmp_path, small_history, capsys, monkeypatch):
    path = tmp_path / "batch.json"
    path.write_bytes(runner._manifest_bytes(small_history.batch.manifest))
    calls = []
    monkeypatch.setattr(bundle_module, "CbrBankRegulatoryClient", lambda *a, **k: pytest.fail("HTTP transport created"))
    def apply(engine, **kwargs):
        calls.append(kwargs)
        return {**runner._failure("APPLY_SCOPE_HASH_MISMATCH", mode="apply"), "database_accessed": True}
    monkeypatch.setattr(runner, "execute_apply", apply)
    assert runner.main(_apply_args(path, small_history.root, small_history.batch.manifest.batch_manifest_sha256),
        environ={"DATABASE_URL": "postgresql://private:password@localhost/db"},
        engine_factory=lambda *a, **k: SimpleNamespace(dispose=lambda: calls.append("disposed")),
        client_factory=lambda *a, **k: pytest.fail("network client created"), clock=lambda: T2) == 1
    captured = capsys.readouterr()
    assert captured.err == "" and "password" not in captured.out
    assert calls[0]["ingested_at"] == T2 and calls[-1] == "disposed"


def test_full_approved_fixture_apply_readback(tmp_path, current_prepared, current_month, monkeypatch):
    root = tmp_path / "real-cache"
    root.mkdir()
    _write_current_cache(root, current_prepared)
    # These are the immutable RAR fixtures, not the synthetic archive seam above.
    monkeypatch.setattr(bundle_module, "CbrBankRegulatoryClient", lambda *a, **k: pytest.fail("offline parser created HTTP client"))
    batch = runner.prepare_batch_from_manifest(manifest=_batch(current_month), artifact_dir=root)
    engine = _sqlite_engine(tmp_path, "full-apply.db")
    report = _apply_for_test(engine, SimpleNamespace(batch=batch, root=root))
    assert report["status"] == "complete", report
    assert report["after_task255_counts"] == {
        "cbr_bank_reporting_subjects": 353, "cbr_bank_source_artifacts": 4,
        "cbr_bank_report_snapshots": 4, "cbr_bank_raw_observations": 38_842,
        "cbr_bank_subject_legal_issuer_evidence": 0, "cbr_bank_subject_legal_issuer_profiles": 0,
    }
    assert report["committed_report_dates"] == [DATE_3.isoformat()]
    assert report["reconciliation_required"] is False
    assert report["pit_ready"] is report["historical_availability_proven"] is False
    engine.dispose()


@pytest.mark.parametrize("tamper", ["raw_checksum", "snapshot_metadata", "insert_count", "identity_count"])
def test_independent_readback_rejects_store_result_or_lineage_corruption(tmp_path, small_history, monkeypatch, tamper):
    engine = _sqlite_engine(tmp_path, "tamper.db")
    persisted = []
    real_counts = runner._task255_counts

    def counts(session):
        values = real_counts(session)
        if persisted and tamper == "identity_count":
            values["cbr_bank_subject_legal_issuer_evidence"] += 1
        return values

    monkeypatch.setattr(runner, "_task255_counts", counts)
    class TamperingStore(CbrBankRawFinancialEvidenceStore):
        def persist_bundle(self, bundle, **kwargs):
            result = super().persist_bundle(bundle, **kwargs)
            persisted.append(bundle.report_date)
            if tamper == "raw_checksum":
                table = production_runner.CbrBankRawObservation.__table__
                row_id = self.session.scalar(select(table.c.id).limit(1))
                self.session.execute(table.update().where(table.c.id == row_id).values(
                    source_row_fingerprint=hashlib.sha256(b"tampered readback").hexdigest()
                ))
            elif tamper == "snapshot_metadata":
                table = production_runner.CbrBankReportSnapshot.__table__
                self.session.execute(table.update().values(subject_count=999))
            elif tamper == "insert_count":
                result = replace(result, artifacts=replace(result.artifacts, inserted=3))
            return result
    report = _apply_for_test(engine, small_history, store_factory=TamperingStore)
    assert report["status"] == "failed"
    assert report["committed_report_dates"] == []
    assert report["database_mutation_executed"] is report["reconciliation_required"] is False
    assert persisted == [DATE_1]
    persisted.clear()
    assert _stored_counts(engine) == _empty_counts()
    engine.dispose()


def test_preflight_uses_repeatable_read_and_preserves_set_show_order(monkeypatch):
    events = []
    batch = _batch(_fake_month(DATE_3))
    class Transaction:
        is_active = True
        def rollback(self):
            events.append("rollback")
            self.is_active = False
    class Connection:
        dialect = SimpleNamespace(name="postgresql")
        def execution_options(self, **kwargs):
            events.append(kwargs)
            return self
        def begin(self):
            events.append("begin")
            return Transaction()
        def close(self):
            events.append("close_connection")
    class FakeSession:
        def __init__(self, **kwargs):
            assert kwargs["autoflush"] is False
        def execute(self, statement):
            events.append(str(statement))
            return SimpleNamespace(scalar_one=lambda: "on")
        def close(self):
            events.append("close_session")
    def schema(session):
        events.append("SELECT schema")
        return _schema_state()
    monkeypatch.setattr(runner, "Session", FakeSession)
    monkeypatch.setattr(runner, "classify_backfill_month", lambda session, month: _candidate(month))
    monkeypatch.setattr(runner, "_task255_counts", lambda session: _empty_counts())
    report = runner.execute_preflight(
        SimpleNamespace(connect=lambda: Connection()),
        prepared=runner.PreparedHistoricalBatch(batch, runner._batch_report(batch, mode="plan")),
        schema_reader=schema,
    )
    assert events[:5] == [
        {"isolation_level": "REPEATABLE READ"}, "begin",
        "SET TRANSACTION READ ONLY", "SHOW transaction_read_only", "SELECT schema",
    ]
    assert events[-3:] == ["rollback", "close_session", "close_connection"]
    assert runner._valid_digest(report["apply_scope_sha256"])
    assert report["transaction_read_only"] is report["database_read_only"] is True


def test_apply_cleanup_failure_does_not_erase_known_commit(tmp_path, small_history, monkeypatch):
    engine = _sqlite_engine(tmp_path, "cleanup.db")
    original_commit = runner._commit_transaction
    original_close = runner.Session.close
    commits = []
    def commit(transaction):
        original_commit(transaction)
        commits.append(True)
    def close(session):
        original_close(session)
        if commits:
            raise RuntimeError("password in cleanup")
    monkeypatch.setattr(runner, "_commit_transaction", commit)
    monkeypatch.setattr(runner.Session, "close", close)
    report = _apply_for_test(engine, small_history)
    assert report["error_code"] == "TRANSACTION_CLEANUP_FAILED"
    assert report["committed_report_dates"] == [DATE_1.isoformat()]
    assert report["reconciliation_required"] is True
    assert report["database_mutation_executed"] is True
    assert report["last_attempted_report_date"] == DATE_1.isoformat()
    engine.dispose()
