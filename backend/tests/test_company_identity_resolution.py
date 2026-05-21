from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal
from app.services.company_identity_resolution_service import (
    CompanyIdentityResolutionService,
)


def _company(db: Session, *, name: str, ticker: str) -> Company:
    company = Company(
        name=name,
        ticker=ticker,
        country="RU",
        signal=AnalysisSignal.INSUFFICIENT_DATA.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def _bond(db: Session, company: Company, *, secid: str) -> Bond:
    bond = Bond(
        company_id=company.id,
        secid=secid,
        isin=secid,
        name=f"{company.name} synthetic bond",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        signal=AnalysisSignal.INSUFFICIENT_DATA.value,
    )
    db.add(bond)
    db.commit()
    return bond


def _profile(db: Session, company: Company, *, status: str = "matched") -> None:
    db.add(
        CompanyIdentityProfile(
            company_id=company.id,
            legal_name=f"{company.name} LLC",
            display_name=company.name,
            short_name=company.name,
            issuer_role="legal_issuer",
            identity_status=status,
            identity_source="manual_review",
            review_status="reviewed",
        )
    )
    db.commit()


def _duplicate(
    db: Session,
    canonical: Company,
    candidate: Company,
    *,
    status: str = "accepted",
    review_status: str = "reviewed",
) -> None:
    db.add(
        CompanyIdentityDuplicateCandidate(
            canonical_company_id=canonical.id,
            candidate_company_id=candidate.id,
            group_key="manual:synthetic",
            match_type="bond_name_phrase",
            match_score=Decimal("0.7500"),
            match_reasons=["Synthetic reviewed duplicate relation"],
            status=status,
            review_status=review_status,
            source="manual_review",
        )
    )
    db.commit()


def test_unmapped_company_resolves_to_itself(db_session: Session) -> None:
    company = _company(db_session, name="Standalone", ticker="STAND")

    resolved = CompanyIdentityResolutionService(db_session).resolve_company(company.id)

    assert resolved.company_id == company.id
    assert resolved.canonical_company_id == company.id
    assert resolved.is_canonical is True
    assert resolved.is_duplicate_candidate is False


def test_accepted_reviewed_candidate_resolves_to_canonical(db_session: Session) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Unknown issuer for RU000SYN001", ticker="SYN")
    _duplicate(db_session, canonical, candidate)

    resolved = CompanyIdentityResolutionService(db_session).resolve_company(candidate.id)

    assert resolved.canonical_company_id == canonical.id
    assert resolved.canonical_company_name == "Canonical"
    assert resolved.is_duplicate_candidate is True
    assert resolved.duplicate_mapping_status == "accepted"
    assert resolved.duplicate_review_status == "reviewed"


def test_pending_and_rejected_mappings_are_ignored(db_session: Session) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    pending = _company(db_session, name="Pending", ticker="PEN")
    rejected = _company(db_session, name="Rejected", ticker="REJ")
    _duplicate(db_session, canonical, pending, status="accepted", review_status="pending")
    _duplicate(db_session, canonical, rejected, status="rejected", review_status="reviewed")

    service = CompanyIdentityResolutionService(db_session)

    assert service.resolve_company(pending.id).canonical_company_id == pending.id
    assert service.resolve_company(rejected.id).canonical_company_id == rejected.id


def test_multiple_accepted_mappings_return_conflict_warning(db_session: Session) -> None:
    first = _company(db_session, name="Canonical A", ticker="A")
    second = _company(db_session, name="Canonical B", ticker="B")
    candidate = _company(db_session, name="Candidate", ticker="C")
    _duplicate(db_session, first, candidate)
    _duplicate(db_session, second, candidate)

    resolved = CompanyIdentityResolutionService(db_session).resolve_company(candidate.id)

    assert resolved.canonical_company_id == candidate.id
    assert resolved.warnings[0].code == "multiple_accepted_canonical_mappings"


def test_canonical_groups_endpoint_returns_members(
    client: TestClient,
    db_session: Session,
) -> None:
    canonical = _company(db_session, name="Canonical", ticker="CAN")
    candidate = _company(db_session, name="Candidate", ticker="CND")
    _profile(db_session, canonical)
    _bond(db_session, canonical, secid="RU000CAN001")
    _bond(db_session, candidate, secid="RU000CND001")
    _duplicate(db_session, canonical, candidate)

    response = client.get("/api/companies/identity/canonical-groups?active_only=true")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "passed"
    assert payload["group_count"] == 1
    assert payload["duplicate_mapping_count"] == 1
    group = payload["groups"][0]
    assert group["canonical_company_id"] == canonical.id
    assert group["canonical_identity_status"] == "matched"
    assert group["duplicate_company_ids"] == [candidate.id]
    assert group["duplicate_members"][0]["duplicate_match_score"] == "0.7500"
