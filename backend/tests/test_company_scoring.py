from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


def create_company(db: Session, ticker: str = "TST") -> Company:
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


def create_report(
    db: Session,
    company: Company,
    *,
    period_year: int = 2025,
    period_quarter: int = 0,
    revenue: Decimal | None = Decimal("100.00"),
    ebitda: Decimal | None = Decimal("50.00"),
    total_debt: Decimal | None = Decimal("100.00"),
    cash: Decimal | None = Decimal("30.00"),
    equity: Decimal | None = Decimal("200.00"),
    short_term_debt: Decimal | None = Decimal("20.00"),
    operating_cash_flow: Decimal | None = Decimal("40.00"),
    net_profit: Decimal | None = Decimal("20.00"),
    interest_expense: Decimal | None = Decimal("10.00"),
) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=period_year,
        period_quarter=period_quarter,
        revenue=revenue,
        ebitda=ebitda,
        total_debt=total_debt,
        cash=cash,
        equity=equity,
        short_term_debt=short_term_debt,
        operating_cash_flow=operating_cash_flow,
        net_profit=net_profit,
        interest_expense=interest_expense,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def calculate(client: TestClient, company_id: int):
    return client.post(f"/api/companies/{company_id}/calculate-score")


def test_calculate_score_for_strong_company(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "STR")
    report = create_report(db_session, company)

    response = calculate(client, company.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_id"] == company.id
    assert payload["report_id"] == report.id
    assert payload["debt_score"] == 100
    assert payload["profitability_score"] == 100
    assert payload["liquidity_score"] == 100
    assert payload["cashflow_score"] == 100
    assert payload["stability_score"] == 90
    assert payload["final_company_score"] == 99
    assert payload["risk_level"] == "low"
    assert payload["explanation"]["ratios"]["net_debt_to_ebitda"] == 1.4


def test_calculate_score_for_high_debt_company(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "DEBT")
    create_report(
        db_session,
        company,
        revenue=Decimal("100.00"),
        ebitda=Decimal("50.00"),
        total_debt=Decimal("600.00"),
        cash=Decimal("0.00"),
        equity=Decimal("100.00"),
        short_term_debt=Decimal("100.00"),
        operating_cash_flow=Decimal("50.00"),
        net_profit=Decimal("10.00"),
    )

    response = calculate(client, company.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["debt_score"] == 20
    assert payload["risk_level"] == "high"
    assert "Долговая нагрузка находится на повышенном уровне" in payload[
        "explanation"
    ]["negative_factors"]


def test_calculate_score_for_unprofitable_company(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "LOSS")
    create_report(
        db_session,
        company,
        net_profit=Decimal("-10.00"),
        operating_cash_flow=Decimal("20.00"),
    )

    response = calculate(client, company.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["profitability_score"] == 20
    assert payload["stability_score"] == 70
    assert payload["explanation"]["ratios"]["net_profit_margin"] == -0.1


def test_calculate_score_with_incomplete_report_data(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "MISS")
    create_report(
        db_session,
        company,
        ebitda=None,
        equity=None,
        short_term_debt=None,
        operating_cash_flow=None,
        net_profit=None,
        interest_expense=None,
    )

    response = calculate(client, company.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["risk_level"] == "insufficient_data"
    assert payload["explanation"]["ratios"]["net_debt_to_ebitda"] is None
    assert len(payload["explanation"]["missing_data"]) >= 4


def test_calculate_score_for_missing_company(client: TestClient) -> None:
    response = calculate(client, 999999)

    assert response.status_code == 404
    assert response.json()["detail"] == "Company not found"


def test_calculate_score_for_company_without_reports(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "NREP")

    response = calculate(client, company.id)

    assert response.status_code == 404
    assert response.json()["detail"] == "Financial report for company not found"


def test_latest_report_prefers_fy_over_q4(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "FY")
    create_report(
        db_session,
        company,
        period_year=2025,
        period_quarter=4,
        revenue=Decimal("100.00"),
        net_profit=Decimal("-30.00"),
        operating_cash_flow=Decimal("-20.00"),
    )
    fy_report = create_report(
        db_session,
        company,
        period_year=2025,
        period_quarter=0,
        revenue=Decimal("100.00"),
        net_profit=Decimal("20.00"),
        operating_cash_flow=Decimal("40.00"),
    )

    response = calculate(client, company.id)

    assert response.status_code == 200
    payload = response.json()
    assert payload["report_id"] == fy_report.id
    assert payload["profitability_score"] == 100

