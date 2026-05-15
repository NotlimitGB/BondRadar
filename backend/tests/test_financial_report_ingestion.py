from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.models.financial_report_import_run import FinancialReportImportRun
from app.models.financial_report_source_document import FinancialReportSourceDocument
from app.services.company_credit_health_service import CompanyCreditHealthService
from app.services.feature_snapshot_service import FeatureSnapshotService


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(
    db: Session,
    *,
    ticker: str = "FRI",
    inn: str = "7701000001",
) -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        inn=inn,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def create_company_score(db: Session, company: Company) -> CompanyScore:
    score = CompanyScore(
        company_id=company.id,
        score=Decimal("80.00"),
        signal=AnalysisSignal.NEUTRAL.value,
        factors={},
        summary="Financial ingestion test score",
        as_of_date=date(2025, 1, 1),
        source="financial-ingestion-test",
        final_company_score=80,
        created_at=dt(2025, 1, 1),
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def create_report(
    db: Session,
    company: Company,
    *,
    period_year: int = 2024,
    period_quarter: int = 0,
    revenue: Decimal = Decimal("1000.00"),
    published_at: datetime | None = None,
) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=period_year,
        period_quarter=period_quarter,
        revenue=revenue,
        ebitda=Decimal("250.00"),
        total_debt=Decimal("400.00"),
        cash=Decimal("100.00"),
        equity=Decimal("600.00"),
        short_term_debt=Decimal("120.00"),
        operating_cash_flow=Decimal("150.00"),
        net_profit=Decimal("120.00"),
        interest_expense=Decimal("50.00"),
        source="test",
        published_at=published_at,
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def create_bond_with_snapshot(db: Session, company: Company) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000{company.ticker[:3]}001",
        name=f"{company.ticker} Bond",
        currency="RUB",
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=75,
        volume=Decimal("1000000.00"),
        maturity_date=date(2030, 1, 1),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    snapshot = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=date(2025, 2, 1),
        price=Decimal("100.000000"),
        clean_price=Decimal("100.000000"),
        dirty_price=Decimal("101.000000"),
        nkd=Decimal("10.000000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        liquidity_score=75,
        source="test",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(bond)
    return bond


def ingest_payload(company: Company, **overrides):
    row = {
        "company_id": company.id,
        "period_year": 2024,
        "period_quarter": 4,
        "published_at": "2025-03-15T12:00:00Z",
        "currency": "RUB",
        "source": "manual",
        "source_file_name": "report-2024-q4.json",
        "revenue": "1000,50",
        "ebitda": "250",
        "total_debt": "400",
        "cash": "100",
        "equity": "600",
        "short_term_debt": "120",
        "operating_cash_flow": "150",
        "net_profit": "120",
        "interest_expense": "50",
    }
    row.update(overrides)
    return {"source": "manual", "rows": [row], "rebuild_existing": False}


def test_json_ingest_creates_report_source_document_and_run(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session)

    response = client.post("/api/financial-reports/ingest", json=ingest_payload(company))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["created"] == 1
    assert payload["warnings"] == []

    report = db_session.execute(
        select(FinancialReport).where(FinancialReport.company_id == company.id)
    ).scalar_one()
    assert report.revenue == Decimal("1000.50")
    assert report.published_at is not None
    assert report.currency == "RUB"

    document = db_session.execute(
        select(FinancialReportSourceDocument).where(
            FinancialReportSourceDocument.financial_report_id == report.id
        )
    ).scalar_one()
    assert document.source_file_name == "report-2024-q4.json"
    assert document.status == "linked"

    run = db_session.get(FinancialReportImportRun, payload["run_id"])
    assert run is not None
    assert run.total_rows == 1


def test_ingest_is_idempotent_and_skips_existing_by_default(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="SKIP", inn="7701000002")
    request = ingest_payload(company)

    first = client.post("/api/financial-reports/ingest", json=request)
    second = client.post("/api/financial-reports/ingest", json=request)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["skipped"] == 1
    report_count = db_session.scalar(
        select(func.count()).select_from(FinancialReport).where(
            FinancialReport.company_id == company.id
        )
    )
    document_count = db_session.scalar(
        select(func.count()).select_from(FinancialReportSourceDocument).where(
            FinancialReportSourceDocument.company_id == company.id
        )
    )
    assert report_count == 1
    assert document_count == 1


def test_rebuild_existing_updates_report(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="RBLD", inn="7701000003")
    client.post("/api/financial-reports/ingest", json=ingest_payload(company))
    request = ingest_payload(company, revenue="2222")
    request["rebuild_existing"] = True

    response = client.post("/api/financial-reports/ingest", json=request)

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    report = db_session.execute(
        select(FinancialReport).where(FinancialReport.company_id == company.id)
    ).scalar_one()
    assert report.revenue == Decimal("2222.00")


def test_company_resolution_by_ticker_and_inn(
    client: TestClient,
    db_session: Session,
) -> None:
    ticker_company = create_company(db_session, ticker="TICK", inn="7701000004")
    inn_company = create_company(db_session, ticker="INNR", inn="7701000005")

    response = client.post(
        "/api/financial-reports/ingest",
        json={
            "source": "manual",
            "rows": [
                ingest_payload(ticker_company, company_id=None, company_ticker="TICK")[
                    "rows"
                ][0],
                ingest_payload(
                    inn_company,
                    company_id=None,
                    company_inn="7701000005",
                    period_quarter=3,
                    source_file_name="report-2024-q3.json",
                )["rows"][0],
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["created"] == 2


def test_unknown_company_and_invalid_decimal_are_row_level_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="ERRS", inn="7701000006")

    response = client.post(
        "/api/financial-reports/ingest",
        json={
            "source": "manual",
            "rows": [
                ingest_payload(company, company_id=999999)["rows"][0],
                ingest_payload(
                    company,
                    revenue="not-a-number",
                    period_quarter=3,
                    source_file_name="bad-decimal.json",
                )["rows"][0],
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_errors"
    assert payload["failed"] == 2
    assert payload["errors"][0]["row_index"] == 1
    assert payload["errors"][0]["period_year"] == 2024
    assert payload["errors"][0]["period_quarter"] == 4
    assert payload["errors"][0]["message"] == "Company not found"
    assert payload["errors"][1]["company_id"] == company.id
    assert payload["errors"][1]["message"] == "Invalid decimal value for revenue"


def test_future_published_report_is_not_used_by_credit_health(
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="LEAK", inn="7701000007")
    create_company_score(db_session, company)
    earlier = create_report(
        db_session,
        company,
        period_year=2023,
        period_quarter=0,
        revenue=Decimal("1000.00"),
        published_at=dt(2025, 1, 15),
    )
    create_report(
        db_session,
        company,
        period_year=2024,
        period_quarter=0,
        revenue=Decimal("9999.00"),
        published_at=dt(2025, 3, 15),
    )

    snapshot = CompanyCreditHealthService(db_session).calculate_for_company(
        company.id,
        as_of_date=date(2025, 2, 1),
    )

    assert snapshot.financial_report_id == earlier.id
    assert snapshot.revenue == Decimal("1000.00")


def test_future_published_report_is_not_used_by_feature_snapshot(
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="FLEK", inn="7701000008")
    bond = create_bond_with_snapshot(db_session, company)
    earlier = create_report(
        db_session,
        company,
        period_year=2023,
        period_quarter=0,
        revenue=Decimal("1000.00"),
        published_at=dt(2025, 1, 15),
    )
    create_report(
        db_session,
        company,
        period_year=2024,
        period_quarter=0,
        revenue=Decimal("9999.00"),
        published_at=dt(2025, 3, 15),
    )

    outcome = FeatureSnapshotService(db_session).build_for_bond_date(
        bond.id,
        date(2025, 2, 1),
        rebuild_existing=True,
    )

    assert outcome.snapshot.financial_report_id == earlier.id


def test_missing_published_at_legacy_fallback_records_warning(
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="MISS", inn="7701000009")
    create_company_score(db_session, company)
    legacy = create_report(
        db_session,
        company,
        period_year=2024,
        period_quarter=0,
        published_at=None,
    )
    bond = create_bond_with_snapshot(db_session, company)

    health = CompanyCreditHealthService(db_session).calculate_for_company(
        company.id,
        as_of_date=date(2025, 2, 1),
    )
    feature = FeatureSnapshotService(db_session).build_for_bond_date(
        bond.id,
        date(2025, 2, 1),
        rebuild_existing=True,
    ).snapshot

    warning = "Financial report publication date is missing, fallback selection was used"
    assert health.financial_report_id == legacy.id
    assert warning in health.risk_factors
    assert health.explanation["risk_factors"].count(warning) == 1
    assert feature.financial_report_id == legacy.id
    assert feature.features_json["leakage_warning"] == warning


def test_annual_report_period_quarter_zero_keeps_priority_over_q4(
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="ANNU", inn="7701000010")
    create_company_score(db_session, company)
    annual = create_report(
        db_session,
        company,
        period_year=2024,
        period_quarter=0,
        revenue=Decimal("3000.00"),
        published_at=dt(2025, 1, 15),
    )
    create_report(
        db_session,
        company,
        period_year=2024,
        period_quarter=4,
        revenue=Decimal("9999.00"),
        published_at=dt(2025, 1, 15),
    )

    snapshot = CompanyCreditHealthService(db_session).calculate_for_company(
        company.id,
        as_of_date=date(2025, 2, 1),
    )

    assert annual.period_quarter == 0
    assert snapshot.financial_report_id == annual.id


def test_list_import_runs_and_source_documents(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, ticker="LIST", inn="7701000011")
    response = client.post("/api/financial-reports/ingest", json=ingest_payload(company))
    assert response.status_code == 200

    runs_response = client.get("/api/financial-reports/import-runs")
    documents_response = client.get(
        f"/api/financial-reports/source-documents?company_id={company.id}"
    )

    assert runs_response.status_code == 200
    assert len(runs_response.json()) == 1
    assert documents_response.status_code == 200
    assert len(documents_response.json()) == 1
