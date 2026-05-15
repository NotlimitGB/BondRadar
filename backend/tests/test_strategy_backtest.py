import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(db: Session, ticker: str = "SBT") -> Company:
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
        isin=f"RU000SBT{index:03d}",
        secid=f"SBT{index:03d}",
        name=f"Strategy Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("10.000"),
        liquidity_score=80,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def create_run(
    db: Session,
    *,
    return_method: str = "risk_adjusted",
    status: str = "completed",
) -> MLModelRun:
    run = MLModelRun(
        status=status,
        model_type="logistic_regression",
        horizon_days=30,
        features=["bond_score", "company_score", "liquidity_score"],
        target="label_binary",
        train_rows=40,
        test_rows=10,
        positive_rows=25,
        negative_rows=25,
        metrics={"accuracy": 0.7},
        feature_importance=[],
        params={"return_method": return_method},
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
    decision_status: str = "eligible_for_analysis",
    risk_level: str = "low",
    liquidity_score: int = 80,
) -> None:
    db.add(
        BondRiskAssessment(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            assessment_score=80,
            decision_status=decision_status,
            risk_level=risk_level,
            required_risk_premium=Decimal("0.020000"),
            yield_to_maturity=Decimal("10.000"),
            coupon_rate=Decimal("10.000"),
            duration_years=Decimal("2.000"),
            liquidity_score=liquidity_score,
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


def add_prediction_and_label(
    db: Session,
    *,
    run: MLModelRun,
    bond: Bond,
    as_of_date: date,
    probability: Decimal,
    future_return: Decimal,
    liquidity_score: int,
    yield_to_maturity: Decimal,
    return_method: str = "risk_adjusted",
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        yield_to_maturity=yield_to_maturity,
        liquidity_score=liquidity_score,
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
            features={"probability": str(probability)},
            created_at=dt(2026, 1, 2),
        )
    )
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=run.horizon_days,
            return_method=return_method,
            future_return=future_return,
            price_return=future_return if return_method == "price" else None,
            net_total_return=(
                future_return if return_method == "total_return" else None
            ),
            risk_adjusted_excess_return=(
                future_return if return_method == "risk_adjusted" else None
            ),
            required_risk_premium=(
                Decimal("0.020000") if return_method == "risk_adjusted" else None
            ),
            label="positive_return" if future_return > 0 else "negative_return",
            label_binary=1 if future_return > 0 else 0,
        )
    )
    db.commit()


def seed_backtest_dataset(
    db: Session,
) -> tuple[MLModelRun, Company, list[Bond]]:
    company = create_company(db)
    bonds = [create_bond(db, company, index) for index in range(1, 5)]
    run = create_run(db)
    dates = [date(2026, 1, 1), date(2026, 2, 1)]
    probabilities = [
        Decimal("0.90"),
        Decimal("0.80"),
        Decimal("0.60"),
        Decimal("0.40"),
    ]
    returns_by_date = [
        [Decimal("0.100000"), Decimal("-0.020000"), Decimal("0.030000"), Decimal("-0.010000")],
        [Decimal("0.050000"), Decimal("0.010000"), Decimal("-0.010000"), Decimal("0.020000")],
    ]
    liquidity = [95, 82, 70, 65]
    yields = [
        Decimal("10.000"),
        Decimal("9.000"),
        Decimal("15.000"),
        Decimal("5.000"),
    ]
    for bond, score in zip(bonds, liquidity):
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31), liquidity_score=score)
    for date_index, as_of_date in enumerate(dates):
        for bond, probability, future_return, score, ytm in zip(
            bonds,
            probabilities,
            returns_by_date[date_index],
            liquidity,
            yields,
        ):
            add_prediction_and_label(
                db,
                run=run,
                bond=bond,
                as_of_date=as_of_date,
                probability=probability,
                future_return=future_return,
                liquidity_score=score,
                yield_to_maturity=ytm,
            )
    return run, company, bonds


def test_backtest_uses_predictions_and_realized_labels(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "initial_capital": "1000",
            "top_n": 2,
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["return_method"] == "risk_adjusted"
    assert payload["horizon_days"] == 30
    assert payload["metrics"]["period_count"] == 2
    assert Decimal(str(payload["final_portfolio_value"])) > Decimal("1000")


def test_model_strategy_selects_highest_probabilities(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "initial_capital": "1000",
            "top_n": 2,
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    selected_ids = [
        candidate["bond_id"]
        for candidate in response.json()["periods"][0]["selected_candidates"]
    ]
    assert selected_ids == [bonds[0].id, bonds[1].id]


def test_transaction_costs_reduce_net_return(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    no_cost = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "initial_capital": "1000",
            "top_n": 2,
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )
    with_cost = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "initial_capital": "1000",
            "top_n": 2,
            "transaction_cost_rate": "0.01",
            "include_baselines": False,
        },
    )

    assert no_cost.status_code == 200
    assert with_cost.status_code == 200
    assert Decimal(str(with_cost.json()["final_portfolio_value"])) < Decimal(
        str(no_cost.json()["final_portfolio_value"])
    )


def test_max_position_weight_leaves_unallocated_capital(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "initial_capital": "1000",
            "top_n": 2,
            "max_position_weight": "0.25",
            "transaction_cost_rate": "0",
            "include_baselines": False,
            "use_portfolio_constraints": False,
        },
    )

    assert response.status_code == 200
    weights = [
        Decimal(str(candidate["weight"]))
        for candidate in response.json()["periods"][0]["selected_candidates"]
    ]
    assert sum(weights) == Decimal("0.50")


def test_constrained_backtest_applies_issuer_cap(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "top_n": 4,
            "max_position_weight": "0.20",
            "max_issuer_weight": "0.30",
            "max_high_risk_weight": "1",
            "transaction_cost_rate": "0",
            "include_baselines": False,
            "include_excluded_candidates": True,
        },
    )

    assert response.status_code == 200
    first_period = response.json()["periods"][0]
    selected = first_period["selected_candidates"]
    company_weight = sum(
        Decimal(str(candidate["weight"]))
        for candidate in selected
        if candidate["company_id"] == bonds[0].company_id
    )
    reasons = [reason for candidate in selected for reason in candidate["selection_reasons"]]
    assert Decimal(str(first_period["max_issuer_weight"])) <= Decimal("0.30")
    assert company_weight <= Decimal("0.30")
    assert "Allocation reduced by issuer concentration cap" in reasons


def test_constrained_backtest_applies_high_risk_cap(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)
    for bond in bonds[:2]:
        add_risk(
            db_session,
            bond=bond,
            as_of_date=date(2026, 1, 1),
            decision_status="eligible_for_analysis",
            risk_level="high",
        )

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
            "top_n": 3,
            "max_position_weight": "0.20",
            "max_issuer_weight": "1",
            "max_high_risk_weight": "0.30",
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    first_period = response.json()["periods"][0]
    reasons = [
        reason
        for candidate in first_period["selected_candidates"]
        for reason in candidate["selection_reasons"]
    ]
    assert Decimal(str(first_period["high_risk_weight"])) <= Decimal("0.30")
    assert "Allocation reduced by high-risk cap" in reasons


def test_unallocated_capital_affects_period_return(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
            "top_n": 1,
            "max_position_weight": "0.20",
            "max_issuer_weight": "1",
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    period = response.json()["periods"][0]
    assert Decimal(str(period["allocated_weight"])) == Decimal("0.20")
    assert Decimal(str(period["unallocated_weight"])) == Decimal("0.80")
    assert Decimal(str(period["gross_period_return"])) == Decimal("0.0200000000")


def test_transaction_cost_uses_constrained_turnover(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
            "top_n": 1,
            "max_position_weight": "0.20",
            "max_issuer_weight": "1",
            "transaction_cost_rate": "0.01",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    period = response.json()["periods"][0]
    assert Decimal(str(period["estimated_costs_return"])) == Decimal("0.0020")
    assert Decimal(str(period["period_return"])) == Decimal("0.0180000000")


def test_empty_allocation_period_charges_exit_turnover(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "EXT")
    bond = create_bond(db_session, company, 77)
    run = create_run(db_session)
    add_risk(db_session, bond=bond, as_of_date=date(2025, 12, 31))
    add_prediction_and_label(
        db_session,
        run=run,
        bond=bond,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.90"),
        future_return=Decimal("0.000000"),
        liquidity_score=80,
        yield_to_maturity=Decimal("12.000"),
    )
    add_prediction_and_label(
        db_session,
        run=run,
        bond=bond,
        as_of_date=date(2026, 2, 1),
        probability=Decimal("0.10"),
        future_return=Decimal("0.000000"),
        liquidity_score=80,
        yield_to_maturity=Decimal("12.000"),
    )

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "top_n": 1,
            "min_probability_positive": "0.50",
            "max_position_weight": "0.20",
            "max_issuer_weight": "1",
            "transaction_cost_rate": "0.01",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    periods = response.json()["periods"]
    first = periods[0]
    second = periods[1]
    assert Decimal(str(first["allocated_weight"])) == Decimal("0.20")
    assert Decimal(str(first["estimated_costs_return"])) == Decimal("0.0020")
    assert Decimal(str(first["period_return"])) == Decimal("-0.0020000000")
    assert Decimal(str(second["allocated_weight"])) == Decimal("0")
    assert Decimal(str(second["gross_period_return"])) == Decimal("0")
    assert Decimal(str(second["estimated_costs_return"])) == Decimal("0.0020")
    assert Decimal(str(second["period_return"])) == Decimal("-0.0020")
    assert Decimal(str(second["portfolio_value_end"])) < Decimal(
        str(second["portfolio_value_start"])
    )
    assert Decimal(str(response.json()["metrics"]["turnover"])) == Decimal("0.20")


def test_missing_risk_exclusion_is_configurable(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MRK")
    bond = create_bond(db_session, company, 99)
    run = create_run(db_session)
    add_prediction_and_label(
        db_session,
        run=run,
        bond=bond,
        as_of_date=date(2026, 1, 1),
        probability=Decimal("0.90"),
        future_return=Decimal("0.020000"),
        liquidity_score=80,
        yield_to_maturity=Decimal("12.000"),
        return_method="risk_adjusted",
    )

    excluded = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "exclude_insufficient_credit_data": True,
            "include_excluded_candidates": True,
            "include_baselines": False,
        },
    )
    allowed = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "exclude_insufficient_credit_data": False,
            "include_baselines": False,
        },
    )

    assert excluded.status_code == 200
    assert excluded.json()["periods"][0]["selected_candidates_count"] == 0
    assert "Risk assessment is missing" in excluded.json()["periods"][0]["excluded_candidates"][0]["exclusion_reasons"]
    assert allowed.status_code == 200
    assert allowed.json()["periods"][0]["selected_candidates_count"] == 1


def test_risk_level_and_decision_status_allow_lists_exclude_candidates(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)
    add_risk(
        db_session,
        bond=bonds[0],
        as_of_date=date(2026, 1, 1),
        decision_status="watchlist",
        risk_level="medium",
    )

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
            "allowed_risk_levels": ["low"],
            "allowed_decision_statuses": ["eligible_for_analysis"],
            "include_excluded_candidates": True,
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    excluded = {
        candidate["bond_id"]: candidate["exclusion_reasons"]
        for candidate in response.json()["periods"][0]["excluded_candidates"]
    }
    assert "Risk level is not allowed" in excluded[bonds[0].id]
    assert "Decision status is not allowed" in excluded[bonds[0].id]


def test_simplified_mode_preserves_previous_selection_shape(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "date_from": "2026-01-01",
            "date_to": "2026-01-01",
            "top_n": 2,
            "max_position_weight": "0.25",
            "transaction_cost_rate": "0",
            "include_baselines": False,
            "use_portfolio_constraints": False,
        },
    )

    assert response.status_code == 200
    period = response.json()["periods"][0]
    assert [candidate["bond_id"] for candidate in period["selected_candidates"]] == [
        bonds[0].id,
        bonds[1].id,
    ]
    assert Decimal(str(period["allocated_weight"])) == Decimal("0.50")


def test_baselines_respect_constraints_when_enabled(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "top_n": 3,
            "max_position_weight": "0.20",
            "max_issuer_weight": "0.30",
            "max_high_risk_weight": "0.20",
            "include_baselines": True,
        },
    )

    assert response.status_code == 200
    for baseline in response.json()["baselines"]:
        assert Decimal(str(baseline["metrics"]["average_max_issuer_weight"])) <= Decimal("0.30")


def test_response_includes_constrained_fields(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={"model_run_id": run.id, "include_baselines": False},
    )

    assert response.status_code == 200
    period = response.json()["periods"][0]
    metrics = response.json()["metrics"]
    for key in [
        "allocated_weight",
        "unallocated_weight",
        "allocated_capital",
        "unallocated_capital",
        "high_risk_weight",
        "max_issuer_weight",
    ]:
        assert key in period
    for key in [
        "average_allocated_weight",
        "average_unallocated_weight",
        "average_high_risk_weight",
        "average_max_issuer_weight",
    ]:
        assert key in metrics
    assert "constraints" in period


def test_risk_filter_excludes_blocked_candidates(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)
    add_risk(
        db_session,
        bond=bonds[0],
        as_of_date=date(2026, 1, 1),
        decision_status="blocked_by_risk",
        risk_level="high",
    )

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "top_n": 2,
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    selected_ids = {
        candidate["bond_id"]
        for candidate in response.json()["periods"][0]["selected_candidates"]
    }
    assert bonds[0].id not in selected_ids
    assert bonds[1].id in selected_ids
    assert bonds[2].id in selected_ids


def test_liquidity_filter_excludes_low_liquidity_candidates(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, bonds = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={
            "model_run_id": run.id,
            "top_n": 3,
            "min_liquidity_score": 90,
            "transaction_cost_rate": "0",
            "include_baselines": False,
        },
    )

    assert response.status_code == 200
    selected_ids = [
        candidate["bond_id"]
        for candidate in response.json()["periods"][0]["selected_candidates"]
    ]
    assert selected_ids == [bonds[0].id]


def test_baselines_are_returned(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={"model_run_id": run.id, "include_baselines": True},
    )

    assert response.status_code == 200
    assert {baseline["name"] for baseline in response.json()["baselines"]} == {
        "equal_weight_all_evaluable",
        "top_yield_to_maturity",
        "top_liquidity",
    }


def test_missing_and_non_completed_model_run_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/strategy/backtests/run",
        json={"model_run_id": 999999},
    )
    non_completed = client.post(
        "/api/strategy/backtests/run",
        json={"model_run_id": running.id},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_no_predictions_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={"model_run_id": run.id},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "No predictions found for selected model run and date range"
    )


def test_invalid_backtest_request_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    cases = [
        (
            {"date_from": "2026-02-01", "date_to": "2026-01-01"},
            "Invalid date range",
        ),
        ({"initial_capital": "0"}, "initial_capital must be positive"),
        ({"top_n": 0}, "top_n must be positive"),
        (
            {"min_probability_positive": "1.1"},
            "min_probability_positive must be between 0 and 1",
        ),
        (
            {"max_position_weight": "1.1"},
            "max_position_weight must be greater than 0 and at most 1",
        ),
        (
            {"max_issuer_weight": "0"},
            "max_issuer_weight must be greater than 0 and at most 1",
        ),
        (
            {"max_high_risk_weight": "1.1"},
            "max_high_risk_weight must be between 0 and 1",
        ),
        (
            {"allowed_risk_levels": ["severe"]},
            "Invalid risk level",
        ),
        (
            {"allowed_decision_statuses": ["manual"]},
            "Invalid decision status",
        ),
        (
            {"transaction_cost_rate": "0.2"},
            "transaction_cost_rate must be between 0 and 0.1",
        ),
        (
            {"rebalance_frequency": "daily"},
            "Invalid rebalance frequency",
        ),
        (
            {"rebalance_gap_days": 0},
            "rebalance_gap_days must be positive",
        ),
    ]

    for override, detail in cases:
        response = client.post(
            "/api/strategy/backtests/run",
            json={"model_run_id": run.id, **override},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_strategy_backtest_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_backtest_dataset(db_session)

    response = client.post(
        "/api/strategy/backtests/run",
        json={"model_run_id": run.id},
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
