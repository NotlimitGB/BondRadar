from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.base import Base
from app.models.bond import Bond
from app.models.bond_legal_issuer_evidence import BondLegalIssuerEvidence
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal
from app.services.bond_legal_issuer_service import (
    BondLegalIssuerService,
    legal_issuer_mapping_blockers,
    legal_issuer_mapping_completeness,
)
from app.services.moex_issuer_identity_source_service import (
    MoexIssuerIdentitySourceResolution,
)


ROOT = Path(__file__).parents[2]
T1 = datetime(2026, 8, 28, 8, 0, tzinfo=timezone.utc)


def create_bond(
    db: Session,
    suffix: str,
    *,
    company_inn: str | None = None,
    with_identity_profile: bool = False,
) -> Bond:
    company = Company(
        name=f"Legacy Company {suffix}",
        ticker=f"LEGAL{suffix}",
        inn=company_inn,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    if with_identity_profile:
        db.add(
            CompanyIdentityProfile(
                company_id=company.id,
                legal_name=company.name,
                inn=company_inn,
                issuer_role="legal_issuer",
                identity_status="verified",
                identity_source="manual_review",
                review_status="accepted",
            )
        )
    bond = Bond(
        company_id=company.id,
        isin=f"RU{int(suffix):010d}",
        secid=f"LEGAL{suffix}",
        name=f"Legal Issuer Bond {suffix}",
        currency="RUB",
        nominal_value=Decimal("1000"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def resolution(
    bond: Bond,
    *,
    issuer_id: str | None = "1001",
    title: str | None = "Issuer One LLC",
    inn: str | None = "7700000001",
    okpo: str | None = "12345678",
    status: str = "EXACT_SECID_ISIN_CORROBORATED",
) -> MoexIssuerIdentitySourceResolution:
    issuer_values = (issuer_id, title, inn, okpo)
    if all(value is not None for value in issuer_values):
        issuer_status = "ISSUER_COMPLETE"
    elif any(value is not None for value in issuer_values):
        issuer_status = "ISSUER_PARTIAL"
    else:
        issuer_status = "ISSUER_MISSING"
    matched_secid = bond.secid
    matched_isin = bond.isin
    if status == "EXACT_ISIN_RECOVERED":
        matched_secid = f"RECOVERED{bond.id}"
    return MoexIssuerIdentitySourceResolution(
        requested_secid=bond.secid,
        expected_isin=bond.isin,
        matched_secid=matched_secid,
        matched_isin=matched_isin,
        candidate_count=1,
        matched_candidate_count=1,
        security_match_status=status,
        issuer_metadata_status=issuer_status,
        issuer_id=issuer_id,
        issuer_title=title,
        issuer_inn=inn,
        issuer_okpo=okpo,
        short_name="Issuer One",
        full_name="Issuer One Bond",
        primary_board="TQCB",
        source_query_count=1,
    )


def record(
    service: BondLegalIssuerService,
    bond: Bond,
    *,
    issuer_id: str | None,
    observed_at: datetime,
    title: str | None = "Issuer One LLC",
    inn: str | None = "7700000001",
    okpo: str | None = "12345678",
    status: str = "EXACT_SECID_ISIN_CORROBORATED",
) -> tuple[BondLegalIssuerEvidence, bool]:
    return service.record_evidence(
        bond=bond,
        requested_secid=bond.secid,
        expected_isin=bond.isin,
        matched_secid=bond.secid or "MISSING",
        matched_isin=bond.isin,
        source_issuer_id=issuer_id,
        issuer_title=title,
        issuer_inn=inn,
        issuer_okpo=okpo,
        security_match_status=status,
        observed_at=observed_at,
    )


def test_verified_mapping_allows_missing_inn_and_okpo(
    db_session: Session,
) -> None:
    service = BondLegalIssuerService(db_session)
    missing_inn = create_bond(db_session, "1")
    profile = service.ingest_moex_security_reference(
        missing_inn,
        resolution(missing_inn, inn=None, okpo=None),
        observed_at=T1,
    )

    assert profile.mapping_state == "verified"
    assert profile.source_issuer_id == "1001"
    assert profile.issuer_title == "Issuer One LLC"
    assert profile.issuer_inn is None
    assert profile.issuer_okpo is None
    assert legal_issuer_mapping_blockers(missing_inn, profile) == []
    assert legal_issuer_mapping_completeness(profile) == {
        "SOURCE_ISSUER_ID_PRESENT": True,
        "ISSUER_TITLE_PRESENT": True,
        "ISSUER_INN_PRESENT": False,
        "ISSUER_OKPO_PRESENT": False,
        "SECID_MATCH_EXACT": True,
        "ISIN_CORROBORATED": True,
    }


@pytest.mark.parametrize(
    "invalid_resolution",
    [
        {"security_match_status": "SECURITY_NOT_FOUND"},
        {"security_match_status": "SECURITY_AMBIGUOUS"},
        {"security_match_status": "SECURITY_IDENTIFIER_CONFLICT"},
        {"security_match_status": "SOURCE_ERROR"},
        {"candidate_count": 0},
        {"matched_candidate_count": 0},
        {"requested_secid": "OTHER"},
        {"expected_isin": "RU9999999999"},
        {"issuer_metadata_status": "ISSUER_COMPLETE", "issuer_inn": None},
    ],
)
def test_invalid_task242_resolution_creates_no_rows(
    db_session: Session,
    invalid_resolution: dict[str, object],
) -> None:
    bond = create_bond(db_session, "2")
    source = resolution(bond)
    invalid = replace(source, **invalid_resolution)

    with pytest.raises(ValueError):
        BondLegalIssuerService(db_session).ingest_moex_security_reference(
            bond,
            invalid,
            observed_at=T1,
        )

    assert db_session.scalar(
        select(func.count()).select_from(BondLegalIssuerEvidence)
    ) == 0
    assert db_session.scalar(
        select(func.count()).select_from(BondLegalIssuerProfile)
    ) == 0


def test_same_issuer_across_bonds_does_not_mutate_legacy_identity(
    db_session: Session,
) -> None:
    first = create_bond(
        db_session,
        "3",
        company_inn="7700000999",
        with_identity_profile=True,
    )
    second = create_bond(db_session, "4")
    company = db_session.get(Company, first.company_id)
    identity = db_session.execute(
        select(CompanyIdentityProfile).where(
            CompanyIdentityProfile.company_id == first.company_id
        )
    ).scalar_one()
    before = (
        first.company_id,
        company.name,
        company.inn,
        identity.identity_status,
        identity.review_status,
        db_session.scalar(select(func.count()).select_from(Company)),
    )
    service = BondLegalIssuerService(db_session)

    first_profile = service.ingest_moex_security_reference(
        first,
        resolution(first, issuer_id="SHARED"),
        observed_at=T1,
    )
    second_profile = service.ingest_moex_security_reference(
        second,
        resolution(second, issuer_id="SHARED"),
        observed_at=T1,
    )
    db_session.flush()

    company = db_session.get(Company, first.company_id)
    identity = db_session.execute(
        select(CompanyIdentityProfile).where(
            CompanyIdentityProfile.company_id == first.company_id
        )
    ).scalar_one()
    after = (
        first.company_id,
        company.name,
        company.inn,
        identity.identity_status,
        identity.review_status,
        db_session.scalar(select(func.count()).select_from(Company)),
    )
    assert first_profile.bond_id != second_profile.bond_id
    assert first_profile.source_issuer_id == second_profile.source_issuer_id == "SHARED"
    assert after == before


def test_retry_and_a_to_b_to_a_reobservation_preserve_chronology(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "5")
    service = BondLegalIssuerService(db_session)
    t2 = T1 + timedelta(days=1)
    t3 = T1 + timedelta(days=2)

    first_a, created_a = record(
        service,
        bond,
        issuer_id="A",
        title="Issuer A",
        inn="7700000001",
        observed_at=T1,
    )
    record(
        service,
        bond,
        issuer_id="B",
        title="Issuer B",
        inn="7700000002",
        observed_at=t2,
    )
    final_a, created_final_a = record(
        service,
        bond,
        issuer_id="A",
        title="Issuer A",
        inn="7700000001",
        observed_at=t3,
    )
    retried_a, retry_created = record(
        service,
        bond,
        issuer_id="A",
        title="Issuer A",
        inn="7700000001",
        observed_at=t3,
    )
    profile = service.resolve_profile(bond)

    rows = list(
        db_session.execute(
            select(BondLegalIssuerEvidence)
            .where(BondLegalIssuerEvidence.bond_id == bond.id)
            .order_by(BondLegalIssuerEvidence.observed_at)
        ).scalars()
    )
    assert created_a is True and created_final_a is True
    assert retry_created is False
    assert retried_a.id == final_a.id
    assert first_a.evidence_fingerprint != final_a.evidence_fingerprint
    assert [row.source_issuer_id for row in rows] == ["A", "B", "A"]
    assert profile.mapping_state == "verified"
    assert profile.source_issuer_id == "A"
    assert profile.last_observed_at == t3


def test_latest_title_supersedes_without_false_identity_conflict(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "6")
    service = BondLegalIssuerService(db_session)
    record(
        service,
        bond,
        issuer_id="A",
        title="Issuer A LLC",
        observed_at=T1,
    )
    record(
        service,
        bond,
        issuer_id="A",
        title="  ISSUER   A   LLC  ",
        observed_at=T1 + timedelta(hours=1),
    )
    db_session.commit()
    db_session.expire_all()
    persisted_bond = db_session.get(Bond, bond.id)
    profile = service.resolve_profile(persisted_bond)

    assert profile.mapping_state == "verified"
    assert profile.source_issuer_id == "A"
    assert profile.issuer_title == "ISSUER A LLC"
    assert db_session.scalar(
        select(func.count()).select_from(BondLegalIssuerEvidence)
    ) == 2


def test_tied_current_conflicts_and_optional_attribute_ambiguity(
    db_session: Session,
) -> None:
    service = BondLegalIssuerService(db_session)

    issuer_conflict = create_bond(db_session, "7")
    record(service, issuer_conflict, issuer_id="A", observed_at=T1)
    record(service, issuer_conflict, issuer_id="B", observed_at=T1)
    assert service.resolve_profile(issuer_conflict).mapping_state == "conflict"

    inn_conflict = create_bond(db_session, "8")
    record(service, inn_conflict, issuer_id="A", inn="1", observed_at=T1)
    record(service, inn_conflict, issuer_id="A", inn="2", observed_at=T1)
    assert service.resolve_profile(inn_conflict).mapping_state == "conflict"

    optional_ambiguity = create_bond(db_session, "9")
    record(
        service,
        optional_ambiguity,
        issuer_id="A",
        title="Issuer A",
        okpo="1",
        observed_at=T1,
    )
    record(
        service,
        optional_ambiguity,
        issuer_id="A",
        title="Issuer A",
        okpo="2",
        observed_at=T1,
    )
    optional_profile = service.resolve_profile(optional_ambiguity)
    assert optional_profile.mapping_state == "verified"
    assert optional_profile.issuer_okpo is None

    title_ambiguity = create_bond(db_session, "10")
    record(
        service,
        title_ambiguity,
        issuer_id="A",
        title="Issuer A",
        observed_at=T1,
    )
    record(
        service,
        title_ambiguity,
        issuer_id="A",
        title="Issuer A JSC",
        observed_at=T1,
    )
    title_profile = service.resolve_profile(title_ambiguity)
    assert title_profile.mapping_state == "observed"
    assert title_profile.source_issuer_id == "A"
    assert title_profile.issuer_title is None


def test_observed_unknown_blockers_and_profile_constraints(
    db_session: Session,
) -> None:
    service = BondLegalIssuerService(db_session)
    observed = create_bond(db_session, "11")
    observed_profile = service.ingest_moex_security_reference(
        observed,
        resolution(observed, status="EXACT_ISIN_RECOVERED"),
        observed_at=T1,
    )
    assert observed_profile.mapping_state == "observed"
    assert legal_issuer_mapping_blockers(observed, observed_profile) == [
        "MAPPING_NOT_VERIFIED",
        "SECID_MATCH_NOT_EXACT",
        "ISIN_NOT_CORROBORATED",
    ]

    unknown = create_bond(db_session, "12")
    unknown_profile = service.ingest_moex_security_reference(
        unknown,
        resolution(
            unknown,
            issuer_id=None,
            title=None,
            inn=None,
            okpo=None,
        ),
        observed_at=T1,
    )
    assert unknown_profile.mapping_state == "unknown"
    assert legal_issuer_mapping_blockers(unknown, unknown_profile) == [
        "MAPPING_UNKNOWN",
        "SOURCE_ISSUER_ID_MISSING",
        "ISSUER_TITLE_MISSING",
        "SECID_MATCH_NOT_EXACT",
        "ISIN_NOT_CORROBORATED",
    ]
    assert legal_issuer_mapping_blockers(unknown, None) == ["PROFILE_MISSING"]

    duplicate = BondLegalIssuerProfile(bond_id=unknown.id)
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    invalid = create_bond(db_session, "13")
    db_session.add(
        BondLegalIssuerProfile(
            bond_id=invalid.id,
            mapping_state="verified",
            mapping_source="moex_security_reference",
            source_issuer_id="A",
            issuer_title=None,
            security_match_status="EXACT_SECID_ISIN_CORROBORATED",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_bond_delete_cascades_profile_and_evidence(db_session: Session) -> None:
    bond = create_bond(db_session, "14")
    BondLegalIssuerService(db_session).ingest_moex_security_reference(
        bond,
        resolution(bond),
        observed_at=T1,
    )
    db_session.commit()
    assert len(bond.legal_issuer_evidence) == 1
    assert bond.legal_issuer_profile is not None
    db_session.delete(bond)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(BondLegalIssuerEvidence)
    ) == 0
    assert db_session.scalar(
        select(func.count()).select_from(BondLegalIssuerProfile)
    ) == 0


def test_low_level_match_contract_and_verified_state_fail_closed(
    db_session: Session,
) -> None:
    bond = create_bond(db_session, "15")
    service = BondLegalIssuerService(db_session)
    with pytest.raises(ValueError, match="does not corroborate ISIN"):
        service.record_evidence(
            bond=bond,
            requested_secid=bond.secid,
            expected_isin=bond.isin,
            matched_secid=bond.secid or "MISSING",
            matched_isin="RU9999999999",
            source_issuer_id="A",
            issuer_title="Issuer A",
            issuer_inn=None,
            issuer_okpo=None,
            security_match_status="EXACT_SECID_ISIN_CORROBORATED",
            observed_at=T1,
        )
    assert db_session.scalar(
        select(func.count()).select_from(BondLegalIssuerEvidence)
    ) == 0

    db_session.add(
        BondLegalIssuerProfile(
            bond_id=bond.id,
            mapping_state="verified",
            mapping_source="moex_security_reference",
            source_issuer_id="A",
            issuer_title="Issuer A",
            security_match_status="EXACT_ISIN_RECOVERED",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_migration_upgrade_downgrade_reupgrade_disposable_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "task243-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))

    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    BondLegalIssuerEvidence.__table__.drop(engine)
    BondLegalIssuerProfile.__table__.drop(engine)
    command.stamp(config, "202608260001")
    command.upgrade(config, "head")
    assert {
        "bond_legal_issuer_profiles",
        "bond_legal_issuer_evidence",
    }.issubset(set(inspect(engine).get_table_names()))
    command.downgrade(config, "202608260001")
    assert "bond_legal_issuer_profiles" not in inspect(engine).get_table_names()
    assert "bond_legal_issuer_evidence" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert "bond_legal_issuer_profiles" in inspect(engine).get_table_names()
    engine.dispose()
