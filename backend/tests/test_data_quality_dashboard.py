from datetime import date, datetime, timezone
from decimal import Decimal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(
    db: Session,
    ticker: str = "QLT",
    *,
    name: str | None = None,
) -> Company:
    company = Company(
        name=name or f"{ticker} Company",
        ticker=ticker,
        inn=f"77{abs(hash(ticker)) % 100000000:08d}",
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
    secid: str | None = None,
    isin: str | None = None,
    name: str | None = None,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin if isin is not None else f"RU000QLT{index:03d}",
        secid=secid if secid is not None else f"QLT{index:03d}",
        name=name or f"{company.ticker} Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        liquidity_score=80,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def add_report(
    db: Session,
    company: Company,
    *,
    source: str = "manual",
    published_at: datetime = dt(2024, 12, 31),
) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=2024,
        period_quarter=0,
        revenue=Decimal("1000.00"),
        ebitda=Decimal("250.00"),
        total_debt=Decimal("400.00"),
        cash=Decimal("100.00"),
        equity=Decimal("600.00"),
        source=source,
        published_at=published_at,
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(report)
    db.flush()
    return report


def add_health(
    db: Session,
    company: Company,
    *,
    as_of_date: date = date(2025, 1, 1),
) -> None:
    db.add(
        CompanyCreditHealthSnapshot(
            company_id=company.id,
            as_of_date=as_of_date,
            credit_health_score=82,
            credit_status="credit_stable",
            risk_level="low",
            data_quality_level="high",
            risk_factors=[],
            positive_factors=[],
            missing_data=[],
            explanation={},
        )
    )
    db.flush()


def add_market(
    db: Session,
    bond: Bond,
    *,
    trade_date: date = date(2025, 1, 1),
    source: str = "manual",
) -> None:
    db.add(
        BondMarketSnapshot(
            bond_id=bond.id,
            trade_date=trade_date,
            price=Decimal("100.000000"),
            clean_price=Decimal("100.000000"),
            dirty_price=Decimal("101.000000"),
            nkd=Decimal("10.000000"),
            yield_to_maturity=Decimal("12.000"),
            duration_years=Decimal("2.000"),
            volume=Decimal("1000000.00"),
            liquidity_score=80,
            source=source,
            raw_payload={"source": source},
        )
    )
    db.flush()


def add_cashflow(
    db: Session,
    bond: Bond,
    *,
    event_date: date = date(2025, 1, 5),
    source: str = "manual",
) -> None:
    db.add(
        BondCashflowEvent(
            bond_id=bond.id,
            event_date=event_date,
            event_type="coupon",
            amount=Decimal("20.000000"),
            currency="RUB",
            source=source,
            raw_payload={"source": source},
        )
    )
    db.flush()


def add_feature(
    db: Session,
    bond: Bond,
    *,
    as_of_date: date = date(2025, 1, 1),
) -> BondFeatureSnapshot:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=80,
        volume=Decimal("1000000.00"),
        missing_data_count=0,
        features_json={},
    )
    db.add(feature)
    db.flush()
    return feature


def add_label(
    db: Session,
    bond: Bond,
    *,
    as_of_date: date = date(2025, 1, 1),
    return_method: str = "price",
    label: str = "positive_return",
    horizon_days: int = 30,
) -> None:
    future_return = Decimal("0.020000") if label == "positive_return" else Decimal("-0.020000")
    label_binary = 1 if label == "positive_return" else 0
    if label == "insufficient_data":
        future_return = None
        label_binary = None
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            return_method=return_method,
            future_return=future_return,
            price_return=future_return,
            net_total_return=future_return,
            risk_adjusted_excess_return=future_return,
            label=label,
            label_binary=label_binary,
        )
    )
    db.flush()


def add_risk(
    db: Session,
    bond: Bond,
    *,
    as_of_date: date = date(2025, 1, 1),
) -> None:
    db.add(
        BondRiskAssessment(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            assessment_score=80,
            decision_status="eligible_for_analysis",
            risk_level="low",
            required_risk_premium=Decimal("0.010000"),
            yield_to_maturity=Decimal("12.000"),
            coupon_rate=Decimal("10.000"),
            duration_years=Decimal("2.000"),
            liquidity_score=80,
            volume=Decimal("1000000.00"),
            gates={},
            warnings=[],
            blocking_reasons=[],
            positive_factors=[],
            negative_factors=[],
            missing_data=[],
            explanation={},
        )
    )
    db.flush()


def add_ml(
    db: Session,
    bond: Bond,
    feature: BondFeatureSnapshot,
    *,
    as_of_date: date = date(2025, 1, 1),
) -> None:
    run = MLModelRun(
        status="completed",
        model_type="logistic_regression",
        horizon_days=30,
        features=["liquidity_score"],
        target="label_binary",
        train_rows=10,
        test_rows=2,
        positive_rows=6,
        negative_rows=6,
        metrics={},
        feature_importance=[],
        params={"return_method": "price"},
        finished_at=dt(2025, 1, 2),
    )
    db.add(run)
    db.flush()
    db.add(
        MLPrediction(
            model_run_id=run.id,
            feature_snapshot_id=feature.id,
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            horizon_days=30,
            probability_positive=Decimal("0.7000000000"),
            predicted_label="predicted_positive_return",
            features={},
            created_at=dt(2025, 1, 2),
        )
    )
    db.flush()


def seed_quality_dataset(db: Session) -> tuple[Company, Bond]:
    company = create_company(db, "QLT")
    bond = create_bond(db, company, 1)
    report = add_report(db, company)
    add_health(db, company)
    add_market(db, bond)
    add_cashflow(db, bond)
    feature = add_feature(db, bond)
    for method in ("price", "total_return", "risk_adjusted"):
        add_label(db, bond, return_method=method, label="positive_return")
    add_risk(db, bond)
    add_ml(db, bond, feature)
    assert report.id is not None
    db.commit()
    return company, bond


def test_overview_returns_counts_and_coverage(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_quality_dataset(db_session)

    response = client.get("/api/data-quality/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["companies_total"] == 1
    assert payload["counts"]["bonds_total"] == 1
    assert payload["counts"]["market_snapshots_total"] == 1
    assert payload["counts"]["labels_total"] == 3
    assert payload["coverage"]["bond_market_snapshot_coverage"] == "1"
    assert payload["coverage"]["company_report_coverage"] == "1"
    assert payload["date_ranges"]["market_snapshots"]["min_date"] == "2025-01-01"
    assert payload["date_ranges"]["labels"]["row_count"] == 3
    assert payload["source_breakdowns"]["market_snapshots_by_source"][0]["source"] == "manual"


def test_overview_handles_empty_db(client: TestClient) -> None:
    response = client.get("/api/data-quality/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["companies_total"] == 0
    assert payload["counts"]["bonds_total"] == 0
    assert payload["coverage"]["bond_market_snapshot_coverage"] is None
    assert payload["date_ranges"]["labels"]["row_count"] == 0


def test_demo_filter_excludes_demo_entities(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_quality_dataset(db_session)
    demo_company = create_company(db_session, "DEMO_QA", name="Demo quality company")
    demo_bond = create_bond(
        db_session,
        demo_company,
        2,
        secid="DEMO_QA_BOND",
        isin="RUDEMOQA0001",
    )
    add_market(db_session, demo_bond, source="demo")
    db_session.commit()

    included = client.get("/api/data-quality/overview")
    excluded = client.get("/api/data-quality/overview?include_demo=false")
    bond_rows = client.get("/api/data-quality/bonds?include_demo=false")

    assert included.status_code == 200
    assert excluded.status_code == 200
    assert bond_rows.status_code == 200
    assert included.json()["counts"]["bonds_total"] == 2
    assert excluded.json()["counts"]["bonds_total"] == 1
    assert all(not item["is_demo"] for item in bond_rows.json()["items"])


def test_label_breakdown_groups_by_return_method(
    client: TestClient,
    db_session: Session,
) -> None:
    _, bond = seed_quality_dataset(db_session)
    add_label(
        db_session,
        bond,
        as_of_date=date(2025, 1, 2),
        return_method="risk_adjusted",
        label="insufficient_data",
    )
    db_session.commit()

    response = client.get("/api/data-quality/overview")

    assert response.status_code == 200
    payload = response.json()
    methods = {item["return_method"] for item in payload["return_method_breakdowns"]}
    assert methods == {"price", "total_return", "risk_adjusted"}
    insufficient = [
        item
        for item in payload["label_breakdowns"]
        if item["return_method"] == "risk_adjusted"
        and item["label"] == "insufficient_data"
    ]
    assert insufficient[0]["rows"] == 1


def test_bond_rows_include_issue_flags(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "GAP")
    bond = create_bond(db_session, company, 1)
    bond.secid = None
    db_session.commit()

    response = client.get("/api/data-quality/bonds")

    assert response.status_code == 200
    flags = response.json()["items"][0]["issue_flags"]
    assert "missing_secid" in flags
    assert "no_market_snapshots" in flags
    assert "no_labels" in flags


def test_company_rows_include_issue_flags(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CGAP")
    create_bond(db_session, company, 1)
    db_session.commit()

    response = client.get("/api/data-quality/companies")

    assert response.status_code == 200
    flags = response.json()["items"][0]["issue_flags"]
    assert "no_financial_reports" in flags
    assert "no_credit_health" in flags
    assert "bonds_missing_market_snapshots" in flags


def test_date_filters_affect_dated_counts(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "DATE")
    bond = create_bond(db_session, company, 1)
    add_market(db_session, bond, trade_date=date(2025, 1, 1))
    add_market(db_session, bond, trade_date=date(2025, 2, 1))
    add_feature(db_session, bond, as_of_date=date(2025, 1, 1))
    add_label(db_session, bond, as_of_date=date(2025, 1, 1), return_method="price")
    add_label(db_session, bond, as_of_date=date(2025, 2, 1), return_method="price")
    db_session.commit()

    response = client.get(
        "/api/data-quality/overview?date_from=2025-01-01&date_to=2025-01-31"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["counts"]["market_snapshots_total"] == 1
    assert payload["counts"]["labels_total"] == 1
    assert payload["date_ranges"]["market_snapshots"]["max_date"] == "2025-01-01"


def test_pagination_works_for_bonds_and_companies(
    client: TestClient,
    db_session: Session,
) -> None:
    for index in range(3):
        company = create_company(db_session, f"PAGE{index}")
        create_bond(db_session, company, index + 1)
    db_session.commit()

    bonds = client.get("/api/data-quality/bonds?limit=1&offset=1")
    companies = client.get("/api/data-quality/companies?limit=2&offset=1")

    assert bonds.status_code == 200
    assert companies.status_code == 200
    assert bonds.json()["total"] == 3
    assert bonds.json()["limit"] == 1
    assert len(bonds.json()["items"]) == 1
    assert companies.json()["total"] == 3
    assert len(companies.json()["items"]) == 2


def test_invalid_filters_return_400(client: TestClient) -> None:
    cases = [
        "/api/data-quality/overview?date_from=2025-02-01&date_to=2025-01-01",
        "/api/data-quality/bonds?limit=0",
        "/api/data-quality/bonds?limit=501",
        "/api/data-quality/companies?offset=-1",
    ]

    for url in cases:
        response = client.get(url)
        assert response.status_code == 400


def test_data_quality_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_quality_dataset(db_session)

    response = client.get("/api/data-quality/overview")

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())

