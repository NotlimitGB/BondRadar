from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
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
from app.services.paper_trading_service import PaperTradingService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


PROMOTE_URL = "/api/ml/evaluation/candidates/promote-to-strategy-robustness"


def create_company(db: Session, index: int = 1) -> Company:
    company = Company(
        name=f"Candidate Robustness Company {index}",
        ticker=f"CR{index:04d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(db: Session, company: Company, index: int) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RUR{index:09d}",
        secid=f"CRB{index:05d}",
        name=f"Candidate Robustness Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("10.000"),
        liquidity_score=80,
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
        features=["bond_score", "company_score", "liquidity_score"],
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


def add_risk(db: Session, bond: Bond, as_of_date: date) -> None:
    db.add(
        BondRiskAssessment(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            assessment_score=80,
            decision_status="eligible_for_analysis",
            risk_level="low",
            required_risk_premium=Decimal("0.020000"),
            yield_to_maturity=Decimal("10.000"),
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
        yield_to_maturity=Decimal("10.000"),
        liquidity_score=80,
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
    label_binary: int = 1,
    future_return: Decimal = Decimal("0.020000"),
) -> None:
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            return_method=return_method,
            future_return=future_return,
            risk_adjusted_excess_return=future_return,
            required_risk_premium=Decimal("0.020000"),
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
    returns: list[Decimal] | None = None,
) -> tuple[MLModelRun, Company, list[Bond]]:
    company = create_company(db, index)
    bonds = [create_bond(db, company, index * 10 + item) for item in range(1, 3)]
    for bond in bonds:
        add_risk(db, bond, date(2025, 12, 31))
    run = create_run(db, index=index)
    realized_returns = returns or [
        Decimal("0.030000") if label == 1 else Decimal("-0.020000")
        for label in labels
    ]
    for offset, (probability, label_binary, future_return) in enumerate(
        zip(probabilities, labels, realized_returns)
    ):
        bond = bonds[offset % len(bonds)]
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
            label_binary=label_binary,
            future_return=future_return,
        )
    db.commit()
    return run, company, bonds


def base_experiment(placeholder_run_id: int, **overrides) -> dict:
    payload = {
        "model_run_id": placeholder_run_id,
        "initial_capital": "1000",
        "transaction_cost_rate": "0",
        "ranking_metric": "total_return",
        "ranking_direction": "desc",
        "include_periods": True,
        "include_baselines": True,
        "variants": [
            {
                "name": "top_one",
                "top_n": 1,
                "min_probability_positive": "0.50",
                "max_position_weight": "1",
                "max_issuer_weight": "1",
                "max_high_risk_weight": "1",
            }
        ],
    }
    payload.update(overrides)
    return payload


def base_robustness(placeholder_run_id: int, **overrides) -> dict:
    payload = {
        "experiment": base_experiment(placeholder_run_id),
        "selected_variant_count": 1,
        "subperiod_mode": "monthly",
        "include_subperiod_details": True,
        "include_candidate_concentration": True,
    }
    payload.update(overrides)
    return payload


def promotion_payload(
    candidates: list[dict],
    strategy_robustness: dict,
    **overrides,
) -> dict:
    payload = {
        "candidate_comparison": {
            "candidates": candidates,
            "ranking_metric": "probability_separation",
            "ranking_direction": "desc",
            "minimum_evaluable_predictions": 2,
            "minimum_positive_labels": 1,
            "minimum_negative_labels": 1,
            "maximum_missing_label_ratio": "0.30",
        },
        "strategy_robustness": strategy_robustness,
        "require_ready_candidate": True,
        "include_candidate_comparison": True,
    }
    payload.update(overrides)
    return payload


def count_rows(db: Session, model) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def test_promotes_best_single_run_candidate_to_robustness_analysis(
    client: TestClient,
    db_session: Session,
) -> None:
    weak_run, _, _ = seed_candidate(
        db_session,
        index=1,
        probabilities=[Decimal("0.60"), Decimal("0.40")],
        labels=[1, 0],
    )
    strong_run, _, _ = seed_candidate(
        db_session,
        index=2,
        probabilities=[Decimal("0.95"), Decimal("0.05")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [
                {"name": "weak", "model_run_id": weak_run.id},
                {"name": "strong", "model_run_id": strong_run.id},
            ],
            base_robustness(weak_run.id),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_candidate"]["name"] == "strong"
    assert payload["selected_candidate"]["model_run_id"] == strong_run.id
    assert payload["robustness_analysis"]["model_run_id"] == strong_run.id
    assert payload["robustness_analysis"]["model_run_ids"] == [strong_run.id]
    assert payload["robustness_analysis"]["analyzed_variant_count"] == 1


def test_promotes_best_multi_run_candidate_to_robustness_analysis(
    client: TestClient,
    db_session: Session,
) -> None:
    single_run, _, _ = seed_candidate(
        db_session,
        index=3,
        probabilities=[Decimal("0.55"), Decimal("0.45")],
        labels=[1, 0],
    )
    first_run, company, bonds = seed_candidate(
        db_session,
        index=4,
        probabilities=[Decimal("0.90")],
        labels=[1],
    )
    second_run = create_run(db_session, index=5)
    add_prediction(
        db_session,
        run=second_run,
        bond=bonds[1],
        company=company,
        as_of_date=date(2026, 1, 2),
        probability=Decimal("0.10"),
    )
    add_label(
        db_session,
        bond=bonds[1],
        as_of_date=date(2026, 1, 2),
        label_binary=0,
        future_return=Decimal("-0.020000"),
    )
    db_session.commit()

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [
                {"name": "single", "model_run_id": single_run.id},
                {"name": "stitched", "model_run_ids": [first_run.id, second_run.id]},
            ],
            base_robustness(single_run.id),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_candidate"]["name"] == "stitched"
    assert payload["selected_candidate"]["prediction_source_mode"] == (
        "multiple_model_runs"
    )
    assert payload["robustness_analysis"]["model_run_id"] is None
    assert payload["robustness_analysis"]["model_run_ids"] == [
        first_run.id,
        second_run.id,
    ]


def test_candidate_comparison_include_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=6,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    visible = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            include_candidate_comparison=True,
        ),
    )
    hidden = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            include_candidate_comparison=False,
        ),
    )

    assert visible.status_code == 200
    assert visible.json()["candidate_comparison"] is not None
    assert hidden.status_code == 200
    assert hidden.json()["candidate_comparison"] is None


def test_robustness_include_flags_are_overridden(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=7,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            include_robustness_subperiod_details=False,
            include_robustness_candidate_concentration=False,
        ),
    )

    assert response.status_code == 200
    variant = response.json()["robustness_analysis"]["variants"][0]
    assert variant["subperiods"] == []
    assert variant["top_bond_concentration"] is None
    assert variant["top_company_concentration"] is None


def test_no_ready_candidate_returns_stable_200(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=8,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            candidate_comparison={
                "candidates": [{"model_run_id": run.id}],
                "minimum_evaluable_predictions": 3,
                "minimum_positive_labels": 1,
                "minimum_negative_labels": 1,
            },
            require_ready_candidate=True,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_candidate"] is None
    assert payload["robustness_analysis"] is None
    assert payload["warnings"][0]["message"] == (
        "No ready ML candidate was available for strategy robustness analysis"
    )


def test_require_ready_candidate_false_allows_non_ready_candidate(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=9,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            candidate_comparison={
                "candidates": [{"model_run_id": run.id}],
                "minimum_evaluable_predictions": 3,
                "minimum_positive_labels": 1,
                "minimum_negative_labels": 1,
            },
            require_ready_candidate=False,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_candidate"]["model_run_id"] == run.id
    assert payload["robustness_analysis"] is not None
    assert any(
        warning["message"]
        == "Selected ML candidate is not marked ready for strategy research"
        for warning in payload["warnings"]
    )


def test_promotion_ranking_override_selects_different_candidate(
    client: TestClient,
    db_session: Session,
) -> None:
    high_separation, _, _ = seed_candidate(
        db_session,
        index=10,
        probabilities=[Decimal("0.95"), Decimal("0.05")],
        labels=[1, 0],
        returns=[Decimal("0.010000"), Decimal("-0.010000")],
    )
    high_return, _, _ = seed_candidate(
        db_session,
        index=11,
        probabilities=[Decimal("0.70"), Decimal("0.30")],
        labels=[1, 0],
        returns=[Decimal("0.200000"), Decimal("-0.010000")],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [
                {"name": "high_separation", "model_run_id": high_separation.id},
                {"name": "high_return", "model_run_id": high_return.id},
            ],
            base_robustness(high_separation.id),
            promote_ranking_metric="average_realized_return",
            promote_ranking_direction="desc",
        ),
    )

    assert response.status_code == 200
    assert response.json()["selected_candidate"]["name"] == "high_return"


def test_candidate_level_failure_does_not_block_valid_candidate(
    client: TestClient,
    db_session: Session,
) -> None:
    valid_run, _, _ = seed_candidate(
        db_session,
        index=12,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [
                {"name": "valid", "model_run_id": valid_run.id},
                {"name": "missing", "model_run_id": 999999},
            ],
            base_robustness(valid_run.id),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_candidate"]["name"] == "valid"
    failed = [
        candidate
        for candidate in payload["candidate_comparison"]["candidates"]
        if candidate["status"] == "failed"
    ]
    assert failed and failed[0]["error"] == "ML model run not found"
    assert payload["robustness_analysis"] is not None


def test_robustness_failure_is_captured_as_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=13,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            {"experiment": {"model_run_id": run.id}},
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_candidate"]["model_run_id"] == run.id
    assert payload["robustness_analysis"] is None
    assert payload["warnings"][0]["message"] == (
        "Strategy robustness analysis failed for selected ML candidate"
    )
    assert payload["warnings"][0]["details"]["error"] == (
        "Provide variants, grid, or preset"
    )


def test_invalid_promotion_ranking_overrides_return_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session, index=14)
    db_session.commit()
    invalid_metric = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            promote_ranking_metric="magic",
        ),
    )
    invalid_direction = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
            promote_ranking_direction="sideways",
        ),
    )

    assert invalid_metric.status_code == 400
    assert invalid_metric.json()["detail"] == "Invalid promote ranking metric"
    assert invalid_direction.status_code == 400
    assert invalid_direction.json()["detail"] == "Invalid promote ranking direction"


def test_promotion_does_not_write_db_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=15,
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
        BondRiskAssessment,
    ]
    before = {model.__name__: count_rows(db_session, model) for model in models}

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
        ),
    )

    assert response.status_code == 200
    after = {model.__name__: count_rows(db_session, model) for model in models}
    assert after == before


def test_promotion_does_not_call_generation_external_or_paper_services(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=16,
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
    monkeypatch.setattr(PaperTradingScenarioService, "run", fail_call)
    monkeypatch.setattr(PaperTradingService, "rebalance", fail_call)

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
        ),
    )

    assert response.status_code == 200


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_candidate(
        db_session,
        index=17,
        probabilities=[Decimal("0.90"), Decimal("0.10")],
        labels=[1, 0],
    )

    response = client.post(
        PROMOTE_URL,
        json=promotion_payload(
            [{"model_run_id": run.id}],
            base_robustness(run.id),
        ),
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
