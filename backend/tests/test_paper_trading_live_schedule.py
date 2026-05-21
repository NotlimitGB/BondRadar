from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.paper_live_cycle_run import PaperLiveCycleRun
from app.models.paper_live_schedule import PaperLiveSchedule
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.schemas.paper_trading_live_cycle import (
    LivePaperCycleRunRead,
    LivePaperCycleRunResponse,
)
from app.services.data_pipeline_service import DataPipelineService
from app.services.dataset_build_service import DatasetBuildService
from app.services.label_builder_service import LabelBuilderService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_readiness_service import LivePaperReadinessService
from app.services.paper_trading_scenario_service import PaperTradingScenarioService
from app.services.paper_trading_service import PaperTradingService
from app.services.total_return_label_service import TotalReturnLabelService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary
from tests.test_ml_candidate_strategy_robustness import count_rows
from tests.test_paper_trading_live_cycle import cycle_payload
from tests.test_paper_trading_live_readiness import seed_live_candidate


SCHEDULE_URL = "/api/paper-trading/live/schedules"


def iso(year: int, month: int, day: int, hour: int = 0) -> str:
    return datetime(year, month, day, hour, tzinfo=timezone.utc).isoformat()


def query_iso(year: int, month: int, day: int, hour: int = 0) -> str:
    return iso(year, month, day, hour).replace("+00:00", "Z")


def schedule_payload(run, **overrides) -> dict:
    payload = {
        "name": "Live paper schedule",
        "cycle_request": cycle_payload(run),
        "next_run_at": iso(2025, 3, 1),
        "interval_days": 7,
        "status": "active",
        "use_current_date_as_of_date": False,
    }
    payload.update(overrides)
    return payload


def create_schedule(client: TestClient, run, **overrides) -> dict:
    response = client.post(SCHEDULE_URL, json=schedule_payload(run, **overrides))
    assert response.status_code == 200
    return response.json()


def run_due(client: TestClient, **overrides) -> dict:
    payload = {
        "now": iso(2025, 3, 10, 10),
        "limit": 10,
        "dry_run": False,
        "lock_minutes": 10,
    }
    payload.update(overrides)
    response = client.post(f"{SCHEDULE_URL}/run-due", json=payload)
    assert response.status_code == 200
    return response.json()


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


def test_create_list_get_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=51)

    created = create_schedule(client, run)
    listed = client.get(SCHEDULE_URL)
    fetched = client.get(f"{SCHEDULE_URL}/{created['id']}")

    assert listed.status_code == 200
    assert fetched.status_code == 200
    assert created["status"] == "active"
    assert created["mode"] == "manual_cycle"
    assert created["interval_days"] == 7
    assert created["cycle_request_json"]["as_of_date"] == "2025-03-10"
    assert created["next_run_at"].startswith("2025-03-01")
    assert listed.json()[0]["id"] == created["id"]
    assert fetched.json()["id"] == created["id"]


def test_update_schedule_and_invalid_values(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=52)
    created = create_schedule(client, run)

    update = client.patch(
        f"{SCHEDULE_URL}/{created['id']}",
        json={
            "name": "Updated live paper schedule",
            "status": "paused",
            "next_run_at": iso(2025, 3, 20),
            "interval_days": 3,
            "max_runs": 2,
            "use_current_date_as_of_date": True,
        },
    )

    assert update.status_code == 200
    payload = update.json()
    assert payload["name"] == "Updated live paper schedule"
    assert payload["status"] == "paused"
    assert payload["interval_days"] == 3
    assert payload["max_runs"] == 2
    assert payload["use_current_date_as_of_date"] is True
    cases = [
        ("post", SCHEDULE_URL, {"name": " "}, "schedule name is required"),
        (
            "post",
            SCHEDULE_URL,
            {"status": "invalid"},
            "invalid schedule status",
        ),
        (
            "post",
            SCHEDULE_URL,
            {"next_run_at": None},
            "next_run_at is required",
        ),
        (
            "post",
            SCHEDULE_URL,
            {"interval_days": 0},
            "interval_days must be positive",
        ),
        (
            "post",
            SCHEDULE_URL,
            {"max_runs": 0},
            "max_runs must be positive when provided",
        ),
        (
            "patch",
            f"{SCHEDULE_URL}/{created['id']}",
            {"status": "invalid"},
            "invalid schedule status",
        ),
        (
            "patch",
            f"{SCHEDULE_URL}/{created['id']}",
            {"interval_days": 0},
            "interval_days must be positive",
        ),
    ]
    for method, url, overrides, detail in cases:
        body = schedule_payload(run)
        body.update(overrides)
        if method == "patch":
            body = overrides
            response = client.patch(url, json=body)
        else:
            response = client.post(url, json=body)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_dry_run_due_schedules_creates_no_cycle(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=53)
    schedule = create_schedule(client, run)
    before = paper_counts(db_session)

    result = run_due(client, dry_run=True)

    assert result["due_schedule_count"] == 1
    assert result["executed_count"] == 0
    assert result["skipped_count"] == 0
    assert result["results"][0]["status"] == "dry_run"
    assert paper_counts(db_session) == before
    after = client.get(f"{SCHEDULE_URL}/{schedule['id']}").json()
    assert after["next_run_at"] == schedule["next_run_at"]


def test_run_due_creates_one_manual_cycle_and_advances_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=54)
    schedule = create_schedule(client, run)
    before = paper_counts(db_session)

    result = run_due(client)

    assert result["due_schedule_count"] == 1
    assert result["executed_count"] == 1
    item = result["results"][0]
    assert item["status"] == "completed"
    assert item["cycle"]["status"] == "completed"
    assert item["cycle"]["schedule_id"] == schedule["id"]
    assert item["cycle"]["scheduled_for"].startswith("2025-03-01")
    fetched = client.get(f"{SCHEDULE_URL}/{schedule['id']}").json()
    assert fetched["last_cycle_run_id"] == item["cycle"]["id"]
    assert fetched["run_count"] == 1
    assert fetched["next_run_at"].startswith("2025-03-15")
    after = paper_counts(db_session)
    assert after["PaperLiveCycleRun"] == before["PaperLiveCycleRun"] + 1
    assert after["PaperPortfolio"] == before["PaperPortfolio"] + 1
    assert after["PaperPortfolioPosition"] > before["PaperPortfolioPosition"]
    assert after["PaperPortfolioTransaction"] > before["PaperPortfolioTransaction"]
    assert after["PaperPortfolioSnapshot"] > before["PaperPortfolioSnapshot"]


def test_scheduled_client_cycle_key_prevents_duplicate_cycle(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=55)
    schedule = create_schedule(client, run)
    first = run_due(client)
    before_second = paper_counts(db_session)

    second = run_due(client)

    assert second["due_schedule_count"] == 0
    assert paper_counts(db_session) == before_second
    key = first["results"][0]["cycle"]["client_cycle_key"]
    assert key.startswith(f"scheduled-cycle:{schedule['id']}:")


def test_paused_and_archived_schedules_do_not_run(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=56)
    paused = create_schedule(client, run, name="Paused paper schedule", status="paused")
    archived = create_schedule(
        client,
        run,
        name="Archived paper schedule",
        status="archived",
    )

    due = run_due(client)
    paused_run = client.post(f"{SCHEDULE_URL}/{paused['id']}/run")
    archived_run = client.post(f"{SCHEDULE_URL}/{archived['id']}/run")

    assert due["due_schedule_count"] == 0
    assert paused_run.status_code == 400
    assert paused_run.json()["detail"] == (
        "Paused live paper schedule cannot be executed"
    )
    assert archived_run.status_code == 400
    assert archived_run.json()["detail"] == (
        "Archived live paper schedule cannot be executed"
    )


def test_max_runs_stops_future_execution(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=57)
    create_schedule(client, run, max_runs=1)
    first = run_due(client)
    before_second = paper_counts(db_session)

    second = run_due(client, now=iso(2025, 3, 20, 10))

    assert first["executed_count"] == 1
    assert second["due_schedule_count"] == 0
    assert paper_counts(db_session) == before_second


def test_run_single_schedule_now(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=58)
    schedule = create_schedule(client, run, next_run_at=iso(2025, 3, 20))

    response = client.post(
        f"{SCHEDULE_URL}/{schedule['id']}/run?now={query_iso(2025, 3, 10, 10)}"
    )

    assert response.status_code == 200
    item = response.json()
    assert item["status"] == "completed"
    assert item["scheduled_for"].startswith("2025-03-10")
    fetched = client.get(f"{SCHEDULE_URL}/{schedule['id']}").json()
    assert fetched["run_count"] == 1
    assert fetched["last_cycle_run_id"] == item["cycle"]["id"]
    assert fetched["next_run_at"].startswith("2025-03-27")


def test_blocked_cycle_still_advances_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=59)
    request = cycle_payload(run)
    request["readiness"]["candidate_strategy_robustness"]["candidate_comparison"][
        "minimum_evaluable_predictions"
    ] = 10
    schedule = create_schedule(client, run, cycle_request=request)

    result = run_due(client)

    item = result["results"][0]
    assert item["status"] == "blocked"
    assert item["cycle"]["status"] == "blocked"
    fetched = client.get(f"{SCHEDULE_URL}/{schedule['id']}").json()
    assert fetched["run_count"] == 1
    assert fetched["last_cycle_run_id"] == item["cycle"]["id"]
    assert fetched["next_run_at"].startswith("2025-03-15")


def test_lock_prevents_duplicate_execution(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=60)
    schedule_payload_body = create_schedule(client, run)
    stored = db_session.get(PaperLiveSchedule, schedule_payload_body["id"])
    now = datetime(2025, 3, 10, 10, tzinfo=timezone.utc)
    stored.locked_at = now
    stored.lock_expires_at = now + timedelta(minutes=30)
    stored.lock_token = "active-lock"
    db_session.commit()

    result = run_due(client, now=now.isoformat())

    assert result["due_schedule_count"] == 1
    assert result["executed_count"] == 0
    assert result["skipped_count"] == 1
    assert result["results"][0]["status"] == "skipped"
    assert result["results"][0]["warnings"][0]["message"] == (
        "Schedule lock could not be acquired"
    )


def test_use_current_date_as_of_date(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=61)
    create_schedule(client, run, use_current_date_as_of_date=True)

    result = run_due(client, now=iso(2025, 3, 10, 10))

    request_json = result["results"][0]["cycle"]["request_json"]
    assert request_json["as_of_date"] == "2025-03-10"
    assert request_json["rebalance"]["as_of_date"] == "2025-03-10"


def test_current_date_mode_missing_predictions_is_diagnostic(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=65)
    create_schedule(client, run, use_current_date_as_of_date=True)

    result = run_due(client, now=iso(2025, 3, 11, 10))

    item = result["results"][0]
    assert item["status"] == "failed"
    assert any(
        error.get("detail")
        == (
            "No predictions found for current execution date. Run data "
            "refresh/predictions first or disable use_current_date_as_of_date."
        )
        for error in item["errors"]
    )
    assert item["cycle"]["summary_json"]["failure_detail"] == (
        "No predictions found for current execution date. Run data "
        "refresh/predictions first or disable use_current_date_as_of_date."
    )


def test_scheduler_does_not_call_lower_level_services_directly(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=62)
    create_schedule(client, run)

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
        (LivePaperReadinessService, "check"),
        (PaperTradingService, "create_portfolio"),
        (PaperTradingService, "rebalance"),
        (PaperTradingService, "mark_period"),
    ]:
        if hasattr(service_class, method_name):
            monkeypatch.setattr(service_class, method_name, fail_call)

    def fake_run(self, request):
        now = datetime.now(timezone.utc)
        cycle = PaperLiveCycleRun(
            status="completed",
            mode="manual",
            client_cycle_key=request.client_cycle_key,
            as_of_date=request.as_of_date,
            request_json=request.model_dump(mode="json"),
            readiness_json={},
            summary_json={},
            warnings_json=[],
            errors_json=[],
            started_at=now,
            finished_at=now,
            created_at=now,
        )
        self.db.add(cycle)
        self.db.commit()
        self.db.refresh(cycle)
        return LivePaperCycleRunResponse(
            cycle=LivePaperCycleRunRead.model_validate(cycle),
            readiness=None,
            portfolio=None,
            mark_period_result=None,
            rebalance_result=None,
            warnings=[],
            errors=[],
        )

    monkeypatch.setattr(LivePaperCycleService, "run", fake_run)

    result = run_due(client)

    assert result["executed_count"] == 1
    assert result["results"][0]["status"] == "completed"


def test_validation_and_missing_schedule(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=63)
    cases = [
        (
            client.post(
                f"{SCHEDULE_URL}/run-due",
                json={"now": iso(2025, 3, 10), "limit": 0},
            ),
            "limit must be between 1 and 100",
        ),
        (
            client.post(
                f"{SCHEDULE_URL}/run-due",
                json={"now": iso(2025, 3, 10), "lock_minutes": 0},
            ),
            "lock_minutes must be between 1 and 120",
        ),
        (
            client.get(f"{SCHEDULE_URL}/999999"),
            "Live paper schedule not found",
        ),
    ]
    invalid_max_runs = client.patch(
        f"{SCHEDULE_URL}/{create_schedule(client, run)['id']}",
        json={"max_runs": 0},
    )
    cases.append((invalid_max_runs, "max_runs must be positive when provided"))

    for response, detail in cases:
        assert response.status_code in {400, 404}
        assert response.json()["detail"] == detail


def test_response_has_no_project_banned_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_live_candidate(db_session, index=64)
    created = client.post(SCHEDULE_URL, json=schedule_payload(run))
    listed = client.get(SCHEDULE_URL)
    fetched = client.get(f"{SCHEDULE_URL}/{created.json()['id']}")
    updated = client.patch(
        f"{SCHEDULE_URL}/{created.json()['id']}",
        json={"name": "Neutral paper schedule"},
    )
    due = client.post(
        f"{SCHEDULE_URL}/run-due",
        json={"now": iso(2025, 3, 10), "dry_run": True},
    )
    single = client.post(
        f"{SCHEDULE_URL}/{created.json()['id']}/run?now={query_iso(2025, 3, 10, 10)}"
    )
    blocked_request = cycle_payload(run)
    blocked_request["readiness"]["candidate_strategy_robustness"][
        "candidate_comparison"
    ]["minimum_evaluable_predictions"] = 10
    blocked_schedule = client.post(
        SCHEDULE_URL,
        json=schedule_payload(
            run,
            name="Blocked paper schedule",
            cycle_request=blocked_request,
            next_run_at=iso(2025, 4, 1),
        ),
    )
    blocked = client.post(
        f"{SCHEDULE_URL}/{blocked_schedule.json()['id']}/run?now={query_iso(2025, 4, 1, 10)}"
    )

    for response in [created, listed, fetched, updated, due, single, blocked]:
        assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(
        [
            created.json(),
            listed.json(),
            fetched.json(),
            updated.json(),
            due.json(),
            single.json(),
            blocked.json(),
        ]
    )
