from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


def _company(
    db: Session,
    *,
    name: str = "Unknown issuer for RU000TEST001",
    ticker: str = "MOEX_RU000TEST001",
    inn: str | None = None,
) -> Company:
    company = Company(
        name=name,
        ticker=ticker,
        inn=inn,
        country="RU",
        signal=AnalysisSignal.INSUFFICIENT_DATA.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _bond(db: Session, company: Company, *, secid: str = "RU000TEST001") -> Bond:
    bond = Bond(
        company_id=company.id,
        secid=secid,
        isin=secid,
        name=f"Synthetic bond {secid}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        signal=AnalysisSignal.INSUFFICIENT_DATA.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def _identity_row(company: Company, **overrides) -> dict:
    row = {
        "company_id": company.id,
        "current_company_name": company.name,
        "legal_name": "Synthetic Issuer LLC",
        "short_name": "Synthetic Issuer",
        "display_name": "Synthetic Issuer",
        "inn": "7700000001",
        "ogrn": "1027700000001",
        "issuer_role": "legal_issuer",
        "identity_status": "matched",
        "identity_confidence": "0.8",
        "identity_source": "operator_csv",
        "review_status": "pending",
        "review_notes": "Synthetic test identity.",
    }
    row.update(overrides)
    return row


def test_identity_diagnostics_counts_unknown_and_samples(
    client: TestClient,
    db_session: Session,
) -> None:
    company = _company(db_session)
    _bond(db_session, company)
    db_session.add(
        FinancialReport(
            company_id=company.id,
            period_year=2025,
            period_quarter=0,
            period_end_date=date(2025, 12, 31),
            source="test",
            signal=AnalysisSignal.INSUFFICIENT_DATA.value,
        )
    )
    db_session.commit()

    response = client.get("/api/companies/identity/diagnostics?active_only=true&limit=5")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert payload["company_count"] == 1
    assert payload["unknown_company_count"] == 1
    assert payload["missing_inn_count"] == 1
    assert payload["companies_with_moex_generated_ticker"] == 1
    assert payload["companies_with_financial_reports_and_weak_identity"] == 1
    assert payload["top_unknown_issuers"][0]["sample_secids"] == ["RU000TEST001"]


def test_identity_preview_valid_row_does_not_mutate(
    client: TestClient,
    db_session: Session,
) -> None:
    company = _company(db_session)

    response = client.post(
        "/api/companies/identity/preview",
        json={"rows": [_identity_row(company)]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["would_create_identity_profiles"] == 1
    assert payload["would_update_companies"] == 1
    assert db_session.execute(select(CompanyIdentityProfile)).scalar_one_or_none() is None
    db_session.refresh(company)
    assert company.name.startswith("Unknown issuer for ")


def test_identity_preview_unknown_company_returns_row_error(client: TestClient) -> None:
    response = client.post(
        "/api/companies/identity/preview",
        json={
            "rows": [
                {
                    "company_id": 999,
                    "legal_name": "Synthetic Missing LLC",
                    "identity_source": "operator_csv",
                }
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["errors"][0]["code"] == "company_not_found"


def test_identity_apply_requires_confirmation(
    client: TestClient,
    db_session: Session,
) -> None:
    company = _company(db_session)

    response = client.post(
        "/api/companies/identity/apply",
        json={"rows": [_identity_row(company)], "confirm_apply": False},
    )

    assert response.status_code == 400
    assert db_session.execute(select(CompanyIdentityProfile)).scalar_one_or_none() is None


def test_identity_apply_creates_profile_and_safe_company_update(
    client: TestClient,
    db_session: Session,
) -> None:
    company = _company(db_session)
    bond = _bond(db_session, company)
    original_bond_name = bond.name

    response = client.post(
        "/api/companies/identity/apply",
        json={"rows": [_identity_row(company)], "confirm_apply": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["affected_rows_summary"] == {
        "affected_company_ids": [company.id],
        "created_profile_count": 1,
        "updated_profile_count": 0,
        "updated_company_count": 1,
        "skipped_count": 0,
        "conflict_count": 0,
        "warning_count": 0,
    }
    profile = db_session.execute(select(CompanyIdentityProfile)).scalar_one()
    assert profile.company_id == company.id
    assert profile.legal_name == "Synthetic Issuer LLC"
    db_session.refresh(company)
    db_session.refresh(bond)
    assert company.name == "Synthetic Issuer"
    assert company.inn == "7700000001"
    assert bond.name == original_bond_name
    assert bond.company_id == company.id
    assert len(db_session.execute(select(Company)).scalars().all()) == 1


def test_identity_apply_blocks_inn_conflict_by_default(
    client: TestClient,
    db_session: Session,
) -> None:
    first = _company(db_session, name="Known A", ticker="A", inn="7700000001")
    second = _company(db_session, ticker="MOEX_RU000TEST002")
    _bond(db_session, second, secid="RU000TEST002")
    assert first.id != second.id

    response = client.post(
        "/api/companies/identity/apply",
        json={"rows": [_identity_row(second)], "confirm_apply": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    assert payload["failed"] == 1
    assert payload["errors"][0]["code"] == "inn_conflict"
    assert db_session.execute(select(CompanyIdentityProfile)).scalar_one_or_none() is None


def test_identity_apply_allow_conflicts_records_conflict_status(
    client: TestClient,
    db_session: Session,
) -> None:
    _company(db_session, name="Known A", ticker="A", inn="7700000001")
    second = _company(db_session, ticker="MOEX_RU000TEST002")

    response = client.post(
        "/api/companies/identity/apply",
        json={
            "rows": [_identity_row(second)],
            "confirm_apply": True,
            "allow_conflicts": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    profile = db_session.execute(select(CompanyIdentityProfile)).scalar_one()
    assert profile.identity_status == "conflict"
    assert "Conflicts allowed" in (profile.review_notes or "")


def test_identity_apply_blocks_verified_profile_conflict(
    client: TestClient,
    db_session: Session,
) -> None:
    company = _company(db_session, name="Reviewed Issuer", ticker="REV", inn="7700000001")
    db_session.add(
        CompanyIdentityProfile(
            company_id=company.id,
            legal_name="Reviewed Issuer LLC",
            inn="7700000001",
            ogrn="1027700000001",
            issuer_role="legal_issuer",
            identity_status="verified",
            identity_source="manual_review",
            review_status="accepted",
        )
    )
    db_session.commit()

    response = client.post(
        "/api/companies/identity/apply",
        json={
            "rows": [
                _identity_row(
                    company,
                    legal_name="Different Issuer LLC",
                    inn="7700000002",
                    ogrn="1027700000002",
                )
            ],
            "confirm_apply": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    codes = {item["code"] for item in payload["errors"]}
    assert "verified_inn_conflict" in codes
    assert "verified_ogrn_conflict" in codes
    assert "verified_legal_name_conflict" in codes
    profile = db_session.execute(select(CompanyIdentityProfile)).scalar_one()
    assert profile.legal_name == "Reviewed Issuer LLC"
