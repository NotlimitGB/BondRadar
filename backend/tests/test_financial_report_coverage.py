from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


COVERAGE_URL = "/api/data-readiness/financial-reports/coverage"


def create_company(db: Session, ticker: str) -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(
    db: Session,
    company: Company,
    index: int,
    *,
    isin: str | None = None,
    secid: str | None = None,
    name: str | None = None,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin or f"RU000FRC{index:03d}"[:12],
        secid=secid or f"FRC{index:05d}",
        name=name or f"Financial coverage bond {index}",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def create_report(
    db: Session,
    company: Company,
    *,
    period_end_date: date,
    full: bool = True,
) -> FinancialReport:
    values = {
        "revenue": Decimal("1000.00"),
        "ebitda": Decimal("250.00"),
        "net_debt": Decimal("300.00"),
        "total_debt": Decimal("400.00"),
        "cash": Decimal("120.00"),
        "equity": Decimal("800.00"),
        "short_term_debt": Decimal("100.00"),
        "operating_cash_flow": Decimal("180.00"),
        "net_profit": Decimal("100.00"),
        "interest_expense": Decimal("50.00"),
    }
    if not full:
        values.update(
            {
                "ebitda": None,
                "net_debt": None,
                "interest_expense": None,
                "operating_cash_flow": None,
            }
        )
    report = FinancialReport(
        company_id=company.id,
        period_year=period_end_date.year,
        period_quarter=0,
        period_end_date=period_end_date,
        signal=AnalysisSignal.NEUTRAL.value,
        **values,
    )
    db.add(report)
    db.flush()
    return report


def create_feature(
    db: Session,
    bond: Bond,
    *,
    as_of_date: date,
    report: FinancialReport | None = None,
    with_ratios: bool = False,
) -> None:
    ratio_values = (
        {
            "net_debt_to_ebitda": Decimal("1.20"),
            "debt_to_equity": Decimal("0.50"),
            "interest_coverage": Decimal("5.00"),
            "cash_to_short_term_debt": Decimal("1.10"),
            "ocf_to_total_debt": Decimal("0.30"),
            "net_profit_margin": Decimal("0.10"),
        }
        if with_ratios
        else {}
    )
    db.add(
        BondFeatureSnapshot(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            financial_report_id=None if report is None else report.id,
            missing_data_count=0 if with_ratios else 3,
            features_json={},
            **ratio_values,
        )
    )
    db.flush()


def test_no_reports_returns_warning_status(
    client: TestClient,
    db_session: Session,
) -> None:
    first = create_company(db_session, "FRCN1")
    second = create_company(db_session, "FRCN2")
    create_bond(db_session, first, 1)
    create_bond(db_session, second, 2)
    db_session.commit()

    response = client.get(COVERAGE_URL, params={"as_of_date": "2026-05-21"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert payload["company_count"] == 2
    assert payload["companies_with_financial_reports"] == 0
    assert payload["coverage_ratio"] in ("0", "0.0000", 0, 0.0)
    assert {warning["code"] for warning in payload["warnings"]} >= {
        "financial_report_coverage_missing"
    }


def test_partial_recent_and_stale_report_coverage(
    client: TestClient,
    db_session: Session,
) -> None:
    recent_company = create_company(db_session, "FRCR")
    stale_company = create_company(db_session, "FRCS")
    missing_company = create_company(db_session, "FRCM")
    ofz_company = create_company(db_session, "FRCO")
    recent_bond = create_bond(db_session, recent_company, 1)
    stale_bond = create_bond(db_session, stale_company, 2)
    missing_bond = create_bond(db_session, missing_company, 3)
    ofz_bond = create_bond(
        db_session,
        ofz_company,
        4,
        isin="SU0000000001",
        secid="OFZ0001",
        name="OFZ coverage control",
    )
    recent_report = create_report(
        db_session,
        recent_company,
        period_end_date=date(2026, 3, 31),
    )
    stale_report = create_report(
        db_session,
        stale_company,
        period_end_date=date(2024, 1, 1),
        full=False,
    )
    ofz_report = create_report(
        db_session,
        ofz_company,
        period_end_date=date(2026, 3, 31),
    )
    create_feature(
        db_session,
        recent_bond,
        as_of_date=date(2026, 5, 19),
        report=recent_report,
        with_ratios=True,
    )
    create_feature(
        db_session,
        stale_bond,
        as_of_date=date(2026, 5, 19),
        report=stale_report,
    )
    create_feature(db_session, missing_bond, as_of_date=date(2026, 5, 19))
    create_feature(
        db_session,
        ofz_bond,
        as_of_date=date(2026, 5, 19),
        report=ofz_report,
        with_ratios=True,
    )
    db_session.commit()

    response = client.get(
        COVERAGE_URL,
        params={"as_of_date": "2026-05-21", "stale_after_days": 540},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["company_count"] == 3
    assert payload["companies_with_financial_reports"] == 2
    assert payload["companies_without_financial_reports"] == 1
    assert payload["active_bond_count"] == 3
    assert payload["active_bonds_with_financial_reports"] == 2
    assert payload["stale_report_company_count"] == 1
    assert payload["latest_report_period_end_date"] == "2026-03-31"
    assert payload["oldest_latest_report_period_end_date"] == "2024-01-01"
    assert payload["missing_field_counts"]["ebitda"] == 1
    assert payload["missing_field_counts"]["interest_expense"] == 1
    feature_coverage = payload["feature_snapshot_coverage"]
    assert feature_coverage["feature_snapshot_count"] == 3
    assert feature_coverage["feature_snapshots_with_financial_report_id"] == 2
    assert feature_coverage["feature_snapshots_with_any_financial_ratio"] == 1
    assert feature_coverage["feature_snapshots_with_core_ratios"] == 1
    assert feature_coverage["ratio_field_counts"]["interest_coverage"] == 1
    assert "financial_report_stale" in {
        warning["code"] for warning in payload["warnings"]
    }


def test_active_only_false_includes_ofz_scope(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "FRCA")
    ofz_company = create_company(db_session, "FRCAO")
    create_bond(db_session, company, 1)
    create_bond(
        db_session,
        ofz_company,
        2,
        isin="SU0000000002",
        secid="OFZ0002",
        name="OFZ included when requested",
    )
    create_report(
        db_session,
        ofz_company,
        period_end_date=date(2026, 3, 31),
    )
    db_session.commit()

    private_scope = client.get(
        COVERAGE_URL,
        params={"as_of_date": "2026-05-21"},
    ).json()
    all_scope = client.get(
        COVERAGE_URL,
        params={"as_of_date": "2026-05-21", "active_only": "false"},
    ).json()

    assert private_scope["company_count"] == 1
    assert private_scope["companies_with_financial_reports"] == 0
    assert all_scope["company_count"] == 2
    assert all_scope["companies_with_financial_reports"] == 1
