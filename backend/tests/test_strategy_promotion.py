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


def create_company(db: Session, ticker: str = "PRM") -> Company:
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
        isin=f"RU000PRM{index:03d}",
        secid=f"PRM{index:03d}",
        name=f"Promotion Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("10.000"),
        liquidity_score=90,
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
            yield_to_maturity=Decimal("10.000"),
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
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        yield_to_maturity=Decimal("10.000"),
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
    future_return: Decimal,
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


def seed_promotion_dataset(
    db: Session,
    *,
    labels_for_second_date: bool = True,
) -> tuple[MLModelRun, list[Bond]]:
    run = create_run(db)
    company = create_company(db)
    bonds = [create_bond(db, company, index) for index in range(1, 3)]
    for bond in bonds:
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))

    for as_of_date in [date(2026, 1, 1), date(2026, 2, 1)]:
        add_prediction(
            db,
            run=run,
            bond=bonds[0],
            as_of_date=as_of_date,
            probability=Decimal("0.95"),
        )
        add_prediction(
            db,
            run=run,
            bond=bonds[1],
            as_of_date=as_of_date,
            probability=Decimal("0.70"),
        )
        if as_of_date == date(2026, 1, 1) or labels_for_second_date:
            add_label(
                db,
                run=run,
                bond=bonds[0],
                as_of_date=as_of_date,
                future_return=Decimal("0.100000"),
            )
            add_label(
                db,
                run=run,
                bond=bonds[1],
                as_of_date=as_of_date,
                future_return=Decimal("-0.080000"),
            )
    return run, bonds


def seed_multi_run_promotion_dataset(
    db: Session,
) -> tuple[MLModelRun, MLModelRun, list[Bond]]:
    first_run = create_run(db)
    second_run = create_run(db)
    company = create_company(db, "WFP")
    bonds = [create_bond(db, company, index) for index in range(11, 13)]
    for bond in bonds:
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))

    for run, as_of_date in [
        (first_run, date(2026, 1, 1)),
        (second_run, date(2026, 2, 1)),
    ]:
        add_prediction(
            db,
            run=run,
            bond=bonds[0],
            as_of_date=as_of_date,
            probability=Decimal("0.95"),
        )
        add_prediction(
            db,
            run=run,
            bond=bonds[1],
            as_of_date=as_of_date,
            probability=Decimal("0.70"),
        )
        add_label(
            db,
            run=run,
            bond=bonds[0],
            as_of_date=as_of_date,
            future_return=Decimal("0.100000"),
        )
        add_label(
            db,
            run=run,
            bond=bonds[1],
            as_of_date=as_of_date,
            future_return=Decimal("-0.080000"),
        )
    return first_run, second_run, bonds


def experiment_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "model_run_id": run.id,
        "date_from": "2026-01-01",
        "date_to": "2026-02-01",
        "initial_capital": "1000",
        "transaction_cost_rate": "0",
        "ranking_metric": "total_return",
        "ranking_direction": "desc",
        "include_periods": False,
        "include_baselines": True,
        "variants": [
            {
                "name": "top_one",
                "top_n": 1,
                "min_probability_positive": "0.50",
                "max_position_weight": "0.50",
                "max_issuer_weight": "1",
                "max_high_risk_weight": "0.20",
            },
            {
                "name": "top_two",
                "top_n": 2,
                "min_probability_positive": "0.50",
                "max_position_weight": "0.50",
                "max_issuer_weight": "1",
                "max_high_risk_weight": "0.20",
            },
        ],
    }
    payload.update(overrides)
    return payload


def promotion_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "experiment": experiment_payload(run),
        "paper_portfolio_name": "Promoted scenario test",
        "scenario_include_performance_report": True,
    }
    payload.update(overrides)
    return payload


def multi_run_experiment_payload(
    first_run: MLModelRun,
    second_run: MLModelRun,
    **overrides,
) -> dict:
    return experiment_payload(
        first_run,
        model_run_id=None,
        model_run_ids=[first_run.id, second_run.id],
        **overrides,
    )


def create_existing_portfolio(client: TestClient, run: MLModelRun) -> dict:
    response = client.post(
        "/api/paper-trading/portfolios",
        json={
            "name": "Reusable paper portfolio",
            "initial_capital": "1000",
            "base_currency": "RUB",
            "model_run_id": run.id,
        },
    )
    assert response.status_code == 200
    return response.json()


def test_promotion_runs_experiment_and_scenario(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] == run.id
    assert payload["model_run_ids"] == [run.id]
    assert payload["model_run_count"] == 1
    assert payload["prediction_source_mode"] == "single_model_run"
    assert payload["experiment"]["successful_variant_count"] == 2
    assert payload["selected_variant"] is not None
    assert payload["scenario"] is not None
    assert payload["scenario"]["model_run_id"] == run.id
    assert payload["scenario"]["model_run_ids"] == [run.id]
    assert payload["scenario"]["model_run_count"] == 1
    assert payload["scenario"]["prediction_source_mode"] == "single_model_run"
    assert payload["scenario"]["portfolio_id"] is not None
    assert payload["scenario"]["final_portfolio_value"] is not None
    assert db_session.execute(select(PaperPortfolio)).scalars().first() is not None


def test_promotion_selects_first_completed_leaderboard_item(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_variant"]["rank"] == 1
    assert (
        payload["selected_variant"]["variant_name"]
        == payload["experiment"]["leaderboard"][0]["variant_name"]
    )


def test_promotion_without_completed_variant_returns_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            run,
            experiment=experiment_payload(
                run,
                date_from="2027-01-01",
                date_to="2027-01-31",
            ),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] == run.id
    assert payload["model_run_ids"] == [run.id]
    assert payload["model_run_count"] == 1
    assert payload["prediction_source_mode"] == "single_model_run"
    assert payload["selected_variant"] is None
    assert payload["scenario"] is None
    assert any("No completed experiment variant" in item["message"] for item in payload["warnings"])
    assert db_session.execute(select(PaperPortfolio)).scalars().first() is None


def test_promotion_reuses_existing_portfolio(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)
    portfolio = create_existing_portfolio(client, run)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(run, portfolio_id=portfolio["id"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"]["portfolio_id"] == portfolio["id"]
    assert len(db_session.execute(select(PaperPortfolio)).scalars().all()) == 1
    assert any(
        "appended" in warning["message"]
        for warning in payload["scenario"]["warnings"]
    )


def test_promotion_captures_run_level_scenario_failure(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            run,
            scenario_date_from="2027-01-01",
            scenario_date_to="2027-01-31",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_variant"] is not None
    assert payload["scenario"] is None
    assert any(
        warning["message"] == "Paper trading scenario failed after experiment promotion"
        for warning in payload["warnings"]
    )


def test_promotion_returns_scenario_with_strict_mark_failures(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session, labels_for_second_date=False)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            run,
            experiment=experiment_payload(
                run,
                date_from="2026-01-01",
                date_to="2026-01-01",
            ),
            scenario_date_to="2026-02-01",
            scenario_allow_partial_marking=False,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"] is not None
    assert payload["scenario"]["mark_failed_count"] == 1


def test_promotion_warns_for_simplified_backtest_variant(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            run,
            experiment=experiment_payload(
                run,
                variants=[
                    {
                        "name": "simple_mode",
                        "top_n": 1,
                        "use_portfolio_constraints": False,
                        "max_position_weight": "0.50",
                        "max_issuer_weight": "1",
                        "max_high_risk_weight": "0.20",
                    }
                ],
            ),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert any("simplified backtest mode" in item["message"] for item in payload["warnings"])


def test_promotion_paper_initial_capital_override(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(run, paper_initial_capital="1500"),
    )

    assert response.status_code == 200
    assert response.json()["scenario"]["summary"]["initial_capital"] == "1500.000000"


def test_promotion_include_cycle_details_false(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(run, scenario_include_cycle_details=False),
    )

    assert response.status_code == 200
    assert response.json()["scenario"]["cycles"] == []


def test_promotion_missing_and_non_completed_model_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            running,
            experiment=experiment_payload(running, model_run_id=999999),
        ),
    )
    non_completed = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(running),
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_multi_run_promotion_runs_experiment_and_scenario(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            first_run,
            experiment=multi_run_experiment_payload(first_run, second_run),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] is None
    assert payload["model_run_ids"] == [first_run.id, second_run.id]
    assert payload["model_run_count"] == 2
    assert payload["prediction_source_mode"] == "multiple_model_runs"
    assert payload["selected_variant"] is not None
    assert payload["selected_variant"]["request"]["model_run_ids"] == [
        first_run.id,
        second_run.id,
    ]
    assert payload["scenario"] is not None
    assert payload["scenario"]["model_run_id"] is None
    assert payload["scenario"]["model_run_ids"] == [first_run.id, second_run.id]
    assert payload["scenario"]["prediction_source_mode"] == "multiple_model_runs"
    assert [cycle["model_run_id"] for cycle in payload["scenario"]["cycles"]] == [
        first_run.id,
        second_run.id,
    ]
    assert any(
        warning["message"]
        == "Multi-run experiment was promoted to a multi-run paper scenario"
        for warning in payload["warnings"]
    )
    assert any(
        "primary model run id" in warning["message"]
        for warning in payload["scenario"]["warnings"]
    )


def test_multi_run_promotion_reuses_existing_portfolio(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_promotion_dataset(db_session)
    portfolio = create_existing_portfolio(client, first_run)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            first_run,
            portfolio_id=portfolio["id"],
            experiment=multi_run_experiment_payload(first_run, second_run),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"]["portfolio_id"] == portfolio["id"]
    assert any(
        "appended" in warning["message"]
        for warning in payload["scenario"]["warnings"]
    )
    assert any(
        "fold model runs" in warning["message"]
        for warning in payload["scenario"]["warnings"]
    )


def test_multi_run_promotion_ranking_override_selects_from_existing_results(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            first_run,
            experiment=multi_run_experiment_payload(first_run, second_run),
            promote_ranking_metric="average_unallocated_weight",
            promote_ranking_direction="asc",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_variant"]["variant_name"] == "top_two"
    assert payload["selected_variant"]["ranking_metric"] == "average_unallocated_weight"
    assert payload["selected_variant"]["ranking_direction"] == "asc"


def test_multi_run_promotion_scenario_failure_is_captured(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run, _ = seed_multi_run_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            first_run,
            experiment=multi_run_experiment_payload(first_run, second_run),
            scenario_date_from="2027-01-01",
            scenario_date_to="2027-01-31",
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_variant"] is not None
    assert payload["scenario"] is None
    assert any(
        warning["message"] == "Paper trading scenario failed after experiment promotion"
        for warning in payload["warnings"]
    )


def test_multi_run_promotion_incompatible_model_runs_fail_via_experiment(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run = create_run(db_session)
    second_run = create_run(db_session, horizon_days=60)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            first_run,
            experiment=multi_run_experiment_payload(first_run, second_run),
        ),
    )

    assert response.status_code == 400
    assert (
        response.json()["detail"]
        == "Model runs must use the same horizon and return method"
    )


def test_multi_run_promotion_missing_and_non_completed_model_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    completed = create_run(db_session)
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            completed,
            experiment=experiment_payload(
                completed,
                model_run_id=None,
                model_run_ids=[completed.id, 999999],
            ),
        ),
    )
    non_completed = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(
            completed,
            experiment=experiment_payload(
                completed,
                model_run_id=None,
                model_run_ids=[completed.id, running.id],
            ),
        ),
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_promotion_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _ = seed_promotion_dataset(db_session)

    response = client.post(
        "/api/strategy/promotions/best-experiment-to-paper-scenario",
        json=promotion_payload(run),
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())

