from datetime import datetime, timedelta, timezone
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
from app.services.data_pipeline_service import DataPipelineService
from app.services.dataset_build_service import DatasetBuildService
from app.services.label_builder_service import LabelBuilderService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService
from app.services.paper_trading_scenario_service import PaperTradingScenarioService
from app.services.paper_trading_service import PaperTradingService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary
from tests.test_ml_candidate_strategy_robustness import count_rows
from tests.test_paper_trading_live_cycle import cycle_payload
from tests.test_paper_trading_live_readiness import seed_live_candidate
from tests.test_paper_trading_live_schedule import (
    create_schedule,
    iso,
    query_iso,
    run_due,
    schedule_payload,
)


MONITORING_URL = "/api/paper-trading/live/monitoring"


def alert_codes(payload: dict) -> set[str]:
    return {alert["code"] for alert in payload["alerts"]}


def create_failed_cycle(db: Session, *, schedule_id: int | None = None) -> PaperLiveCycleRun:
    cycle = PaperLiveCycleRun(
        status="failed",
        mode="manual",
        schedule_id=schedule_id,
        request_json={},
        readiness_json={},
        summary_json={"failure_detail": "synthetic failure"},
        warnings_json=[],
        errors_json=[{"detail": "synthetic failure"}],
        started_at=datetime(2025, 3, 10, 9, tzinfo=timezone.utc),
        finished_at=datetime(2025, 3, 10, 9, 5, tzinfo=timezone.utc),
        created_at=datetime(2025, 3, 10, 9, tzinfo=timezone.utc),
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


def create_running_cycle(db: Session) -> PaperLiveCycleRun:
    cycle = PaperLiveCycleRun(
        status="running",
        mode="manual",
        request_json={},
        readiness_json={},
        summary_json={},
        warnings_json=[],
        errors_json=[],
        started_at=datetime(2025, 3, 10, 7, tzinfo=timezone.utc),
        created_at=datetime(2025, 3, 10, 7, tzinfo=timezone.utc),
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)
    return cycle


def create_direct_portfolio(
    db: Session,
    *,
    name: str,
    status: str = "active",
) -> PaperPortfolio:
    portfolio = PaperPortfolio(
        name=name,
        status=status,
        base_currency="RUB",
        initial_capital=Decimal("50000.000000"),
        cash_balance=Decimal("50000.000000"),
        current_value=Decimal("50000.000000"),
        params_json={},
        summary_json={},
        warnings_json=[],
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def core_counts(db: Session) -> dict[str, int]:
    models = [
        PaperLiveSchedule,
        PaperLiveCycleRun,
        PaperPortfolio,
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
        MLModelRun,
        MLPrediction,
        BondReturnLabel,
        Bond,
        Company,
        BondFeatureSnapshot,
        BondRiskAssessment,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


def test_overview_with_no_data(client: TestClient) -> None:
    response = client.get(f"{MONITORING_URL}/overview")

    assert response.status_code == 200
    payload = response.json()
    assert payload["health_status"] == "unknown"
    assert payload["schedule_count"] == 0
    assert payload["portfolio_count"] == 0
    assert payload["recent_cycle_count"] == 0
    assert "no_active_schedules" in alert_codes(payload)


def test_overview_with_active_schedule_and_completed_cycle(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=71)
    create_schedule(client, run)
    run_due(client, now=iso(2025, 3, 10, 10))

    response = client.get(
        f"{MONITORING_URL}/overview?now={query_iso(2025, 3, 10, 10)}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["health_status"] in {"healthy", "warning"}
    assert payload["active_schedule_count"] == 1
    assert payload["portfolio_count"] >= 1
    assert payload["completed_cycle_count"] >= 1
    assert payload["schedules"]
    assert payload["portfolios"]
    assert payload["recent_cycles"]


def test_blocked_and_failed_cycles_affect_health(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=72)
    blocked_request = cycle_payload(run)
    blocked_request["readiness"]["candidate_strategy_robustness"][
        "candidate_comparison"
    ]["minimum_evaluable_predictions"] = 10
    create_schedule(client, run, cycle_request=blocked_request)
    run_due(client, now=iso(2025, 3, 10, 10))

    blocked = client.get(
        f"{MONITORING_URL}/overview?now={query_iso(2025, 3, 10, 10)}"
    )
    assert blocked.status_code == 200
    assert blocked.json()["health_status"] == "warning"
    assert "recent_blocked_cycles" in alert_codes(blocked.json())

    create_failed_cycle(db_session)
    failed = client.get(
        f"{MONITORING_URL}/overview?now={query_iso(2025, 3, 10, 10)}"
    )
    assert failed.status_code == 200
    assert failed.json()["health_status"] == "critical"
    assert "recent_failed_cycles" in alert_codes(failed.json())


def test_schedule_detail(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=73)
    schedule = create_schedule(client, run)
    run_due(client, now=iso(2025, 3, 10, 10))

    response = client.get(
        f"{MONITORING_URL}/schedules/{schedule['id']}?now={query_iso(2025, 3, 10, 10)}"
    )
    missing = client.get(f"{MONITORING_URL}/schedules/999999")

    assert response.status_code == 200
    payload = response.json()
    assert payload["schedule"]["id"] == schedule["id"]
    assert payload["recent_cycles"]
    assert payload["schedule"]["health_status"] in {"healthy", "warning", "critical"}
    assert isinstance(payload["alerts"], list)
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Live paper schedule not found"


def test_portfolio_detail_and_include_flags(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=74)
    create_schedule(client, run)
    due = run_due(client, now=iso(2025, 3, 10, 10))
    portfolio_id = due["results"][0]["cycle"]["portfolio_id"]

    response = client.get(f"{MONITORING_URL}/portfolios/{portfolio_id}")
    hidden = client.get(
        f"{MONITORING_URL}/portfolios/{portfolio_id}"
        "?include_performance=false"
        "&include_equity_curve=false"
        "&include_contributions=false"
        "&include_positions=false"
        "&include_recent_cycles=false"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio"]["id"] == portfolio_id
    assert payload["performance"] is not None
    assert payload["equity_curve"]
    assert payload["contributions"] is not None
    assert payload["positions"]
    assert payload["recent_cycles"]
    assert hidden.status_code == 200
    hidden_payload = hidden.json()
    assert hidden_payload["performance"] is None
    assert hidden_payload["equity_curve"] == []
    assert hidden_payload["contributions"] is None
    assert hidden_payload["positions"] == []
    assert hidden_payload["recent_cycles"] == []


def test_cycle_list_filters(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=75)
    schedule = create_schedule(client, run)
    due = run_due(client, now=iso(2025, 3, 10, 10))
    cycle = due["results"][0]["cycle"]
    create_failed_cycle(db_session, schedule_id=schedule["id"])

    all_cycles = client.get(f"{MONITORING_URL}/cycles")
    completed = client.get(f"{MONITORING_URL}/cycles?status=completed")
    by_schedule = client.get(f"{MONITORING_URL}/cycles?schedule_id={schedule['id']}")
    by_portfolio = client.get(
        f"{MONITORING_URL}/cycles?portfolio_id={cycle['portfolio_id']}"
    )
    invalid = client.get(f"{MONITORING_URL}/cycles?status=invalid")

    assert all_cycles.status_code == 200
    assert all_cycles.json()["total_returned"] >= 2
    assert completed.status_code == 200
    assert {row["status"] for row in completed.json()["cycles"]} == {"completed"}
    assert by_schedule.status_code == 200
    assert all(row["schedule_id"] == schedule["id"] for row in by_schedule.json()["cycles"])
    assert by_portfolio.status_code == 200
    assert all(row["portfolio_id"] == cycle["portfolio_id"] for row in by_portfolio.json()["cycles"])
    assert invalid.status_code == 400
    assert invalid.json()["detail"] == "invalid cycle status"


def test_schedule_alerts(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=76)
    due = create_schedule(client, run, name="Due paper schedule")
    locked = create_schedule(client, run, name="Locked paper schedule")
    stale = create_schedule(client, run, name="Stale lock paper schedule")
    capped = create_schedule(client, run, name="Capped paper schedule", max_runs=1)
    for schedule_id, expires_at, token in [
        (locked["id"], datetime(2025, 3, 10, 11, tzinfo=timezone.utc), "active"),
        (stale["id"], datetime(2025, 3, 10, 9, tzinfo=timezone.utc), "stale"),
    ]:
        stored = db_session.get(PaperLiveSchedule, schedule_id)
        stored.locked_at = datetime(2025, 3, 10, 8, tzinfo=timezone.utc)
        stored.lock_expires_at = expires_at
        stored.lock_token = token
    capped_stored = db_session.get(PaperLiveSchedule, capped["id"])
    capped_stored.run_count = 1
    db_session.commit()

    responses = [
        client.get(f"{MONITORING_URL}/schedules/{schedule_id}?now={query_iso(2025, 3, 10, 10)}").json()
        for schedule_id in [due["id"], locked["id"], stale["id"], capped["id"]]
    ]
    codes = set().union(*(alert_codes(response) for response in responses))

    assert "schedule_due" in codes
    assert "schedule_locked" in codes
    assert "schedule_lock_stale" in codes
    assert "schedule_max_runs_reached" in codes


def test_portfolio_alerts(
    client: TestClient,
    db_session: Session,
) -> None:
    archived = create_direct_portfolio(
        db_session,
        name="Archived paper portfolio",
        status="archived",
    )
    empty = create_direct_portfolio(db_session, name="Empty paper portfolio")

    archived_response = client.get(f"{MONITORING_URL}/portfolios/{archived.id}")
    empty_response = client.get(f"{MONITORING_URL}/portfolios/{empty.id}")

    assert archived_response.status_code == 200
    assert "portfolio_archived" in alert_codes(archived_response.json())
    assert empty_response.status_code == 200
    codes = alert_codes(empty_response.json())
    assert "portfolio_no_snapshots" in codes
    assert "portfolio_no_active_positions" in codes


def test_stale_running_cycle(
    client: TestClient,
    db_session: Session,
) -> None:
    create_running_cycle(db_session)

    response = client.get(
        f"{MONITORING_URL}/overview?now={query_iso(2025, 3, 10, 10)}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["health_status"] == "critical"
    assert "stale_running_cycle" in alert_codes(payload)


def test_validation_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_direct_portfolio(db_session, name="Validation paper portfolio")
    cases = [
        (
            client.get(f"{MONITORING_URL}/overview?schedule_limit=0"),
            "schedule_limit must be between 1 and 100",
        ),
        (
            client.get(f"{MONITORING_URL}/overview?portfolio_limit=0"),
            "portfolio_limit must be between 1 and 100",
        ),
        (
            client.get(f"{MONITORING_URL}/overview?cycle_limit=0"),
            "cycle_limit must be between 1 and 200",
        ),
        (
            client.get(
                f"{MONITORING_URL}/portfolios/{portfolio.id}?contribution_limit=0"
            ),
            "contribution_limit must be between 1 and 500",
        ),
        (
            client.get(
                f"{MONITORING_URL}/portfolios/{portfolio.id}"
                "?date_from=2025-03-10&date_to=2025-01-10"
            ),
            "Invalid date range",
        ),
        (
            client.get(f"{MONITORING_URL}/cycles?status=invalid"),
            "invalid cycle status",
        ),
    ]

    for response, detail in cases:
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_monitoring_is_read_only(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=77)
    schedule = create_schedule(client, run)
    due = run_due(client, now=iso(2025, 3, 10, 10))
    portfolio_id = due["results"][0]["cycle"]["portfolio_id"]
    before = core_counts(db_session)

    responses = [
        client.get(f"{MONITORING_URL}/overview"),
        client.get(f"{MONITORING_URL}/schedules/{schedule['id']}"),
        client.get(f"{MONITORING_URL}/portfolios/{portfolio_id}"),
        client.get(f"{MONITORING_URL}/cycles"),
    ]

    assert all(response.status_code == 200 for response in responses)
    assert core_counts(db_session) == before


def test_monitoring_does_not_call_execution_or_generation_services(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=78)
    schedule = create_schedule(client, run)
    due = run_due(client, now=iso(2025, 3, 10, 10))
    portfolio_id = due["results"][0]["cycle"]["portfolio_id"]

    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    for service_class, method_name in [
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
    ]:
        if hasattr(service_class, method_name):
            monkeypatch.setattr(service_class, method_name, fail_call)

    responses = [
        client.get(f"{MONITORING_URL}/overview"),
        client.get(f"{MONITORING_URL}/schedules/{schedule['id']}"),
        client.get(f"{MONITORING_URL}/portfolios/{portfolio_id}"),
        client.get(f"{MONITORING_URL}/cycles"),
    ]

    assert all(response.status_code == 200 for response in responses)


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=79)
    schedule = create_schedule(client, run)
    due = run_due(client, now=iso(2025, 3, 10, 10))
    portfolio_id = due["results"][0]["cycle"]["portfolio_id"]
    responses = [
        client.get(f"{MONITORING_URL}/overview"),
        client.get(f"{MONITORING_URL}/schedules/{schedule['id']}"),
        client.get(f"{MONITORING_URL}/portfolios/{portfolio_id}"),
        client.get(f"{MONITORING_URL}/cycles"),
    ]

    for response in responses:
        assert response.status_code == 200
        assert_no_forbidden_investment_vocabulary(response.json())
