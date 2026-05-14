from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


def create_company(db: Session, ticker: str = "MLC") -> Company:
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


def create_bond(
    db: Session,
    company: Company,
    *,
    isin: str = "RU000ML00001",
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        name=f"Dataset Bond {isin}",
        currency="RUB",
        yield_to_maturity=Decimal("12.500"),
        duration_years=Decimal("2.000"),
        liquidity_score=70,
        volume=Decimal("1000000.00"),
        maturity_date=date(2030, 1, 1),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def create_report(
    db: Session,
    company: Company,
    *,
    created_at: datetime | None = None,
) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=2025,
        period_quarter=0,
        revenue=Decimal("1000.00"),
        ebitda=Decimal("250.00"),
        total_debt=Decimal("400.00"),
        cash=Decimal("100.00"),
        equity=Decimal("600.00"),
        short_term_debt=Decimal("120.00"),
        operating_cash_flow=Decimal("150.00"),
        net_profit=Decimal("120.00"),
        interest_expense=Decimal("50.00"),
        source="test",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    if created_at is not None:
        report.created_at = created_at
        report.updated_at = created_at
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def create_company_score(
    db: Session,
    company: Company,
    *,
    created_at: datetime,
    score_value: int = 82,
) -> CompanyScore:
    score = CompanyScore(
        company_id=company.id,
        score=Decimal(score_value),
        signal=AnalysisSignal.NEUTRAL.value,
        factors={},
        summary="Company score",
        as_of_date=created_at.date(),
        source=f"test-company-{score_value}",
        final_company_score=score_value,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def create_bond_score(
    db: Session,
    bond: Bond,
    *,
    created_at: datetime,
    score_value: int = 74,
) -> BondScore:
    score = BondScore(
        bond_id=bond.id,
        score=Decimal(score_value),
        signal=AnalysisSignal.NEUTRAL.value,
        factors={},
        summary="Bond score",
        as_of_date=created_at.date(),
        source=f"test-bond-{score_value}",
        final_bond_score=score_value,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(score)
    db.commit()
    db.refresh(score)
    return score


def post_snapshot(
    client: TestClient,
    bond: Bond,
    trade_date: date,
    *,
    price: str = "100.00",
    dirty_price: str | None = None,
):
    payload = {
        "bond_id": bond.id,
        "trade_date": trade_date.isoformat(),
        "price": price,
        "clean_price": price,
        "dirty_price": dirty_price,
        "yield_to_maturity": "13.500",
        "duration_years": "2.500",
        "volume": "1500000.00",
        "liquidity_score": 80,
        "source": "manual",
    }
    return client.post("/api/market-snapshots", json=payload)


def build_dataset(
    client: TestClient,
    as_of_date: date,
    *,
    horizon_days: int = 30,
    rebuild_existing: bool = False,
):
    return client.post(
        "/api/datasets/build",
        json={
            "as_of_date_from": as_of_date.isoformat(),
            "as_of_date_to": as_of_date.isoformat(),
            "horizon_days": horizon_days,
            "rebuild_existing": rebuild_existing,
        },
    )


def test_market_snapshot_create_and_upsert(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "MSU")
    bond = create_bond(db_session, company)
    trade_date = date(2026, 1, 10)

    first = post_snapshot(client, bond, trade_date, price="100.00")
    second = post_snapshot(client, bond, trade_date, price="101.25")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["price"] in ("101.250000", "101.25", 101.25)

    listed = client.get(f"/api/market-snapshots?bond_id={bond.id}")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_market_snapshot_unknown_bond(client: TestClient) -> None:
    response = client.post(
        "/api/market-snapshots",
        json={"bond_id": 999, "trade_date": "2026-01-10", "price": "100"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Bond not found"


def test_dataset_build_creates_feature_and_positive_label(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "DSP")
    bond = create_bond(db_session, company)
    create_report(db_session, company, created_at=datetime(2026, 1, 5, 12, 0, 0))
    create_company_score(
        db_session, company, created_at=datetime(2026, 1, 8, 12, 0, 0)
    )
    create_bond_score(db_session, bond, created_at=datetime(2026, 1, 8, 12, 0, 0))
    post_snapshot(client, bond, as_of_date, price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), price="105.00")

    response = build_dataset(client, as_of_date)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["features_created"] == 1
    assert payload["labels_created"] == 1

    features = client.get(f"/api/datasets/features?bond_id={bond.id}").json()
    labels = client.get(f"/api/datasets/labels?bond_id={bond.id}").json()
    assert features[0]["company_score"] in ("82.00", "82.000000", 82, 82.0)
    assert features[0]["bond_score"] in ("74.00", "74.000000", 74, 74.0)
    assert features[0]["missing_data_count"] == 0
    assert labels[0]["label"] == "positive_return"
    assert labels[0]["label_binary"] == 1
    assert Decimal(str(labels[0]["future_return"])) > 0


def test_feature_snapshot_does_not_use_future_scores(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "NFL")
    bond = create_bond(db_session, company, isin="RU000ML00002")
    create_company_score(
        db_session, company, created_at=datetime(2026, 1, 11, 0, 0, 0)
    )
    create_bond_score(db_session, bond, created_at=datetime(2026, 1, 11, 0, 0, 0))
    post_snapshot(client, bond, as_of_date)

    response = build_dataset(client, as_of_date)

    assert response.status_code == 200
    features = client.get(f"/api/datasets/features?bond_id={bond.id}").json()
    assert features[0]["company_score"] is None
    assert features[0]["bond_score"] is None
    assert "company_score" in features[0]["features_json"]["missing_data"]
    assert "bond_score" in features[0]["features_json"]["missing_data"]


def test_financial_report_period_fallback_records_warning(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "FRF")
    bond = create_bond(db_session, company, isin="RU000ML00003")
    create_report(db_session, company, created_at=datetime(2026, 2, 1, 12, 0, 0))
    post_snapshot(client, bond, as_of_date)

    response = build_dataset(client, as_of_date)

    assert response.status_code == 200
    features = client.get(f"/api/datasets/features?bond_id={bond.id}").json()
    assert features[0]["financial_report_id"] is not None
    assert "leakage_warning" in features[0]["features_json"]


def test_future_snapshot_outside_lookup_window_is_insufficient(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "FOW")
    bond = create_bond(db_session, company, isin="RU000ML00004")
    post_snapshot(client, bond, as_of_date, price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=38), price="110.00")

    response = build_dataset(client, as_of_date)

    assert response.status_code == 200
    labels = client.get(f"/api/datasets/labels?bond_id={bond.id}").json()
    assert labels[0]["label"] == "insufficient_data"
    assert labels[0]["label_binary"] is None
    assert labels[0]["future_return"] is None


def test_missing_future_snapshot_is_insufficient(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "MFS")
    bond = create_bond(db_session, company, isin="RU000ML00005")
    post_snapshot(client, bond, as_of_date, price="100.00")

    response = build_dataset(client, as_of_date)

    assert response.status_code == 200
    labels = client.get(f"/api/datasets/labels?bond_id={bond.id}").json()
    assert labels[0]["label"] == "insufficient_data"


def test_rebuild_false_skips_feature_and_builds_missing_label(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "RBF")
    bond = create_bond(db_session, company, isin="RU000ML00006")
    post_snapshot(client, bond, as_of_date, price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), price="101.00")
    db_session.add(
        BondFeatureSnapshot(
            bond_id=bond.id,
            company_id=company.id,
            as_of_date=as_of_date,
            missing_data_count=0,
            features_json={},
        )
    )
    db_session.commit()

    response = build_dataset(client, as_of_date)

    assert response.status_code == 200
    payload = response.json()
    assert payload["features_created"] == 0
    assert payload["features_updated"] == 0
    assert payload["labels_created"] == 1


def test_rebuild_true_updates_existing_feature_and_label(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "RBT")
    bond = create_bond(db_session, company, isin="RU000ML00007")
    post_snapshot(client, bond, as_of_date, price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), price="102.00")
    db_session.add(
        BondFeatureSnapshot(
            bond_id=bond.id,
            company_id=company.id,
            as_of_date=as_of_date,
            missing_data_count=8,
            features_json={},
        )
    )
    db_session.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=30,
            label="insufficient_data",
        )
    )
    db_session.commit()

    response = build_dataset(client, as_of_date, rebuild_existing=True)

    assert response.status_code == 200
    payload = response.json()
    assert payload["features_updated"] == 1
    assert payload["labels_updated"] == 1
    labels = client.get(f"/api/datasets/labels?bond_id={bond.id}").json()
    assert labels[0]["label"] == "positive_return"


def test_dataset_build_validation_errors(client: TestClient) -> None:
    invalid_range = client.post(
        "/api/datasets/build",
        json={
            "as_of_date_from": "2026-02-01",
            "as_of_date_to": "2026-01-01",
            "horizon_days": 30,
        },
    )
    invalid_horizon = client.post(
        "/api/datasets/build",
        json={
            "as_of_date_from": "2026-01-01",
            "as_of_date_to": "2026-02-01",
            "horizon_days": 0,
        },
    )

    assert invalid_range.status_code == 400
    assert invalid_range.json()["detail"] == "Invalid date range"
    assert invalid_horizon.status_code == 400
    assert invalid_horizon.json()["detail"] == "horizon_days must be positive"


def test_dataset_list_endpoints_and_no_trading_labels(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "LST")
    bond = create_bond(db_session, company, isin="RU000ML00008")
    post_snapshot(client, bond, as_of_date, price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), price="99.00")
    build_dataset(client, as_of_date)

    runs = client.get("/api/datasets/runs")
    features = client.get("/api/datasets/features")
    labels = client.get("/api/datasets/labels")

    assert runs.status_code == 200
    assert features.status_code == 200
    assert labels.status_code == 200
    assert runs.json()
    assert features.json()
    assert labels.json()
    forbidden = {"buy", "sell", "hold", "strong_buy", "strong_sell"}
    assert {item["label"] for item in labels.json()}.isdisjoint(forbidden)
