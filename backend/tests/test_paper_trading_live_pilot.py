from datetime import datetime, timezone
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
from app.models.paper_live_schedule import PaperLiveSchedule
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessSelectedCandidate,
)
from app.schemas.paper_trading_live_readiness import (
    LivePaperReadinessResponse,
    LivePaperReadinessWarning,
)
from app.services.data_pipeline_service import DataPipelineService
from app.services.dataset_build_service import DatasetBuildService
from app.services.label_builder_service import LabelBuilderService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_pilot_service import LivePaperReadinessService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService
from app.services.paper_trading_scenario_service import PaperTradingScenarioService
from app.services.paper_trading_service import PaperTradingService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary
from tests.test_ml_candidate_strategy_robustness import count_rows
from tests.test_paper_trading_live_readiness import seed_live_candidate


PILOT_URL = "/api/paper-trading/live/pilots/bootstrap"
SCHEDULE_URL = "/api/paper-trading/live/schedules"


def iso(year: int, month: int, day: int, hour: int = 0) -> str:
    return datetime(year, month, day, hour, tzinfo=timezone.utc).isoformat()


def pilot_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "name": "50k live paper pilot",
        "description": "Virtual pilot preparation",
        "model_run_id": run.id,
        "return_method": "risk_adjusted",
        "horizon_days": 30,
        "virtual_initial_capital": "50000",
        "planned_duration_days": 90,
        "date_from": "2025-01-10",
        "date_to": "2025-03-10",
        "next_run_at": iso(2025, 3, 15, 10),
        "interval_days": 1,
        "top_n": 1,
        "min_probability_positive": "0.50",
        "use_portfolio_constraints": True,
        "max_position_weight": "1",
        "max_issuer_weight": "1",
        "max_high_risk_weight": "0.20",
        "transaction_cost_rate": "0",
    }
    payload.update(overrides)
    return payload


def paper_counts(db: Session) -> dict[str, int]:
    models = [
        PaperLiveSchedule,
        PaperLiveCycleRun,
        PaperPortfolio,
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


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


def fake_warning_readiness(*_, **__) -> LivePaperReadinessResponse:
    selected = MLCandidateStrategyRobustnessSelectedCandidate(
        name="pilot_model_run_1",
        rank=1,
        ranking_metric="probability_separation",
        ranking_direction="desc",
        ranking_value=Decimal("0.10"),
        model_run_id=1,
        model_run_ids=[1],
        model_run_count=1,
        prediction_source_mode="single_model_run",
        ready_for_strategy_research=False,
        issues=[],
    )
    return LivePaperReadinessResponse(
        readiness_status="warning",
        virtual_initial_capital=Decimal("50000"),
        planned_duration_days=90,
        selected_candidate=selected,
        candidate_comparison=None,
        robustness_analysis={"analyzed_variant_count": 1, "variants": []},
        gates=[],
        warnings=[
            LivePaperReadinessWarning(
                message="Synthetic readiness warning",
                details={},
            )
        ],
    )


def fake_not_ready_readiness(*_, **__) -> LivePaperReadinessResponse:
    return LivePaperReadinessResponse(
        readiness_status="not_ready",
        virtual_initial_capital=Decimal("50000"),
        planned_duration_days=90,
        selected_candidate=None,
        candidate_comparison=None,
        robustness_analysis=None,
        gates=[],
        warnings=[],
    )


def test_prepared_dry_run_only_creates_no_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=101)
    before_core = core_counts(db_session)
    before_paper = paper_counts(db_session)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(run, dry_run_only=True),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "prepared"
    assert payload["created_schedule_id"] is None
    assert payload["readiness_status"] == "ready"
    assert payload["schedule"] is None
    assert payload["payloads"]["readiness_request"]
    assert payload["payloads"]["cycle_request"]
    assert payload["payloads"]["schedule_request"]
    assert core_counts(db_session) == before_core
    assert paper_counts(db_session) == before_paper


def test_creates_schedule_when_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=102)
    before_core = core_counts(db_session)
    before_paper = paper_counts(db_session)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(run, dry_run_only=False, create_schedule=True),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "scheduled"
    assert payload["created_schedule_id"] is not None
    assert payload["schedule"]["status"] == "active"
    assert payload["schedule"]["use_current_date_as_of_date"] is False
    assert payload["schedule"]["cycle_request_json"]["readiness"]
    assert payload["schedule"]["cycle_request_json"]["rebalance"]
    assert payload["schedule"]["cycle_request_json"]["as_of_date"] == "2025-03-10"
    assert (
        payload["schedule"]["cycle_request_json"]["rebalance"]["as_of_date"]
        == "2025-03-10"
    )
    assert payload["readiness_status"] == "ready"
    assert core_counts(db_session) == before_core
    after_paper = paper_counts(db_session)
    assert after_paper["PaperLiveSchedule"] == before_paper["PaperLiveSchedule"] + 1
    assert after_paper["PaperLiveCycleRun"] == before_paper["PaperLiveCycleRun"]
    assert after_paper["PaperPortfolio"] == before_paper["PaperPortfolio"]
    assert after_paper["PaperPortfolioPosition"] == before_paper["PaperPortfolioPosition"]
    assert (
        after_paper["PaperPortfolioTransaction"]
        == before_paper["PaperPortfolioTransaction"]
    )
    assert after_paper["PaperPortfolioSnapshot"] == before_paper["PaperPortfolioSnapshot"]


def test_current_date_mode_must_be_explicit(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=111)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(run, use_current_date_as_of_date=True),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schedule"]["use_current_date_as_of_date"] is True
    assert any(
        warning["details"].get("use_current_date_as_of_date") is True
        for warning in payload["warnings"]
    )


def test_risk_policy_is_propagated_to_readiness_cycle_and_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=112)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(
            run,
            dry_run_only=True,
            min_liquidity_score=70,
            exclude_blocked_by_risk=True,
            exclude_insufficient_credit_data=True,
            allowed_risk_levels=["low", "medium"],
            allowed_decision_statuses=["eligible_for_analysis", "watchlist"],
        ),
    )

    assert response.status_code == 200
    payloads = response.json()["payloads"]
    variant = payloads["readiness_request"]["candidate_strategy_robustness"][
        "strategy_robustness"
    ]["experiment"]["variants"][0]
    rebalance = payloads["cycle_request"]["rebalance"]
    schedule_rebalance = payloads["schedule_request"]["cycle_request"]["rebalance"]
    expected = {
        "min_liquidity_score": 70,
        "exclude_blocked_by_risk": True,
        "exclude_insufficient_credit_data": True,
        "allowed_risk_levels": ["low", "medium"],
        "allowed_decision_statuses": ["eligible_for_analysis", "watchlist"],
    }
    for key, value in expected.items():
        assert variant[key] == value
        assert rebalance[key] == value
        assert schedule_rebalance[key] == value


def test_risk_override_requires_confirmation_and_reason(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=113)

    missing_enabled = client.post(
        PILOT_URL,
        json=pilot_payload(run, exclude_blocked_by_risk=False),
    )
    missing_reason = client.post(
        PILOT_URL,
        json=pilot_payload(
            run,
            exclude_blocked_by_risk=False,
            risk_override_enabled=True,
            risk_override_reason=" ",
        ),
    )
    valid = client.post(
        PILOT_URL,
        json=pilot_payload(
            run,
            exclude_blocked_by_risk=False,
            exclude_insufficient_credit_data=False,
            max_high_risk_weight="1.0",
            allowed_risk_levels=["critical"],
            allowed_decision_statuses=["blocked_by_risk"],
            risk_override_enabled=True,
            risk_override_reason="Technical virtual paper validation.",
            dry_run_only=True,
        ),
    )

    assert missing_enabled.status_code == 400
    assert "risk_override_enabled is required" in missing_enabled.json()["detail"]
    assert missing_reason.status_code == 400
    assert missing_reason.json()["detail"] == (
        "risk_override_reason is required when risk_override_enabled is true"
    )
    assert valid.status_code == 200
    payload = valid.json()
    assert payload["payloads"]["cycle_request"]["rebalance"][
        "exclude_blocked_by_risk"
    ] is False
    assert payload["payloads"]["cycle_request"]["rebalance"][
        "exclude_insufficient_credit_data"
    ] is False
    assert payload["payloads"]["cycle_request"]["rebalance"][
        "risk_override_enabled"
    ] is True
    assert any(
        warning["details"].get("risk_override_enabled") is True
        for warning in payload["warnings"]
    )


def test_blocked_warning_status_requires_allowance(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=103)
    monkeypatch.setattr(LivePaperReadinessService, "check", fake_warning_readiness)

    blocked = client.post(
        PILOT_URL,
        json=pilot_payload(run, allow_readiness_warning=False),
    )
    allowed = client.post(
        PILOT_URL,
        json=pilot_payload(run, allow_readiness_warning=True),
    )

    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    assert blocked.json()["created_schedule_id"] is None
    assert any(
        warning["message"] == "Readiness warning status blocked pilot schedule creation"
        for warning in blocked.json()["warnings"]
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "scheduled"
    assert allowed.json()["created_schedule_id"] is not None


def test_blocked_not_ready_status_requires_allowance(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=104)
    before = paper_counts(db_session)
    monkeypatch.setattr(LivePaperReadinessService, "check", fake_not_ready_readiness)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(run, allow_not_ready=False),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["created_schedule_id"] is None
    assert payload["schedule"] is None
    assert paper_counts(db_session) == before


def test_create_schedule_false_returns_prepared(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=105)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(run, create_schedule=False, dry_run_only=False),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "prepared"
    assert payload["created_schedule_id"] is None
    assert payload["payloads"]["schedule_request"]


def test_monitoring_overview_optional(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=106)

    included = client.post(
        PILOT_URL,
        json=pilot_payload(run, dry_run_only=True, include_monitoring_overview=True),
    )
    hidden = client.post(
        PILOT_URL,
        json=pilot_payload(run, dry_run_only=True, include_monitoring_overview=False),
    )

    assert included.status_code == 200
    assert hidden.status_code == 200
    assert included.json()["monitoring_overview"] is not None
    assert hidden.json()["monitoring_overview"] is None


def test_validation_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=107)
    cases = [
        ({"name": " "}, "name must not be blank"),
        ({"model_run_id": 0}, "model_run_id must be positive"),
        ({"virtual_initial_capital": "0"}, "virtual_initial_capital must be positive"),
        (
            {"planned_duration_days": 0},
            "planned_duration_days must be between 1 and 365",
        ),
        (
            {"planned_duration_days": 366},
            "planned_duration_days must be between 1 and 365",
        ),
        (
            {"date_from": "2025-03-15", "date_to": "2025-03-14"},
            "date_from must be before or equal to date_to",
        ),
        ({"next_run_at": None}, "next_run_at is required"),
        ({"interval_days": 0}, "interval_days must be positive"),
        ({"max_runs": 0}, "max_runs must be positive when provided"),
        ({"top_n": 0}, "top_n must be positive"),
        (
            {"min_probability_positive": "-0.01"},
            "min_probability_positive must be between 0 and 1",
        ),
        (
            {"min_probability_positive": "1.01"},
            "min_probability_positive must be between 0 and 1",
        ),
        (
            {"max_position_weight": "-0.01"},
            "max_position_weight must be between 0 and 1",
        ),
        (
            {"max_position_weight": "1.01"},
            "max_position_weight must be between 0 and 1",
        ),
        (
            {"max_issuer_weight": "-0.01"},
            "max_issuer_weight must be between 0 and 1",
        ),
        (
            {"max_high_risk_weight": "1.01"},
            "max_high_risk_weight must be between 0 and 1",
        ),
        (
            {"transaction_cost_rate": "-0.01"},
            "transaction_cost_rate must be non-negative",
        ),
    ]
    for overrides, detail in cases:
        response = client.post(PILOT_URL, json=pilot_payload(run, **overrides))
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_forbidden_service_calls_are_not_used(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=108)

    def unexpected(*_, **__):
        raise AssertionError("unexpected service call")

    forbidden = [
        (LivePaperScheduleService, "run_due"),
        (LivePaperScheduleService, "run_schedule_once"),
        (LivePaperCycleService, "run"),
        (PaperTradingService, "create_portfolio"),
        (PaperTradingService, "rebalance"),
        (PaperTradingService, "mark_period"),
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
    ]
    for target, name in forbidden:
        monkeypatch.setattr(target, name, unexpected, raising=False)

    response = client.post(
        PILOT_URL,
        json=pilot_payload(run, include_monitoring_overview=False),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "scheduled"


def test_generated_schedule_can_be_dry_run_and_executed(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=109)
    bootstrap = client.post(
        PILOT_URL,
        json=pilot_payload(run, next_run_at=iso(2025, 3, 10, 10)),
    )
    assert bootstrap.status_code == 200
    schedule_id = bootstrap.json()["created_schedule_id"]
    before_cycles = count_rows(db_session, PaperLiveCycleRun)

    dry_run = client.post(
        f"{SCHEDULE_URL}/run-due",
        json={"now": iso(2025, 3, 10, 10), "dry_run": True},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["due_schedule_count"] == 1
    assert count_rows(db_session, PaperLiveCycleRun) == before_cycles

    real_run = client.post(
        f"{SCHEDULE_URL}/run-due",
        json={"now": iso(2025, 3, 10, 10), "dry_run": False},
    )

    assert real_run.status_code == 200
    assert real_run.json()["executed_count"] == 1
    item = real_run.json()["results"][0]
    assert item["cycle"]["schedule_id"] == schedule_id
    assert item["cycle"]["status"] in {"completed", "blocked"}


def test_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=110)
    prepared = client.post(PILOT_URL, json=pilot_payload(run, dry_run_only=True))
    scheduled = client.post(PILOT_URL, json=pilot_payload(run))
    monkeypatch.setattr(LivePaperReadinessService, "check", fake_not_ready_readiness)
    blocked = client.post(PILOT_URL, json=pilot_payload(run))

    assert prepared.status_code == 200
    assert scheduled.status_code == 200
    assert blocked.status_code == 200
    assert_no_forbidden_investment_vocabulary(prepared.json())
    assert_no_forbidden_investment_vocabulary(scheduled.json())
    assert_no_forbidden_investment_vocabulary(blocked.json())
