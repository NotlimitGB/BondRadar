from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
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
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
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
from app.services.feature_snapshot_service import FeatureSnapshotService
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


LIVE_READINESS_URL = "/api/data-readiness/live"


def count_rows(db: Session, model: type) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def today() -> date:
    return datetime.now(timezone.utc).date()


def create_company(db: Session, index: int) -> Company:
    company = Company(
        name=f"Live Data Company {index}",
        ticker=f"LDR{index:04d}",
        inn=f"77{index:010d}"[:12],
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(
    db: Session,
    company: Company,
    index: int,
    *,
    name: str | None = None,
    isin: str | None = None,
    secid: str | None = None,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin or f"RU000LDR{index:03d}"[:12],
        secid=secid or f"LDR{index:05d}",
        name=name or f"Live Data Corporate Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        current_price=Decimal("100.000"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        maturity_date=date(2030, 1, 1),
        liquidity_score=80,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def create_financial_report(db: Session, company: Company) -> FinancialReport:
    report = FinancialReport(
        company_id=company.id,
        period_year=today().year,
        period_quarter=0,
        period_end_date=today(),
        revenue=Decimal("1000.00"),
        ebitda=Decimal("250.00"),
        net_debt=Decimal("300.00"),
        total_debt=Decimal("400.00"),
        cash=Decimal("120.00"),
        equity=Decimal("800.00"),
        short_term_debt=Decimal("100.00"),
        operating_cash_flow=Decimal("180.00"),
        net_profit=Decimal("100.00"),
        interest_expense=Decimal("50.00"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(report)
    db.flush()
    return report


def create_model_run(db: Session, *, index: int, created_at: datetime) -> MLModelRun:
    run = MLModelRun(
        status="completed",
        model_type="logistic_regression",
        horizon_days=30,
        features=["yield_to_maturity", "duration_years"],
        target="risk_adjusted",
        train_rows=100,
        test_rows=20,
        positive_rows=60,
        negative_rows=40,
        metrics={"accuracy": 0.7},
        feature_importance=[],
        params={},
        artifact_path=f"/tmp/live-data-model-{index}.joblib",
        created_at=created_at,
    )
    db.add(run)
    db.flush()
    return run


def add_data_chain(
    db: Session,
    bonds: list[Bond],
    *,
    as_of_date: date,
    run: MLModelRun,
    reports_by_company: dict[int, FinancialReport],
) -> None:
    for index, bond in enumerate(bonds, start=1):
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
                source="live-readiness-test",
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
                source="live-readiness-test",
                raw_payload={"test": True},
            )
        )
        feature = BondFeatureSnapshot(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            financial_report_id=reports_by_company[bond.company_id].id,
            yield_to_maturity=Decimal("12.000"),
            duration_years=Decimal("2.000"),
            liquidity_score=80,
            volume=Decimal("1000000.00"),
            net_debt_to_ebitda=Decimal("1.200"),
            debt_to_equity=Decimal("0.500"),
            interest_coverage=Decimal("5.000"),
            cash_to_short_term_debt=Decimal("1.100"),
            ocf_to_total_debt=Decimal("0.300"),
            net_profit_margin=Decimal("0.100"),
            missing_data_count=0,
            features_json={"test": True},
        )
        db.add(feature)
        db.flush()
        db.add(
            MLPrediction(
                model_run_id=run.id,
                feature_snapshot_id=feature.id,
                bond_id=bond.id,
                company_id=bond.company_id,
                as_of_date=as_of_date,
                horizon_days=30,
                probability_positive=Decimal("0.6500000000"),
                predicted_label=(
                    "predicted_positive_return"
                    if index % 2
                    else "predicted_negative_return"
                ),
                features={"test": True},
            )
        )
    db.commit()


def seed_dataset(
    db: Session,
    *,
    corporate_count: int,
    ofz_count: int = 0,
    as_of_date: date | None = None,
) -> tuple[list[Bond], list[Bond], MLModelRun]:
    data_date = as_of_date or today()
    run = create_model_run(
        db,
        index=corporate_count + ofz_count,
        created_at=datetime.combine(data_date, datetime.min.time(), tzinfo=timezone.utc),
    )
    corporate_bonds: list[Bond] = []
    ofz_bonds: list[Bond] = []
    for index in range(1, corporate_count + 1):
        company = create_company(db, index)
        corporate_bonds.append(create_bond(db, company, index))
    for index in range(1, ofz_count + 1):
        company = create_company(db, 1000 + index)
        ofz_bonds.append(
            create_bond(
                db,
                company,
                1000 + index,
                name=f"ОФЗ live data {index}",
                isin=f"SU{index:010d}",
                secid=f"OFZ{index:05d}",
            )
        )
    reports_by_company = {
        bond.company_id: create_financial_report(db, bond.company)
        for bond in corporate_bonds + ofz_bonds
    }
    add_data_chain(
        db,
        corporate_bonds + ofz_bonds,
        as_of_date=data_date,
        run=run,
        reports_by_company=reports_by_company,
    )
    return corporate_bonds, ofz_bonds, run


def check_by_name(payload: dict, name: str) -> dict:
    return next(check for check in payload["checks"] if check["name"] == name)


def core_counts(db: Session) -> dict[str, int]:
    models = [
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
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
        PaperLiveSchedule,
        PaperLiveCycleRun,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


def test_empty_database_returns_not_ready(client: TestClient) -> None:
    response = client.get(
        LIVE_READINESS_URL,
        params={
            "minimum_corporate_bonds": 1,
            "minimum_bonds_with_recent_market_snapshot": 1,
            "minimum_bonds_with_recent_features": 1,
            "minimum_bonds_with_predictions": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["corporate_bond_count"] == 0
    assert check_by_name(payload, "corporate_universe_available")["status"] == "failed"
    assert check_by_name(payload, "completed_model_run_available")["status"] == "failed"
    assert payload["next_steps"]


def test_stale_data_returns_warning(client: TestClient, db_session: Session) -> None:
    stale_date = today() - timedelta(days=40)
    seed_dataset(db_session, corporate_count=2, as_of_date=stale_date)

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "recent_days": 7,
            "minimum_corporate_bonds": 2,
            "minimum_bonds_with_recent_market_snapshot": 2,
            "minimum_bonds_with_recent_features": 2,
            "minimum_bonds_with_predictions": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert check_by_name(payload, "recent_market_snapshots_available")["status"] == "warning"
    assert check_by_name(payload, "recent_feature_snapshots_available")["status"] == "warning"
    assert check_by_name(payload, "recent_predictions_available")["status"] == "warning"


def test_enough_recent_corporate_data_returns_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    _, _, run = seed_dataset(db_session, corporate_count=3, as_of_date=today())

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "recent_days": 7,
            "minimum_corporate_bonds": 3,
            "minimum_bonds_with_recent_market_snapshot": 3,
            "minimum_bonds_with_recent_features": 3,
            "minimum_bonds_with_predictions": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["latest_completed_model_run_id"] == run.id
    assert check_by_name(payload, "paper_pilot_data_ready")["status"] == "passed"


def test_ofz_are_excluded_by_default(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(db_session, corporate_count=1, ofz_count=2, as_of_date=today())

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "minimum_corporate_bonds": 1,
            "minimum_bonds_with_recent_market_snapshot": 1,
            "minimum_bonds_with_recent_features": 1,
            "minimum_bonds_with_predictions": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["corporate_bond_count"] == 1
    assert payload["ofz_bond_count"] == 2
    assert payload["working_bond_count"] == 1
    assert payload["market_snapshot_count"] == 1
    assert payload["bonds_with_predictions_for_latest_run_count"] == 1


def test_include_ofz_expands_working_universe_counts(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(db_session, corporate_count=1, ofz_count=2, as_of_date=today())

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "include_ofz": True,
            "minimum_corporate_bonds": 1,
            "minimum_bonds_with_recent_market_snapshot": 3,
            "minimum_bonds_with_recent_features": 3,
            "minimum_bonds_with_predictions": 3,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["corporate_bond_count"] == 1
    assert payload["ofz_bond_count"] == 2
    assert payload["working_bond_count"] == 3
    assert payload["market_snapshot_count"] == 3
    assert payload["bonds_with_recent_market_snapshot_count"] == 3
    assert payload["bonds_with_predictions_for_latest_run_count"] == 3


def test_validation_errors(client: TestClient) -> None:
    cases = [
        ({"recent_days": 0}, "recent_days must be between 1 and 365"),
        ({"recent_days": 366}, "recent_days must be between 1 and 365"),
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
        response = client.get(LIVE_READINESS_URL, params=params)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_no_db_writes(client: TestClient, db_session: Session) -> None:
    seed_dataset(db_session, corporate_count=2, as_of_date=today())
    before = core_counts(db_session)

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "minimum_corporate_bonds": 2,
            "minimum_bonds_with_recent_market_snapshot": 2,
            "minimum_bonds_with_recent_features": 2,
            "minimum_bonds_with_predictions": 2,
        },
    )

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

    monkeypatch.setattr(MoexMarketDataService, "sync", fail_call)
    monkeypatch.setattr(MoexCashflowService, "sync", fail_call)
    monkeypatch.setattr(FeatureSnapshotService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_labels", fail_call)
    monkeypatch.setattr(TotalReturnLabelService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(LabelBuilderService, "build_for_bond_date", fail_call)
    monkeypatch.setattr(DatasetBuildService, "build", fail_call)
    monkeypatch.setattr(DataPipelineService, "run", fail_call)
    monkeypatch.setattr(MLTrainingService, "train", fail_call)
    monkeypatch.setattr(MLPredictionService, "predict", fail_call)
    monkeypatch.setattr(PaperTradingService, "rebalance", fail_call)
    monkeypatch.setattr(PaperTradingService, "mark_period", fail_call)
    monkeypatch.setattr(PaperTradingScenarioService, "run", fail_call)
    monkeypatch.setattr(LivePaperCycleService, "run", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_due", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_schedule_once", fail_call)

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "minimum_corporate_bonds": 2,
            "minimum_bonds_with_recent_market_snapshot": 2,
            "minimum_bonds_with_recent_features": 2,
            "minimum_bonds_with_predictions": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_forbidden_vocabulary_helper(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_dataset(db_session, corporate_count=2, as_of_date=today())

    response = client.get(
        LIVE_READINESS_URL,
        params={
            "minimum_corporate_bonds": 2,
            "minimum_bonds_with_recent_market_snapshot": 2,
            "minimum_bonds_with_recent_features": 2,
            "minimum_bonds_with_predictions": 2,
        },
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
