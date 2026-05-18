from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
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
from tests.test_ml_candidate_strategy_robustness import (
    add_label,
    add_prediction,
    add_risk,
    count_rows,
    create_bond,
    create_company,
    create_run,
)


READINESS_URL = "/api/paper-trading/live/readiness"
GATE_ORDER = [
    "selected_candidate_available",
    "selected_candidate_ready",
    "robustness_analysis_available",
    "analyzed_variant_count",
    "completed_subperiods",
    "robustness_fail_flags",
    "robustness_warning_flags",
    "virtual_pilot_configuration",
]


def seed_live_candidate(
    db: Session,
    *,
    index: int,
) -> tuple[MLModelRun, Company, list[Bond]]:
    company = create_company(db, index)
    bonds = [create_bond(db, company, index * 10 + item) for item in range(1, 3)]
    for bond in bonds:
        add_risk(db, bond, date(2025, 1, 1))
    run = create_run(db, index=index)
    for as_of_date in [date(2025, 1, 10), date(2025, 2, 10), date(2025, 3, 10)]:
        add_prediction(
            db,
            run=run,
            bond=bonds[0],
            company=company,
            as_of_date=as_of_date,
            probability=Decimal("0.90"),
        )
        add_label(
            db,
            bond=bonds[0],
            as_of_date=as_of_date,
            label_binary=1,
            future_return=Decimal("0.020000"),
        )
        add_prediction(
            db,
            run=run,
            bond=bonds[1],
            company=company,
            as_of_date=as_of_date,
            probability=Decimal("0.10"),
        )
        add_label(
            db,
            bond=bonds[1],
            as_of_date=as_of_date,
            label_binary=0,
            future_return=Decimal("-0.010000"),
        )
    db.commit()
    return run, company, bonds


def readiness_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "candidate_strategy_robustness": {
            "candidate_comparison": {
                "candidates": [
                    {
                        "name": "demo_candidate",
                        "model_run_id": run.id,
                    }
                ],
                "return_method": "risk_adjusted",
                "horizon_days": 30,
                "ranking_metric": "probability_separation",
                "ranking_direction": "desc",
                "include_prediction_quality": False,
                "include_failed_candidates": True,
                "minimum_evaluable_predictions": 2,
                "minimum_positive_labels": 1,
                "minimum_negative_labels": 1,
                "maximum_missing_label_ratio": "0.50",
            },
            "strategy_robustness": {
                "experiment": {
                    "model_run_id": run.id,
                    "date_from": "2025-01-10",
                    "date_to": "2025-03-14",
                    "initial_capital": "50000",
                    "transaction_cost_rate": "0.001",
                    "ranking_metric": "total_return",
                    "ranking_direction": "desc",
                    "include_periods": False,
                    "include_baselines": False,
                    "variants": [
                        {
                            "name": "ready_top_one",
                            "top_n": 1,
                            "min_probability_positive": "0.50",
                            "use_portfolio_constraints": True,
                            "max_position_weight": "1",
                            "max_issuer_weight": "1",
                            "max_high_risk_weight": "1",
                        }
                    ],
                },
                "selected_variant_count": 1,
                "subperiod_mode": "monthly",
                "include_subperiod_details": False,
                "include_candidate_concentration": False,
                "minimum_completed_subperiods": 1,
            },
            "require_ready_candidate": True,
            "include_candidate_comparison": True,
        }
    }
    payload.update(overrides)
    return payload


def gate_by_code(payload: dict, code: str) -> dict:
    return next(gate for gate in payload["gates"] if gate["code"] == code)


def test_ready_response(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=1)

    response = client.post(READINESS_URL, json=readiness_payload(run))

    assert response.status_code == 200
    payload = response.json()
    assert payload["readiness_status"] == "ready"
    assert payload["virtual_initial_capital"] == "50000"
    assert payload["planned_duration_days"] == 90
    assert payload["selected_candidate"] is not None
    assert payload["candidate_comparison"] is not None
    assert payload["robustness_analysis"] is not None
    assert [gate["code"] for gate in payload["gates"]] == GATE_ORDER
    assert {gate["status"] for gate in payload["gates"]} == {"passed"}


def test_missing_ready_candidate_returns_not_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=2)
    payload = readiness_payload(run)
    payload["candidate_strategy_robustness"]["candidate_comparison"][
        "minimum_evaluable_predictions"
    ] = 10

    response = client.post(READINESS_URL, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["readiness_status"] == "not_ready"
    assert body["selected_candidate"] is None
    assert body["robustness_analysis"] is None
    assert gate_by_code(body, "selected_candidate_available")["status"] == "failed"
    assert gate_by_code(body, "robustness_analysis_available")["status"] == "failed"


def test_non_ready_candidate_allowed_returns_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=3)
    payload = readiness_payload(run)
    payload["candidate_strategy_robustness"]["candidate_comparison"][
        "minimum_evaluable_predictions"
    ] = 10
    payload["candidate_strategy_robustness"]["require_ready_candidate"] = False

    response = client.post(READINESS_URL, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["selected_candidate"] is not None
    assert body["readiness_status"] == "warning"
    assert gate_by_code(body, "selected_candidate_ready")["status"] == "warning"


def test_robustness_failure_is_reflected(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=4)
    payload = readiness_payload(run)
    payload["candidate_strategy_robustness"]["strategy_robustness"] = {
        "experiment": {"model_run_id": run.id}
    }

    response = client.post(READINESS_URL, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["readiness_status"] == "not_ready"
    assert body["robustness_analysis"] is None
    assert gate_by_code(body, "robustness_analysis_available")["status"] == "failed"
    assert any(
        warning["message"]
        == "Strategy robustness analysis failed for selected ML candidate"
        for warning in body["warnings"]
    )


def test_include_candidate_comparison_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=5)
    hidden = readiness_payload(run, include_candidate_comparison=False)
    hidden["candidate_strategy_robustness"]["include_candidate_comparison"] = True
    visible = readiness_payload(run, include_candidate_comparison=True)
    visible["candidate_strategy_robustness"]["include_candidate_comparison"] = False

    hidden_response = client.post(READINESS_URL, json=hidden)
    visible_response = client.post(READINESS_URL, json=visible)

    assert hidden_response.status_code == 200
    assert hidden_response.json()["candidate_comparison"] is None
    assert visible_response.status_code == 200
    assert visible_response.json()["candidate_comparison"] is not None


def test_include_robustness_analysis_flag_hides_payload_but_keeps_gates(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=6)

    response = client.post(
        READINESS_URL,
        json=readiness_payload(run, include_robustness_analysis=False),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["readiness_status"] == "ready"
    assert payload["robustness_analysis"] is None
    assert {gate["status"] for gate in payload["gates"]} == {"passed"}


def test_warning_flags_behavior(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=7)
    base = readiness_payload(run)
    robustness = base["candidate_strategy_robustness"]["strategy_robustness"]
    robustness["include_candidate_concentration"] = True
    robustness["maximum_top_bond_selection_share"] = "0.20"
    robustness["maximum_top_company_selection_share"] = "0.20"

    allowed = client.post(READINESS_URL, json=base)

    blocked = readiness_payload(run)
    blocked_robustness = blocked["candidate_strategy_robustness"][
        "strategy_robustness"
    ]
    blocked_robustness["include_candidate_concentration"] = True
    blocked_robustness["maximum_top_bond_selection_share"] = "0.20"
    blocked_robustness["maximum_top_company_selection_share"] = "0.20"
    blocked["allow_warning_flags"] = False
    blocked_response = client.post(READINESS_URL, json=blocked)

    capped = readiness_payload(run)
    capped_robustness = capped["candidate_strategy_robustness"]["strategy_robustness"]
    capped_robustness["include_candidate_concentration"] = True
    capped_robustness["maximum_top_bond_selection_share"] = "0.20"
    capped_robustness["maximum_top_company_selection_share"] = "0.20"
    capped["maximum_warning_flag_count"] = 0
    capped_response = client.post(READINESS_URL, json=capped)

    assert allowed.status_code == 200
    assert allowed.json()["readiness_status"] == "warning"
    assert gate_by_code(allowed.json(), "robustness_warning_flags")["status"] == (
        "warning"
    )
    assert blocked_response.status_code == 200
    assert blocked_response.json()["readiness_status"] == "not_ready"
    assert gate_by_code(
        blocked_response.json(),
        "robustness_warning_flags",
    )["status"] == "failed"
    assert capped_response.status_code == 200
    assert capped_response.json()["readiness_status"] == "not_ready"
    assert gate_by_code(
        capped_response.json(),
        "robustness_warning_flags",
    )["status"] == "failed"


def test_validation_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=8)
    cases = [
        (
            {"virtual_initial_capital": "0"},
            "virtual_initial_capital must be positive",
        ),
        (
            {"planned_duration_days": 0},
            "planned_duration_days must be between 1 and 365",
        ),
        (
            {"planned_duration_days": 366},
            "planned_duration_days must be between 1 and 365",
        ),
        (
            {"minimum_analyzed_variant_count": 0},
            "minimum_analyzed_variant_count must be positive",
        ),
        (
            {"minimum_completed_subperiods": 0},
            "minimum_completed_subperiods must be positive",
        ),
        (
            {"maximum_warning_flag_count": -1},
            "maximum_warning_flag_count must be non-negative",
        ),
    ]

    for overrides, expected_detail in cases:
        response = client.post(
            READINESS_URL,
            json=readiness_payload(run, **overrides),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == expected_detail


def test_readiness_does_not_write_db_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=9)
    models = [
        MLModelRun,
        MLPrediction,
        BondReturnLabel,
        Bond,
        Company,
        BondFeatureSnapshot,
        BondRiskAssessment,
        PaperPortfolio,
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
    ]
    before = {model.__name__: count_rows(db_session, model) for model in models}

    response = client.post(READINESS_URL, json=readiness_payload(run))

    assert response.status_code == 200
    after = {model.__name__: count_rows(db_session, model) for model in models}
    assert after == before


def test_readiness_does_not_call_generation_external_or_paper_services(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=10)

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
    monkeypatch.setattr(PaperTradingService, "mark_period", fail_call)

    response = client.post(READINESS_URL, json=readiness_payload(run))

    assert response.status_code == 200


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=11)

    response = client.post(READINESS_URL, json=readiness_payload(run))

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
