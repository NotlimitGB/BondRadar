from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.company import Company
from app.models.legal_issuer import LegalIssuer
from app.services.cbr_legal_issuer_bridge import coverage_probe
from app.services.cbr_legal_issuer_bridge.contracts import (
    CbrBridgeState,
    CbrCreditOrganizationRegistryRecord,
    CbrCreditOrganizationRegistrySnapshot,
    FinOrgRecord,
    FinOrgSearchResult,
)
from app.services.cbr_legal_issuer_bridge.service import CbrLegalIssuerBridgeService


NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
GENERATED = datetime(2026, 8, 31, 12, 1, tzinfo=timezone.utc)
REGISTRY_DATE = date(2026, 8, 30)
LAST_UPDATE = datetime(2026, 8, 30, 1, 30, tzinfo=timezone.utc)


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _PostgresGuardSession:
    def __init__(self, session: Session, *, read_only: str = "on") -> None:
        self.session = session
        self.read_only = read_only
        self.statements: list[str] = []
        self.rollback_called = False
        self.close_called = False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    @property
    def no_autoflush(self):
        return self.session.no_autoflush

    def execute(self, statement, *args, **kwargs):
        sql = str(statement).strip()
        self.statements.append(sql)
        upper = sql.upper()
        if upper == "SET TRANSACTION READ ONLY":
            return _ScalarResult(None)
        if upper == "SHOW TRANSACTION_READ_ONLY":
            return _ScalarResult(self.read_only)
        assert upper.startswith("SELECT"), f"unexpected database statement: {sql}"
        return self.session.execute(statement, *args, **kwargs)

    def rollback(self):
        self.rollback_called = True
        self.session.rollback()

    def close(self):
        self.close_called = True
        self.session.close()


def _registry_record(index: int) -> CbrCreditOrganizationRegistryRecord:
    return CbrCreditOrganizationRegistryRecord(
        regn=str(index),
        ogrn=f"1{index:012d}",
        name=f"Registry diagnostic {index}",
        organization_type=None,
        legal_form=None,
        registration_date=None,
        license_status=None,
        location=None,
        registry_as_of=REGISTRY_DATE,
        retrieved_at=NOW,
    )


class _RegistryClient:
    def fetch(self, *, retrieved_at):
        assert retrieved_at == NOW
        records = tuple(_registry_record(index) for index in range(1, 6))
        return CbrCreditOrganizationRegistrySnapshot(
            records=records,
            registry_as_of=REGISTRY_DATE,
            retrieved_at=NOW,
            ambiguous_regns=(),
            conflicting_ogrns=(),
        )


class _FinOrgClient:
    def get_last_update(self):
        return LAST_UPDATE

    def search_by_ogrns(self, ogrns):
        records = tuple(
            FinOrgRecord(
                source_id=str(index),
                ogrn=f"1{index:012d}",
                inn="7700000001" if index == 5 else f"77{index:08d}",
                inn_status="VALID",
                name=f"FinOrg diagnostic {index}",
                status=None,
                error_text=None,
            )
            for index in range(1, 6)
        )
        return FinOrgSearchResult(
            requested_ogrns=tuple(ogrns), records=records, source_error=None
        )


def _bridge_service() -> CbrLegalIssuerBridgeService:
    return CbrLegalIssuerBridgeService(
        fullcolist_client=_RegistryClient(), finorg_client=_FinOrgClient()
    )


def _synthetic_fixture_loader(report_date, *, retrieved_at, fixture_root=None):
    assert report_date == date(2026, 8, 1)
    assert retrieved_at == NOW
    assert fixture_root is None
    return ("1", "2", "3", "4", "5"), {
        "task251_form_count": 4,
        "task251_value_subject_union_count": 5,
        "task251_records_by_form": {"0409101": 5},
        "task251_subjects_by_form": {"0409101": 5},
        "task251_subject_set_hashes": {"0409101": "a" * 64},
    }


def _seed_coverage_rows(session: Session) -> None:
    session.execute(
        insert(LegalIssuer.__table__),
        [
            {
                "id": 1,
                "source_issuer_id": "moex-1",
                "resolution_state": "verified",
                "issuer_title": "Issuer 1",
                "issuer_inn": "7700000001",
            },
            {
                "id": 2,
                "source_issuer_id": "moex-3a",
                "resolution_state": "verified",
                "issuer_title": "Issuer 3A",
                "issuer_inn": "7700000003",
            },
            {
                "id": 3,
                "source_issuer_id": "moex-3b",
                "resolution_state": "verified",
                "issuer_title": "Issuer 3B",
                "issuer_inn": "7700000003",
            },
            {
                "id": 4,
                "source_issuer_id": "moex-4",
                "resolution_state": "observed",
                "issuer_title": "Issuer 4",
                "issuer_inn": "7700000004",
            },
        ],
    )
    session.execute(
        insert(Company.__table__),
        [{"id": 1, "name": "Legacy", "ticker": "LEG", "country": "RU"}],
    )
    session.execute(
        insert(Bond.__table__),
        [
            {"id": index, "company_id": 1, "name": f"Bond {index}", "currency": "RUB"}
            for index in range(1, 4)
        ],
    )
    session.execute(
        insert(BondLegalIssuerProfile.__table__),
        [
            {
                "id": 1,
                "bond_id": 1,
                "mapping_state": "verified",
                "mapping_source": "moex_security_reference",
                "source_issuer_id": "moex-1",
                "issuer_title": "Issuer 1",
                "security_match_status": "EXACT_SECID_ISIN_CORROBORATED",
            },
            {
                "id": 2,
                "bond_id": 2,
                "mapping_state": "verified",
                "mapping_source": "moex_security_reference",
                "source_issuer_id": "moex-1",
                "issuer_title": "Issuer 1",
                "security_match_status": "EXACT_SECID_ISIN_CORROBORATED",
            },
            {
                "id": 3,
                "bond_id": 3,
                "mapping_state": "observed",
                "mapping_source": "moex_security_reference",
                "source_issuer_id": "moex-1",
                "issuer_title": "Issuer 1",
                "security_match_status": "EXACT_SECID",
            },
        ],
    )
    session.commit()


def test_synthetic_coverage_is_exact_bounded_and_deterministic(db_session: Session) -> None:
    _seed_coverage_rows(db_session)
    guarded = _PostgresGuardSession(db_session)
    report = coverage_probe.build_coverage_report(
        guarded,
        report_date=date(2026, 8, 1),
        retrieved_at=NOW,
        generated_at=GENERATED,
        bridge_service=_bridge_service(),
        fixture_loader=_synthetic_fixture_loader,
    )

    assert guarded.statements[:2] == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
    ]
    assert all(
        statement.upper().startswith("SELECT")
        for statement in guarded.statements[2:]
    )
    assert report["task252_state_counts"][CbrBridgeState.VERIFIED.value] == 2
    assert report["task252_state_counts"][CbrBridgeState.LEGAL_ISSUER_NOT_FOUND.value] == 1
    assert report["task252_state_counts"][CbrBridgeState.LEGAL_ISSUER_INN_AMBIGUOUS.value] == 1
    assert report["task252_state_counts"][CbrBridgeState.LEGAL_ISSUER_NOT_VERIFIED.value] == 1
    assert set(report["task252_state_counts"]) == {state.value for state in CbrBridgeState}
    assert report["legal_issuer_total"] == 4
    assert report["legal_issuer_verified"] == 3
    assert report["legal_issuer_with_inn"] == 4
    assert report["legal_issuer_without_inn"] == 0
    assert report["matched_legal_issuer_count"] == 1
    assert report["matched_bond_profile_row_count"] == 2
    assert report["matched_bond_count"] == 2
    assert report["source_identity_failure_count"] == 0
    assert report["legal_issuer_not_found_count"] == 1
    assert report["identity_quality_blocker_count"] == 2
    assert report["transaction_read_only"] is True
    assert report["database_read_only"] is True
    assert report["database_mutation_executed"] is False
    assert report["pit_status"] == "CURRENT_ONLY"
    assert report["historical_backcast_allowed"] is False

    reordered = coverage_probe.build_coverage_report(
        _PostgresGuardSession(db_session),
        report_date=date(2026, 8, 1),
        retrieved_at=NOW,
        generated_at=GENERATED,
        bridge_service=_bridge_service(),
        fixture_loader=lambda *args, **kwargs: (
            ("5", "4", "2", "1", "3"),
            _synthetic_fixture_loader(*args, **kwargs)[1],
        ),
    )
    assert reordered == report


def test_guard_and_cli_fail_closed_without_disclosing_configuration(
    db_session: Session, monkeypatch, capsys
) -> None:
    blocked = _PostgresGuardSession(db_session, read_only="off")
    with pytest.raises(coverage_probe.CoverageProbeError):
        coverage_probe.build_coverage_report(
            blocked,
            report_date=date(2026, 8, 1),
            retrieved_at=NOW,
            generated_at=GENERATED,
            bridge_service=_bridge_service(),
            fixture_loader=_synthetic_fixture_loader,
        )
    assert blocked.statements == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
    ]

    engine_calls = []
    assert coverage_probe.main(
        [
            "--task251-fixture-report-date",
            "2026-08-01",
            "--database-url-env",
            "DATABASE_URL",
        ],
        environ={"DATABASE_URL": "postgresql://user:secret@db/name"},
        engine_factory=lambda *args, **kwargs: engine_calls.append(args),
    ) == 2
    assert engine_calls == []
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENTS"

    assert coverage_probe.main(
        [
            "--task251-fixture-report-date",
            "bad-date",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-read-only",
        ],
        environ={},
    ) == 2
    assert json.loads(capsys.readouterr().out)["error_code"] == "INVALID_ARGUMENTS"
    assert coverage_probe.main(
        [
            "--task251-fixture-report-date",
            "2026-08-01",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-read-only",
        ],
        environ={},
    ) == 1
    assert json.loads(capsys.readouterr().out)["error_code"] == "DATABASE_CONFIGURATION_UNAVAILABLE"
    assert coverage_probe.main(
        [
            "--task251-fixture-report-date",
            "2026-08-01",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-read-only",
        ],
        environ={"DATABASE_URL": "sqlite:///secret.db"},
        engine_factory=lambda *args, **kwargs: engine_calls.append(args),
    ) == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "DATABASE_CONFIGURATION_INVALID"
    assert "secret" not in output
    assert engine_calls == []

    class _FakeSession:
        def __init__(self):
            self.rolled_back = False
            self.closed = False

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    class _FakeEngine:
        def __init__(self):
            self.disposed = False

        def dispose(self):
            self.disposed = True

    fake_session = _FakeSession()
    fake_engine = _FakeEngine()
    monkeypatch.setattr(
        coverage_probe,
        "sessionmaker",
        lambda **kwargs: lambda: fake_session,
    )
    monkeypatch.setattr(
        coverage_probe,
        "build_coverage_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("password=secret")),
    )
    result = coverage_probe.main(
        [
            "--task251-fixture-report-date",
            "2026-08-01",
            "--database-url-env",
            "DATABASE_URL",
            "--confirm-read-only",
        ],
        environ={"DATABASE_URL": "postgresql://user:secret@db/name"},
        engine_factory=lambda *args, **kwargs: fake_engine,
        bridge_service_factory=lambda: object(),
    )
    output = capsys.readouterr().out
    assert result == 1
    assert json.loads(output)["error_code"] == "COVERAGE_PROBE_FAILED"
    assert "secret" not in output
    assert fake_session.rolled_back and fake_session.closed and fake_engine.disposed


def test_task251_fixture_union_and_source_contract_remain_unchanged() -> None:
    regns, projection = coverage_probe.load_task251_fixture_regns(
        date(2026, 8, 1), retrieved_at=NOW
    )
    assert len(regns) == 353
    assert projection["task251_subjects_by_form"] == {
        "0409101": 353,
        "0409102": 212,
        "0409123": 352,
        "0409135": 345,
    }
    assert projection["task251_subject_set_hashes"] == {
        "0409101": "692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd",
        "0409102": "90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8",
        "0409123": "5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc",
        "0409135": "660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619",
    }
    assert projection["task251_records_by_form"] == {
        "0409101": 25654,
        "0409102": 10079,
        "0409123": 1400,
        "0409135": 1709,
    }


def test_module_has_no_mutation_or_secret_output_surface() -> None:
    source = Path(coverage_probe.__file__).read_text(encoding="utf-8")
    for forbidden in (
        ".add(",
        ".flush(",
        ".commit(",
        ".delete(",
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "ALTER ",
        "CREATE TABLE",
        "settings.DATABASE_URL",
    ):
        assert forbidden not in source
    assert "SET TRANSACTION READ ONLY" in source
    assert "SHOW transaction_read_only" in source
    assert "BondLegalIssuerProfile.mapping_source == LEGAL_ISSUER_MAPPING_SOURCE" in source
    assert "BondLegalIssuerProfile.mapping_state == \"verified\"" in source
