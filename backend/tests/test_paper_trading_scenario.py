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
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_run(
    db: Session,
    *,
    status: str = "completed",
    horizon_days: int = 30,
    return_method: str = "risk_adjusted",
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
        params={"return_method": return_method},
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


def add_prediction_for_existing_feature(
    db: Session,
    *,
    run: MLModelRun,
    feature: BondFeatureSnapshot,
    probability: Decimal = Decimal("0.90"),
) -> None:
    db.add(
        MLPrediction(
            model_run_id=run.id,
            feature_snapshot_id=feature.id,
            bond_id=feature.bond_id,
            company_id=feature.company_id,
            as_of_date=feature.as_of_date,
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
    return_method = (run.params or {}).get("return_method") or "price"
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=run.horizon_days,
            return_method=return_method,
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


def seed_multi_run_scenario(
    db: Session,
    *,
    first_date: date = date(2026, 1, 1),
    second_date: date = date(2026, 2, 1),
    first_horizon_days: int = 30,
    second_horizon_days: int = 30,
    first_return_method: str = "risk_adjusted",
    second_return_method: str = "risk_adjusted",
    labels: bool = True,
    ticker: str = "PMR",
) -> tuple[MLModelRun, MLModelRun, Bond]:
    first_run = create_run(
        db,
        horizon_days=first_horizon_days,
        return_method=first_return_method,
    )
    second_run = create_run(
        db,
        horizon_days=second_horizon_days,
        return_method=second_return_method,
    )
    company = create_company(db, ticker)
    bond = create_bond(db, company, 1)
    add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))
    add_prediction(db, run=first_run, bond=bond, as_of_date=first_date)
    add_prediction(db, run=second_run, bond=bond, as_of_date=second_date)
    if labels:
        add_label(db, run=first_run, bond=bond, as_of_date=first_date)
        add_label(db, run=second_run, bond=bond, as_of_date=second_date)
    return first_run, second_run, bond


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
        "max_high_risk_weight": "0.20",
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
    assert payload["model_run_id"] == run.id
    assert payload["model_run_ids"] == [run.id]
    assert payload["model_run_count"] == 1
    assert payload["prediction_source_mode"] == "single_model_run"
    assert all(cycle["model_run_id"] == run.id for cycle in payload["cycles"])
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


def test_multi_run_scenario_uses_predictions_from_all_completed_runs(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            first_run,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] is None
    assert payload["model_run_ids"] == [first_run.id, second_run.id]
    assert payload["model_run_count"] == 2
    assert payload["prediction_source_mode"] == "multiple_model_runs"
    assert [cycle["as_of_date"] for cycle in payload["cycles"]] == [
        "2026-01-01",
        "2026-02-01",
    ]
    assert [cycle["model_run_id"] for cycle in payload["cycles"]] == [
        first_run.id,
        second_run.id,
    ]


def test_multi_run_label_dates_use_shared_horizon_gap(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_scenario(
        db_session,
        first_date=date(2026, 1, 1),
        second_date=date(2026, 1, 10),
    )

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            first_run,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycles_requested"] == 1
    assert payload["cycles"][0]["as_of_date"] == "2026-01-01"
    assert payload["cycles"][0]["model_run_id"] == first_run.id


def test_multi_run_weekly_and_monthly_modes_resolve_cycle_model_run(
    client: TestClient,
    db_session: Session,
) -> None:
    weekly_first, weekly_second, _ = seed_multi_run_scenario(
        db_session,
        first_date=date(2026, 1, 1),
        second_date=date(2026, 1, 8),
        ticker="PMW",
    )
    weekly = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            weekly_first,
            model_run_id=None,
            model_run_ids=[weekly_first.id, weekly_second.id],
            rebalance_frequency="weekly",
        ),
    )

    monthly_first, monthly_second, _ = seed_multi_run_scenario(
        db_session,
        first_date=date(2026, 3, 1),
        second_date=date(2026, 4, 1),
        ticker="PMM",
    )
    monthly = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            monthly_first,
            model_run_id=None,
            model_run_ids=[monthly_first.id, monthly_second.id],
            rebalance_frequency="monthly",
        ),
    )

    assert weekly.status_code == 200
    assert monthly.status_code == 200
    assert [cycle["model_run_id"] for cycle in weekly.json()["cycles"]] == [
        weekly_first.id,
        weekly_second.id,
    ]
    assert [cycle["model_run_id"] for cycle in monthly.json()["cycles"]] == [
        monthly_first.id,
        monthly_second.id,
    ]


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


def test_multi_run_requires_compatible_horizon_and_return_method(
    client: TestClient,
    db_session: Session,
) -> None:
    horizon_first, horizon_second, _ = seed_multi_run_scenario(
        db_session,
        first_horizon_days=30,
        second_horizon_days=60,
    )
    horizon_response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            horizon_first,
            model_run_id=None,
            model_run_ids=[horizon_first.id, horizon_second.id],
        ),
    )

    method_first, method_second, _ = seed_multi_run_scenario(
        db_session,
        first_return_method="risk_adjusted",
        second_return_method="total_return",
        ticker="PMT",
    )
    method_response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            method_first,
            model_run_id=None,
            model_run_ids=[method_first.id, method_second.id],
        ),
    )

    assert horizon_response.status_code == 400
    assert (
        horizon_response.json()["detail"]
        == "Model runs must use the same horizon and return method"
    )
    assert method_response.status_code == 400
    assert (
        method_response.json()["detail"]
        == "Model runs must use the same horizon and return method"
    )


def test_multi_run_missing_and_non_completed_model_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    completed = create_run(db_session)
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            completed,
            model_run_id=None,
            model_run_ids=[completed.id, 999999],
        ),
    )
    non_completed = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            completed,
            model_run_id=None,
            model_run_ids=[completed.id, running.id],
        ),
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_scenario_model_selector_validation_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)

    missing_payload = scenario_payload(run)
    missing_payload.pop("model_run_id")
    missing = client.post("/api/paper-trading/scenarios/run", json=missing_payload)
    both = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, model_run_ids=[run.id]),
    )
    empty = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, model_run_id=None, model_run_ids=[]),
    )
    duplicate = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(run, model_run_id=None, model_run_ids=[run.id, run.id]),
    )
    too_many = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            run,
            model_run_id=None,
            model_run_ids=list(range(1, 202)),
        ),
    )

    assert missing.status_code == 400
    assert missing.json()["detail"] == "Provide model_run_id or model_run_ids"
    assert both.status_code == 400
    assert both.json()["detail"] == "Use only one of model_run_id or model_run_ids"
    assert empty.status_code == 400
    assert empty.json()["detail"] == "model_run_ids must not be empty"
    assert duplicate.status_code == 400
    assert duplicate.json()["detail"] == "model_run_ids must not contain duplicates"
    assert too_many.status_code == 400
    assert too_many.json()["detail"] == "model_run_ids must not exceed 200"


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


def test_multi_run_duplicate_same_date_resolves_by_request_order(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run = create_run(db_session)
    second_run = create_run(db_session)
    company = create_company(db_session, "PMD")
    bond = create_bond(db_session, company, 1)
    as_of_date = date(2026, 1, 1)
    add_risk(db_session, bond=bond, as_of_date=date(2025, 12, 31))
    add_prediction(db_session, run=first_run, bond=bond, as_of_date=as_of_date)
    feature = db_session.execute(
        select(BondFeatureSnapshot).where(
            BondFeatureSnapshot.bond_id == bond.id,
            BondFeatureSnapshot.as_of_date == as_of_date,
        )
    ).scalar_one()
    add_prediction_for_existing_feature(
        db_session,
        run=second_run,
        feature=feature,
        probability=Decimal("0.95"),
    )
    add_label(db_session, run=first_run, bond=bond, as_of_date=as_of_date)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            first_run,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cycles"][0]["model_run_id"] == second_run.id
    assert any(
        warning["message"]
        == "Duplicate walk-forward prediction dates were resolved by model_run_ids order"
        for warning in payload["warnings"]
    )


def test_multi_run_new_portfolio_uses_primary_model_run_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            first_run,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
            date_to="2026-01-01",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    portfolio = db_session.get(PaperPortfolio, payload["portfolio_id"])
    assert portfolio is not None
    assert portfolio.model_run_id == first_run.id
    assert any("primary model run id" in warning["message"] for warning in payload["warnings"])


def test_multi_run_existing_portfolio_reuse_keeps_portfolio_and_warns(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_scenario(db_session)
    portfolio = create_portfolio(client, first_run)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            first_run,
            portfolio_id=portfolio["id"],
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] == portfolio["id"]
    assert any("appended" in warning["message"] for warning in payload["warnings"])
    assert any("fold model runs" in warning["message"] for warning in payload["warnings"])


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


def test_multi_run_performance_report_included_when_requested(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_scenario(db_session)

    response = client.post(
        "/api/paper-trading/scenarios/run",
        json=scenario_payload(
            first_run,
            model_run_id=None,
            model_run_ids=[first_run.id, second_run.id],
            include_performance_report=True,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["performance_report"] is not None
    assert payload["summary"]["snapshot_count"] == payload["performance_report"]["metrics"]["snapshot_count"]


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
    assert_no_forbidden_investment_vocabulary(response.json())

