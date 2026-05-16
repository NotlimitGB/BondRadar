import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal

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
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_run(
    db: Session,
    *,
    status: str = "completed",
    horizon_days: int = 30,
) -> MLModelRun:
    run = MLModelRun(
        status=status,
        model_type="logistic_regression",
        horizon_days=horizon_days,
        features=["bond_score", "company_score", "liquidity_score"],
        target="label_binary",
        train_rows=50,
        test_rows=10,
        positive_rows=30,
        negative_rows=30,
        metrics={},
        feature_importance=[],
        params={"return_method": "risk_adjusted"},
        finished_at=dt(2026, 1, 1),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def create_company(db: Session, ticker: str = "PSC") -> Company:
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
    prefix = company.ticker
    bond = Bond(
        company_id=company.id,
        isin=f"RU000{prefix}{index:03d}"[:12],
        secid=f"{prefix}{index:03d}",
        name=f"Scenario Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def add_risk(db: Session, *, bond: Bond, as_of_date: date) -> None:
    db.add(
        BondRiskAssessment(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            assessment_score=85,
            decision_status="eligible_for_analysis",
            risk_level="low",
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
    probability: Decimal = Decimal("0.90"),
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=90,
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
    future_return: Decimal = Decimal("0.050000"),
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


def seed_scenario(
    db: Session,
    *,
    dates: list[date] | None = None,
    labels: bool = True,
    bonds_count: int = 1,
    ticker: str = "PSC",
) -> tuple[MLModelRun, list[Bond], list[date]]:
    run = create_run(db)
    company = create_company(db, ticker)
    bonds = [create_bond(db, company, index + 1) for index in range(bonds_count)]
    selected_dates = dates or [date(2026, 1, 1), date(2026, 2, 1)]
    for bond in bonds:
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))
    for as_of_date in selected_dates:
        for index, bond in enumerate(bonds):
            add_prediction(
                db,
                run=run,
                bond=bond,
                as_of_date=as_of_date,
                probability=Decimal("0.90") - Decimal(index) * Decimal("0.01"),
            )
            if labels:
                add_label(
                    db,
                    run=run,
                    bond=bond,
                    as_of_date=as_of_date,
                    future_return=Decimal("0.050000"),
                )
    return run, bonds, selected_dates


def create_portfolio(client: TestClient, run: MLModelRun) -> dict:
    response = client.post(
        "/api/paper-trading/portfolios",
        json={
            "name": "Existing scenario portfolio",
            "initial_capital": "1000",
            "base_currency": "RUB",
            "model_run_id": run.id,
        },
    )
    assert response.status_code == 200
    return response.json()


def scenario_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "name": "Scenario test",
        "initial_capital": "1000",
        "model_run_id": run.id,
        "top_n": 1,
        "max_position_weight": "0.20",
        "max_issuer_weight": "1",
        "max_high_risk_weight": "1",
        "transaction_cost_rate": "0",
        "include_performance_report": True,
    }
    payload.update(overrides)
    return payload


def test_scenario_creates_portfolio_when_omitted(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] is not None
    assert payload["cycles_completed"] == 2
    assert payload["rebalance_success_count"] == 2
    assert payload["mark_success_count"] == 2
    assert db_session.execute(select(PaperPortfolio)).scalars().first() is not None
    assert db_session.execute(select(PaperPortfolioTransaction)).scalars().first() is not None
    assert db_session.execute(select(PaperPortfolioSnapshot)).scalars().first() is not None


def test_scenario_reuses_existing_portfolio(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session)
    portfolio = create_portfolio(client, run)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, portfolio_id=portfolio["id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] == portfolio["id"]
    assert len(db_session.execute(select(PaperPortfolio)).scalars().all()) == 1
    assert any("appended" in warning["message"] for warning in payload["warnings"])


def test_label_dates_use_horizon_gap(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(
        db_session,
        dates=[date(2026, 1, 1), date(2026, 1, 10), date(2026, 2, 1)],
    )

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycles_requested"] == 2
    assert [cycle["as_of_date"] for cycle in payload["cycles"]] == [
        "2026-01-01",
        "2026-02-01",
    ]


def test_weekly_and_monthly_date_modes(
    client: TestClient,
    db_session: Session,
) -> None:
    weekly_run, _, _ = seed_scenario(
        db_session,
        dates=[date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 8)],
    )
    weekly = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(weekly_run, rebalance_frequency="weekly"),
    )

    monthly_run, _, _ = seed_scenario(
        db_session,
        dates=[date(2026, 3, 1), date(2026, 3, 15), date(2026, 4, 1)],
        ticker="PSM",
    )
    monthly = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(monthly_run, rebalance_frequency="monthly"),
    )

    assert weekly.status_code == 200
    assert monthly.status_code == 200
    assert weekly.json()["cycles_requested"] == 2
    assert monthly.json()["cycles_requested"] == 2


def test_mark_period_applies_labels_across_cycles(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mark_success_count"] == 2
    assert Decimal(str(payload["final_portfolio_value"])) > Decimal("1000")


def test_missing_labels_partial_mode_warns_and_continues(
    client: TestClient,
    db_session: Session,
) -> None:
    run, bonds, dates = seed_scenario(
        db_session,
        labels=False,
        bonds_count=2,
    )
    add_label(db_session, run=run, bond=bonds[0], as_of_date=dates[0])
    add_label(db_session, run=run, bond=bonds[0], as_of_date=dates[1])

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            run,
            top_n=2,
            allow_partial_marking=True,
            max_position_weight="0.20",
            max_issuer_weight="1",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["mark_success_count"] == 2
    assert any("Missing realized label" in warning["message"] for warning in payload["warnings"])


def test_missing_labels_strict_mode_records_mark_failure(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session, labels=False)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, allow_partial_marking=False),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["rebalance_success_count"] == 2
    assert payload["mark_failed_count"] == 2
    assert payload["cycles"][0]["mark_status"] == "failed"


def test_stop_on_mark_error_stops_scenario(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session, labels=False)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            run,
            allow_partial_marking=False,
            stop_on_mark_error=True,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycles_requested"] == 2
    assert len(payload["cycles"]) == 1
    assert payload["mark_failed_count"] == 1


def test_no_predictions_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "No predictions found for selected model run and date range"


def test_missing_and_non_completed_model_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(running, model_run_id=999999),
    )
    non_completed = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(running),
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_archived_portfolio_rejected(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session)
    portfolio = create_portfolio(client, run)
    stored = db_session.get(PaperPortfolio, portfolio["id"])
    stored.status = "archived"
    db_session.commit()

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, portfolio_id=portfolio["id"]),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Paper portfolio is archived"


def test_performance_report_included_when_requested(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, include_performance_report=True),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["performance_report"] is not None
    assert payload["summary"]["snapshot_count"] == payload["performance_report"]["metrics"]["snapshot_count"]
    assert payload["summary"]["total_fee_amount"] == payload["performance_report"]["metrics"]["total_fee_amount"]


def test_scenario_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run),
    )

    assert response.status_code == 200
    text = json.dumps(response.json(), ensure_ascii=False).lower()
    forbidden_patterns = [
        r"\bbuy\b",
        r"\bsell\b",
        r"\bhold\b",
        r"\bstrong_buy\b",
        r"\bstrong_sell\b",
        r"\bmust_buy\b",
        r"\bmust_sell\b",
        r"\bпокупать\b",
        r"\bпродавать\b",
    ]
    assert all(re.search(pattern, text) is None for pattern in forbidden_patterns)
