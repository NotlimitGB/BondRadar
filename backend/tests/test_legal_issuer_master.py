from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, event, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.models.bond import Bond
from app.models.bond_legal_issuer_evidence import BondLegalIssuerEvidence
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.financial_report import FinancialReport
from app.models.legal_issuer import LegalIssuer
from app.models.legal_issuer_evidence import LegalIssuerEvidence
from app.services.bond_legal_issuer_service import BondLegalIssuerService
from app.services.legal_issuer_master_service import (
    LegalIssuerMasterService,
    legal_issuer_master_blockers,
    legal_issuer_master_completeness,
)


ROOT = Path(__file__).parents[2]
T1 = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)


def load_probe_module() -> Any:
    path = ROOT / "scripts" / "legal_issuer_master_readiness_probe.py"
    spec = importlib.util.spec_from_file_location(
        "legal_issuer_master_readiness_probe",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def create_bond(
    db: Session,
    suffix: str,
    *,
    with_identity: bool = False,
) -> Bond:
    company = Company(
        name=f"Legacy Company {suffix}",
        ticker=f"LC{suffix}",
        inn=f"77{int(suffix):08d}",
        country="RU",
    )
    db.add(company)
    db.flush()
    if with_identity:
        db.add(
            CompanyIdentityProfile(
                company_id=company.id,
                legal_name=company.name,
                inn=company.inn,
                identity_status="verified",
                identity_source="manual_review",
                review_status="accepted",
                issuer_role="legal_issuer",
            )
        )
    bond = Bond(
        company_id=company.id,
        secid=f"SEC{int(suffix):05d}",
        isin=f"RU{int(suffix):010d}",
        name=f"Bond {suffix}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
    )
    db.add(bond)
    db.flush()
    return bond


def task243_evidence(
    db: Session,
    bond: Bond,
    *,
    issuer_id: str | None = "1001",
    title: str | None = "Issuer One LLC",
    inn: str | None = "7700000001",
    okpo: str | None = "12345678",
    observed_at: datetime = T1,
) -> BondLegalIssuerEvidence:
    row, _ = BondLegalIssuerService(db).record_evidence(
        bond=bond,
        requested_secid=bond.secid,
        expected_isin=bond.isin,
        matched_secid=bond.secid or "MISSING",
        matched_isin=bond.isin,
        source_issuer_id=issuer_id,
        issuer_title=title,
        issuer_inn=inn,
        issuer_okpo=okpo,
        security_match_status="EXACT_SECID_ISIN_CORROBORATED",
        observed_at=observed_at,
    )
    return row


def ingest(
    db: Session,
    upstream: BondLegalIssuerEvidence,
) -> tuple[LegalIssuer, LegalIssuerEvidence, bool]:
    return LegalIssuerMasterService(db).ingest_task243_evidence(upstream)


def test_basic_master_multiple_bonds_missing_fields_and_no_inn_merge(
    db_session: Session,
) -> None:
    first = create_bond(db_session, "1")
    second = create_bond(db_session, "2")
    third = create_bond(db_session, "3")
    first_row = task243_evidence(db_session, first, okpo=None)
    second_row = task243_evidence(db_session, second, okpo=None)
    third_row = task243_evidence(
        db_session,
        third,
        issuer_id="2002",
        title="Issuer Two LLC",
        inn="7700000001",
        okpo=None,
    )

    first_issuer, _, _ = ingest(db_session, first_row)
    second_issuer, _, _ = ingest(db_session, second_row)
    third_issuer, _, _ = ingest(db_session, third_row)

    assert first_issuer.id == second_issuer.id
    assert first_issuer.id != third_issuer.id
    assert first_issuer.resolution_state == third_issuer.resolution_state == "verified"
    assert first_issuer.issuer_okpo is None
    assert db_session.scalar(select(func.count()).select_from(LegalIssuer)) == 2
    assert db_session.scalar(select(func.count()).select_from(LegalIssuerEvidence)) == 3
    assert legal_issuer_master_completeness(first_issuer) == {
        "SOURCE_ISSUER_ID_PRESENT": True,
        "ISSUER_TITLE_PRESENT": True,
        "ISSUER_INN_PRESENT": True,
        "ISSUER_OKPO_PRESENT": False,
        "MASTER_VERIFIED": True,
        "MASTER_CONFLICT": False,
    }

    no_inn = create_bond(db_session, "4")
    no_inn_issuer, _, _ = ingest(
        db_session,
        task243_evidence(
            db_session,
            no_inn,
            issuer_id="3003",
            title="Foreign Issuer",
            inn=None,
            okpo=None,
        ),
    )
    assert no_inn_issuer.resolution_state == "verified"
    assert no_inn_issuer.issuer_inn is None


def test_retry_new_observation_and_a_to_b_to_a_chronology(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "5")
    t2 = T1 + timedelta(days=1)
    t3 = T1 + timedelta(days=2)
    rows = [
        task243_evidence(
            db_session, bond, issuer_id="A", title="Issuer A", observed_at=T1
        ),
        task243_evidence(
            db_session, bond, issuer_id="B", title="Issuer B", observed_at=t2
        ),
        task243_evidence(
            db_session, bond, issuer_id="A", title="Issuer A", observed_at=t3
        ),
    ]
    service = LegalIssuerMasterService(db_session)
    first_issuer, first_evidence, created = service.ingest_task243_evidence(rows[0])
    _, retried, retry_created = service.ingest_task243_evidence(rows[0])
    service.ingest_task243_evidence(rows[1])
    final_a, _, final_created = service.ingest_task243_evidence(rows[2])
    BondLegalIssuerService(db_session).resolve_profile(bond)
    profile = db_session.execute(
        select(BondLegalIssuerProfile).where(BondLegalIssuerProfile.bond_id == bond.id)
    ).scalar_one()
    resolution = service.resolve_for_bond_profile(profile)

    assert created is True and retry_created is False and final_created is True
    assert first_evidence.id == retried.id
    assert first_issuer.id == final_a.id
    assert db_session.scalar(select(func.count()).select_from(LegalIssuer)) == 2
    assert db_session.scalar(select(func.count()).select_from(LegalIssuerEvidence)) == 3
    a_rows = list(
        db_session.execute(
            select(LegalIssuerEvidence)
            .where(LegalIssuerEvidence.legal_issuer_id == final_a.id)
            .order_by(LegalIssuerEvidence.observed_at)
        ).scalars()
    )
    assert [row.observed_at for row in a_rows] == [T1, t3]
    assert resolution.resolved is True
    assert resolution.legal_issuer.id == final_a.id
    assert resolution.blockers == ()


def test_latest_per_security_title_and_optional_attribute_semantics(
    db_session: Session,
) -> None:
    first = create_bond(db_session, "6")
    second = create_bond(db_session, "7")
    service = LegalIssuerMasterService(db_session)
    old = task243_evidence(
        db_session,
        first,
        issuer_id="SHARED",
        title="Old Title",
        observed_at=T1,
    )
    corroborating = task243_evidence(
        db_session,
        second,
        issuer_id="SHARED",
        title="New Title",
        observed_at=T1 + timedelta(hours=1),
    )
    newest = task243_evidence(
        db_session,
        first,
        issuer_id="SHARED",
        title="New Title",
        observed_at=T1 + timedelta(hours=2),
    )
    for row in (old, corroborating, newest):
        issuer, _, _ = service.ingest_task243_evidence(row)
    assert issuer.resolution_state == "verified"
    assert issuer.issuer_title == "New Title"
    assert db_session.scalar(select(func.count()).select_from(LegalIssuerEvidence)) == 3

    ambiguous_first = create_bond(db_session, "8")
    ambiguous_second = create_bond(db_session, "9")
    for row in (
        task243_evidence(
            db_session,
            ambiguous_first,
            issuer_id="AMB",
            title="Title A",
            okpo="1",
        ),
        task243_evidence(
            db_session,
            ambiguous_second,
            issuer_id="AMB",
            title="Title B",
            okpo="2",
        ),
    ):
        ambiguous, _, _ = service.ingest_task243_evidence(row)
    assert ambiguous.resolution_state == "observed"
    assert ambiguous.issuer_title is None
    assert ambiguous.issuer_okpo is None


def test_inn_conflict_and_tied_latest_rows_fail_closed(db_session: Session) -> None:
    first = create_bond(db_session, "10")
    second = create_bond(db_session, "11")
    service = LegalIssuerMasterService(db_session)
    for row in (
        task243_evidence(
            db_session,
            first,
            issuer_id="CONFLICT",
            title="Conflict Issuer",
            inn="1",
        ),
        task243_evidence(
            db_session,
            second,
            issuer_id="CONFLICT",
            title="Conflict Issuer",
            inn="2",
        ),
    ):
        conflicted, _, _ = service.ingest_task243_evidence(row)
    assert conflicted.resolution_state == "conflict"
    assert conflicted.issuer_inn is None

    tied = create_bond(db_session, "12")
    for row in (
        task243_evidence(
            db_session,
            tied,
            issuer_id="TIED",
            title="Title A",
            observed_at=T1,
        ),
        task243_evidence(
            db_session,
            tied,
            issuer_id="TIED",
            title="Title B",
            observed_at=T1,
        ),
    ):
        tied_issuer, _, _ = service.ingest_task243_evidence(row)
    assert tied_issuer.resolution_state == "observed"
    assert tied_issuer.issuer_title is None


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("contract_version", "wrong-contract"),
        ("source", "unsupported-source"),
        ("security_match_status", "SECURITY_NOT_FOUND"),
        ("source_issuer_id", None),
        ("evidence_fingerprint", "0" * 64),
    ],
)
def test_invalid_upstream_evidence_is_all_or_nothing(
    db_session: Session,
    field_name: str,
    invalid_value: object,
) -> None:
    bond = create_bond(db_session, f"{20 + len(db_session.new)}")
    row = task243_evidence(db_session, bond)
    setattr(row, field_name, invalid_value)
    with pytest.raises(ValueError):
        ingest(db_session, row)
    assert db_session.scalar(select(func.count()).select_from(LegalIssuer)) == 0
    assert db_session.scalar(select(func.count()).select_from(LegalIssuerEvidence)) == 0
    db_session.rollback()


def test_profile_blockers_and_legacy_financial_boundaries(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "30", with_identity=True)
    report = FinancialReport(
        company_id=bond.company_id,
        period_year=2025,
        period_quarter=0,
        revenue=Decimal("100.00"),
    )
    db_session.add(report)
    db_session.flush()
    company = db_session.get(Company, bond.company_id)
    identity = db_session.execute(
        select(CompanyIdentityProfile).where(
            CompanyIdentityProfile.company_id == bond.company_id
        )
    ).scalar_one()
    before = (
        bond.company_id,
        company.name,
        company.inn,
        identity.identity_status,
        report.company_id,
        report.revenue,
        db_session.scalar(select(func.count()).select_from(Company)),
        db_session.scalar(select(func.count()).select_from(FinancialReport)),
    )
    upstream = task243_evidence(db_session, bond)
    BondLegalIssuerService(db_session).resolve_profile(bond)
    profile = db_session.execute(
        select(BondLegalIssuerProfile).where(BondLegalIssuerProfile.bond_id == bond.id)
    ).scalar_one()
    service = LegalIssuerMasterService(db_session)
    missing = service.resolve_for_bond_profile(profile)
    assert missing.blockers == ("LEGAL_ISSUER_MASTER_MISSING",)
    issuer, _, _ = service.ingest_task243_evidence(upstream)
    resolved = service.resolve_for_bond_profile(profile)
    assert resolved.resolved is True and resolved.legal_issuer.id == issuer.id
    issuer.resolution_state = "conflict"
    assert legal_issuer_master_blockers(profile, issuer) == [
        "LEGAL_ISSUER_MASTER_CONFLICT"
    ]
    assert legal_issuer_master_blockers(None, None) == [
        "BOND_LEGAL_ISSUER_PROFILE_MISSING"
    ]
    db_session.refresh(company)
    db_session.refresh(identity)
    db_session.refresh(report)
    after = (
        bond.company_id,
        company.name,
        company.inn,
        identity.identity_status,
        report.company_id,
        report.revenue,
        db_session.scalar(select(func.count()).select_from(Company)),
        db_session.scalar(select(func.count()).select_from(FinancialReport)),
    )
    assert after == before


def test_model_constraints_uniqueness_and_cascade(db_session: Session) -> None:
    bond = create_bond(db_session, "31")
    issuer, evidence, _ = ingest(db_session, task243_evidence(db_session, bond))
    db_session.commit()
    duplicate = LegalIssuer(
        identity_source=issuer.identity_source,
        source_issuer_id=issuer.source_issuer_id,
        resolution_state="observed",
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    duplicate_evidence = LegalIssuerEvidence(
        legal_issuer_id=issuer.id,
        source=issuer.identity_source,
        source_issuer_id=issuer.source_issuer_id,
        upstream_evidence_fingerprint=evidence.upstream_evidence_fingerprint,
        source_bond_id=bond.id,
        source_security_secid=bond.secid,
        source_security_isin=bond.isin,
        security_match_status="EXACT_SECID_ISIN_CORROBORATED",
        observed_at=T1,
        upstream_ingestion_at=T1,
        evidence_fingerprint="f" * 64,
    )
    db_session.add(duplicate_evidence)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    persisted = db_session.get(LegalIssuer, issuer.id)
    assert len(persisted.evidence) == 1
    db_session.delete(persisted)
    db_session.commit()
    assert db_session.scalar(select(func.count()).select_from(LegalIssuerEvidence)) == 0


def test_readiness_probe_is_deterministic_bounded_and_select_only(
    db_session: Session,
) -> None:
    probe = load_probe_module()
    first = create_bond(db_session, "40")
    second = create_bond(db_session, "41")
    third = create_bond(db_session, "42")
    rows = [
        task243_evidence(db_session, first, issuer_id="A", title="Issuer A", inn="7"),
        task243_evidence(db_session, second, issuer_id="A", title="Issuer A", inn="7"),
        task243_evidence(db_session, third, issuer_id="B", title="Issuer B", inn="7"),
    ]
    task243 = BondLegalIssuerService(db_session)
    for bond in (first, second, third):
        task243.resolve_profile(bond)
    db_session.commit()
    statements: list[str] = []

    def guard(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement.strip().upper())
        if not statement.lstrip().upper().startswith("SELECT"):
            raise AssertionError(f"Mutation attempted: {statement}")

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", guard)
    try:
        first_report = probe.build_legal_issuer_master_readiness_report(
            db_session,
            sample_limit=1,
            generated_at=T1,
        )
        second_report = probe.build_legal_issuer_master_readiness_report(
            db_session,
            sample_limit=1,
            generated_at=T1,
        )
    finally:
        event.remove(engine, "before_cursor_execute", guard)
    assert first_report == second_report
    assert first_report["summary"] == {
        "total_bond_legal_issuer_profiles": 3,
        "verified_profiles": 3,
        "non_verified_profiles": 0,
        "task243_evidence_count": 3,
        "unique_mapping_source_issuer_ids": 2,
        "profiles_missing_source_issuer_id": 0,
        "evidence_missing_source_issuer_id": 0,
        "issuer_ids_with_missing_inn": 0,
        "issuer_ids_with_multiple_current_non_null_inns": 0,
        "issuer_ids_with_multiple_current_titles": 0,
        "issuer_ids_with_multiple_current_okpos": 0,
        "inns_shared_by_multiple_source_issuer_ids": 1,
        "planned_legal_issuer_row_count": 2,
        "planned_issuer_evidence_row_count": 3,
        "profiles_resolvable_to_planned_master": 3,
        "unresolved_profile_count": 0,
    }
    assert first_report["securities_per_source_issuer_id_distribution"] == {
        "1": 1,
        "2": 1,
    }
    assert all(len(sample) <= 1 for sample in first_report["samples"].values())
    assert all(statement.startswith("SELECT") for statement in statements)
    assert len(statements) == 4
    rendered_json = probe.serialize_report(first_report, "json")
    rendered_markdown = probe.serialize_report(first_report, "markdown")
    assert json.loads(rendered_json)["schema"] == probe.SCHEMA
    assert "Legal Issuer Master Readiness Probe" in rendered_markdown
    assert "Legacy Company" not in rendered_json + rendered_markdown


def test_probe_postgresql_enforcement_output_and_sanitized_failure(
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = load_probe_module()

    class FakeResult:
        def __init__(self, value: str | None = None) -> None:
            self.value = value

        def scalar_one(self) -> str | None:
            return self.value

    class FakePostgresSession:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_bind(self) -> Any:
            return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

        def execute(self, statement: Any) -> FakeResult:
            sql = str(statement)
            self.calls.append(sql)
            return FakeResult("on" if sql == "SHOW transaction_read_only" else None)

    fake = FakePostgresSession()
    assert probe.enforce_read_only_transaction(fake) is True
    assert fake.calls == ["SET TRANSACTION READ ONLY", "SHOW transaction_read_only"]

    bond = create_bond(db_session, "50")
    task243_evidence(db_session, bond)
    BondLegalIssuerService(db_session).resolve_profile(bond)
    db_session.commit()

    class SessionContext:
        def __enter__(self) -> Session:
            return db_session

        def __exit__(self, *args: object) -> None:
            return None

    import app.db.session as session_module

    monkeypatch.setattr(session_module, "SessionLocal", lambda: SessionContext())
    output = tmp_path / "readiness.json"
    assert probe.main(["--output", str(output), "--format", "json"]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "completed"
    assert probe.main(["--sample-limit", "0"]) == 1
    failure = json.loads(capsys.readouterr().err)
    assert failure == {
        "error": probe.FAILURE_CODE,
        "schema": probe.SCHEMA,
        "status": "failed",
    }


def test_migration_upgrade_downgrade_reupgrade_disposable_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "task244-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    LegalIssuerEvidence.__table__.drop(engine)
    LegalIssuer.__table__.drop(engine)
    command.stamp(config, "202608280001")
    command.upgrade(config, "head")
    assert {"legal_issuers", "legal_issuer_evidence"}.issubset(
        set(inspect(engine).get_table_names())
    )
    command.downgrade(config, "202608280001")
    assert "legal_issuers" not in inspect(engine).get_table_names()
    assert "legal_issuer_evidence" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "legal_issuers" in inspect(engine).get_table_names()
    engine.dispose()
