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


COMPARE_URL = "/api/ml/evaluation/candidates/compare"


def compare_payload(candidates: list[dict], **overrides) -> dict:
    payload = {
        "candidates": candidates,
        "positive_probability_cutoff": "0.50",
        "ranking_metric": "probability_separation",
        "ranking_direction": "desc",
        "include_prediction_quality": False,
        "include_failed_candidates": True,
        "bucket_count": 10,
        "minimum_evaluable_predictions": 2,
        "minimum_positive_labels": 1,
        "minimum_negative_labels": 1,
        "maximum_missing_label_ratio": "0.30",
        "max_candidates": 20,
        "limit": 100,
        "offset": 0,
    }
    payload.update(overrides)
    return payload


def create_company(db: Session, index: int = 1) -> Company:
    company = Company(
        name=f"Candidate Comparison Company {index}",
        ticker=f"CC{index:04d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(db: Session, company: Company, index: int = 1) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RUC{index:09d}",
        secid=f"CCB{index:05d}",
        name=f"Candidate Comparison Bond {index}",
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
        train_rows=60,
        test_rows=20,
        positive_rows=30,
        negative_rows=30,
        metrics={"accuracy": 0.75},
        feature_importance=[{"feature": "bond_score", "importance": 0.5}],
        params={"return_method": return_method, "candidate": index},
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
) -> None:
    feature = feature_for(db, bond=bond, company=company, as_of_date=as_of_date)
    db.add(
        MLPrediction(
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
    )
    db.flush()


def add_label(
    db: Session,
    *,
    bond: Bond,
    as_of_date: date,
    horizon_days: int = 30,
    return_method: str = "risk_adjusted",
    label_binary: int | None = 1,
    future_return: Decimal | None = Decimal("0.020000"),
) -> None:
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            return_method=return_method,
            future_return=future_return,
            risk_adjusted_excess_return=(
                future_return if return_method == "risk_adjusted" else None
            ),
            net_total_return=(
                future_return if return_method == "total_return" else None
            ),
            price_return=future_return if return_method == "price" else None,
            label="positive_return" if label_binary == 1 else "negative_return",
            label_binary=label_binary,
        )
    )
    db.flush()


def seed_candidate(
    db: Session,
    *,
    index: int,
    probabilities: list[Decimal],
    labels: list[int],
    return_method: str = "risk_adjusted",
) -> tuple[MLModelRun, Company, Bond]:
    company = create_company(db, index)
    bond = create_bond(db, company, index)
    run = create_run(db, index=index, return_method=return_method)
    for offset, (probability, label_binary) in enumerate(zip(probabilities, labels)):
        as_of_date = date(2026, 1, 1 + offset)
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
            return_method=return_method,
            label_binary=label_binary,
            future_return=(
                Decimal("0.030000") if label_binary == 1 else Decimal("-0.020000")
            ),
        )
    db.commit()
    return run, company, bond


def count_rows(db: Session, model) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def test_two_successful_candidates_rank_by_probability_separation(
    client: TestClient,
    db_session: Session,
) -> None:
    strong_run, _, _ = seed_candidate(
        db_session,
        index=1,
        probabilities=[
            Decimal("0.90"),
            Decimal("0.80"),
            Decimal("0.10"),
            Decimal("0.20"),
        ],
        labels=[1, 1, 0, 0],
    )
    weak_run, _, _ = seed_candidate(
        db_session,
        index=2,
        probabilities=[
            Decimal("0.55"),
            Decimal("0.52"),
            Decimal("0.48"),
            Decimal("0.45"),
        ],
        labels=[1, 1, 0, 0],
    )

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [
                {"name": "weak", "model_run_id": weak_run.id},
                {"name": "strong", "model_run_id": strong_run.id},
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["candidate_count"] == 2
    assert payload["completed_candidate_count"] == 2
    assert payload["leaderboard"][0]["name"] == "strong"
    assert payload["selected_candidate"]["name"] == "strong"
    assert payload["selected_candidate"]["ready_for_strategy_research"] is True


def test_ranking_by_missing_label_ratio_ascending(
    client: TestClient,
    db_session: Session,
) -> None:
    complete_run, _, _ = seed_candidate(
        db_session,
        index=3,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )
    company = create_company(db_session, 4)
    bond = create_bond(db_session, company, 4)
    sparse_run = create_run(db_session, index=4)
    add_prediction(
        db_session,
        run=sparse_run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.90"),
    )
    add_label(
        db_session,
        bond=bond,
        as_of_date=date(2026, 1, 1),
        label_binary=1,
        future_return=Decimal("0.020000"),
    )
    add_prediction(
        db_session,
        run=sparse_run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 2),
        probability=Decimal("0.10"),
    )
    db_session.commit()

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [
                {"name": "sparse", "model_run_id": sparse_run.id},
                {"name": "complete", "model_run_id": complete_run.id},
            ],
            ranking_metric="missing_label_ratio",
            ranking_direction="asc",
            maximum_missing_label_ratio="1.0",
            minimum_negative_labels=0,
        ),
    )

    assert response.status_code == 200
    assert response.json()["leaderboard"][0]["name"] == "complete"


def test_multi_run_candidate_works(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, company, bond = seed_candidate(
        db_session,
        index=5,
        probabilities=[Decimal("0.90")],
        labels=[1],
    )
    second_run = create_run(db_session, index=6)
    add_prediction(
        db_session,
        run=second_run,
        bond=bond,
        company=company,
        as_of_date=date(2026, 1, 2),
        probability=Decimal("0.10"),
    )
    add_label(
        db_session,
        bond=bond,
        as_of_date=date(2026, 1, 2),
        label_binary=0,
        future_return=Decimal("-0.020000"),
    )
    db_session.commit()

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [{"name": "stitched", "model_run_ids": [first_run.id, second_run.id]}]
        ),
    )

    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["prediction_source_mode"] == "multiple_model_runs"
    assert candidate["model_run_ids"] == [first_run.id, second_run.id]


def test_failed_candidate_does_not_fail_whole_comparison(
    client: TestClient,
    db_session: Session,
) -> None:
    valid_run, _, _ = seed_candidate(
        db_session,
        index=7,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [
                {"name": "valid", "model_run_id": valid_run.id},
                {"name": "missing", "model_run_id": 999999},
            ]
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["completed_candidate_count"] == 1
    assert payload["failed_candidate_count"] == 1
    assert payload["leaderboard"][0]["name"] == "valid"
    failed = next(candidate for candidate in payload["candidates"] if candidate["status"] == "failed")
    assert failed["error"] == "ML model run not found"


def test_include_failed_candidates_false_hides_failed_result(
    client: TestClient,
    db_session: Session,
) -> None:
    valid_run, _, _ = seed_candidate(
        db_session,
        index=8,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [
                {"name": "valid", "model_run_id": valid_run.id},
                {"name": "missing", "model_run_id": 999999},
            ],
            include_failed_candidates=False,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["failed_candidate_count"] == 1
    assert [candidate["name"] for candidate in payload["candidates"]] == ["valid"]
    assert [item["name"] for item in payload["leaderboard"]] == ["valid"]


def test_include_prediction_quality_toggles_nested_details(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=9,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    compact = client.post(
        COMPARE_URL,
        json=compare_payload([{"model_run_id": run.id}]),
    )
    detailed = client.post(
        COMPARE_URL,
        json=compare_payload(
            [{"model_run_id": run.id}],
            include_prediction_quality=True,
        ),
    )

    assert compact.status_code == 200
    assert compact.json()["candidates"][0]["prediction_quality"] is None
    assert detailed.status_code == 200
    nested = detailed.json()["candidates"][0]["prediction_quality"]
    assert nested["overview"]["prediction_count"] == 2
    assert nested["run_rows"]
    assert nested["probability_buckets"]


def test_generated_candidate_names(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, _, _ = seed_candidate(
        db_session,
        index=10,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )
    second_run, _, _ = seed_candidate(
        db_session,
        index=11,
        probabilities=[Decimal("0.80"), Decimal("0.20")],
        labels=[1, 0],
    )

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [{"model_run_id": first_run.id}, {"model_run_id": second_run.id}]
        ),
    )

    assert response.status_code == 200
    assert [candidate["name"] for candidate in response.json()["candidates"]] == [
        "candidate_1",
        "candidate_2",
    ]


def test_selected_candidate_requires_ready_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    not_ready_run, _, _ = seed_candidate(
        db_session,
        index=12,
        probabilities=[Decimal("0.95"), Decimal("0.05")],
        labels=[1, 0],
    )
    ready_run, _, _ = seed_candidate(
        db_session,
        index=13,
        probabilities=[Decimal("0.80"), Decimal("0.20")],
        labels=[1, 0],
    )

    response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [
                {"name": "not_ready", "model_run_id": not_ready_run.id},
                {"name": "ready", "model_run_id": ready_run.id},
            ],
            minimum_evaluable_predictions=3,
        ),
    )
    assert response.status_code == 200
    assert response.json()["selected_candidate"] is None
    assert response.json()["warnings"][0]["message"] == (
        "No ready completed candidate with ranking value was available"
    )

    ready_response = client.post(
        COMPARE_URL,
        json=compare_payload(
            [
                {"name": "not_ready", "model_run_id": not_ready_run.id},
                {"name": "ready", "model_run_id": ready_run.id},
            ],
            minimum_evaluable_predictions=2,
        ),
    )
    assert ready_response.status_code == 200
    assert ready_response.json()["selected_candidate"]["name"] == "not_ready"


def test_request_level_validation_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    db_session.commit()
    base_candidate = {"model_run_id": run.id}
    cases = [
        (compare_payload([]), "candidates must not be empty"),
        (
            compare_payload([base_candidate, base_candidate], max_candidates=1),
            "candidates must not exceed max_candidates",
        ),
        (
            compare_payload([base_candidate], max_candidates=0),
            "max_candidates must be between 1 and 100",
        ),
        (
            compare_payload(
                [base_candidate],
                date_from="2026-02-01",
                date_to="2026-01-01",
            ),
            "Invalid date range",
        ),
        (
            compare_payload([base_candidate], return_method="magic"),
            "Invalid return method",
        ),
        (
            compare_payload([base_candidate], horizon_days=0),
            "horizon_days must be positive",
        ),
        (
            compare_payload([base_candidate], positive_probability_cutoff="-0.1"),
            "positive_probability_cutoff must be between 0 and 1",
        ),
        (
            compare_payload([base_candidate], ranking_metric="magic"),
            "Invalid ranking metric",
        ),
        (
            compare_payload([base_candidate], ranking_direction="sideways"),
            "Invalid ranking direction",
        ),
        (
            compare_payload([base_candidate], bucket_count=1),
            "bucket_count must be between 2 and 50",
        ),
        (
            compare_payload([base_candidate], minimum_evaluable_predictions=0),
            "minimum_evaluable_predictions must be positive",
        ),
        (
            compare_payload([base_candidate], minimum_positive_labels=-1),
            "class minimums must be non-negative",
        ),
        (
            compare_payload([base_candidate], maximum_missing_label_ratio="1.1"),
            "maximum_missing_label_ratio must be between 0 and 1",
        ),
        (
            compare_payload([base_candidate], limit=0),
            "limit must be between 1 and 500",
        ),
        (
            compare_payload([base_candidate], offset=-1),
            "offset must be non-negative",
        ),
    ]

    for payload, detail in cases:
        response = client.post(COMPARE_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_candidate_level_validation_becomes_failed_result(
    client: TestClient,
    db_session: Session,
) -> None:
    base = create_run(db_session, index=14)
    different_method = create_run(db_session, index=15, return_method="price")
    running = create_run(db_session, index=16, status="running")
    db_session.commit()
    candidates = [
        {
            "name": "both",
            "model_run_id": base.id,
            "model_run_ids": [base.id],
        },
        {"name": "empty", "model_run_ids": []},
        {"name": "duplicate", "model_run_ids": [base.id, base.id]},
        {"name": "missing", "model_run_id": 999999},
        {"name": "running", "model_run_id": running.id},
        {
            "name": "incompatible",
            "model_run_ids": [base.id, different_method.id],
        },
    ]

    response = client.post(COMPARE_URL, json=compare_payload(candidates))

    assert response.status_code == 200
    payload = response.json()
    assert payload["completed_candidate_count"] == 0
    assert payload["failed_candidate_count"] == len(candidates)
    errors = {candidate["name"]: candidate["error"] for candidate in payload["candidates"]}
    assert errors["both"] == "Use only one of model_run_id or model_run_ids"
    assert errors["empty"] == "model_run_ids must not be empty"
    assert errors["duplicate"] == "model_run_ids must not contain duplicates"
    assert errors["missing"] == "ML model run not found"
    assert errors["running"] == "ML model run is not completed"
    assert errors["incompatible"] == (
        "Model runs must use the same horizon and return method"
    )


def test_comparison_does_not_write_db_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=17,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )
    models = [
        MLModelRun,
        MLPrediction,
        BondReturnLabel,
        Bond,
        Company,
        BondFeatureSnapshot,
    ]
    before = {model.__name__: count_rows(db_session, model) for model in models}

    response = client.post(
        COMPARE_URL,
        json=compare_payload([{"model_run_id": run.id}]),
    )

    assert response.status_code == 200
    after = {model.__name__: count_rows(db_session, model) for model in models}
    assert after == before


def test_comparison_does_not_call_generation_or_external_services(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=18,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

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

    response = client.post(
        COMPARE_URL,
        json=compare_payload([{"model_run_id": run.id}]),
    )

    assert response.status_code == 200


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=19,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        COMPARE_URL,
        json=compare_payload([{"model_run_id": run.id}]),
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
