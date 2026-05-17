from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.services.data_pipeline_service import DataPipelineService
from app.services.dataset_build_service import DatasetBuildService
from app.services.label_builder_service import LabelBuilderService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_scenario_service import PaperTradingScenarioService
from app.services.strategy_backtest_service import StrategyBacktestService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


REPORT_URL = "/api/ml/evaluation/prediction-quality/report"


def report_payload(run: MLModelRun | None = None, **overrides) -> dict:
    payload = {
        "model_run_id": run.id if run is not None else None,
        "positive_probability_cutoff": "0.50",
        "include_run_rows": True,
        "include_date_rows": True,
        "include_probability_buckets": True,
        "include_missing_label_examples": True,
        "bucket_count": 10,
        "minimum_evaluable_predictions": 1,
        "minimum_positive_labels": 1,
        "minimum_negative_labels": 1,
        "maximum_missing_label_ratio": "0.30",
        "limit": 100,
        "offset": 0,
    }
    payload.update(overrides)
    return payload


def create_company(db: Session, index: int = 1) -> Company:
    company = Company(
        name=f"Prediction Quality Company {index}",
        ticker=f"PQ{index:04d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(db: Session, company: Company, index: int = 1) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RUQ{index:09d}",
        secid=f"PQB{index:05d}",
        name=f"Prediction Quality Bond {index}",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def create_run(
    db: Session,
    *,
    index: int = 1,
    horizon_days: int = 30,
    return_method: str = "risk_adjusted",
    status: str = "completed",
) -> MLModelRun:
    run = MLModelRun(
        status=status,
        model_type="logistic_regression",
        horizon_days=horizon_days,
        features=["bond_score", "company_score"],
        target="label_binary",
        train_rows=40,
        test_rows=10,
        positive_rows=20,
        negative_rows=20,
        metrics={"accuracy": 0.7},
        feature_importance=[{"feature": "bond_score", "importance": 0.5}],
        params={"return_method": return_method, "fold": index},
    )
    db.add(run)
    db.flush()
    return run


def feature_for(
    db: Session,
    *,
    bond: Bond,
    company: Company,
    as_of_date: date,
) -> BondFeatureSnapshot:
    existing = db.execute(
        select(BondFeatureSnapshot).where(
            BondFeatureSnapshot.bond_id == bond.id,
            BondFeatureSnapshot.as_of_date == as_of_date,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=company.id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        missing_data_count=0,
        features_json={"bond_score": 70},
    )
    db.add(feature)
    db.flush()
    return feature


def add_prediction(
    db: Session,
    *,
    run: MLModelRun,
    bond: Bond,
    company: Company,
    as_of_date: date,
    probability: Decimal,
) -> MLPrediction:
    feature = feature_for(db, bond=bond, company=company, as_of_date=as_of_date)
    prediction = MLPrediction(
        model_run_id=run.id,
        feature_snapshot_id=feature.id,
        bond_id=bond.id,
        company_id=company.id,
        as_of_date=as_of_date,
        horizon_days=run.horizon_days,
        probability_positive=probability,
        predicted_label=(
            "predicted_positive_return"
            if probability >= Decimal("0.50")
            else "predicted_negative_return"
        ),
        features={"bond_score": 70},
    )
    db.add(prediction)
    db.flush()
    return prediction


def add_label(
    db: Session,
    *,
    bond: Bond,
    as_of_date: date,
    horizon_days: int = 30,
    return_method: str = "risk_adjusted",
    label: str = "positive_return",
    label_binary: int | None = 1,
    future_return: Decimal | None = Decimal("0.020000"),
) -> BondReturnLabel:
    label_row = BondReturnLabel(
        bond_id=bond.id,
        as_of_date=as_of_date,
        horizon_days=horizon_days,
        return_method=return_method,
        future_return=future_return,
        risk_adjusted_excess_return=(
            future_return if return_method == "risk_adjusted" else None
        ),
        net_total_return=future_return if return_method == "total_return" else None,
        price_return=future_return if return_method == "price" else None,
        label=label,
        label_binary=label_binary,
    )
    db.add(label_row)
    db.flush()
    return label_row


def seed_confusion_dataset(
    db: Session,
) -> tuple[MLModelRun, Company, Bond]:
    company = create_company(db, 1)
    bond = create_bond(db, company, 1)
    run = create_run(db)
    rows = [
        (date(2026, 1, 1), Decimal("0.80"), 1, Decimal("0.050000")),
        (date(2026, 1, 2), Decimal("0.70"), 0, Decimal("-0.020000")),
        (date(2026, 1, 3), Decimal("0.20"), 0, Decimal("-0.030000")),
        (date(2026, 1, 4), Decimal("0.10"), 1, Decimal("0.010000")),
    ]
    for as_of_date, probability, label_binary, future_return in rows:
        add_prediction(
            db,
            run=run,
            bond=bond,
            company=company,
            as_of_date=as_of_date,
            probability=probability,
        )
        add_label(
            db,
            bond=bond,
            as_of_date=as_of_date,
            label="positive_return" if label_binary == 1 else "negative_return",
            label_binary=label_binary,
            future_return=future_return,
        )
    db.commit()
    return run, company, bond


def count_rows(db: Session, model) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def test_single_run_report_with_matched_labels(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)

    response = client.post(REPORT_URL, json=report_payload(run))

    assert response.status_code == 200
    payload = response.json()
    assert payload["prediction_source_mode"] == "single_model_run"
    assert payload["model_run_ids"] == [run.id]
    assert payload["overview"]["prediction_count"] == 4
    assert payload["overview"]["evaluable_prediction_count"] == 4
    assert payload["overview"]["positive_label_count"] == 2
    assert payload["overview"]["negative_label_count"] == 2
    assert payload["metrics"]["true_positive_count"] == 1
    assert payload["metrics"]["true_negative_count"] == 1
    assert Decimal(str(payload["metrics"]["accuracy"])) == Decimal("0.5")
    assert Decimal(str(payload["metrics"]["precision"])) == Decimal("0.5")
    assert Decimal(str(payload["metrics"]["recall"])) == Decimal("0.5")
    assert Decimal(str(payload["metrics"]["f1_score"])) == Decimal("0.5")


def test_multi_run_report_stitches_predictions(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, 2)
    first_bond = create_bond(db_session, company, 2)
    second_bond = create_bond(db_session, company, 3)
    first_run = create_run(db_session, index=1)
    second_run = create_run(db_session, index=2)
    add_prediction(
        db_session,
        run=first_run,
        bond=first_bond,
        company=company,
        as_of_date=date(2026, 2, 1),
        probability=Decimal("0.80"),
    )
    add_label(
        db_session,
        bond=first_bond,
        as_of_date=date(2026, 2, 1),
        label_binary=1,
        future_return=Decimal("0.030000"),
    )
    add_prediction(
        db_session,
        run=second_run,
        bond=second_bond,
        company=company,
        as_of_date=date(2026, 3, 1),
        probability=Decimal("0.20"),
    )
    add_label(
        db_session,
        bond=second_bond,
        as_of_date=date(2026, 3, 1),
        label="negative_return",
        label_binary=0,
        future_return=Decimal("-0.020000"),
    )
    db_session.commit()

    response = client.post(
        REPORT_URL,
        json=report_payload(
            None,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["prediction_source_mode"] == "multiple_model_runs"
    assert payload["model_run_ids"] == [first_run.id, second_run.id]
    assert payload["overview"]["prediction_count"] == 2
    assert [row["model_run_id"] for row in payload["run_rows"]] == [
        first_run.id,
        second_run.id,
    ]


def test_duplicate_multi_run_predictions_resolve_by_order(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, 4)
    bond = create_bond(db_session, company, 4)
    first_run = create_run(db_session, index=1)
    second_run = create_run(db_session, index=2)
    as_of_date = date(2026, 1, 10)
    add_prediction(
        db_session,
        run=first_run,
        bond=bond,
        company=company,
        as_of_date=as_of_date,
        probability=Decimal("0.10"),
    )
    add_prediction(
        db_session,
        run=second_run,
        bond=bond,
        company=company,
        as_of_date=as_of_date,
        probability=Decimal("0.90"),
    )
    add_label(
        db_session,
        bond=bond,
        as_of_date=as_of_date,
        label_binary=1,
        future_return=Decimal("0.040000"),
    )
    db_session.commit()

    response = client.post(
        REPORT_URL,
        json=report_payload(
            None,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["prediction_count"] == 1
    assert payload["metrics"]["true_positive_count"] == 1
    assert Decimal(str(payload["metrics"]["average_probability_positive"])) == Decimal(
        "0.9000000000"
    )
    assert payload["run_rows"][0]["issues"][0] == "no_predictions"
    assert any(
        warning["message"]
        == "Duplicate walk-forward predictions were resolved by model_run_ids order"
        for warning in payload["warnings"]
    )


def test_missing_labels_counted_and_examples_returned(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, 5)
    bond = create_bond(db_session, company, 5)
    run = create_run(db_session)
    add_prediction(
        db_session,
        run=run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 15),
        probability=Decimal("0.60"),
    )
    db_session.commit()

    response = client.post(
        REPORT_URL,
        json=report_payload(run, maximum_missing_label_ratio="1.0"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["missing_label_count"] == 1
    assert payload["missing_label_examples"] == [
        {
            "model_run_id": run.id,
            "bond_id": bond.id,
            "as_of_date": "2026-01-15",
            "horizon_days": 30,
            "return_method": "risk_adjusted",
            "probability_positive": "0.6000000000",
            "reason": "No matching realized label",
        }
    ]


def test_date_range_filters_predictions(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)

    response = client.post(
        REPORT_URL,
        json=report_payload(
            run,
            date_from="2026-01-02",
            date_to="2026-01-03",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["prediction_count"] == 2
    assert [row["as_of_date"] for row in payload["date_rows"]] == [
        "2026-01-02",
        "2026-01-03",
    ]


def test_requested_metadata_must_match_model_run(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)

    horizon = client.post(REPORT_URL, json=report_payload(run, horizon_days=60))
    method = client.post(REPORT_URL, json=report_payload(run, return_method="price"))

    assert horizon.status_code == 400
    assert horizon.json()["detail"] == (
        "Model runs must use the same horizon and return method"
    )
    assert method.status_code == 400
    assert method.json()["detail"] == (
        "Model runs must use the same horizon and return method"
    )


def test_incompatible_missing_and_non_completed_model_run_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    base = create_run(db_session, index=1)
    different_method = create_run(db_session, index=2, return_method="price")
    different_horizon = create_run(db_session, index=3, horizon_days=60)
    running = create_run(db_session, index=4, status="running")
    db_session.commit()

    method = client.post(
        REPORT_URL,
        json=report_payload(
            None,
            model_run_id=None,
            model_run_ids=[base.id, different_method.id],
        ),
    )
    horizon = client.post(
        REPORT_URL,
        json=report_payload(
            None,
            model_run_id=None,
            model_run_ids=[base.id, different_horizon.id],
        ),
    )
    missing = client.post(REPORT_URL, json={"model_run_id": 999999})
    non_completed = client.post(REPORT_URL, json={"model_run_id": running.id})

    assert method.status_code == 400
    assert method.json()["detail"] == (
        "Model runs must use the same horizon and return method"
    )
    assert horizon.status_code == 400
    assert horizon.json()["detail"] == (
        "Model runs must use the same horizon and return method"
    )
    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_probability_buckets_and_separation(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, 6)
    bond = create_bond(db_session, company, 6)
    run = create_run(db_session)
    rows = [
        (date(2026, 2, 1), Decimal("0.10"), 0, Decimal("-0.030000")),
        (date(2026, 2, 2), Decimal("0.20"), 0, Decimal("-0.020000")),
        (date(2026, 2, 3), Decimal("0.80"), 1, Decimal("0.030000")),
        (date(2026, 2, 4), Decimal("1.00"), 1, Decimal("0.040000")),
    ]
    for as_of_date, probability, label_binary, future_return in rows:
        add_prediction(
            db_session,
            run=run,
            bond=bond,
            company=company,
            as_of_date=as_of_date,
            probability=probability,
        )
        add_label(
            db_session,
            bond=bond,
            as_of_date=as_of_date,
            label="positive_return" if label_binary else "negative_return",
            label_binary=label_binary,
            future_return=future_return,
        )
    db_session.commit()

    response = client.post(REPORT_URL, json=report_payload(run, bucket_count=10))

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["metrics"]["probability_separation"])) == Decimal(
        "0.75"
    )
    assert len(payload["probability_buckets"]) == 10
    assert payload["probability_buckets"][-1]["prediction_count"] == 1
    assert payload["probability_buckets"][-1]["positive_label_ratio"] in (
        "1",
        1,
        "1.0",
    )


def test_run_and_date_row_issues(
    client: TestClient,
    db_session: Session,
) -> None:
    empty_run = create_run(db_session, index=7)
    company = create_company(db_session, 7)
    bond = create_bond(db_session, company, 7)
    missing_label_run = create_run(db_session, index=8)
    add_prediction(
        db_session,
        run=missing_label_run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.90"),
    )
    db_session.commit()

    empty = client.post(REPORT_URL, json=report_payload(empty_run))
    missing = client.post(
        REPORT_URL,
        json=report_payload(
            missing_label_run,
            maximum_missing_label_ratio="0.20",
        ),
    )

    assert empty.status_code == 200
    assert {"no_predictions", "no_evaluable_predictions"}.issubset(
        set(empty.json()["run_rows"][0]["issues"])
    )
    assert missing.status_code == 200
    missing_payload = missing.json()
    assert "high_missing_label_ratio" in missing_payload["run_rows"][0]["issues"]
    assert "zero_predicted_negative" in missing_payload["date_rows"][0]["issues"]
    assert missing_payload["issue_summary"]["high_missing_label_ratio"] == 1


def test_include_flags_and_pagination(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)

    paged = client.post(REPORT_URL, json=report_payload(run, limit=1, offset=1))
    suppressed = client.post(
        REPORT_URL,
        json=report_payload(
            run,
            include_run_rows=False,
            include_date_rows=False,
            include_probability_buckets=False,
            include_missing_label_examples=False,
        ),
    )

    assert paged.status_code == 200
    assert [row["as_of_date"] for row in paged.json()["date_rows"]] == ["2026-01-02"]
    assert suppressed.status_code == 200
    payload = suppressed.json()
    assert payload["overview"]["prediction_count"] == 4
    assert payload["run_rows"] == []
    assert payload["date_rows"] == []
    assert payload["probability_buckets"] == []
    assert payload["missing_label_examples"] == []


def test_invalid_requests_return_exact_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    db_session.commit()
    cases = [
        ({}, "Provide model_run_id or model_run_ids"),
        (
            {"model_run_id": run.id, "model_run_ids": [run.id]},
            "Use only one of model_run_id or model_run_ids",
        ),
        ({"model_run_ids": []}, "model_run_ids must not be empty"),
        (
            {"model_run_ids": [run.id, run.id]},
            "model_run_ids must not contain duplicates",
        ),
        (
            {"model_run_ids": list(range(1, 202))},
            "model_run_ids must not exceed 200",
        ),
        (
            report_payload(run, date_from="2026-02-01", date_to="2026-01-01"),
            "Invalid date range",
        ),
        (report_payload(run, return_method="magic"), "Invalid return method"),
        (report_payload(run, horizon_days=0), "horizon_days must be positive"),
        (
            report_payload(run, positive_probability_cutoff="-0.1"),
            "positive_probability_cutoff must be between 0 and 1",
        ),
        (
            report_payload(run, bucket_count=1),
            "bucket_count must be between 2 and 50",
        ),
        (
            report_payload(run, minimum_evaluable_predictions=0),
            "minimum_evaluable_predictions must be positive",
        ),
        (
            report_payload(run, minimum_positive_labels=-1),
            "class minimums must be non-negative",
        ),
        (
            report_payload(run, maximum_missing_label_ratio="1.1"),
            "maximum_missing_label_ratio must be between 0 and 1",
        ),
        (report_payload(run, limit=0), "limit must be between 1 and 500"),
        (report_payload(run, offset=-1), "offset must be non-negative"),
    ]

    for payload, detail in cases:
        response = client.post(REPORT_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_report_does_not_write_db_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)
    models = [
        MLModelRun,
        MLPrediction,
        BondReturnLabel,
        Bond,
        Company,
        BondFeatureSnapshot,
    ]
    before = {model.__name__: count_rows(db_session, model) for model in models}

    response = client.post(REPORT_URL, json=report_payload(run))

    assert response.status_code == 200
    after = {model.__name__: count_rows(db_session, model) for model in models}
    assert after == before


def test_report_does_not_call_generation_or_external_services(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)

    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    monkeypatch.setattr(MLTrainingService, "train", fail_call)
    monkeypatch.setattr(MLPredictionService, "predict", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_labels", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(LabelBuilderService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(DatasetBuildService, "build", fail_call)
    monkeypatch.setattr(DataPipelineService, "run", fail_call)
    monkeypatch.setattr(MoexMarketDataService, "sync", fail_call)
    monkeypatch.setattr(MoexCashflowService, "sync", fail_call)
    monkeypatch.setattr(StrategyBacktestService, "run", fail_call)
    monkeypatch.setattr(PaperTradingScenarioService, "run", fail_call)

    response = client.post(REPORT_URL, json=report_payload(run))

    assert response.status_code == 200


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_confusion_dataset(db_session)

    response = client.post(REPORT_URL, json=report_payload(run))

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
