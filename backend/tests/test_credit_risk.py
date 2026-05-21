from datetime import date
from decimal import Decimal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


def create_company(db: Session, ticker: str = "CRD") -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def create_company_score(
    db: Session,
    company: Company,
    *,
    value: int = 85,
) -> CompanyScore:
    score = CompanyScore(
        company_id=company.id,
        score=Decimal(value),
        signal=AnalysisSignal.NEUTRAL.value,
        factors={},
        summary="Credit risk test score",
        as_of_date=date(2026, 5, 14),
        source=f"credit-risk-test-{value}",
        final_company_score=value,
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def create_report(
    db: Session,
    company: Company,
    *,
    strong: bool = True,
) -> FinancialReport:
    if strong:
        values = {
            "revenue": Decimal("1000.00"),
            "ebitda": Decimal("300.00"),
            "net_debt": Decimal("250.00"),
            "total_debt": Decimal("350.00"),
            "cash": Decimal("100.00"),
            "equity": Decimal("700.00"),
            "short_term_debt": Decimal("80.00"),
            "operating_cash_flow": Decimal("120.00"),
            "net_profit": Decimal("140.00"),
            "interest_expense": Decimal("40.00"),
        }
    else:
        values = {
            "revenue": Decimal("500.00"),
            "ebitda": Decimal("50.00"),
            "net_debt": Decimal("320.00"),
            "total_debt": Decimal("600.00"),
            "cash": Decimal("10.00"),
            "equity": Decimal("-50.00"),
            "short_term_debt": Decimal("120.00"),
            "operating_cash_flow": Decimal("-20.00"),
            "net_profit": Decimal("-80.00"),
            "interest_expense": Decimal("80.00"),
        }
    report = FinancialReport(
        company_id=company.id,
        period_year=2025,
        period_quarter=0,
        signal=AnalysisSignal.NEUTRAL.value,
        **values,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def create_bond(
    db: Session,
    company: Company,
    *,
    isin: str = "RU000CRD001",
    yield_to_maturity: Decimal = Decimal("12.00"),
    liquidity_score: int | None = 80,
    duration_years: Decimal = Decimal("2.00"),
    is_subordinated: bool = False,
    is_perpetual: bool = False,
    amortization: bool | None = False,
    offer_date: date | None = None,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        name=f"Credit Risk Bond {isin}",
        currency="RUB",
        coupon_rate=Decimal("10.00"),
        yield_to_maturity=yield_to_maturity,
        duration_years=duration_years,
        liquidity_score=liquidity_score,
        volume=Decimal("25000000.00"),
        maturity_date=date(2030, 5, 14),
        offer_date=offer_date,
        is_subordinated=is_subordinated,
        is_perpetual=is_perpetual,
        amortization=amortization,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def test_stable_company_health(client: TestClient, db_session: Session) -> None:
    company = create_company(db_session, "STBL")
    create_report(db_session, company, strong=True)
    create_company_score(db_session, company, value=90)

    response = client.post(f"/api/credit-risk/companies/{company.id}/calculate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["credit_status"] == "credit_stable"
    assert payload["risk_level"] == "low"
    assert payload["positive_factors"]
    assert payload["credit_health_score"] >= 80


def test_distressed_company_health(client: TestClient, db_session: Session) -> None:
    company = create_company(db_session, "DIST")
    create_report(db_session, company, strong=False)
    create_company_score(db_session, company, value=25)

    response = client.post(f"/api/credit-risk/companies/{company.id}/calculate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["credit_status"] == "credit_distressed"
    assert payload["risk_level"] == "critical"
    assert any("Interest coverage" in item for item in payload["risk_factors"])
    assert any("Equity is negative" in item for item in payload["risk_factors"])


def test_missing_financial_report_does_not_crash(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "NREP")

    response = client.post(f"/api/credit-risk/companies/{company.id}/calculate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["credit_status"] == "insufficient_data"
    assert "Financial report is missing" in payload["missing_data"]


def test_insufficient_credit_data_does_not_become_confirmed_block(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "BINS")
    bond = create_bond(db_session, company, isin="RU000CRD010")

    response = client.post(f"/api/credit-risk/bonds/{bond.id}/assess")

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_credit_status"] == "insufficient_data"
    assert payload["decision_status"] == "insufficient_data"
    assert payload["risk_level"] == "unknown"
    assert payload["gates"]["credit_gate"] == "warning"


def test_missing_company_score_does_not_crash(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "NSCR")
    create_report(db_session, company, strong=True)

    response = client.post(f"/api/credit-risk/companies/{company.id}/calculate")

    assert response.status_code == 200
    payload = response.json()
    assert "Company score is missing" in payload["missing_data"]


def test_high_yield_weak_issuer_warning(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "HYLD")
    create_report(db_session, company, strong=False)
    create_company_score(db_session, company, value=25)
    bond = create_bond(
        db_session,
        company,
        isin="RU000CRD002",
        yield_to_maturity=Decimal("22.00"),
    )

    response = client.post(f"/api/credit-risk/bonds/{bond.id}/assess")

    assert response.status_code == 200
    payload = response.json()
    assert "High yield may reflect elevated credit/default risk" in payload["warnings"]
    assert payload["decision_status"] == "blocked_by_risk"
    assert "High yield is combined with weak issuer credit" in payload["blocking_reasons"]


def test_credit_gate_blocks_distressed_issuer(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "BLCK")
    create_report(db_session, company, strong=False)
    create_company_score(db_session, company, value=25)
    bond = create_bond(db_session, company, isin="RU000CRD003")

    response = client.post(f"/api/credit-risk/bonds/{bond.id}/assess")

    assert response.status_code == 200
    payload = response.json()
    assert payload["decision_status"] == "blocked_by_risk"
    assert payload["gates"]["credit_gate"] == "blocked"


def test_liquidity_gate(client: TestClient, db_session: Session) -> None:
    company = create_company(db_session, "LIQG")
    create_report(db_session, company, strong=True)
    create_company_score(db_session, company, value=90)
    bond = create_bond(
        db_session,
        company,
        isin="RU000CRD004",
        liquidity_score=30,
    )

    response = client.post(f"/api/credit-risk/bonds/{bond.id}/assess")

    assert response.status_code == 200
    payload = response.json()
    assert payload["gates"]["liquidity_gate"] == "blocked"
    assert any("Liquidity" in item for item in payload["blocking_reasons"])


def test_structure_gate_warnings(client: TestClient, db_session: Session) -> None:
    company = create_company(db_session, "STRC")
    create_report(db_session, company, strong=True)
    create_company_score(db_session, company, value=90)
    bond = create_bond(
        db_session,
        company,
        isin="RU000CRD005",
        is_subordinated=True,
        is_perpetual=True,
        amortization=True,
        offer_date=date(2028, 5, 14),
    )

    response = client.post(f"/api/credit-risk/bonds/{bond.id}/assess")

    assert response.status_code == 200
    payload = response.json()
    assert payload["gates"]["structure_gate"] == "warning"
    assert "Bond is subordinated" in payload["warnings"]
    assert "Bond is perpetual" in payload["warnings"]
    assert "Bond has amortization schedule" in payload["warnings"]
    assert "Bond has offer date before maturity" in payload["warnings"]


def test_latest_endpoints(client: TestClient, db_session: Session) -> None:
    company = create_company(db_session, "LAT")
    create_report(db_session, company, strong=True)
    create_company_score(db_session, company, value=90)
    bond = create_bond(db_session, company, isin="RU000CRD006")
    health = client.post(f"/api/credit-risk/companies/{company.id}/calculate").json()
    assessment = client.post(f"/api/credit-risk/bonds/{bond.id}/assess").json()

    latest_health = client.get(f"/api/credit-risk/companies/{company.id}/latest")
    latest_assessment = client.get(f"/api/credit-risk/bonds/{bond.id}/latest")

    assert latest_health.status_code == 200
    assert latest_health.json()["id"] == health["id"]
    assert latest_assessment.status_code == 200
    assert latest_assessment.json()["id"] == assessment["id"]


def test_recalculate_all_handles_partial_errors(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "ALLR")
    create_report(db_session, company, strong=True)
    create_company_score(db_session, company, value=90)
    create_bond(db_session, company, isin="RU000CRD007")
    orphan_bond = Bond(
        company_id=999999,
        isin="RU000CRD008",
        name="Orphan risk bond",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db_session.add(orphan_bond)
    db_session.commit()

    response = client.post("/api/credit-risk/recalculate-all")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["calculated"] == 2
    assert payload["failed"] == 1
    assert payload["errors"][0]["entity_type"] == "bond"


def test_no_recommendation_vocabulary(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "VOC")
    create_report(db_session, company, strong=True)
    create_company_score(db_session, company, value=90)
    bond = create_bond(db_session, company, isin="RU000CRD009")

    health_payload = client.post(
        f"/api/credit-risk/companies/{company.id}/calculate"
    ).json()
    assessment_payload = client.post(f"/api/credit-risk/bonds/{bond.id}/assess").json()

    assert_no_forbidden_investment_vocabulary([health_payload, assessment_payload])

