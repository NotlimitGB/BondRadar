from datetime import date, datetime, timezone
from decimal import Decimal
import json

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
from app.services.data_pipeline_service import DataPipelineService
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

    payload = json.dumps(response.json()).lower()
    forbidden = [
        "buy",
        "sell",
        "hold",
        "strong_buy",
        "strong_sell",
        "must_buy",
        "must_sell",
        "покупать",
        "продавать",
    ]
    assert all(word not in payload for word in forbidden)
