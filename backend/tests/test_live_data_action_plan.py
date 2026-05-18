from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.data_pipeline_run import DataPipelineRun
from app.models.data_pipeline_step_run import DataPipelineStepRun
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.models.paper_live_cycle_run import PaperLiveCycleRun
from app.models.paper_live_schedule import PaperLiveSchedule
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.services.data_pipeline_service import DataPipelineService
from app.services.dataset_build_service import DatasetBuildService
from app.services.feature_snapshot_service import FeatureSnapshotService
from app.services.label_builder_service import LabelBuilderService
from app.services.ml_evaluation_service import MLEvaluationService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService
from app.services.paper_trading_service import PaperTradingService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary
from tests.test_live_data_readiness import (
    add_data_chain,
    create_bond,
    create_company,
    count_rows,
    create_model_run,
    seed_dataset,
    today,
)


ACTION_PLAN_URL = "/api/data-readiness/live/action-plan"


def action_by_name(payload: dict, name: str) -> dict:
    return next(action for action in payload["actions"] if action["name"] == name)


def core_counts(db: Session) -> dict[str, int]:
    models = [
        DataPipelineRun,
        DataPipelineStepRun,
        Bond,
        Company,
        BondMarketSnapshot,
        BondCashflowEvent,
        BondFeatureSnapshot,
        BondReturnLabel,
        BondRiskAssessment,
        MLModelRun,
        MLPrediction,
        PaperPortfolio,
        PaperPortfolioSnapshot,
        PaperPortfolioTransaction,
        PaperLiveSchedule,
        PaperLiveCycleRun,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


def seed_features_without_model(
    db: Session,
    *,
    corporate_count: int,
    as_of_date: date,
) -> list[Bond]:
    bonds: list[Bond] = []
    for index in range(1, corporate_count + 1):
        company = create_company(db, index)
        bond = create_bond(db, company, index)
        bonds.append(bond)
        db.add(
            BondMarketSnapshot(
                bond_id=bond.id,
                trade_date=as_of_date,
                price=Decimal("100.000000"),
                clean_price=Decimal("100.000000"),
                dirty_price=Decimal("101.000000"),
                nkd=Decimal("10.000000"),
                yield_to_maturity=Decimal("12.000"),
                duration_years=Decimal("2.000"),
                volume=Decimal("1000000.00"),
                liquidity_score=80,
                source="live-action-plan-test",
                raw_payload={"test": True},
            )
        )
        db.add(
            BondCashflowEvent(
                bond_id=bond.id,
                event_date=as_of_date + timedelta(days=30),
                event_type="coupon",
                amount=Decimal("20.000000"),
                currency="RUB",
                source="live-action-plan-test",
                raw_payload={"test": True},
            )
        )
        db.add(
            BondFeatureSnapshot(
                bond_id=bond.id,
                company_id=bond.company_id,
                as_of_date=as_of_date,
                yield_to_maturity=Decimal("12.000"),
                duration_years=Decimal("2.000"),
                liquidity_score=80,
                volume=Decimal("1000000.00"),
                missing_data_count=0,
                features_json={"test": True},
            )
        )
    db.commit()
    return bonds


def base_params(minimum: int = 2) -> dict:
    return {
        "minimum_corporate_bonds": minimum,
        "minimum_bonds_with_recent_market_snapshot": minimum,
        "minimum_bonds_with_recent_features": minimum,
        "minimum_bonds_with_predictions": minimum,
    }


def test_empty_database_is_blocked(client: TestClient) -> None:
    response = client.get(ACTION_PLAN_URL, params=base_params(1))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["can_run_pipeline"] is False
    assert "corporate_universe_available" in payload["blocked_steps"]
    assert action_by_name(payload, "corporate_universe_available")["status"] == "blocked"
    assert payload["pipeline_payload"]["steps"] == []
    assert "moex_market_sync" not in payload["recommended_steps"]
    assert payload["next_steps"]


def test_stale_data_recommends_pipeline_refresh(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(
        db_session,
        corporate_count=2,
        as_of_date=today() - timedelta(days=40),
    )

    response = client.get(ACTION_PLAN_URL, params=base_params(2))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_attention"
    assert "moex_market_sync" in payload["recommended_steps"]
    assert "dataset_build_price" in payload["recommended_steps"]
    assert "labels_total_return" in payload["recommended_steps"]
    assert "labels_risk_adjusted" in payload["recommended_steps"]
    assert "ml_predict" in payload["recommended_steps"]
    assert payload["pipeline_payload"]["steps"] == payload["recommended_steps"]


def test_ready_data_returns_ready_to_run(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(db_session, corporate_count=2, as_of_date=today())

    response = client.get(ACTION_PLAN_URL, params=base_params(2))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready_to_run"
    assert payload["readiness_status"] == "ready"
    assert payload["can_bootstrap_paper_pilot"] is True
    assert payload["recommended_steps"] == []
    assert "data_readiness_check" in payload["optional_steps"]
    assert any(command["path"] == "/api/paper-trading/live/pilots/bootstrap" for command in payload["commands"])


def test_no_completed_model_run_recommends_ml_train(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_features_without_model(db_session, corporate_count=2, as_of_date=today())

    response = client.get(ACTION_PLAN_URL, params=base_params(2))

    assert response.status_code == 200
    payload = response.json()
    assert "ml_train" in payload["recommended_steps"]
    assert "ml_predict" in payload["recommended_steps"]
    assert payload["can_generate_predictions"] is True


def test_ml_training_disabled_without_model_blocks_prediction_generation(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_features_without_model(db_session, corporate_count=2, as_of_date=today())

    response = client.get(
        ACTION_PLAN_URL,
        params={**base_params(2), "include_ml_training": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "ml_train" not in payload["pipeline_payload"]["steps"]
    assert "ml_predict" not in payload["pipeline_payload"]["steps"]
    assert payload["can_generate_predictions"] is False
    assert any(warning["code"] == "ml_training_not_included" for warning in payload["warnings"])


def test_predictions_disabled_omits_prediction_and_evaluation_steps(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(
        db_session,
        corporate_count=2,
        as_of_date=today() - timedelta(days=40),
    )

    response = client.get(
        ACTION_PLAN_URL,
        params={**base_params(2), "include_predictions": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "ml_predict" not in payload["recommended_steps"]
    assert "ml_evaluate" not in payload["recommended_steps"]
    assert "ml_predict" not in payload["pipeline_payload"]["steps"]
    assert "ml_evaluate" not in payload["pipeline_payload"]["steps"]


def test_invalid_params_return_exact_400_details(client: TestClient) -> None:
    cases = [
        ({"recent_days": 0}, "recent_days must be between 1 and 365"),
        ({"recent_days": 366}, "recent_days must be between 1 and 365"),
        ({"horizon_days": 0}, "horizon_days must be between 1 and 365"),
        ({"horizon_days": 366}, "horizon_days must be between 1 and 365"),
        (
            {"date_from": "2025-03-14", "date_to": "2025-01-10"},
            "date_from must be before or equal to date_to",
        ),
        ({"mode": "unknown"}, "mode must be one of existing PIPELINE_MODES"),
        (
            {"return_method": "unknown"},
            "return_method must be one of existing PIPELINE_RETURN_METHODS",
        ),
        (
            {"minimum_corporate_bonds": -1},
            "minimum_corporate_bonds must be non-negative",
        ),
        (
            {"minimum_bonds_with_recent_market_snapshot": -1},
            "minimum_bonds_with_recent_market_snapshot must be non-negative",
        ),
        (
            {"minimum_bonds_with_recent_features": -1},
            "minimum_bonds_with_recent_features must be non-negative",
        ),
        (
            {"minimum_bonds_with_predictions": -1},
            "minimum_bonds_with_predictions must be non-negative",
        ),
    ]
    for params, detail in cases:
        response = client.get(ACTION_PLAN_URL, params=params)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_no_db_writes(client: TestClient, db_session: Session) -> None:
    seed_dataset(db_session, corporate_count=2, as_of_date=today())
    before = core_counts(db_session)

    response = client.get(ACTION_PLAN_URL, params=base_params(2))

    assert response.status_code == 200
    assert core_counts(db_session) == before


def test_no_forbidden_service_calls(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    seed_dataset(db_session, corporate_count=2, as_of_date=today())

    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    monkeypatch.setattr(DataPipelineService, "run", fail_call)
    monkeypatch.setattr(MoexMarketDataService, "sync", fail_call)
    monkeypatch.setattr(MoexCashflowService, "sync", fail_call)
    monkeypatch.setattr(FeatureSnapshotService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(LabelBuilderService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_labels", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(DatasetBuildService, "build", fail_call)
    monkeypatch.setattr(MLTrainingService, "train", fail_call)
    monkeypatch.setattr(MLPredictionService, "predict", fail_call)
    monkeypatch.setattr(MLEvaluationService, "evaluate_run", fail_call)
    monkeypatch.setattr(PaperTradingService, "create_portfolio", fail_call)
    monkeypatch.setattr(PaperTradingService, "rebalance", fail_call)
    monkeypatch.setattr(PaperTradingService, "mark_period", fail_call)
    monkeypatch.setattr(LivePaperCycleService, "run", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_due", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_schedule_once", fail_call)

    response = client.get(ACTION_PLAN_URL, params=base_params(2))

    assert response.status_code == 200
    assert response.json()["readiness_status"] == "ready"


def test_forbidden_vocabulary_helper(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(db_session, corporate_count=2, as_of_date=today())

    response = client.get(ACTION_PLAN_URL, params=base_params(2))

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
