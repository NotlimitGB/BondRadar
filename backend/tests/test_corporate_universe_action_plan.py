from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
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
from app.services.data_pipeline_service import DataPipelineService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_bond_universe_service import MoexBondUniverseService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_iss_client import MoexIssClient
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService
from app.services.paper_trading_service import PaperTradingService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary
from tests.test_live_data_readiness import count_rows, create_bond, create_company


ACTION_PLAN_URL = "/api/data-readiness/corporate-universe/action-plan"


def check_by_name(payload: dict, name: str) -> dict:
    return next(check for check in payload["checks"] if check["name"] == name)


def action_by_name(payload: dict, name: str) -> dict:
    return next(action for action in payload["actions"] if action["name"] == name)


def core_counts(db: Session) -> dict[str, int]:
    models = [
        Bond,
        Company,
        BondMarketSnapshot,
        BondCashflowEvent,
        BondFeatureSnapshot,
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
    return {model.__name__: count_rows(db, model) for model in models}


def seed_corporate_bonds(
    db: Session,
    *,
    count: int,
    missing_secid: bool = False,
    missing_isin: bool = False,
) -> list[Bond]:
    bonds: list[Bond] = []
    for index in range(1, count + 1):
        company = create_company(db, index)
        if missing_secid or missing_isin:
            bond = Bond(
                company_id=company.id,
                isin=None if missing_isin and index == 1 else f"RU000CU{index:05d}"[:12],
                secid=None if missing_secid and index == 1 else f"CU{index:06d}",
                name=f"Corporate Universe Bond {index}",
                currency="RUB",
                nominal_value=Decimal("1000.00"),
                current_price=Decimal("100.000"),
                coupon_rate=Decimal("10.000"),
                yield_to_maturity=Decimal("12.000"),
                duration_years=Decimal("2.000"),
                volume=Decimal("1000000.00"),
                maturity_date=date(2030, 1, 1),
                liquidity_score=80,
                signal="neutral",
            )
            db.add(bond)
            db.flush()
        else:
            bond = create_bond(db, company, index)
        bonds.append(bond)
    db.commit()
    return bonds


def seed_ofz_bonds(db: Session, *, count: int) -> list[Bond]:
    bonds: list[Bond] = []
    for index in range(1, count + 1):
        company = create_company(db, 1000 + index)
        bonds.append(
            create_bond(
                db,
                company,
                1000 + index,
                name=f"ОФЗ universe {index}",
                isin=f"SU{index:010d}",
                secid=f"OFZ{index:05d}",
            )
        )
    db.commit()
    return bonds


def test_empty_db_recommends_moex_universe_sync(client: TestClient) -> None:
    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 2},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_sync"
    assert payload["can_sync_universe"] is True
    assert payload["can_continue_to_data_pipeline"] is False
    assert payload["sync_payload"]["board"] == "TQCB"
    assert any(
        command["method"] == "POST"
        and command["path"] == "/api/market-data/moex/bonds/sync"
        for command in payload["commands"]
    )


def test_enough_corporate_bonds_returns_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_corporate_bonds(db_session, count=3)

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["can_continue_to_data_pipeline"] is True
    assert action_by_name(payload, "run_live_data_action_plan")["status"] == "recommended"
    assert check_by_name(payload, "bond_secid_coverage")["status"] == "passed"
    assert check_by_name(payload, "bond_isin_coverage")["status"] == "passed"


def test_ofz_separated_by_default(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_corporate_bonds(db_session, count=1)
    seed_ofz_bonds(db_session, count=2)

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 1},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["local_total_bond_count"] == 3
    assert payload["local_corporate_bond_count"] == 1
    assert payload["local_ofz_bond_count"] == 2
    assert payload["local_working_bond_count"] == 1
    assert len(payload["sample_ofz_bonds"]) == 2


def test_include_ofz_expands_working_universe(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_corporate_bonds(db_session, count=1)
    seed_ofz_bonds(db_session, count=2)

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 1, "include_ofz": True},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["local_total_bond_count"] == 3
    assert payload["local_corporate_bond_count"] == 1
    assert payload["local_ofz_bond_count"] == 2
    assert payload["local_working_bond_count"] == 3


def test_missing_metadata_creates_warnings_and_needs_sync(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_corporate_bonds(
        db_session,
        count=3,
        missing_secid=True,
        missing_isin=True,
    )

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "needs_sync"
    assert check_by_name(payload, "bond_secid_coverage")["status"] == "warning"
    assert check_by_name(payload, "bond_isin_coverage")["status"] == "warning"
    assert payload["warnings"]


def test_invalid_params_return_exact_400_details(client: TestClient) -> None:
    cases = [
        ({"board": "   "}, "board must not be blank"),
        (
            {"minimum_corporate_bonds": -1},
            "minimum_corporate_bonds must be non-negative",
        ),
        ({"max_pages": 0}, "max_pages must be between 1 and 1000"),
        ({"max_pages": 1001}, "max_pages must be between 1 and 1000"),
        ({"page_size": 0}, "page_size must be between 1 and 500"),
        ({"page_size": 501}, "page_size must be between 1 and 500"),
        ({"sample_limit": 0}, "sample_limit must be between 1 and 100"),
        ({"sample_limit": 101}, "sample_limit must be between 1 and 100"),
    ]
    for params, detail in cases:
        response = client.get(ACTION_PLAN_URL, params=params)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_no_db_writes(client: TestClient, db_session: Session) -> None:
    seed_corporate_bonds(db_session, count=2)
    before = core_counts(db_session)

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 2},
    )

    assert response.status_code == 200
    assert core_counts(db_session) == before


def test_no_forbidden_service_calls(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    seed_corporate_bonds(db_session, count=2)

    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    monkeypatch.setattr(MoexBondUniverseService, "sync", fail_call)
    monkeypatch.setattr(MoexIssClient, "fetch_bond_universe", fail_call)
    monkeypatch.setattr(MoexIssClient, "fetch_bond_description", fail_call)
    monkeypatch.setattr(MoexMarketDataService, "sync", fail_call)
    monkeypatch.setattr(MoexCashflowService, "sync", fail_call)
    monkeypatch.setattr(DataPipelineService, "run", fail_call)
    monkeypatch.setattr(MLTrainingService, "train", fail_call)
    monkeypatch.setattr(MLPredictionService, "predict", fail_call)
    monkeypatch.setattr(PaperTradingService, "rebalance", fail_call)
    monkeypatch.setattr(LivePaperCycleService, "run", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_due", fail_call)

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 2},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_forbidden_vocabulary_helper(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_corporate_bonds(db_session, count=2)

    response = client.get(
        ACTION_PLAN_URL,
        params={"minimum_corporate_bonds": 2},
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
