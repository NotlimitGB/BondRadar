from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal


def create_company(db: Session, ticker: str = "BND") -> Company:
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
    final_company_score: int | None = 90,
    score: Decimal = Decimal("90.00"),
    source: str = "test",
) -> CompanyScore:
    company_score = CompanyScore(
        company_id=company.id,
        score=score,
        signal=AnalysisSignal.NEUTRAL.value,
        factors={},
        summary="Test company score",
        as_of_date=date(2026, 5, 14),
        source=source,
        final_company_score=final_company_score,
    )
    db.add(company_score)
    db.commit()
    db.refresh(company_score)
    return company_score


def create_bond(
    db: Session,
    company_id: int,
    *,
    isin: str = "RU000A100900",
    yield_to_maturity: Decimal | None = Decimal("16.00"),
    duration_years: Decimal | None = Decimal("2.00"),
    liquidity_score: int | None = None,
    volume: Decimal | None = Decimal("25000000.00"),
    maturity_date: date | None = date(2029, 5, 14),
    offer_date: date | None = None,
    amortization: bool | None = False,
) -> Bond:
    bond = Bond(
        company_id=company_id,
        isin=isin,
        name=f"Bond {isin}",
        currency="RUB",
        yield_to_maturity=yield_to_maturity,
        duration_years=duration_years,
        liquidity_score=liquidity_score,
        volume=volume,
        maturity_date=maturity_date,
        offer_date=offer_date,
        amortization=amortization,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def calculate(client: TestClient, bond_id: int):
    return client.post(f"/api/bonds/{bond_id}/calculate-score")


def test_bond_score_with_strong_issuer(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "STRB")
    company_score = create_company_score(db_session, company, final_company_score=90)
    bond = create_bond(db_session, company.id)

    response = calculate(client, bond.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["bond_id"] == bond.id
    assert payload["company_score_id"] == company_score.id
    assert payload["final_bond_score"] >= 80
    assert payload["signal"] == "interesting_for_analysis"
    assert payload["explanation"]["positive_factors"]


def test_bond_score_with_weak_issuer_and_high_yield(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "WEAK")
    create_company_score(db_session, company, final_company_score=35)
    bond = create_bond(
        db_session,
        company.id,
        isin="RU000A100901",
        yield_to_maturity=Decimal("20.00"),
    )

    response = calculate(client, bond.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_penalty"] >= 25
    assert payload["signal"] in {"increased_risk", "high_risk"}
    assert "High yield may reflect elevated issuer credit risk" in payload[
        "explanation"
    ]["risk_warnings"]


def test_bond_score_with_low_liquidity(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "LIQ")
    create_company_score(db_session, company, final_company_score=90)
    bond = create_bond(
        db_session,
        company.id,
        isin="RU000A100902",
        liquidity_score=30,
        volume=None,
    )

    response = calculate(client, bond.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["liquidity_score"] == 30
    assert payload["risk_penalty"] >= 15
    assert "Bond liquidity is limited" in payload["explanation"]["risk_warnings"]


def test_bond_score_with_long_duration(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "DUR")
    create_company_score(db_session, company, final_company_score=90)
    bond = create_bond(
        db_session,
        company.id,
        isin="RU000A100903",
        duration_years=Decimal("9.00"),
    )

    response = calculate(client, bond.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["duration_score"] == 25
    assert payload["risk_penalty"] >= 15
    assert "Long duration increases sensitivity to rate changes" in payload[
        "explanation"
    ]["risk_warnings"]


def test_bond_score_with_incomplete_data(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "MISSB")
    bond = create_bond(
        db_session,
        company.id,
        isin="RU000A100904",
        yield_to_maturity=None,
        duration_years=None,
        liquidity_score=None,
        volume=None,
    )

    response = calculate(client, bond.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["signal"] == "insufficient_data"
    assert payload["yield_score"] is None
    assert payload["duration_score"] is None
    assert payload["liquidity_score"] is None
    assert "Company score is missing" in payload["explanation"]["missing_data"]


def test_bond_score_for_missing_bond(client: TestClient) -> None:
    response = calculate(client, 999999)

    assert response.status_code == 404
    assert response.json()["detail"] == "Bond not found"


def test_bond_score_for_missing_company(
    client: TestClient, db_session: Session
) -> None:
    bond = create_bond(db_session, 999999, isin="RU000A100905")

    response = calculate(client, bond.id)

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_bond_score_without_company_score_does_not_fail(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "NCS")
    bond = create_bond(db_session, company.id, isin="RU000A100906")

    response = calculate(client, bond.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_score_id"] is None
    assert "Company score is missing" in payload["explanation"]["missing_data"]


def test_get_latest_bond_score(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "GETS")
    create_company_score(db_session, company, final_company_score=90)
    bond = create_bond(db_session, company.id, isin="RU000A100907")
    calculated = calculate(client, bond.id).json()

    response = client.get(f"/api/bonds/{bond.id}/score")

    assert response.status_code == 200
    assert response.json()["id"] == calculated["id"]


def test_get_latest_bond_score_missing_score(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "NOSC")
    bond = create_bond(db_session, company.id, isin="RU000A100908")

    response = client.get(f"/api/bonds/{bond.id}/score")

    assert response.status_code == 404
    assert response.json()["detail"] == "Bond score not found"


def test_recalculate_all_scores_handles_partial_failures(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "ALL")
    create_company_score(db_session, company, final_company_score=90)
    create_bond(db_session, company.id, isin="RU000A100909")
    create_bond(db_session, 999999, isin="RU000A100910")

    response = client.post("/api/scores/recalculate-all")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_bonds"] == 2
    assert payload["calculated"] == 1
    assert payload["failed"] == 1
    assert payload["errors"][0]["bond_id"]

