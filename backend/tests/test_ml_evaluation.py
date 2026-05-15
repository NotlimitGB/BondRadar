import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction


def create_company(db: Session, ticker: str = "MLE") -> Company:
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


def create_bond(db: Session, company: Company, isin: str = "RU000MLE001") -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        secid=isin[-6:],
        name=f"Evaluation Bond {isin}",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def create_run(
    db: Session,
    *,
    return_method: str = "price",
    status: str = "completed",
) -> MLModelRun:
    run = MLModelRun(
        status=status,
        model_type="logistic_regression",
        horizon_days=30,
        features=["bond_score", "company_score"],
        target="label_binary",
        train_rows=20,
        test_rows=5,
        positive_rows=12,
        negative_rows=13,
        metrics={"accuracy": 0.8},
        feature_importance=[{"feature": "bond_score", "importance": 0.4}],
        params={"return_method": return_method, "model_type": "logistic_regression"},
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def add_feature_prediction(
    db: Session,
    *,
    run: MLModelRun,
    bond: Bond,
    company: Company,
    as_of_date: date,
    probability: Decimal,
    predicted_label: str,
) -> BondFeatureSnapshot:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=company.id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        missing_data_count=0,
        features_json={},
    )
    db.add(feature)
    db.flush()
    db.add(
        MLPrediction(
            model_run_id=run.id,
            feature_snapshot_id=feature.id,
            bond_id=bond.id,
            company_id=company.id,
            as_of_date=as_of_date,
            horizon_days=run.horizon_days,
            probability_positive=probability,
            predicted_label=predicted_label,
            features={"bond_score": 70.0},
            created_at=datetime(2026, 1, 1, 12, 0, 0),
        )
    )
    db.commit()
    db.refresh(feature)
    return feature


def add_label(
    db: Session,
    *,
    bond: Bond,
    as_of_date: date,
    return_method: str,
    label: str,
    label_binary: int | None,
    future_return: Decimal | None,
) -> None:
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=30,
            return_method=return_method,
            future_return=future_return,
            price_return=future_return if return_method == "price" else None,
            net_total_return=(
                future_return if return_method == "total_return" else None
            ),
            risk_adjusted_excess_return=(
                future_return if return_method == "risk_adjusted" else None
            ),
            required_risk_premium=(
                Decimal("0.020000") if return_method == "risk_adjusted" else None
            ),
            label=label,
            label_binary=label_binary,
        )
    )
    db.commit()


def seed_evaluation_dataset(
    db: Session,
    *,
    return_method: str = "risk_adjusted",
) -> tuple[MLModelRun, Company, Bond]:
    company = create_company(db)
    bond = create_bond(db, company)
    run = create_run(db, return_method=return_method)
    start = date(2026, 1, 1)
    rows = [
        (Decimal("0.85"), "predicted_positive_return", "positive_return", 1, Decimal("0.04")),
        (Decimal("0.75"), "predicted_positive_return", "negative_return", 0, Decimal("-0.01")),
        (Decimal("0.25"), "predicted_negative_return", "negative_return", 0, Decimal("-0.03")),
        (Decimal("0.15"), "predicted_negative_return", "positive_return", 1, Decimal("0.02")),
    ]
    for index, (probability, predicted, label, label_binary, future_return) in enumerate(rows):
        day = start + timedelta(days=index)
        add_feature_prediction(
            db,
            run=run,
            bond=bond,
            company=company,
            as_of_date=day,
            probability=probability,
            predicted_label=predicted,
        )
        add_label(
            db,
            bond=bond,
            as_of_date=day,
            return_method=return_method,
            label=label,
            label_binary=label_binary,
            future_return=future_return,
        )
    return run, company, bond


def test_evaluation_report_for_completed_run(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_evaluation_dataset(db_session)

    response = client.get(f"/api/ml/evaluation/runs/{run.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] == run.id
    assert payload["return_method"] == "risk_adjusted"
    assert payload["coverage"]["total_predictions"] == 4
    assert payload["coverage"]["evaluable_predictions"] == 4
    assert payload["evaluation_metrics"]["evaluable_count"] == 4
    assert payload["evaluation_metrics"]["confusion_matrix"]["true_positive"] == 1
    assert payload["calibration"]["brier_score"] is not None
    assert payload["feature_importance"]


def test_evaluation_joins_labels_by_return_method(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "JRM")
    bond = create_bond(db_session, company, "RU000JRM001")
    run = create_run(db_session, return_method="risk_adjusted")
    as_of_date = date(2026, 1, 10)
    add_feature_prediction(
        db_session,
        run=run,
        bond=bond,
        company=company,
        as_of_date=as_of_date,
        probability=Decimal("0.80"),
        predicted_label="predicted_positive_return",
    )
    add_label(
        db_session,
        bond=bond,
        as_of_date=as_of_date,
        return_method="price",
        label="negative_return",
        label_binary=0,
        future_return=Decimal("-0.050000"),
    )
    add_label(
        db_session,
        bond=bond,
        as_of_date=as_of_date,
        return_method="risk_adjusted",
        label="positive_return",
        label_binary=1,
        future_return=Decimal("0.010000"),
    )

    response = client.get(f"/api/ml/evaluation/runs/{run.id}/rows")

    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["return_method"] == "risk_adjusted"
    assert row["actual_label"] == "positive_return"
    assert row["risk_adjusted_excess_return"] in ("0.010000", 0.01, "0.01")


def test_missing_and_insufficient_labels_are_counted(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MIS")
    bond = create_bond(db_session, company, "RU000MIS001")
    run = create_run(db_session, return_method="price")
    add_feature_prediction(
        db_session,
        run=run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.60"),
        predicted_label="predicted_positive_return",
    )
    add_feature_prediction(
        db_session,
        run=run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 2),
        probability=Decimal("0.40"),
        predicted_label="predicted_negative_return",
    )
    add_label(
        db_session,
        bond=bond,
        as_of_date=date(2026, 1, 2),
        return_method="price",
        label="insufficient_data",
        label_binary=None,
        future_return=None,
    )

    response = client.get(f"/api/ml/evaluation/runs/{run.id}")
    rows = client.get(f"/api/ml/evaluation/runs/{run.id}/rows")

    assert response.status_code == 200
    payload = response.json()
    assert payload["coverage"]["missing_label_predictions"] == 1
    assert payload["coverage"]["insufficient_label_predictions"] == 1
    assert payload["evaluation_metrics"]["evaluable_count"] == 0
    assert rows.status_code == 200
    assert {row["is_evaluable"] for row in rows.json()["rows"]} == {False}


def test_probability_buckets_and_row_pagination(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_evaluation_dataset(db_session)

    report = client.get(f"/api/ml/evaluation/runs/{run.id}?bucket_size=0.25")
    rows = client.get(f"/api/ml/evaluation/runs/{run.id}/rows?limit=2&offset=1")

    assert report.status_code == 200
    buckets = report.json()["calibration"]["buckets"]
    assert len(buckets) == 4
    assert any(bucket["predictions_count"] > 0 for bucket in buckets)
    assert any(bucket["actual_positive_rate"] is not None for bucket in buckets)
    assert rows.status_code == 200
    assert rows.json()["total"] == 4
    assert rows.json()["limit"] == 2
    assert len(rows.json()["rows"]) == 2


def test_compare_endpoint_filters_return_method(
    client: TestClient,
    db_session: Session,
) -> None:
    risk_run, _, _ = seed_evaluation_dataset(db_session, return_method="risk_adjusted")
    price_run = create_run(db_session, return_method="price")

    explicit = client.get(
        f"/api/ml/evaluation/compare?run_ids={risk_run.id}&run_ids={price_run.id}"
    )
    filtered = client.get("/api/ml/evaluation/compare?return_method=risk_adjusted")

    assert explicit.status_code == 200
    assert {row["model_run_id"] for row in explicit.json()["rows"]} == {
        risk_run.id,
        price_run.id,
    }
    assert filtered.status_code == 200
    assert {row["return_method"] for row in filtered.json()["rows"]} == {
        "risk_adjusted"
    }


def test_invalid_filters(client: TestClient, db_session: Session) -> None:
    run, _, _ = seed_evaluation_dataset(db_session)

    invalid_range = client.get(
        f"/api/ml/evaluation/runs/{run.id}?as_of_date_from=2026-02-01&as_of_date_to=2026-01-01"
    )
    invalid_probability = client.get(
        f"/api/ml/evaluation/runs/{run.id}?min_probability=-0.1"
    )
    invalid_probability_order = client.get(
        f"/api/ml/evaluation/runs/{run.id}?min_probability=0.9&max_probability=0.1"
    )
    invalid_bucket = client.get(f"/api/ml/evaluation/runs/{run.id}?bucket_size=0.75")
    invalid_limit = client.get(f"/api/ml/evaluation/runs/{run.id}/rows?limit=0")
    invalid_offset = client.get(f"/api/ml/evaluation/runs/{run.id}/rows?offset=-1")
    invalid_compare_method = client.get("/api/ml/evaluation/compare?return_method=magic")
    invalid_compare_limit = client.get("/api/ml/evaluation/compare?limit=101")

    assert invalid_range.status_code == 400
    assert invalid_range.json()["detail"] == "Invalid date range"
    assert invalid_probability.status_code == 400
    assert invalid_probability.json()["detail"] == (
        "probability filters must be between 0 and 1"
    )
    assert invalid_probability_order.status_code == 400
    assert invalid_probability_order.json()["detail"] == (
        "min_probability cannot exceed max_probability"
    )
    assert invalid_bucket.status_code == 400
    assert invalid_bucket.json()["detail"] == (
        "bucket_size must be greater than 0 and at most 0.5"
    )
    assert invalid_limit.status_code == 400
    assert invalid_offset.status_code == 400
    assert invalid_compare_method.status_code == 400
    assert invalid_compare_method.json()["detail"] == "Invalid return method"
    assert invalid_compare_limit.status_code == 400


def test_missing_and_non_completed_run_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.get("/api/ml/evaluation/runs/999")
    non_completed = client.get(f"/api/ml/evaluation/runs/{running.id}")

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_evaluation_dataset(db_session)

    report = client.get(f"/api/ml/evaluation/runs/{run.id}").json()
    rows = client.get(f"/api/ml/evaluation/runs/{run.id}/rows").json()
    comparison = client.get("/api/ml/evaluation/compare").json()
    text = json.dumps([report, rows, comparison], ensure_ascii=False).lower()
    forbidden_patterns = [
        r"\bbuy\b",
        r"\bsell\b",
        r"\bhold\b",
        r"\bstrong_buy\b",
        r"\bstrong_sell\b",
        r"\bmust_buy\b",
        r"\bmust_sell\b",
        r"\bпокупать\b",
        r"\bпродавать\b",
    ]

    assert all(re.search(pattern, text) is None for pattern in forbidden_patterns)
