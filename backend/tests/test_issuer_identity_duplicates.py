from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal


def _company(
    db: Session,
    *,
    name: str,
    ticker: str,
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


def _bond(db: Session, company: Company, *, secid: str, name: str) -> Bond:
    bond = Bond(
        company_id=company.id,
        secid=secid,
        isin=secid,
        name=name,
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        signal=AnalysisSignal.INSUFFICIENT_DATA.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def _profile(db: Session, company: Company, **overrides) -> CompanyIdentityProfile:
    values = {
        "company_id": company.id,
        "legal_name": f"{company.name} LLC",
        "display_name": company.name,
        "short_name": company.name,
        "inn": company.inn,
        "ogrn": None,
        "issuer_role": "legal_issuer",
        "identity_status": "matched",
        "identity_source": "manual_review",
        "review_status": "reviewed",
    }
    values.update(overrides)
    profile = CompanyIdentityProfile(**values)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def _review_row(canonical: Company, candidate: Company, **overrides) -> dict:
    row = {
        "canonical_company_id": canonical.id,
        "canonical_company_name": canonical.name,
        "candidate_company_id": candidate.id,
        "candidate_company_name": candidate.name,
        "match_type": "manual_review",
        "match_score": "0.7500",
        "match_reasons": ["Synthetic reviewed duplicate relation"],
        "status": "accepted",
        "review_status": "reviewed",
        "review_notes": "Synthetic duplicate review.",
    }
    row.update(overrides)
    return row


def test_duplicate_diagnostics_same_inn_high_confidence(
    client: TestClient,
    db_session: Session,
) -> None:
    first = _company(db_session, name="Synthetic A", ticker="A")
    second = _company(db_session, name="Synthetic B", ticker="B")
    _profile(db_session, first, legal_name="Synthetic A LLC", inn="7700000001")
    _profile(db_session, second, legal_name="Synthetic B LLC", inn="7700000001")
    _bond(db_session, first, secid="RU000SYN001", name="Synthetic A BO 001")
    _bond(db_session, second, secid="RU000SYN002", name="Synthetic B BO 001")

    response = client.get("/api/companies/identity/duplicates/diagnostics?min_score=0.70")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert payload["high_confidence_count"] == 1
    candidate = payload["groups"][0]["candidates"][0]
    assert candidate["match_type"] == "exact_inn"
    assert candidate["match_score"] == "1.0000"


def test_duplicate_diagnostics_same_ogrn_and_legal_name(
    client: TestClient,
    db_session: Session,
) -> None:
    first = _company(db_session, name="Issuer One", ticker="ONE")
    second = _company(db_session, name="Issuer Two", ticker="TWO")
    _profile(
        db_session,
        first,
        legal_name='Открытое акционерное общество "Синтетика"',
        display_name="Синтетика",
        ogrn="1027700000001",
    )
    _profile(
        db_session,
        second,
        legal_name="ОАО Синтетика",
        display_name="Синтетика",
        ogrn="1027700000001",
    )
    _bond(db_session, first, secid="RU000SYN101", name="Синтетика БО 001")
    _bond(db_session, second, secid="RU000SYN102", name="Синтетика БО 002")

    response = client.get("/api/companies/identity/duplicates/diagnostics?min_score=0.90")

    assert response.status_code == 200
    payload = response.json()
    match_types = {
        candidate["match_type"]
        for group in payload["groups"]
        for candidate in group["candidates"]
    }
    assert "exact_ogrn" in match_types or "exact_legal_name" in match_types
    assert payload["high_confidence_count"] >= 1


def test_duplicate_diagnostics_display_name_in_unknown_bond_name(
    client: TestClient,
    db_session: Session,
) -> None:
    canonical = _company(db_session, name="РЖД", ticker="RZD")
    candidate = _company(
        db_session,
        name="Unknown issuer for RU000A100HY9",
        ticker="MOEX_RU000A100HY9",
    )
    _profile(
        db_session,
        canonical,
        legal_name='Открытое акционерное общество "Российские железные дороги"',
        display_name="РЖД",
        short_name="РЖД",
        identity_status="matched",
    )
    _bond(db_session, canonical, secid="RU000RZD001", name="РЖД БО 001")
    _bond(db_session, candidate, secid="RU000A100HY9", name="РЖД ОАО БО 001Р-16R")

    response = client.get("/api/companies/identity/duplicates/diagnostics?min_score=0.70")

    assert response.status_code == 200
    payload = response.json()
    candidate_row = payload["groups"][0]["candidates"][0]
    assert payload["medium_confidence_count"] >= 1
    assert candidate_row["company_id"] == candidate.id
    assert candidate_row["match_type"] == "bond_name_phrase"
    assert candidate_row["match_score"] == "0.7500"


def test_duplicate_diagnostics_weak_phrase_below_threshold_ignored(
    client: TestClient,
    db_session: Session,
) -> None:
    first = _company(db_session, name="AA", ticker="AA")
    second = _company(db_session, name="BB", ticker="BB")
    _bond(db_session, first, secid="RU000SYN201", name="AA BO 001")
    _bond(db_session, second, secid="RU000SYN202", name="AA BO 002")

    response = client.get("/api/companies/identity/duplicates/diagnostics?min_score=0.70")

    assert response.status_code == 200
    assert response.json()["candidate_pair_count"] == 0


def test_rejected_persisted_candidate_excluded_unless_requested(
    client: TestClient,
    db_session: Session,
) -> None:
    first = _company(db_session, name="Synthetic A", ticker="A")
    second = _company(db_session, name="Synthetic B", ticker="B")
    _profile(db_session, first, inn="7700000001")
    _profile(db_session, second, inn="7700000001")
    _bond(db_session, first, secid="RU000SYN301", name="Synthetic A BO 001")
    _bond(db_session, second, secid="RU000SYN302", name="Synthetic B BO 001")
    db_session.add(
        CompanyIdentityDuplicateCandidate(
            canonical_company_id=first.id,
            candidate_company_id=second.id,
            group_key="manual:rejected",
            match_type="manual_review",
            match_score=Decimal("1.0000"),
            match_reasons=["Rejected during synthetic review"],
            status="rejected",
            review_status="rejected",
            source="manual_review",
        )
    )
    db_session.commit()

    hidden = client.get("/api/companies/identity/duplicates/diagnostics?min_score=0.90")
    shown = client.get(
        "/api/companies/identity/duplicates/diagnostics?min_score=0.90&include_rejected=true"
    )

    assert hidden.status_code == 200
    assert shown.status_code == 200
    assert hidden.json()["candidate_pair_count"] == 0
    assert shown.json()["candidate_pair_count"] == 1
    assert shown.json()["groups"][0]["candidates"][0]["persisted_status"] == "rejected"


def test_duplicate_preview_is_read_only(client: TestClient, db_session: Session) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Candidate", ticker="CND")
    _profile(db_session, canonical)

    response = client.post(
        "/api/companies/identity/duplicates/preview",
        json={"rows": [_review_row(canonical, candidate)]},
    )

    assert response.status_code == 200
    assert response.json()["would_create_duplicate_candidates"] == 1
    assert db_session.execute(select(CompanyIdentityDuplicateCandidate)).scalar_one_or_none() is None


def test_duplicate_apply_requires_confirmation(
    client: TestClient,
    db_session: Session,
) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Candidate", ticker="CND")
    _profile(db_session, canonical)

    response = client.post(
        "/api/companies/identity/duplicates/apply",
        json={"rows": [_review_row(canonical, candidate)], "confirm_apply": False},
    )

    assert response.status_code == 400
    assert db_session.execute(select(CompanyIdentityDuplicateCandidate)).scalar_one_or_none() is None


def test_duplicate_apply_persists_decision_without_merge_or_bond_move(
    client: TestClient,
    db_session: Session,
) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Candidate", ticker="CND")
    _profile(db_session, canonical)
    bond = _bond(db_session, candidate, secid="RU000SYN401", name="Candidate BO 001")

    response = client.post(
        "/api/companies/identity/duplicates/apply",
        json={"rows": [_review_row(canonical, candidate)], "confirm_apply": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["affected_rows_summary"]["created_candidate_count"] == 1
    decision = db_session.execute(select(CompanyIdentityDuplicateCandidate)).scalar_one()
    assert decision.canonical_company_id == canonical.id
    assert decision.candidate_company_id == candidate.id
    db_session.refresh(bond)
    assert bond.company_id == candidate.id
    assert len(db_session.execute(select(Company)).scalars().all()) == 2


def test_duplicate_apply_blocks_verified_candidate_conflict(
    client: TestClient,
    db_session: Session,
) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Candidate", ticker="CND")
    _profile(db_session, canonical, inn="7700000001", ogrn="1027700000001")
    _profile(
        db_session,
        candidate,
        inn="7700000002",
        ogrn="1027700000002",
        identity_status="verified",
        review_status="accepted",
    )

    response = client.post(
        "/api/companies/identity/duplicates/apply",
        json={"rows": [_review_row(canonical, candidate)], "confirm_apply": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "failed"
    codes = {item["code"] for item in payload["errors"]}
    assert "candidate_verified_inn_conflict" in codes
    assert db_session.execute(select(CompanyIdentityDuplicateCandidate)).scalar_one_or_none() is None


def test_duplicate_apply_allows_conflicts_when_explicit(
    client: TestClient,
    db_session: Session,
) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Candidate", ticker="CND")
    _profile(db_session, canonical, inn="7700000001", ogrn="1027700000001")
    _profile(
        db_session,
        candidate,
        inn="7700000002",
        ogrn="1027700000002",
        identity_status="verified",
        review_status="accepted",
    )

    response = client.post(
        "/api/companies/identity/duplicates/apply",
        json={
            "rows": [_review_row(canonical, candidate)],
            "confirm_apply": True,
            "allow_conflicts": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "warning"
    assert db_session.execute(select(CompanyIdentityDuplicateCandidate)).scalar_one()
