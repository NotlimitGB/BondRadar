from datetime import date, datetime, timezone
from decimal import Decimal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(db: Session, ticker: str = "PPT") -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def create_bond(db: Session, company: Company, index: int) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000PPT{index:03d}",
        secid=f"PPT{index:03d}",
        name=f"Paper Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def create_run(db: Session, *, status: str = "completed") -> MLModelRun:
    run = MLModelRun(
        status=status,
        model_type="logistic_regression",
        horizon_days=30,
        features=["bond_score", "company_score", "liquidity_score"],
        target="label_binary",
        train_rows=30,
        test_rows=10,
        positive_rows=20,
        negative_rows=20,
        metrics={},
        feature_importance=[],
        params={"return_method": "risk_adjusted"},
        finished_at=dt(2026, 1, 1),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def add_risk(
    db: Session,
    *,
    bond: Bond,
    as_of_date: date,
    risk_level: str = "low",
    decision_status: str = "eligible_for_analysis",
) -> None:
    db.add(
        BondRiskAssessment(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            assessment_score=85,
            decision_status=decision_status,
            risk_level=risk_level,
            required_risk_premium=Decimal("0.020000"),
            yield_to_maturity=Decimal("12.000"),
            coupon_rate=Decimal("10.000"),
            duration_years=Decimal("2.000"),
            liquidity_score=90,
            volume=Decimal("1000000.00"),
            gates={},
            warnings=[],
            blocking_reasons=[],
            positive_factors=[],
            negative_factors=[],
            missing_data=[],
            explanation={},
        )
    )
    db.commit()


def add_prediction(
    db: Session,
    *,
    run: MLModelRun,
    bond: Bond,
    as_of_date: date,
    probability: Decimal,
    liquidity_score: int = 90,
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=liquidity_score,
        volume=Decimal("1000000.00"),
        missing_data_count=0,
        features_json={},
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
            horizon_days=run.horizon_days,
            probability_positive=probability,
            predicted_label=(
                "predicted_positive_return"
                if probability >= Decimal("0.50")
                else "predicted_negative_return"
            ),
            features={},
            created_at=dt(2026, 1, 2),
        )
    )
    db.commit()


def add_label(
    db: Session,
    *,
    run: MLModelRun,
    bond: Bond,
    as_of_date: date,
    future_return: Decimal,
) -> None:
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=run.horizon_days,
            return_method="risk_adjusted",
            future_return=future_return,
            risk_adjusted_excess_return=future_return,
            required_risk_premium=Decimal("0.020000"),
            label="positive_return" if future_return > 0 else "negative_return",
            label_binary=1 if future_return > 0 else 0,
        )
    )
    db.commit()


def seed_prediction_context(
    db: Session,
) -> tuple[MLModelRun, Company, Bond, Bond]:
    run = create_run(db)
    company = create_company(db)
    bond_a = create_bond(db, company, 1)
    bond_b = create_bond(db, company, 2)
    for bond in (bond_a, bond_b):
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))
    add_prediction(
        db,
        run=run,
        bond=bond_a,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.90"),
    )
    add_prediction(
        db,
        run=run,
        bond=bond_b,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.80"),
    )
    return run, company, bond_a, bond_b


def create_portfolio(client: TestClient, run_id: int | None = None) -> dict:
    payload = {
        "name": "Test paper portfolio",
        "initial_capital": "1000",
        "base_currency": "RUB",
    }
    if run_id is not None:
        payload["model_run_id"] = run_id
    response = client.post("/api/paper-trading/portfolios", json=payload)
    assert response.status_code == 200
    return response.json()


def rebalance(
    client: TestClient,
    portfolio_id: int,
    *,
    as_of_date: str = "2026-01-01",
    cost: str = "0",
    top_n: int = 1,
) -> dict:
    response = client.post(
        f"/api/paper-trading/portfolios/{portfolio_id}/rebalance",
        json={
            "as_of_date": as_of_date,
            "top_n": top_n,
            "max_position_weight": "0.20",
            "max_issuer_weight": "1",
            "transaction_cost_rate": cost,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_create_portfolio_creates_initial_state(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)

    payload = create_portfolio(client, run.id)

    assert payload["cash_balance"] == "1000.000000"
    assert payload["current_value"] == "1000.000000"
    assert payload["return_method"] == "risk_adjusted"
    assert db_session.execute(select(PaperPortfolio)).scalar_one().initial_capital == Decimal("1000.000000")
    assert db_session.execute(select(PaperPortfolioSnapshot)).scalar_one() is not None
    tx = db_session.execute(select(PaperPortfolioTransaction)).scalar_one()
    assert tx.transaction_type == "portfolio_created"


def test_create_portfolio_model_run_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/paper-trading/portfolios",
        json={"name": "Missing", "initial_capital": "1000", "model_run_id": 999999},
    )
    non_completed = client.post(
        "/api/paper-trading/portfolios",
        json={"name": "Running", "initial_capital": "1000", "model_run_id": running.id},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_rebalance_creates_active_positions_transactions_and_snapshot(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bond_a, _ = seed_prediction_context(db_session)
    portfolio = create_portfolio(client, run.id)

    result = rebalance(client, portfolio["id"])

    assert result["selected_positions"][0]["bond_id"] == bond_a.id
    assert result["selected_positions"][0]["is_active"] is True
    assert Decimal(str(result["snapshot"]["allocated_weight"])) > 0
    tx_types = {
        tx.transaction_type
        for tx in db_session.execute(select(PaperPortfolioTransaction)).scalars()
    }
    assert "allocation_increase" in tx_types


def test_rebalance_fee_reduces_portfolio_value(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, _ = seed_prediction_context(db_session)
    no_fee = create_portfolio(client, run.id)
    with_fee = create_portfolio(client, run.id)

    no_fee_result = rebalance(client, no_fee["id"], cost="0")
    fee_result = rebalance(client, with_fee["id"], cost="0.01")

    assert Decimal(str(fee_result["fee_amount"])) > 0
    assert Decimal(str(fee_result["portfolio"]["current_value"])) < Decimal(
        str(no_fee_result["portfolio"]["current_value"])
    )
    tx_types = [
        tx.transaction_type
        for tx in db_session.execute(
            select(PaperPortfolioTransaction).where(
                PaperPortfolioTransaction.portfolio_id == with_fee["id"]
            )
        ).scalars()
    ]
    assert "rebalance_fee" in tx_types


def test_rebalance_deactivates_removed_positions(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bond_a, bond_b = seed_prediction_context(db_session)
    add_prediction(
        db_session,
        run=run,
        bond=bond_a,
        as_of_date=date(2026, 2, 1),
        probability=Decimal("0.10"),
    )
    add_prediction(
        db_session,
        run=run,
        bond=bond_b,
        as_of_date=date(2026, 2, 1),
        probability=Decimal("0.95"),
    )
    portfolio = create_portfolio(client, run.id)
    rebalance(client, portfolio["id"], as_of_date="2026-01-01")

    second = rebalance(client, portfolio["id"], as_of_date="2026-02-01")

    assert second["selected_positions"][0]["bond_id"] == bond_b.id
    positions = {
        position.bond_id: position
        for position in db_session.execute(select(PaperPortfolioPosition)).scalars()
    }
    assert positions[bond_a.id].is_active is False
    assert positions[bond_a.id].current_amount == Decimal("0.000000")
    tx_types = {
        tx.transaction_type
        for tx in db_session.execute(select(PaperPortfolioTransaction)).scalars()
    }
    assert "allocation_removed" in tx_types


def test_mark_period_applies_realized_labels(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bond_a, _ = seed_prediction_context(db_session)
    add_label(
        db_session,
        run=run,
        bond=bond_a,
        as_of_date=date(2026, 1, 1),
        future_return=Decimal("0.100000"),
    )
    portfolio = create_portfolio(client, run.id)
    rebalance(client, portfolio["id"], as_of_date="2026-01-01")

    response = client.post(
        f"/api/paper-trading/portfolios/{portfolio['id']}/mark-period",
        json={"as_of_date": "2026-01-01"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["transactions"][0]["transaction_type"] == "period_return"
    assert Decimal(str(payload["updated_positions"][0]["current_amount"])) == Decimal("220.000000")
    assert payload["snapshot"]["as_of_date"] == "2026-01-31"
    assert Decimal(str(payload["portfolio"]["current_value"])) > Decimal("1000")


def test_missing_labels_partial_and_strict_modes(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bond_a, _ = seed_prediction_context(db_session)
    add_label(
        db_session,
        run=run,
        bond=bond_a,
        as_of_date=date(2026, 1, 1),
        future_return=Decimal("0.050000"),
    )
    portfolio = create_portfolio(client, run.id)
    rebalance(client, portfolio["id"], as_of_date="2026-01-01", top_n=2)

    strict = client.post(
        f"/api/paper-trading/portfolios/{portfolio['id']}/mark-period",
        json={"as_of_date": "2026-01-01", "allow_partial": False},
    )
    partial = client.post(
        f"/api/paper-trading/portfolios/{portfolio['id']}/mark-period",
        json={"as_of_date": "2026-01-01", "allow_partial": True},
    )

    assert strict.status_code == 400
    assert strict.json()["detail"] == "Missing realized labels for active positions"
    assert partial.status_code == 200
    assert partial.json()["warnings"]


def test_no_active_positions_mark_period_error(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    portfolio = create_portfolio(client, run.id)

    response = client.post(
        f"/api/paper-trading/portfolios/{portfolio['id']}/mark-period",
        json={"as_of_date": "2026-01-01"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Paper portfolio has no active positions"


def test_list_and_read_endpoints(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, _ = seed_prediction_context(db_session)
    portfolio = create_portfolio(client, run.id)
    rebalance(client, portfolio["id"])

    assert client.get("/api/paper-trading/portfolios").status_code == 200
    assert client.get(f"/api/paper-trading/portfolios/{portfolio['id']}").status_code == 200
    assert client.get(f"/api/paper-trading/portfolios/{portfolio['id']}/positions").status_code == 200
    assert client.get(f"/api/paper-trading/portfolios/{portfolio['id']}/transactions").status_code == 200
    assert client.get(f"/api/paper-trading/portfolios/{portfolio['id']}/snapshots").status_code == 200


def test_archived_portfolio_cannot_rebalance(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, _ = seed_prediction_context(db_session)
    portfolio = create_portfolio(client, run.id)
    stored = db_session.get(PaperPortfolio, portfolio["id"])
    stored.status = "archived"
    db_session.commit()

    response = client.post(
        f"/api/paper-trading/portfolios/{portfolio['id']}/rebalance",
        json={},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Paper portfolio is archived"


def test_paper_trading_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bond_a, _ = seed_prediction_context(db_session)
    add_label(
        db_session,
        run=run,
        bond=bond_a,
        as_of_date=date(2026, 1, 1),
        future_return=Decimal("0.010000"),
    )
    portfolio = create_portfolio(client, run.id)
    rebalance_payload = rebalance(client, portfolio["id"])
    mark_payload = client.post(
        f"/api/paper-trading/portfolios/{portfolio['id']}/mark-period",
        json={"as_of_date": "2026-01-01"},
    ).json()
    list_payload = client.get("/api/paper-trading/portfolios").json()

    assert_no_forbidden_investment_vocabulary(
        [portfolio, rebalance_payload, mark_payload, list_payload]
    )

