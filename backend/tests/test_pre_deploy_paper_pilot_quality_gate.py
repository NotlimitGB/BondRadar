from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.data_pipeline_run import DataPipelineRun
from app.models.data_pipeline_step_run import DataPipelineStepRun
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.models.paper_live_cycle_run import PaperLiveCycleRun
from app.models.paper_live_schedule import PaperLiveSchedule
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.schemas.corporate_universe_action_plan import (
    CorporateUniverseActionPlanResponse,
)
from app.schemas.live_data_readiness import (
    LiveDataReadinessCheck,
    LiveDataReadinessResponse,
)
from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessResponse,
    MLCandidateStrategyRobustnessSelectedCandidate,
)
from app.schemas.paper_trading_live_pilot import (
    LivePaperPilotBootstrapPayloads,
    LivePaperPilotBootstrapResponse,
)
from app.schemas.paper_trading_live_readiness import (
    LivePaperReadinessResponse,
)
from app.schemas.paper_trading_live_schedule import LivePaperScheduleRunDueResponse
from app.services.corporate_universe_action_plan_service import (
    CorporateUniverseActionPlanService,
)
from app.services.data_pipeline_service import DataPipelineService
from app.services.live_data_readiness_service import LiveDataReadinessService
from app.services.ml_candidate_strategy_robustness_service import (
    MLCandidateStrategyRobustnessService,
)
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.ml_validation_suite_service import MLValidationSuiteService
from app.services.moex_bond_universe_service import MoexBondUniverseService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_pilot_service import (
    LivePaperPilotBootstrapService,
)
from app.services.paper_trading_live_readiness_service import LivePaperReadinessService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService
from app.services.paper_trading_service import PaperTradingService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


QUALITY_GATE_URL = "/api/pre-deploy/paper-pilot/quality-gate"
EXTERNAL_RISK_URL = "/api/risk/external-regime"


def count_rows(db: Session, model: type) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def now() -> datetime:
    return datetime.now(timezone.utc)


def base_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_run_id": 1,
        "return_method": "risk_adjusted",
        "horizon_days": 30,
        "date_from": "2025-01-10",
        "date_to": "2025-03-14",
        "next_run_at": "2025-03-15T10:00:00Z",
    }
    payload.update(overrides)
    return payload


def create_model_run(
    db: Session,
    *,
    run_id: int | None = None,
    status_value: str = "completed",
    horizon_days: int = 30,
    return_method: str | None = "risk_adjusted",
) -> MLModelRun:
    params: dict[str, Any] = {"horizon_days": horizon_days}
    if return_method is not None:
        params["return_method"] = return_method
    model_run = MLModelRun(
        id=run_id,
        status=status_value,
        model_type="logistic_regression",
        horizon_days=horizon_days,
        features=["duration"],
        target="risk_adjusted_h30",
        train_rows=80,
        test_rows=20,
        positive_rows=50,
        negative_rows=50,
        metrics={"accuracy": 0.7},
        feature_importance=[],
        params=params,
        artifact_path="/tmp/model.joblib",
        started_at=now(),
        finished_at=now(),
        created_at=now(),
    )
    db.add(model_run)
    db.commit()
    db.refresh(model_run)
    return model_run


def corporate_plan(status_value: str = "ready") -> CorporateUniverseActionPlanResponse:
    ready = status_value == "ready"
    return CorporateUniverseActionPlanResponse(
        status=status_value,
        as_of=now(),
        board="TQCB",
        include_ofz=False,
        local_total_bond_count=25 if ready else 0,
        local_corporate_bond_count=25 if ready else 0,
        local_ofz_bond_count=0,
        local_working_bond_count=25 if ready else 0,
        local_company_count=25 if ready else 0,
        bonds_with_secid_count=25 if ready else 0,
        bonds_with_isin_count=25 if ready else 0,
        bonds_with_company_count=25 if ready else 0,
        sample_corporate_bonds=[],
        sample_ofz_bonds=[],
        checks=[],
        actions=[],
        commands=[],
        sync_payload={"board": "TQCB"},
        curl_example="curl -s",
        can_sync_universe=True,
        can_continue_to_data_pipeline=ready,
        warnings=[] if ready else [{"code": "local_corporate_universe_size"}],
        errors=[],
        next_steps=[],
    )


def live_data_response(status_value: str = "ready") -> LiveDataReadinessResponse:
    check_status = {
        "ready": "passed",
        "warning": "warning",
        "not_ready": "failed",
    }[status_value]
    checks = [
        LiveDataReadinessCheck(
            name="paper_pilot_data_ready",
            status=check_status,
            message="Live data chain was checked",
            details={"blocking_checks": [] if status_value != "not_ready" else ["data"]},
        )
    ]
    return LiveDataReadinessResponse(
        status=status_value,
        as_of=now(),
        corporate_bond_count=25 if status_value != "not_ready" else 0,
        ofz_bond_count=0,
        total_bond_count=25 if status_value != "not_ready" else 0,
        working_bond_count=25 if status_value != "not_ready" else 0,
        company_count=25 if status_value != "not_ready" else 0,
        latest_market_snapshot_date=date(2025, 3, 14),
        market_snapshot_count=25 if status_value != "not_ready" else 0,
        bonds_with_recent_market_snapshot_count=25 if status_value != "not_ready" else 0,
        latest_cashflow_date=date(2025, 3, 14),
        cashflow_event_count=25 if status_value != "not_ready" else 0,
        bonds_with_cashflows_count=25 if status_value != "not_ready" else 0,
        latest_feature_snapshot_date=date(2025, 3, 14),
        feature_snapshot_count=25 if status_value != "not_ready" else 0,
        bonds_with_recent_features_count=25 if status_value != "not_ready" else 0,
        latest_completed_model_run_id=1 if status_value != "not_ready" else None,
        latest_completed_model_run_created_at=now()
        if status_value != "not_ready"
        else None,
        prediction_count_for_latest_run=25 if status_value != "not_ready" else 0,
        bonds_with_predictions_for_latest_run_count=25
        if status_value != "not_ready"
        else 0,
        latest_prediction_date=date(2025, 3, 14),
        checks=checks,
        warnings=[],
        next_steps=["Review live data action plan."],
    )


def robustness_response(
    *,
    warning_flag: bool = False,
    fail_flag: bool = False,
    ready_candidate: bool = True,
) -> MLCandidateStrategyRobustnessResponse:
    flags = []
    if warning_flag:
        flags.append({"code": "subperiod_balance", "level": "warning", "message": "check"})
    if fail_flag:
        flags.append({"code": "subperiod_count", "level": "fail", "message": "check"})
    selected = MLCandidateStrategyRobustnessSelectedCandidate(
        name="candidate",
        rank=1,
        ranking_metric="probability_separation",
        ranking_direction="desc",
        ranking_value=Decimal("0.20"),
        model_run_id=1,
        model_run_ids=[1],
        model_run_count=1,
        prediction_source_mode="single_model",
        ready_for_strategy_research=ready_candidate,
        issues=[] if ready_candidate else ["insufficient evaluation"],
    )
    return MLCandidateStrategyRobustnessResponse(
        selected_candidate=selected,
        candidate_comparison={"selected_candidate": selected.model_dump(mode="json")},
        robustness_analysis={
            "analyzed_variant_count": 1,
            "variants": [
                {
                    "variant_name": "pre_deploy_top_n",
                    "completed_subperiod_count": 2,
                    "flags": flags,
                }
            ],
        },
        warnings=[],
    )


def live_paper_readiness(
    status_value: str = "ready",
) -> LivePaperReadinessResponse:
    return LivePaperReadinessResponse(
        readiness_status=status_value,
        virtual_initial_capital=Decimal("50000"),
        planned_duration_days=90,
        selected_candidate=robustness_response().selected_candidate,
        candidate_comparison={},
        robustness_analysis={"analyzed_variant_count": 1},
        gates=[],
        warnings=[],
    )


def bootstrap_response(
    *,
    status_value: str = "prepared",
    created_schedule_id: int | None = None,
) -> LivePaperPilotBootstrapResponse:
    return LivePaperPilotBootstrapResponse(
        status=status_value,
        created_schedule_id=created_schedule_id,
        readiness_status="ready",
        selected_model_run_id=1,
        virtual_initial_capital=Decimal("50000"),
        planned_duration_days=90,
        next_run_at=now(),
        interval_days=1,
        max_runs=None,
        readiness=None,
        schedule=None,
        monitoring_overview=None,
        payloads=LivePaperPilotBootstrapPayloads(
            readiness_request={},
            cycle_request={},
            schedule_request={},
        ),
        next_steps=[],
        warnings=[],
        errors=[],
    )


def scheduler_response(
    *,
    due_schedule_count: int = 1,
    dry_run: bool = True,
    errors: list[dict[str, Any]] | None = None,
) -> LivePaperScheduleRunDueResponse:
    return LivePaperScheduleRunDueResponse(
        now=now(),
        dry_run=dry_run,
        due_schedule_count=due_schedule_count,
        executed_count=0,
        skipped_count=0,
        results=[],
        warnings=[],
        errors=list(errors or []),
    )


def patch_ready_services(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        CorporateUniverseActionPlanService,
        "plan",
        lambda self, **kwargs: corporate_plan("ready"),
    )
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda self, **kwargs: live_data_response("ready"),
    )
    monkeypatch.setattr(
        MLCandidateStrategyRobustnessService,
        "analyze",
        lambda self, request: robustness_response(),
    )
    monkeypatch.setattr(
        LivePaperReadinessService,
        "check",
        lambda self, request: live_paper_readiness("ready"),
    )
    monkeypatch.setattr(
        LivePaperPilotBootstrapService,
        "bootstrap",
        lambda self, request: bootstrap_response(),
    )
    monkeypatch.setattr(
        LivePaperScheduleService,
        "run_due",
        lambda self, request: scheduler_response(),
    )


def gate_by_code(payload: dict[str, Any], code: str) -> dict[str, Any]:
    return next(gate for gate in payload["gates"] if gate["code"] == code)


def warning_codes(payload: dict[str, Any]) -> set[str]:
    return {warning["code"] for warning in payload["warnings"]}


def test_missing_model_run_blocks(client: TestClient, monkeypatch: Any) -> None:
    patch_ready_services(monkeypatch)

    response = client.post(QUALITY_GATE_URL, json=base_payload(model_run_id=999))

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert gate_by_code(payload, "model_run_available")["status"] == "failed"
    assert payload["ready_for_50k_paper_pilot"] is False
    assert payload["ready_for_vds_deploy"] is False


def test_data_not_ready_blocks_before_robustness_and_bootstrap(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    monkeypatch.setattr(
        CorporateUniverseActionPlanService,
        "plan",
        lambda self, **kwargs: corporate_plan("ready"),
    )
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda self, **kwargs: live_data_response("not_ready"),
    )

    def fail_if_called(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("service should not be called")

    monkeypatch.setattr(MLCandidateStrategyRobustnessService, "analyze", fail_if_called)
    monkeypatch.setattr(LivePaperPilotBootstrapService, "bootstrap", fail_if_called)
    monkeypatch.setattr(
        LivePaperScheduleService,
        "run_due",
        lambda self, request: scheduler_response(),
    )

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert gate_by_code(payload, "live_data_ready")["status"] == "failed"
    assert gate_by_code(payload, "strategy_robustness_ready")["status"] == "skipped"
    assert gate_by_code(payload, "pilot_bootstrap_dry_run_ready")["status"] == "skipped"


def test_ready_core_gates_with_manual_deploy_warnings(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert payload["ready_for_50k_paper_pilot"] is True
    assert payload["ready_for_vds_deploy"] is False
    assert gate_by_code(payload, "backend_test_plan_ready")["status"] == "warning"
    assert gate_by_code(payload, "frontend_build_plan_ready")["status"] == "warning"
    assert gate_by_code(payload, "deployment_runbook_ready")["status"] == "warning"


def test_normal_external_risk_passes_gate(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    gate = gate_by_code(payload, "external_risk_regime_ready")
    assert gate["status"] == "passed"
    assert gate["details"]["mode"] == "normal"
    assert payload["external_risk_regime"]["mode"] == "normal"


def test_elevated_external_risk_warns_and_blocks_pilot_until_allowed(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    regime = client.put(
        EXTERNAL_RISK_URL,
        json={
            "mode": "elevated",
            "reason": "Manual operator caution before paper execution window.",
        },
    )
    assert regime.status_code == 200

    blocked = client.post(QUALITY_GATE_URL, json=base_payload())
    assert blocked.status_code == 200
    blocked_payload = blocked.json()
    blocked_gate = gate_by_code(blocked_payload, "external_risk_regime_ready")
    assert blocked_gate["status"] == "warning"
    assert blocked_gate["details"]["accepted"] is False
    assert blocked_payload["ready_for_50k_paper_pilot"] is False

    allowed = client.post(
        QUALITY_GATE_URL,
        json=base_payload(allow_external_risk_warning=True),
    )
    assert allowed.status_code == 200
    allowed_payload = allowed.json()
    allowed_gate = gate_by_code(allowed_payload, "external_risk_regime_ready")
    assert allowed_gate["status"] == "warning"
    assert allowed_gate["details"]["accepted"] is True
    assert allowed_payload["ready_for_50k_paper_pilot"] is True


def test_severe_external_risk_fails_gate_by_default(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    regime = client.put(
        EXTERNAL_RISK_URL,
        json={
            "mode": "severe",
            "reason": "Manual severe external risk overlay.",
        },
    )
    assert regime.status_code == 200

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    gate = gate_by_code(payload, "external_risk_regime_ready")
    assert gate["status"] == "failed"
    assert payload["status"] == "blocked"
    assert payload["ready_for_50k_paper_pilot"] is False


def test_severe_external_risk_override_still_warns(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    regime = client.put(
        EXTERNAL_RISK_URL,
        json={
            "mode": "severe",
            "reason": "Manual severe external risk overlay.",
        },
    )
    assert regime.status_code == 200

    response = client.post(
        QUALITY_GATE_URL,
        json=base_payload(allow_external_risk_severe=True),
    )

    assert response.status_code == 200
    payload = response.json()
    gate = gate_by_code(payload, "external_risk_regime_ready")
    assert gate["status"] == "warning"
    assert gate["details"]["external_risk_override_used"] is True
    assert payload["status"] == "warning"


def test_robustness_warning_can_be_allowed_or_blocked(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    monkeypatch.setattr(
        MLCandidateStrategyRobustnessService,
        "analyze",
        lambda self, request: robustness_response(warning_flag=True),
    )

    blocked = client.post(QUALITY_GATE_URL, json=base_payload())
    assert blocked.status_code == 200
    blocked_payload = blocked.json()
    assert blocked_payload["status"] == "blocked"
    assert gate_by_code(blocked_payload, "strategy_robustness_ready")["status"] == "failed"

    allowed = client.post(
        QUALITY_GATE_URL,
        json=base_payload(allow_robustness_warning=True),
    )
    assert allowed.status_code == 200
    allowed_payload = allowed.json()
    assert allowed_payload["status"] == "warning"
    assert gate_by_code(allowed_payload, "strategy_robustness_ready")["status"] == "warning"
    assert allowed_payload["ready_for_50k_paper_pilot"] is True


def test_live_paper_warning_can_be_allowed_or_blocked(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    monkeypatch.setattr(
        LivePaperReadinessService,
        "check",
        lambda self, request: live_paper_readiness("warning"),
    )

    blocked = client.post(QUALITY_GATE_URL, json=base_payload())
    assert blocked.status_code == 200
    blocked_payload = blocked.json()
    assert blocked_payload["status"] == "blocked"
    assert gate_by_code(blocked_payload, "live_paper_readiness_ready")["status"] == "failed"

    allowed = client.post(
        QUALITY_GATE_URL,
        json=base_payload(allow_live_paper_warning=True),
    )
    assert allowed.status_code == 200
    allowed_payload = allowed.json()
    assert allowed_payload["status"] == "warning"
    assert gate_by_code(allowed_payload, "live_paper_readiness_ready")["status"] == "warning"
    assert allowed_payload["ready_for_50k_paper_pilot"] is True


def test_pilot_bootstrap_dry_run_must_not_create_schedule(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    monkeypatch.setattr(
        LivePaperPilotBootstrapService,
        "bootstrap",
        lambda self, request: bootstrap_response(
            status_value="scheduled",
            created_schedule_id=123,
        ),
    )

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert gate_by_code(payload, "pilot_bootstrap_dry_run_ready")["status"] == "failed"


def test_scheduler_dry_run_always_receives_dry_run_true(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    seen = {"dry_run": None}

    def run_due(self: LivePaperScheduleService, request: Any) -> LivePaperScheduleRunDueResponse:
        seen["dry_run"] = request.dry_run
        return scheduler_response()

    monkeypatch.setattr(LivePaperScheduleService, "run_due", run_due)

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    assert seen["dry_run"] is True
    assert response.json()["payloads"]["scheduler_dry_run_request"]["dry_run"] is True


def test_invalid_params_return_exact_400_details(client: TestClient) -> None:
    cases = [
        ({"model_run_id": 0}, "model_run_id must be positive"),
        ({"recent_days": 0}, "recent_days must be between 1 and 365"),
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
        (
            {"date_from": "2025-03-15", "date_to": "2025-03-14"},
            "date_from must be before or equal to date_to",
        ),
        ({"horizon_days": 0}, "horizon_days must be between 1 and 365"),
        ({"return_method": "bad"}, "return_method must be supported"),
        ({"ranking_direction": "sideways"}, "ranking_direction must be asc or desc"),
        ({"top_n": 0}, "top_n must be positive"),
        (
            {"min_probability_positive": "1.20"},
            "min_probability_positive must be between 0 and 1",
        ),
        (
            {"positive_probability_cutoff": "-0.01"},
            "positive_probability_cutoff must be between 0 and 1",
        ),
        ({"initial_capital": "0"}, "initial_capital must be positive"),
        (
            {"virtual_initial_capital": "0"},
            "virtual_initial_capital must be positive",
        ),
        (
            {"planned_duration_days": 0},
            "planned_duration_days must be between 1 and 365",
        ),
        ({"interval_days": 0}, "interval_days must be positive"),
        ({"max_runs": 0}, "max_runs must be positive when provided"),
        (
            {"transaction_cost_rate": "-0.001"},
            "transaction_cost_rate must be non-negative",
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
            "maximum_warning_flag_count must be non-negative when provided",
        ),
    ]
    for override, detail in cases:
        response = client.post(QUALITY_GATE_URL, json=base_payload(**override))
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_no_db_writes_for_quality_gate(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)
    models = [
        Bond,
        Company,
        BondMarketSnapshot,
        BondCashflowEvent,
        BondFeatureSnapshot,
        BondReturnLabel,
        MLModelRun,
        MLPrediction,
        DataPipelineRun,
        DataPipelineStepRun,
        PaperPortfolio,
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
        PaperLiveSchedule,
        PaperLiveCycleRun,
    ]
    before = {model: count_rows(db_session, model) for model in models}

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    after = {model: count_rows(db_session, model) for model in models}
    assert after == before


def test_forbidden_services_are_not_called(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("forbidden service call")

    monkeypatch.setattr(DataPipelineService, "run", forbidden, raising=False)
    monkeypatch.setattr(MoexBondUniverseService, "sync", forbidden, raising=False)
    monkeypatch.setattr(MoexMarketDataService, "sync", forbidden, raising=False)
    monkeypatch.setattr(MoexCashflowService, "sync", forbidden, raising=False)
    monkeypatch.setattr(MLTrainingService, "train", forbidden, raising=False)
    monkeypatch.setattr(MLPredictionService, "predict", forbidden, raising=False)
    monkeypatch.setattr(MLValidationSuiteService, "run", forbidden, raising=False)
    monkeypatch.setattr(PaperTradingService, "rebalance", forbidden, raising=False)
    monkeypatch.setattr(PaperTradingService, "mark_period", forbidden, raising=False)
    monkeypatch.setattr(LivePaperCycleService, "run", forbidden, raising=False)
    monkeypatch.setattr(
        LivePaperScheduleService,
        "run_schedule_once",
        forbidden,
        raising=False,
    )

    def safe_run_due(self: LivePaperScheduleService, request: Any) -> LivePaperScheduleRunDueResponse:
        if not request.dry_run:
            raise AssertionError("scheduler must stay in dry-run mode")
        return scheduler_response()

    monkeypatch.setattr(LivePaperScheduleService, "run_due", safe_run_due)

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    assert response.json()["status"] == "warning"


def test_response_avoids_forbidden_vocabulary(
    client: TestClient,
    db_session: Session,
    monkeypatch: Any,
) -> None:
    create_model_run(db_session, run_id=1)
    patch_ready_services(monkeypatch)

    response = client.post(QUALITY_GATE_URL, json=base_payload())

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
