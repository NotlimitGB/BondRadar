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
from app.models.paper_live_cycle_run import PaperLiveCycleRun
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
from tests.test_paper_trading_live_readiness import (
    readiness_payload,
    seed_live_candidate,
)


CYCLE_URL = "/api/paper-trading/live/cycles"
RUN_URL = f"{CYCLE_URL}/run"


def cycle_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "readiness": readiness_payload(run),
        "as_of_date": "2025-03-10",
        "rebalance": {
            "top_n": 1,
            "min_probability_positive": "0.50",
            "max_position_weight": "1",
            "max_issuer_weight": "1",
            "max_high_risk_weight": "1",
            "transaction_cost_rate": "0",
        },
    }
    payload.update(overrides)
    return payload


def warning_readiness_payload(run: MLModelRun) -> dict:
    payload = readiness_payload(run)
    robustness = payload["candidate_strategy_robustness"]["strategy_robustness"]
    robustness["include_candidate_concentration"] = True
    robustness["maximum_top_bond_selection_share"] = "0.20"
    robustness["maximum_top_company_selection_share"] = "0.20"
    return payload


def core_counts(db: Session) -> dict[str, int]:
    models = [
        MLModelRun,
        MLPrediction,
        BondReturnLabel,
        Bond,
        Company,
        BondFeatureSnapshot,
        BondRiskAssessment,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


def paper_counts(db: Session) -> dict[str, int]:
    models = [
        PaperLiveCycleRun,
        PaperPortfolio,
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


def gate_by_code(payload: dict, code: str) -> dict:
    return next(gate for gate in payload["readiness"]["gates"] if gate["code"] == code)


def seed_multi_run_candidate(db: Session) -> tuple[MLModelRun, MLModelRun]:
    company = create_company(db, 90)
    bonds = [create_bond(db, company, 901), create_bond(db, company, 902)]
    for bond in bonds:
        add_risk(db, bond, date(2025, 1, 1))
    first_run = create_run(db, index=90)
    second_run = create_run(db, index=91)
    rows = [
        (first_run, bonds[0], date(2025, 1, 10), Decimal("0.90"), 1, Decimal("0.020000")),
        (first_run, bonds[1], date(2025, 1, 10), Decimal("0.10"), 0, Decimal("-0.010000")),
        (second_run, bonds[0], date(2025, 2, 10), Decimal("0.88"), 1, Decimal("0.020000")),
        (second_run, bonds[1], date(2025, 2, 10), Decimal("0.12"), 0, Decimal("-0.010000")),
        (second_run, bonds[0], date(2025, 3, 10), Decimal("0.86"), 1, Decimal("0.020000")),
        (second_run, bonds[1], date(2025, 3, 10), Decimal("0.14"), 0, Decimal("-0.010000")),
    ]
    for run, bond, as_of_date, probability, label_binary, future_return in rows:
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
    return first_run, second_run


def multi_run_cycle_payload(first_run: MLModelRun, second_run: MLModelRun) -> dict:
    payload = cycle_payload(first_run)
    payload["readiness"]["candidate_strategy_robustness"]["candidate_comparison"][
        "candidates"
    ] = [
        {
            "name": "stitched_candidate",
            "model_run_ids": [first_run.id, second_run.id],
        }
    ]
    payload["readiness"]["candidate_strategy_robustness"]["strategy_robustness"][
        "experiment"
    ]["model_run_id"] = first_run.id
    return payload


def post_ready_cycle(
    client: TestClient,
    run: MLModelRun,
    **overrides,
) -> dict:
    response = client.post(RUN_URL, json=cycle_payload(run, **overrides))
    assert response.status_code == 200
    return response.json()


def test_completed_manual_cycle_creates_portfolio_and_rebalances(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=21)
    before_core = core_counts(db_session)
    before_paper = paper_counts(db_session)

    response = client.post(RUN_URL, json=cycle_payload(run))

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle"]["status"] == "completed"
    assert payload["readiness"]["readiness_status"] == "ready"
    assert payload["portfolio"] is not None
    assert payload["rebalance_result"] is not None
    assert payload["rebalance_result"]["selected_positions"]
    assert payload["cycle"]["portfolio_id"] is not None
    assert payload["cycle"]["selected_model_run_id"] == run.id
    assert core_counts(db_session) == before_core
    after_paper = paper_counts(db_session)
    assert after_paper["PaperLiveCycleRun"] == before_paper["PaperLiveCycleRun"] + 1
    assert after_paper["PaperPortfolio"] == before_paper["PaperPortfolio"] + 1
    assert after_paper["PaperPortfolioPosition"] > before_paper["PaperPortfolioPosition"]
    assert after_paper["PaperPortfolioTransaction"] > before_paper["PaperPortfolioTransaction"]
    assert after_paper["PaperPortfolioSnapshot"] > before_paper["PaperPortfolioSnapshot"]


def test_blocked_when_readiness_is_not_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=22)
    payload = cycle_payload(run)
    payload["readiness"]["candidate_strategy_robustness"]["candidate_comparison"][
        "minimum_evaluable_predictions"
    ] = 10
    before_paper = paper_counts(db_session)

    response = client.post(RUN_URL, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["cycle"]["status"] == "blocked"
    assert body["readiness"]["readiness_status"] == "not_ready"
    assert body["portfolio"] is None
    assert body["rebalance_result"] is None
    after_paper = paper_counts(db_session)
    assert after_paper["PaperLiveCycleRun"] == before_paper["PaperLiveCycleRun"] + 1
    assert after_paper["PaperPortfolio"] == before_paper["PaperPortfolio"]
    assert after_paper["PaperPortfolioPosition"] == before_paper["PaperPortfolioPosition"]
    assert after_paper["PaperPortfolioTransaction"] == before_paper["PaperPortfolioTransaction"]
    assert after_paper["PaperPortfolioSnapshot"] == before_paper["PaperPortfolioSnapshot"]


def test_readiness_warning_requires_explicit_allowance(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=23)
    blocked_payload = cycle_payload(run, readiness=warning_readiness_payload(run))

    blocked = client.post(RUN_URL, json=blocked_payload)
    allowed = client.post(
        RUN_URL,
        json=cycle_payload(
            run,
            readiness=warning_readiness_payload(run),
            allow_readiness_warning=True,
            portfolio_name="Warning allowed cycle",
        ),
    )

    assert blocked.status_code == 200
    assert blocked.json()["cycle"]["status"] == "blocked"
    assert blocked.json()["readiness"]["readiness_status"] == "warning"
    assert blocked.json()["rebalance_result"] is None
    assert allowed.status_code == 200
    assert allowed.json()["cycle"]["status"] == "completed"
    assert allowed.json()["readiness"]["readiness_status"] == "warning"


def test_existing_portfolio_is_reused(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=24)
    initial = post_ready_cycle(client, run)
    portfolio_id = initial["cycle"]["portfolio_id"]
    before = paper_counts(db_session)

    response = client.post(
        RUN_URL,
        json=cycle_payload(run, portfolio_id=portfolio_id),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle"]["status"] == "completed"
    assert payload["cycle"]["portfolio_id"] == portfolio_id
    after = paper_counts(db_session)
    assert after["PaperPortfolio"] == before["PaperPortfolio"]
    assert after["PaperLiveCycleRun"] == before["PaperLiveCycleRun"] + 1


def test_client_cycle_key_idempotency(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=25)
    payload = cycle_payload(run, client_cycle_key="manual-cycle-test-1")
    first = client.post(RUN_URL, json=payload)
    before_second = paper_counts(db_session)

    second = client.post(RUN_URL, json=payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["cycle"]["id"] == first.json()["cycle"]["id"]
    assert paper_counts(db_session) == before_second
    assert any(
        warning["message"]
        == "Existing live paper cycle was returned for client_cycle_key"
        for warning in second.json()["warnings"]
    )


def test_multi_run_selected_candidate_is_blocked(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run = seed_multi_run_candidate(db_session)

    response = client.post(
        RUN_URL,
        json=multi_run_cycle_payload(first_run, second_run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle"]["status"] == "blocked"
    assert payload["rebalance_result"] is None
    assert any(
        warning["message"]
        == "Manual live paper cycle requires a single selected ML model run"
        for warning in payload["warnings"]
    )


def test_mark_period_before_rebalance(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=26)
    first = post_ready_cycle(
        client,
        run,
        as_of_date="2025-01-10",
        rebalance={
            "top_n": 1,
            "min_probability_positive": "0.50",
            "max_position_weight": "1",
            "max_issuer_weight": "1",
            "max_high_risk_weight": "1",
            "transaction_cost_rate": "0",
        },
    )
    portfolio_id = first["cycle"]["portfolio_id"]

    response = client.post(
        RUN_URL,
        json=cycle_payload(
            run,
            portfolio_id=portfolio_id,
            as_of_date="2025-02-10",
            mark_period_before_rebalance=True,
            mark_period={"as_of_date": "2025-01-10", "allow_partial": True},
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycle"]["status"] == "completed"
    assert payload["mark_period_result"] is not None
    assert payload["rebalance_result"] is not None
    assert payload["mark_period_result"]["transactions"][0]["transaction_type"] == (
        "period_return"
    )


def test_validation_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=27)
    cases = [
        ({"portfolio_id": 0}, "portfolio_id must be positive"),
        ({"client_cycle_key": "   "}, "client_cycle_key must not be blank"),
        ({"portfolio_name": "  "}, "portfolio_name must not be blank"),
        (
            {"create_portfolio_if_missing": False},
            "portfolio_id is required when create_portfolio_if_missing is false",
        ),
    ]

    for overrides, expected_detail in cases:
        response = client.post(RUN_URL, json=cycle_payload(run, **overrides))
        assert response.status_code == 400
        assert response.json()["detail"] == expected_detail


def test_forbidden_side_effect_services_are_not_called(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=28)

    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    for service_class, method_name in [
        (MLTrainingService, "train"),
        (MLPredictionService, "predict"),
        (TotalReturnLabelService, "build_labels"),
        (TotalReturnLabelService, "build_for_bond_date"),
        (LabelBuilderService, "build_for_bond_date"),
        (DatasetBuildService, "build"),
        (DataPipelineService, "run"),
        (MoexMarketDataService, "sync"),
        (MoexCashflowService, "sync"),
        (PaperTradingScenarioService, "run"),
    ]:
        if hasattr(service_class, method_name):
            monkeypatch.setattr(service_class, method_name, fail_call)

    response = client.post(RUN_URL, json=cycle_payload(run))

    assert response.status_code == 200
    assert response.json()["cycle"]["status"] == "completed"


def test_list_and_get_cycle_runs(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=29)
    first = post_ready_cycle(client, run, portfolio_name="Cycle list one")
    second = post_ready_cycle(client, run, portfolio_name="Cycle list two")

    listed = client.get(f"{CYCLE_URL}?limit=1")
    fetched = client.get(f"{CYCLE_URL}/{second['cycle']['id']}")
    missing = client.get(f"{CYCLE_URL}/999999")

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == second["cycle"]["id"]
    assert fetched.status_code == 200
    assert fetched.json()["id"] == second["cycle"]["id"]
    assert fetched.json()["id"] != first["cycle"]["id"]
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Live paper cycle run not found"


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=30)
    completed = client.post(RUN_URL, json=cycle_payload(run))
    blocked_payload = cycle_payload(run)
    blocked_payload["readiness"]["candidate_strategy_robustness"][
        "candidate_comparison"
    ]["minimum_evaluable_predictions"] = 10
    blocked = client.post(RUN_URL, json=blocked_payload)
    listed = client.get(CYCLE_URL)
    fetched = client.get(f"{CYCLE_URL}/{completed.json()['cycle']['id']}")

    assert completed.status_code == 200
    assert blocked.status_code == 200
    assert listed.status_code == 200
    assert fetched.status_code == 200
    assert_no_forbidden_investment_vocabulary(
        [completed.json(), blocked.json(), listed.json(), fetched.json()]
    )
