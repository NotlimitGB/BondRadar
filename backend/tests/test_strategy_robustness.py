import json
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
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


def create_company(db: Session, ticker: str = "ROB") -> Company:
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
        isin=f"RU000{company.ticker}{index:03d}"[:12],
        secid=f"{company.ticker}{index:03d}",
        name=f"Robustness Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("10.000"),
        liquidity_score=85,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


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
            liquidity_score=85,
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
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        yield_to_maturity=Decimal("10.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=85,
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
    db.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=run.horizon_days,
            return_method=(run.params or {}).get("return_method") or "price",
            future_return=future_return,
            risk_adjusted_excess_return=future_return,
            required_risk_premium=Decimal("0.020000"),
            label="positive_return" if future_return > 0 else "negative_return",
            label_binary=1 if future_return > 0 else 0,
        )
    )
    db.commit()


def seed_single_run_dataset(
    db: Session,
    *,
    returns: list[Decimal] | None = None,
    dates: list[date] | None = None,
    ticker: str = "ROB",
) -> tuple[MLModelRun, list[Bond], list[date]]:
    run = create_run(db)
    company = create_company(db, ticker)
    bonds = [create_bond(db, company, index) for index in range(1, 3)]
    selected_dates = dates or [date(2026, 1, 1), date(2026, 2, 1), date(2026, 3, 1)]
    selected_returns = returns or [
        Decimal("0.050000"),
        Decimal("0.040000"),
        Decimal("0.030000"),
    ]
    for bond in bonds:
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))
    for as_of_date, future_return in zip(selected_dates, selected_returns):
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[0],
            as_of_date=as_of_date,
            probability=Decimal("0.95"),
            future_return=future_return,
        )
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[1],
            as_of_date=as_of_date,
            probability=Decimal("0.70"),
            future_return=-abs(future_return) / Decimal("2"),
        )
    return run, bonds, selected_dates


def seed_multi_run_dataset(db: Session) -> tuple[MLModelRun, MLModelRun]:
    first_run = create_run(db)
    second_run = create_run(db)
    company = create_company(db, "RMW")
    bonds = [create_bond(db, company, index) for index in range(11, 13)]
    for bond in bonds:
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))
    for run, as_of_date, future_return in [
        (first_run, date(2026, 1, 1), Decimal("0.050000")),
        (second_run, date(2026, 2, 1), Decimal("0.040000")),
    ]:
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[0],
            as_of_date=as_of_date,
            probability=Decimal("0.95"),
            future_return=future_return,
        )
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[1],
            as_of_date=as_of_date,
            probability=Decimal("0.70"),
            future_return=-abs(future_return) / Decimal("2"),
        )
    return first_run, second_run


def experiment_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "model_run_id": run.id,
        "date_from": "2026-01-01",
        "date_to": "2026-03-01",
        "initial_capital": "1000",
        "transaction_cost_rate": "0",
        "ranking_metric": "total_return",
        "ranking_direction": "desc",
        "include_periods": False,
        "include_baselines": False,
        "variants": [
            {
                "name": "top_one",
                "top_n": 1,
                "min_probability_positive": "0.50",
                "max_position_weight": "1",
                "max_issuer_weight": "1",
                "max_high_risk_weight": "1",
            },
            {
                "name": "top_two",
                "top_n": 2,
                "min_probability_positive": "0.50",
                "max_position_weight": "0.50",
                "max_issuer_weight": "1",
                "max_high_risk_weight": "1",
            },
        ],
    }
    payload.update(overrides)
    return payload


def robustness_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "experiment": experiment_payload(run),
        "selected_variant_count": 1,
        "subperiod_mode": "monthly",
        "include_subperiod_details": True,
        "include_candidate_concentration": True,
    }
    payload.update(overrides)
    return payload


def flag_codes(payload: dict) -> set[str]:
    return {flag["code"] for flag in payload["variants"][0]["flags"]}


def row_count(db: Session, model) -> int:
    return db.execute(select(func.count()).select_from(model)).scalar_one()


def test_robustness_endpoint_returns_stable_analysis(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session)

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] == run.id
    assert payload["prediction_source_mode"] == "single_model_run"
    assert payload["analyzed_variant_count"] == 1
    assert payload["variants"]
    assert payload["variants"][0]["subperiod_count"] == 3
    assert payload["variants"][0]["completed_subperiod_count"] == 3
    assert len(payload["variants"][0]["subperiods"]) == 3


def test_robustness_supports_multi_run_experiment_selector(
    client: TestClient,
    db_session: Session,
) -> None:
    first_run, second_run = seed_multi_run_dataset(db_session)

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(
            first_run,
            experiment=experiment_payload(
                first_run,
                model_run_id=None,
                model_run_ids=[first_run.id, second_run.id],
                date_from="2026-01-01",
                date_to="2026-02-01",
            ),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["model_run_id"] is None
    assert payload["model_run_ids"] == [first_run.id, second_run.id]
    assert payload["prediction_source_mode"] == "multiple_model_runs"
    assert payload["variants"][0]["completed_subperiod_count"] == 2


def test_robustness_no_completed_variants_returns_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session)

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(
            run,
            experiment=experiment_payload(
                run,
                date_from="2027-01-01",
                date_to="2027-03-01",
            ),
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["variants"] == []
    assert any("No completed experiment variants" in warning["message"] for warning in payload["warnings"])


def test_robustness_monthly_and_quarterly_subperiod_generation(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(
        db_session,
        dates=[
            date(2026, 1, 15),
            date(2026, 2, 15),
            date(2026, 3, 15),
            date(2026, 4, 15),
        ],
        returns=[
            Decimal("0.040000"),
            Decimal("0.030000"),
            Decimal("0.020000"),
            Decimal("0.010000"),
        ],
        ticker="RCA",
    )
    experiment = experiment_payload(
        run,
        date_from="2026-01-15",
        date_to="2026-04-20",
    )

    monthly = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run, experiment=experiment, subperiod_mode="monthly"),
    )
    quarterly = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run, experiment=experiment, subperiod_mode="quarterly"),
    )

    assert monthly.status_code == 200
    assert quarterly.status_code == 200
    assert [
        (item["date_from"], item["date_to"])
        for item in monthly.json()["variants"][0]["subperiods"]
    ] == [
        ("2026-01-15", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
        ("2026-03-01", "2026-03-31"),
        ("2026-04-01", "2026-04-20"),
    ]
    assert [
        (item["date_from"], item["date_to"])
        for item in quarterly.json()["variants"][0]["subperiods"]
    ] == [
        ("2026-01-15", "2026-03-31"),
        ("2026-04-01", "2026-04-20"),
    ]


def test_robustness_fixed_window_subperiod_generation(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session)

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(
            run,
            subperiod_mode="fixed_window",
            subperiod_days=30,
        ),
    )

    assert response.status_code == 200
    assert [
        (item["date_from"], item["date_to"])
        for item in response.json()["variants"][0]["subperiods"]
    ] == [
        ("2026-01-01", "2026-01-30"),
        ("2026-01-31", "2026-03-01"),
    ]


def test_robustness_subperiod_failures_are_captured(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(
        db_session,
        dates=[date(2026, 1, 1), date(2026, 3, 1)],
        returns=[Decimal("0.050000"), Decimal("0.040000")],
        ticker="RFL",
    )

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run),
    )

    assert response.status_code == 200
    variant = response.json()["variants"][0]
    assert any(item["status"] == "failed" for item in variant["subperiods"])
    assert "subperiod_failures_present" in {flag["code"] for flag in variant["flags"]}


def test_robustness_low_positive_subperiod_ratio_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(
        db_session,
        returns=[
            Decimal("-0.050000"),
            Decimal("-0.040000"),
            Decimal("0.010000"),
        ],
        ticker="RLP",
    )

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(
            run,
            minimum_positive_subperiod_ratio="0.75",
        ),
    )

    assert response.status_code == 200
    assert "low_positive_subperiod_ratio" in flag_codes(response.json())


def test_robustness_single_subperiod_dominance_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(
        db_session,
        returns=[
            Decimal("0.010000"),
            Decimal("0.010000"),
            Decimal("0.500000"),
        ],
        ticker="RSD",
    )

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run),
    )

    assert response.status_code == 200
    assert "single_subperiod_dominates_result" in flag_codes(response.json())


def test_robustness_bond_and_company_concentration_flags(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session, ticker="RCC")

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(
            run,
            maximum_top_bond_selection_share="0.50",
            maximum_top_company_selection_share="0.50",
        ),
    )

    assert response.status_code == 200
    variant = response.json()["variants"][0]
    assert variant["top_bond_concentration"] is not None
    assert variant["top_company_concentration"] is not None
    assert "high_bond_concentration" in flag_codes(response.json())
    assert "high_company_concentration" in flag_codes(response.json())


def test_robustness_invalid_request_values_return_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session)
    cases = [
        (
            {"selected_variant_count": 0},
            "selected_variant_count must be between 1 and 20",
        ),
        ({"subperiod_mode": "daily"}, "Invalid subperiod mode"),
        (
            {"subperiod_mode": "fixed_window"},
            "subperiod_days must be positive when subperiod_mode is fixed_window",
        ),
        ({"max_subperiods": 0}, "max_subperiods must be between 1 and 120"),
        (
            {"minimum_completed_subperiods": 0},
            "minimum_completed_subperiods must be positive",
        ),
        (
            {"minimum_positive_subperiod_ratio": "1.1"},
            "ratio values must be between 0 and 1",
        ),
    ]

    for overrides, expected_detail in cases:
        response = client.post(
            "/api/strategy/robustness/analyze",
            json=robustness_payload(run, **overrides),
        )
        assert response.status_code == 400
        assert response.json()["detail"] == expected_detail


def test_robustness_endpoint_does_not_write_persistent_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session)
    models = [
        PaperPortfolio,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
        MLModelRun,
        MLPrediction,
    ]
    before = {model: row_count(db_session, model) for model in models}

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run),
    )

    assert response.status_code == 200
    after = {model: row_count(db_session, model) for model in models}
    assert after == before


def test_robustness_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _ = seed_single_run_dataset(db_session)

    response = client.post(
        "/api/strategy/robustness/analyze",
        json=robustness_payload(run),
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
        r"\bРїРѕРєСѓРїР°С‚СЊ\b",
        r"\bРїСЂРѕРґР°РІР°С‚СЊ\b",
    ]
    assert all(re.search(pattern, text) is None for pattern in forbidden_patterns)
