from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction


def create_company(db: Session, ticker: str = "MLT") -> Company:
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


def create_bond(db: Session, company: Company, isin: str = "RU000MLT001") -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        secid=isin[-6:],
        name=f"ML Bond {isin}",
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


def add_ml_dataset_row(
    db: Session,
    bond: Bond,
    company: Company,
    *,
    as_of_date: date,
    positive: bool,
    insufficient: bool = False,
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=company.id,
        as_of_date=as_of_date,
        bond_score=Decimal("82.00") if positive else Decimal("35.00"),
        company_score=Decimal("78.00") if positive else Decimal("42.00"),
        yield_to_maturity=Decimal("14.500") if positive else Decimal("7.500"),
        duration_years=Decimal("2.000") if positive else Decimal("6.500"),
        liquidity_score=85 if positive else 30,
        volume=Decimal("25000000.00") if positive else Decimal("100000.00"),
        spread_to_ofz=None,
        net_debt_to_ebitda=Decimal("1.000000") if positive else Decimal("4.000000"),
        debt_to_equity=Decimal("0.400000") if positive else Decimal("2.500000"),
        interest_coverage=Decimal("8.000000") if positive else Decimal("1.200000"),
        cash_to_short_term_debt=Decimal("2.000000") if positive else Decimal("0.300000"),
        ocf_to_total_debt=Decimal("0.500000") if positive else Decimal("0.050000"),
        net_profit_margin=Decimal("0.150000") if positive else Decimal("-0.020000"),
        days_to_maturity=900 if positive else 1800,
        has_offer=False,
        has_amortization=False,
        missing_data_count=1 if positive else 3,
        features_json={},
        created_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    db.add(feature)
    db.flush()
    if insufficient:
        label = "insufficient_data"
        label_binary = None
        future_return = None
    else:
        label = "positive_return" if positive else "negative_return"
        label_binary = 1 if positive else 0
        future_return = Decimal("0.030000") if positive else Decimal("-0.020000")
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=30,
            future_return=future_return,
            label=label,
            label_binary=label_binary,
            created_at=datetime(2026, 1, 2, 12, 0, 0),
        )
    )
    db.commit()


def seed_training_dataset(
    db: Session,
    *,
    usable_rows: int = 40,
    insufficient_rows: int = 0,
    one_class: bool = False,
) -> tuple[Company, Bond]:
    company = create_company(db)
    bond = create_bond(db, company)
    start_date = date(2026, 1, 1)
    for index in range(usable_rows):
        positive = True if one_class else index % 2 == 0
        add_ml_dataset_row(
            db,
            bond,
            company,
            as_of_date=start_date + timedelta(days=index),
            positive=positive,
        )
    for index in range(insufficient_rows):
        add_ml_dataset_row(
            db,
            bond,
            company,
            as_of_date=start_date + timedelta(days=usable_rows + index),
            positive=True,
            insufficient=True,
        )
    return company, bond


def train_payload(**overrides):
    payload = {
        "horizon_days": 30,
        "test_size": 0.2,
        "min_rows": 10,
        "model_type": "logistic_regression",
        "random_state": 42,
    }
    payload.update(overrides)
    return payload


def test_train_model_successfully(
    client: TestClient,
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "ML_ARTIFACT_DIR", str(tmp_path))
    seed_training_dataset(db_session, usable_rows=40)

    response = client.post("/api/ml/train", json=train_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["train_rows"] > 0
    assert payload["test_rows"] > 0
    assert payload["positive_rows"] == 20
    assert payload["negative_rows"] == 20
    assert payload["metrics"]["accuracy"] is not None
    assert "confusion_matrix" in payload["metrics"]
    assert payload["feature_importance"]
    assert Path(payload["artifact_path"]).exists()


def test_training_excludes_insufficient_data(
    client: TestClient,
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "ML_ARTIFACT_DIR", str(tmp_path))
    seed_training_dataset(db_session, usable_rows=24, insufficient_rows=6)

    response = client.post("/api/ml/train", json=train_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["train_rows"] + payload["test_rows"] == 24


def test_training_fails_for_one_class_target(
    client: TestClient,
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "ML_ARTIFACT_DIR", str(tmp_path))
    seed_training_dataset(db_session, usable_rows=30, one_class=True)

    response = client.post("/api/ml/train", json=train_payload())

    assert response.status_code == 400
    assert response.json()["detail"] == "Training dataset must contain at least two classes"


def test_training_fails_when_not_enough_rows(
    client: TestClient,
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "ML_ARTIFACT_DIR", str(tmp_path))
    seed_training_dataset(db_session, usable_rows=8)

    response = client.post("/api/ml/train", json=train_payload(min_rows=10))

    assert response.status_code == 400
    assert response.json()["detail"] == "Not enough training rows"


def test_invalid_train_request(client: TestClient) -> None:
    invalid_horizon = client.post("/api/ml/train", json=train_payload(horizon_days=0))
    invalid_range = client.post(
        "/api/ml/train",
        json=train_payload(as_of_date_from="2026-02-01", as_of_date_to="2026-01-01"),
    )
    invalid_test_size = client.post("/api/ml/train", json=train_payload(test_size=0.5))
    invalid_model_type = client.post(
        "/api/ml/train",
        json=train_payload(model_type="random_forest"),
    )

    assert invalid_horizon.status_code == 400
    assert invalid_horizon.json()["detail"] == "horizon_days must be positive"
    assert invalid_range.status_code == 400
    assert invalid_range.json()["detail"] == "Invalid date range"
    assert invalid_test_size.status_code == 400
    assert (
        invalid_test_size.json()["detail"]
        == "test_size must be greater than 0 and less than 0.5"
    )
    assert invalid_model_type.status_code == 400
    assert invalid_model_type.json()["detail"] == "Unsupported model type"


def test_list_and_get_model_runs(
    client: TestClient,
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "ML_ARTIFACT_DIR", str(tmp_path))
    seed_training_dataset(db_session, usable_rows=30)
    train_response = client.post("/api/ml/train", json=train_payload())
    run_id = train_response.json()["run_id"]

    list_response = client.get("/api/ml/runs")
    get_response = client.get(f"/api/ml/runs/{run_id}")

    assert list_response.status_code == 200
    assert any(run["id"] == run_id for run in list_response.json())
    assert get_response.status_code == 200
    assert get_response.json()["id"] == run_id


def test_predict_with_trained_model_and_upsert(
    client: TestClient,
    db_session: Session,
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "ML_ARTIFACT_DIR", str(tmp_path))
    seed_training_dataset(db_session, usable_rows=30)
    train_response = client.post("/api/ml/train", json=train_payload())
    run_id = train_response.json()["run_id"]

    first = client.post(
        "/api/ml/predict",
        json={"model_run_id": run_id, "limit": 5, "save_predictions": True},
    )
    second = client.post(
        "/api/ml/predict",
        json={"model_run_id": run_id, "limit": 5, "save_predictions": True},
    )
    listed = client.get(f"/api/ml/predictions?model_run_id={run_id}")

    assert first.status_code == 200
    assert second.status_code == 200
    predictions = first.json()["predictions"]
    assert predictions
    allowed = {"predicted_positive_return", "predicted_negative_return"}
    for prediction in predictions:
        probability = Decimal(str(prediction["probability_positive"]))
        assert Decimal("0") <= probability <= Decimal("1")
        assert prediction["predicted_label"] in allowed
    assert db_session.query(MLPrediction).filter_by(model_run_id=run_id).count() == 5
    assert listed.status_code == 200
    assert listed.json()["total"] == 5


def test_predict_fails_for_missing_or_not_completed_model(
    client: TestClient,
    db_session: Session,
) -> None:
    missing = client.post("/api/ml/predict", json={"model_run_id": 999})
    run = MLModelRun(
        status="running",
        model_type="logistic_regression",
        horizon_days=30,
        features=["bond_score"],
        target="label_binary",
        train_rows=0,
        test_rows=0,
        positive_rows=0,
        negative_rows=0,
        metrics={},
        feature_importance=[],
        params={},
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    not_completed = client.post("/api/ml/predict", json={"model_run_id": run.id})

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert not_completed.status_code == 400
    assert not_completed.json()["detail"] == "ML model run is not completed"


def test_predict_fails_when_artifact_is_missing(
    client: TestClient,
    db_session: Session,
) -> None:
    run = MLModelRun(
        status="completed",
        model_type="logistic_regression",
        horizon_days=30,
        features=["bond_score"],
        target="label_binary",
        train_rows=10,
        test_rows=2,
        positive_rows=6,
        negative_rows=6,
        metrics={},
        feature_importance=[],
        params={},
        artifact_path="missing-artifact.joblib",
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    response = client.post("/api/ml/predict", json={"model_run_id": run.id})

    assert response.status_code == 500
    assert response.json()["detail"] == "ML model artifact is missing"


def test_no_investment_advice_labels_and_no_forbidden_dependencies() -> None:
    requirements = Path("backend/requirements.txt").read_text(encoding="utf-8")
    forbidden_dependencies = {
        "pandas",
        "xgboost",
        "catboost",
        "tensorflow",
        "pytorch",
        "transformers",
    }
    forbidden_labels = {"buy", "sell", "hold", "strong_buy", "strong_sell"}
    allowed_prediction_labels = {
        "predicted_positive_return",
        "predicted_negative_return",
    }

    assert all(package not in requirements for package in forbidden_dependencies)
    assert allowed_prediction_labels.isdisjoint(forbidden_labels)
