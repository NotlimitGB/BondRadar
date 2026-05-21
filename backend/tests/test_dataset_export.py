import csv
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.enums import AnalysisSignal


def create_company(db: Session, ticker: str) -> Company:
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


def create_bond(db: Session, company: Company, *, isin: str, secid: str) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        secid=secid,
        name=f"Dataset Bond {secid}",
        currency="RUB",
        is_floating_coupon=False,
        is_subordinated=False,
        is_perpetual=False,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def add_dataset_row(
    db: Session,
    bond: Bond,
    company: Company,
    *,
    as_of_date: date,
    label: str,
    label_binary: int | None,
    future_return: Decimal | None,
    missing_data_count: int = 0,
    horizon_days: int = 30,
    yield_to_maturity: Decimal | None = Decimal("12.500"),
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=company.id,
        as_of_date=as_of_date,
        bond_score=Decimal("74.00"),
        company_score=Decimal("82.00"),
        yield_to_maturity=yield_to_maturity,
        duration_years=Decimal("2.500"),
        liquidity_score=80,
        volume=Decimal("1500000.00"),
        net_debt_to_ebitda=Decimal("1.500000"),
        debt_to_equity=Decimal("0.700000"),
        interest_coverage=Decimal("4.000000"),
        cash_to_short_term_debt=Decimal("1.200000"),
        ocf_to_total_debt=Decimal("0.300000"),
        net_profit_margin=Decimal("0.120000"),
        days_to_maturity=900,
        has_offer=False,
        has_amortization=False,
        missing_data_count=missing_data_count,
        features_json={},
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.add(feature)
    db.flush()
    label_row = BondReturnLabel(
        bond_id=bond.id,
        as_of_date=as_of_date,
        horizon_days=horizon_days,
        future_return=future_return,
        label=label,
        label_binary=label_binary,
        created_at=datetime(2026, 1, 2, 12, 0, 0),
    )
    db.add(label_row)
    db.commit()


def seed_export_dataset(db: Session) -> tuple[Company, Company, Bond, Bond]:
    company_a = create_company(db, "EXP1")
    company_b = create_company(db, "EXP2")
    bond_a = create_bond(db, company_a, isin="RU000EXP001", secid="EXP001")
    bond_b = create_bond(db, company_b, isin="RU000EXP002", secid="EXP002")
    add_dataset_row(
        db,
        bond_a,
        company_a,
        as_of_date=date(2026, 1, 10),
        label="positive_return",
        label_binary=1,
        future_return=Decimal("0.050000"),
    )
    add_dataset_row(
        db,
        bond_a,
        company_a,
        as_of_date=date(2026, 1, 11),
        label="insufficient_data",
        label_binary=None,
        future_return=None,
        missing_data_count=4,
        yield_to_maturity=None,
    )
    add_dataset_row(
        db,
        bond_b,
        company_b,
        as_of_date=date(2026, 1, 10),
        label="negative_return",
        label_binary=0,
        future_return=Decimal("-0.020000"),
    )
    return company_a, company_b, bond_a, bond_b


def test_json_dataset_export_returns_rows(
    client: TestClient, db_session: Session
) -> None:
    seed_export_dataset(db_session)

    response = client.get("/api/datasets/export?horizon_days=30")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["limit"] == 100
    assert payload["offset"] == 0
    row = payload["rows"][0]
    assert "bond_score" in row
    assert "future_return" in row
    assert "feature_snapshot_id" in row
    assert "label_id" in row


def test_dataset_export_filters(
    client: TestClient, db_session: Session
) -> None:
    company_a, company_b, bond_a, _ = seed_export_dataset(db_session)

    by_bond = client.get(f"/api/datasets/export?bond_id={bond_a.id}")
    by_company = client.get(f"/api/datasets/export?company_id={company_b.id}")
    by_label = client.get("/api/datasets/export?label=positive_return")
    without_insufficient = client.get(
        "/api/datasets/export?include_insufficient=false"
    )
    conflicting = client.get(
        "/api/datasets/export?label=insufficient_data&include_insufficient=false"
    )

    assert by_bond.status_code == 200
    assert {row["bond_id"] for row in by_bond.json()["rows"]} == {bond_a.id}
    assert by_company.status_code == 200
    assert {row["company_id"] for row in by_company.json()["rows"]} == {company_b.id}
    assert by_label.status_code == 200
    assert {row["label"] for row in by_label.json()["rows"]} == {"positive_return"}
    assert without_insufficient.status_code == 200
    assert "insufficient_data" not in {
        row["label"] for row in without_insufficient.json()["rows"]
    }
    assert conflicting.status_code == 200
    assert conflicting.json()["total"] == 0
    assert conflicting.json()["rows"] == []
    assert company_a.id != company_b.id


def test_dataset_export_validation_errors(client: TestClient) -> None:
    invalid_label = client.get("/api/datasets/export?label=buy")
    invalid_horizon = client.get("/api/datasets/export?horizon_days=0")
    invalid_range = client.get(
        "/api/datasets/export?as_of_date_from=2026-02-01&as_of_date_to=2026-01-01"
    )
    invalid_limit = client.get("/api/datasets/export?limit=5001")
    invalid_offset = client.get("/api/datasets/export?offset=-1")

    assert invalid_label.status_code == 400
    assert invalid_label.json()["detail"] == "Invalid label"
    assert invalid_horizon.status_code == 400
    assert invalid_horizon.json()["detail"] == "horizon_days must be positive"
    assert invalid_range.status_code == 400
    assert invalid_range.json()["detail"] == "Invalid date range"
    assert invalid_limit.status_code == 400
    assert invalid_limit.json()["detail"] == "limit must be between 1 and 5000"
    assert invalid_offset.status_code == 400
    assert invalid_offset.json()["detail"] == "offset must be non-negative"


def test_csv_export_returns_flat_csv(
    client: TestClient, db_session: Session
) -> None:
    seed_export_dataset(db_session)

    response = client.get("/api/datasets/export.csv?horizon_days=30")

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "bondradar_dataset_h30.csv" in response.headers["content-disposition"]
    rows = list(csv.DictReader(StringIO(response.text)))
    assert rows
    assert "bond_id" in rows[0]
    assert "future_return" in rows[0]
    assert "features_json" not in rows[0]
    insufficient = next(row for row in rows if row["label"] == "insufficient_data")
    assert insufficient["future_return"] == ""
    assert insufficient["yield_to_maturity"] == ""
    assert rows[0]["has_offer"] in {"true", "false"}


def test_quality_report_returns_stats(
    client: TestClient, db_session: Session
) -> None:
    seed_export_dataset(db_session)

    response = client.get("/api/datasets/quality-report?horizon_days=30")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_rows"] == 3
    assert payload["as_of_date_min"] == "2026-01-10"
    assert payload["as_of_date_max"] == "2026-01-11"
    assert payload["bond_count"] == 2
    assert payload["company_count"] == 2
    assert payload["label_distribution"]["positive_return"] == 1
    assert payload["label_distribution"]["negative_return"] == 1
    assert payload["label_distribution"]["insufficient_data"] == 1
    assert payload["label_distribution"]["label_binary_null"] == 1
    missing_by_feature = {
        item["feature"]: item for item in payload["missing_features"]
    }
    assert missing_by_feature["yield_to_maturity"]["missing_count"] == 1
    numeric_by_feature = {
        item["feature"]: item for item in payload["numeric_feature_stats"]
    }
    assert numeric_by_feature["future_return"]["count"] == 2
    assert numeric_by_feature["future_return"]["missing_count"] == 1
    ratio_coverage = payload["financial_ratio_coverage"]
    assert ratio_coverage["feature_snapshot_count"] == 3
    assert "interest_coverage" in ratio_coverage["ratio_field_counts"]
    assert ratio_coverage["average_missing_data_count"] is not None
    assert len(payload["coverage_by_bond"]) == 2
    assert len(payload["coverage_by_company"]) == 2


def test_empty_dataset_export_and_quality_report(client: TestClient) -> None:
    json_response = client.get("/api/datasets/export?horizon_days=30")
    csv_response = client.get("/api/datasets/export.csv?horizon_days=30")
    quality_response = client.get("/api/datasets/quality-report?horizon_days=30")

    assert json_response.status_code == 200
    assert json_response.json()["total"] == 0
    assert json_response.json()["rows"] == []
    assert csv_response.status_code == 200
    assert csv_response.text.splitlines()[0].startswith("bond_id,company_id")
    assert len(csv_response.text.splitlines()) == 1
    assert quality_response.status_code == 200
    payload = quality_response.json()
    assert payload["total_rows"] == 0
    assert payload["missing_features"] == []
    assert payload["numeric_feature_stats"] == []
    assert payload["financial_ratio_coverage"]["feature_snapshot_count"] == 0
    assert payload["financial_ratio_coverage"]["financial_report_id_ratio"] is None
    assert payload["coverage_by_bond"] == []
    assert payload["coverage_by_company"] == []


def test_no_ml_dependencies_added() -> None:
    requirements = Path("backend/requirements.txt").read_text(encoding="utf-8")
    forbidden = {
        "pandas",
        "numpy",
        "xgboost",
        "catboost",
        "tensorflow",
        "pytorch",
    }

    assert all(package not in requirements for package in forbidden)
