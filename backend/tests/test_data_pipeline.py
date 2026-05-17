from datetime import date, datetime, timezone
from decimal import Decimal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.company_score import CompanyScore
from app.models.data_pipeline_run import DataPipelineRun
from app.models.data_pipeline_step_run import DataPipelineStepRun
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.schemas.moex import (
    MoexCashflowSyncResult,
    MoexMarketDataSyncResult,
)
from app.services.data_pipeline_service import DataPipelineService, StepExecutionResult
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(db: Session, ticker: str = "PIPE") -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        inn=f"77{abs(hash(ticker)) % 100000000:08d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def create_demo_context(db: Session) -> tuple[Company, Bond]:
    company = create_company(db)
    report = FinancialReport(
        company_id=company.id,
        period_year=2024,
        period_quarter=0,
        revenue=Decimal("1000.00"),
        ebitda=Decimal("250.00"),
        net_debt=Decimal("300.00"),
        total_debt=Decimal("400.00"),
        cash=Decimal("100.00"),
        equity=Decimal("600.00"),
        short_term_debt=Decimal("100.00"),
        operating_cash_flow=Decimal("150.00"),
        net_profit=Decimal("120.00"),
        interest_expense=Decimal("50.00"),
        source="pipeline-test",
        published_at=dt(2024, 12, 31),
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    score = CompanyScore(
        company_id=company.id,
        report=report,
        score=Decimal("82.00"),
        signal=AnalysisSignal.NEUTRAL.value,
        factors={},
        summary="Pipeline company score",
        as_of_date=date(2024, 12, 31),
        source="pipeline-test",
        final_company_score=82,
        created_at=dt(2024, 12, 31),
    )
    bond = Bond(
        company_id=company.id,
        isin=f"RU000{company.ticker}01"[:12],
        secid=f"{company.ticker}01",
        name=f"{company.ticker} Bond",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        maturity_date=date(2030, 1, 1),
        liquidity_score=80,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add_all([report, score, bond])
    db.flush()
    start = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=date(2025, 1, 10),
        price=Decimal("100.000000"),
        clean_price=Decimal("100.000000"),
        dirty_price=Decimal("101.000000"),
        nkd=Decimal("10.000000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        liquidity_score=80,
        source="test",
    )
    end = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=date(2025, 2, 9),
        price=Decimal("101.000000"),
        clean_price=Decimal("101.000000"),
        dirty_price=Decimal("102.000000"),
        nkd=Decimal("10.000000"),
        yield_to_maturity=Decimal("11.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1100000.00"),
        liquidity_score=82,
        source="test",
    )
    coupon = BondCashflowEvent(
        bond_id=bond.id,
        event_date=date(2025, 1, 25),
        event_type="coupon",
        amount=Decimal("20.000000"),
        amount_percent=None,
        currency="RUB",
        source="test",
        raw_payload={"test": True},
    )
    db.add_all([start, end, coupon])
    db.commit()
    db.refresh(company)
    db.refresh(bond)
    return company, bond


def fake_market_sync(self, request):
    return MoexMarketDataSyncResult(
        total_bonds=len(request.bond_ids or []),
        processed_bonds=len(request.bond_ids or []),
        skipped_bonds=0,
        created=1,
        updated=0,
        skipped=0,
        errors=[],
        warnings=[],
    )


def fake_cashflow_sync(self, request):
    return MoexCashflowSyncResult(
        total_bonds=len(request.bond_ids or []),
        processed_bonds=len(request.bond_ids or []),
        skipped_bonds=0,
        created=1,
        updated=0,
        skipped=0,
        errors=[],
        warnings=[],
    )


def fake_readiness_step(status_value: str = "ready"):
    def _fake(self, request, bond_ids, company_ids):
        warning_gates = ["cashflow_coverage"] if status_value == "warning" else []
        failed_gates = ["evaluable_rows"] if status_value == "not_ready" else []
        gates = [
            {"name": name, "status": "warning", "message": f"{name} warning", "details": {}}
            for name in warning_gates
        ] + [
            {"name": name, "status": "fail", "message": f"{name} failed", "details": {}}
            for name in failed_gates
        ]
        result = {
            "status": status_value,
            "summary": {
                "ready_for_ml_training": status_value == "ready",
                "evaluable_label_count": 30 if status_value != "not_ready" else 0,
                "positive_label_count": 15 if status_value != "not_ready" else 0,
                "negative_label_count": 15 if status_value != "not_ready" else 0,
                "insufficient_ratio": "0.10",
            },
            "gates": gates,
            "bond_issues": [],
            "warnings": [gate["message"] for gate in gates if gate["status"] == "warning"],
            "recommended_next_actions": [],
        }
        warnings = [
            {"step": "data_readiness_check", "message": message}
            for message in result["warnings"]
        ]
        return StepExecutionResult(result=result, errors=[], warnings=warnings)

    return _fake


def fake_ml_train(self, request, bond_ids, company_ids):
    return StepExecutionResult(
        result={"model_run_id": 777, "status": "completed"},
        errors=[],
        warnings=[],
    )


def test_pipeline_creates_run_and_step_records(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    monkeypatch.setattr(MoexMarketDataService, "sync", fake_market_sync)
    monkeypatch.setattr(MoexCashflowService, "sync", fake_cashflow_sync)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "steps": ["moex_market_sync", "moex_cashflow_sync"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["summary"]["market_snapshots_created"] == 1
    assert payload["summary"]["cashflow_events_created"] == 1
    assert len(payload["run"]["steps"]) == 2
    assert db_session.execute(select(DataPipelineRun)).scalar_one().status == "completed"
    assert len(list(db_session.execute(select(DataPipelineStepRun)).scalars())) == 2


def test_pipeline_validation_errors(client: TestClient) -> None:
    invalid_date = client.post(
        "/api/pipeline/run",
        json={"date_from": "2025-02-01", "date_to": "2025-01-01"},
    )
    invalid_step = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-01",
            "date_to": "2025-01-10",
            "steps": ["not_a_step"],
        },
    )
    predict_without_ml = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-01",
            "date_to": "2025-01-10",
            "run_predictions": True,
        },
    )
    eval_without_predictions = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-01",
            "date_to": "2025-01-10",
            "run_evaluation": True,
        },
    )

    assert invalid_date.status_code == 400
    assert invalid_date.json()["detail"] == "Invalid date range"
    assert invalid_step.status_code == 400
    assert invalid_step.json()["detail"] == "Invalid pipeline step"
    assert predict_without_ml.status_code == 400
    assert predict_without_ml.json()["detail"] == "run_predictions requires run_ml"
    assert eval_without_predictions.status_code == 400
    assert eval_without_predictions.json()["detail"] == "run_evaluation requires run_predictions"


def test_pipeline_passes_selected_scope_to_moex_services(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    captured: dict[str, list[int] | None] = {}

    def capture_market(self, request):
        captured["market_bond_ids"] = request.bond_ids
        captured["market_dates"] = [request.date_from.isoformat(), request.date_to.isoformat()]
        return fake_market_sync(self, request)

    def capture_cashflow(self, request):
        captured["cashflow_bond_ids"] = request.bond_ids
        return fake_cashflow_sync(self, request)

    monkeypatch.setattr(MoexMarketDataService, "sync", capture_market)
    monkeypatch.setattr(MoexCashflowService, "sync", capture_cashflow)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "steps": ["moex_market_sync", "moex_cashflow_sync"],
        },
    )

    assert response.status_code == 200
    assert captured["market_bond_ids"] == [bond.id]
    assert captured["cashflow_bond_ids"] == [bond.id]
    assert captured["market_dates"] == ["2025-01-10", "2025-01-10"]


def test_credit_health_and_bond_risk_steps_create_snapshots(
    client: TestClient,
    db_session: Session,
) -> None:
    company, bond = create_demo_context(db_session)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "steps": ["credit_health", "bond_risk_assessment"],
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["credit_health_calculated"] == 1
    assert response.json()["summary"]["bond_risk_assessments_calculated"] == 1
    assert db_session.execute(
        select(CompanyCreditHealthSnapshot).where(
            CompanyCreditHealthSnapshot.company_id == company.id
        )
    ).scalar_one_or_none() is not None
    assert db_session.execute(
        select(BondRiskAssessment).where(BondRiskAssessment.bond_id == bond.id)
    ).scalar_one_or_none() is not None


def test_dataset_and_label_steps_create_all_return_methods(
    client: TestClient,
    db_session: Session,
) -> None:
    _, bond = create_demo_context(db_session)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "benchmark_return": "0.005",
            "steps": [
                "credit_health",
                "bond_risk_assessment",
                "dataset_build_price",
                "labels_total_return",
                "labels_risk_adjusted",
            ],
            "rebuild_existing": True,
        },
    )

    assert response.status_code == 200
    labels = list(
        db_session.execute(
            select(BondReturnLabel).where(BondReturnLabel.bond_id == bond.id)
        ).scalars()
    )
    methods = {label.return_method for label in labels}
    assert {"price", "total_return", "risk_adjusted"} <= methods
    assert response.json()["summary"]["price_labels_created"] == 1
    assert response.json()["summary"]["total_return_labels_created"] == 1
    assert response.json()["summary"]["risk_adjusted_labels_created"] == 1


def test_ml_request_auto_inserts_readiness_before_train(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    monkeypatch.setattr(MoexMarketDataService, "sync", fake_market_sync)
    monkeypatch.setattr(MoexCashflowService, "sync", fake_cashflow_sync)
    monkeypatch.setattr(
        DataPipelineService,
        "_data_readiness_check",
        fake_readiness_step("ready"),
    )
    monkeypatch.setattr(DataPipelineService, "_ml_train", fake_ml_train)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "return_methods": ["price"],
            "run_ml": True,
            "readiness_min_rows": 1,
        },
    )

    assert response.status_code == 200
    step_names = [step["step_name"] for step in response.json()["run"]["steps"]]
    assert "data_readiness_check" in step_names
    assert step_names.index("data_readiness_check") < step_names.index("ml_train")


def test_data_only_pipeline_does_not_auto_insert_readiness(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    monkeypatch.setattr(MoexMarketDataService, "sync", fake_market_sync)
    monkeypatch.setattr(MoexCashflowService, "sync", fake_cashflow_sync)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "return_methods": ["price"],
        },
    )

    assert response.status_code == 200
    step_names = [step["step_name"] for step in response.json()["run"]["steps"]]
    assert "data_readiness_check" not in step_names
    assert "ml_train" not in step_names


def test_explicit_readiness_true_inserts_before_ml(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    monkeypatch.setattr(
        DataPipelineService,
        "_data_readiness_check",
        fake_readiness_step("ready"),
    )
    monkeypatch.setattr(DataPipelineService, "_ml_train", fake_ml_train)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "steps": ["dataset_build_price", "ml_train"],
            "run_readiness_check": True,
            "readiness_min_rows": 1,
        },
    )

    assert response.status_code == 200
    step_names = [step["step_name"] for step in response.json()["run"]["steps"]]
    assert step_names == ["dataset_build_price", "data_readiness_check", "ml_train"]


def test_not_ready_readiness_skips_ml_by_default(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        DataPipelineService,
        "_data_readiness_check",
        fake_readiness_step("not_ready"),
    )

    def fail_if_called(self, request, bond_ids, company_ids):
        raise AssertionError("ML train should have been skipped")

    monkeypatch.setattr(DataPipelineService, "_ml_train", fail_if_called)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "steps": ["data_readiness_check", "ml_train"],
            "run_ml": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_errors"
    assert payload["summary"]["readiness_status"] == "not_ready"
    assert payload["summary"]["ready_for_ml_training"] is False
    assert payload["summary"]["readiness_failed_gates"] == ["evaluable_rows"]
    assert payload["summary"]["readiness_evaluable_rows"] == 0
    assert payload["summary"]["readiness_positive_rows"] == 0
    assert payload["summary"]["readiness_negative_rows"] == 0
    assert payload["summary"]["readiness_insufficient_ratio"] == "0.10"
    steps = payload["run"]["steps"]
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "skipped"
    assert steps[1]["warnings_json"][0]["message"] == (
        "ML steps skipped because dataset readiness is not_ready"
    )


def test_warning_readiness_proceeds_when_allowed(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        DataPipelineService,
        "_data_readiness_check",
        fake_readiness_step("warning"),
    )
    monkeypatch.setattr(DataPipelineService, "_ml_train", fake_ml_train)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "steps": ["data_readiness_check", "ml_train"],
            "run_ml": True,
            "allow_readiness_warning": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_errors"
    assert payload["summary"]["readiness_status"] == "warning"
    assert payload["summary"]["readiness_warning_gates"] == ["cashflow_coverage"]
    assert payload["run"]["steps"][1]["status"] == "completed"
    assert payload["summary"]["ml_model_run_id"] == 777


def test_warning_readiness_skips_ml_when_disallowed(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        DataPipelineService,
        "_data_readiness_check",
        fake_readiness_step("warning"),
    )
    monkeypatch.setattr(DataPipelineService, "_ml_train", fake_ml_train)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "steps": ["data_readiness_check", "ml_train"],
            "run_ml": True,
            "allow_readiness_warning": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_errors"
    assert payload["run"]["steps"][1]["status"] == "skipped"
    assert payload["run"]["steps"][1]["warnings_json"][0]["message"] == (
        "ML steps skipped because readiness status is warning"
    )


def test_readiness_disabled_allows_ml_with_warning(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(DataPipelineService, "_ml_train", fake_ml_train)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "steps": ["ml_train"],
            "run_readiness_check": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    step_names = [step["step_name"] for step in payload["run"]["steps"]]
    assert step_names == ["ml_train"]
    assert payload["status"] == "completed_with_errors"
    assert payload["warnings"][0]["message"] == (
        "ML steps are running without readiness check"
    )
    assert payload["summary"]["ml_model_run_id"] == 777


def test_pipeline_readiness_validation_errors(client: TestClient) -> None:
    cases = [
        ({"readiness_min_rows": 0}, "readiness_min_rows must be positive"),
        (
            {"readiness_min_positive_rows": -1},
            "readiness_min_positive_rows must be non-negative",
        ),
        (
            {"readiness_min_negative_rows": -1},
            "readiness_min_negative_rows must be non-negative",
        ),
        (
            {"readiness_max_insufficient_ratio": "1.1"},
            "readiness_max_insufficient_ratio must be between 0 and 1",
        ),
        (
            {"readiness_max_bond_issues": 0},
            "readiness_max_bond_issues must be between 1 and 500",
        ),
    ]

    for override, detail in cases:
        payload = {
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            **override,
        }
        response = client.post("/api/pipeline/run", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_step_failure_marks_run_completed_with_errors(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    create_demo_context(db_session)

    def boom(self, request, company_ids):
        raise RuntimeError("synthetic step failure")

    monkeypatch.setattr(DataPipelineService, "_credit_health", boom)

    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "steps": ["credit_health"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_errors"
    assert payload["errors"][0]["message"] == "synthetic step failure"
    assert payload["run"]["steps"][0]["status"] == "failed"


def test_list_get_and_steps_endpoints(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    monkeypatch.setattr(MoexMarketDataService, "sync", fake_market_sync)
    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "steps": ["moex_market_sync"],
        },
    )
    run_id = response.json()["run"]["id"]

    list_response = client.get("/api/pipeline/runs")
    get_response = client.get(f"/api/pipeline/runs/{run_id}")
    steps_response = client.get(f"/api/pipeline/runs/{run_id}/steps")
    missing_response = client.get("/api/pipeline/runs/999999")

    assert list_response.status_code == 200
    assert len(list_response.json()) == 1
    assert get_response.status_code == 200
    assert get_response.json()["id"] == run_id
    assert steps_response.status_code == 200
    assert len(steps_response.json()) == 1
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "Pipeline run not found"


def test_pipeline_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    _, bond = create_demo_context(db_session)
    monkeypatch.setattr(MoexMarketDataService, "sync", fake_market_sync)
    response = client.post(
        "/api/pipeline/run",
        json={
            "date_from": "2025-01-10",
            "date_to": "2025-01-10",
            "bond_ids": [bond.id],
            "steps": ["moex_market_sync"],
        },
    )

    assert_no_forbidden_investment_vocabulary(response.json())

