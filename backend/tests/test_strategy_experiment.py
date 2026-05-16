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


def create_company(db: Session, ticker: str = "SET") -> Company:
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
        isin=f"RU000SET{index:03d}",
        secid=f"SET{index:03d}",
        name=f"Experiment Bond {index}",
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


def create_run(db: Session, *, status: str = "completed") -> MLModelRun:
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
        metrics={},
        feature_importance=[],
        params={"return_method": "risk_adjusted"},
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
            assessment_score=80,
            decision_status="eligible_for_analysis",
            risk_level="low",
            required_risk_premium=Decimal("0.020000"),
            yield_to_maturity=Decimal("10.000"),
            coupon_rate=Decimal("10.000"),
            duration_years=Decimal("2.000"),
            liquidity_score=80,
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
        liquidity_score=80,
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
            return_method="risk_adjusted",
            future_return=future_return,
            risk_adjusted_excess_return=future_return,
            required_risk_premium=Decimal("0.020000"),
            label="positive_return" if future_return > 0 else "negative_return",
            label_binary=1 if future_return > 0 else 0,
        )
    )
    db.commit()


def seed_experiment_dataset(db: Session) -> MLModelRun:
    run = create_run(db)
    company = create_company(db)
    bonds = [create_bond(db, company, index) for index in range(1, 4)]
    for bond in bonds:
        add_risk(db, bond=bond, as_of_date=date(2025, 12, 31))
    for as_of_date in [date(2026, 1, 1), date(2026, 2, 1)]:
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[0],
            as_of_date=as_of_date,
            probability=Decimal("0.95"),
            future_return=Decimal("0.100000"),
        )
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[1],
            as_of_date=as_of_date,
            probability=Decimal("0.70"),
            future_return=Decimal("-0.080000"),
        )
        add_prediction_and_label(
            db,
            run=run,
            bond=bonds[2],
            as_of_date=as_of_date,
            probability=Decimal("0.45"),
            future_return=Decimal("0.020000"),
        )
    return run


def compare_payload(run: MLModelRun, **overrides) -> dict:
    payload = {
        "model_run_id": run.id,
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


def test_compare_two_successful_variants(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    response = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["successful_variant_count"] == 2
    assert payload["failed_variant_count"] == 0
    assert [item["variant_name"] for item in payload["leaderboard"]] == [
        "top_one",
        "top_two",
    ]
    assert Decimal(str(payload["leaderboard"][0]["total_return"])) > Decimal(
        str(payload["leaderboard"][1]["total_return"])
    )


def test_ranking_ascending_for_max_drawdown(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    response = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(
            run,
            ranking_metric="max_drawdown",
            ranking_direction="asc",
        ),
    )

    assert response.status_code == 200
    leaderboard = response.json()["leaderboard"]
    assert Decimal(str(leaderboard[0]["max_drawdown"])) <= Decimal(
        str(leaderboard[1]["max_drawdown"])
    )


def test_failed_variant_does_not_fail_whole_experiment(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    response = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(
            run,
            variants=[
                {
                    "name": "valid",
                    "top_n": 1,
                    "max_position_weight": "1",
                    "max_issuer_weight": "1",
                    "max_high_risk_weight": "1",
                },
                {
                    "name": "invalid_variant",
                    "top_n": 0,
                    "max_position_weight": "1",
                    "max_issuer_weight": "1",
                    "max_high_risk_weight": "1",
                },
            ],
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["successful_variant_count"] == 1
    assert payload["failed_variant_count"] == 1
    assert payload["results"][1]["status"] == "failed"
    assert payload["leaderboard"][-1]["status"] == "failed"


def test_include_periods_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    hidden = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, include_periods=False),
    )
    included = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, include_periods=True),
    )

    assert hidden.status_code == 200
    assert included.status_code == 200
    assert hidden.json()["results"][0]["periods"] == []
    assert len(included.json()["results"][0]["periods"]) > 0


def test_include_baselines_flag(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    included = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, include_baselines=True),
    )
    hidden = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, include_baselines=False),
    )

    assert included.status_code == 200
    assert hidden.status_code == 200
    assert included.json()["results"][0]["baseline_summaries"]
    assert hidden.json()["results"][0]["baseline_summaries"] == []


def test_empty_variants_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    response = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, variants=[]),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "variants must not be empty"


def test_too_many_variants_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)
    variants = [{"name": f"variant_{index}"} for index in range(3)]

    response = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, variants=variants, max_variants=2),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "variants must not exceed max_variants"


def test_invalid_ranking_metric_and_direction_return_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    bad_metric = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, ranking_metric="unknown"),
    )
    bad_direction = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run, ranking_direction="sideways"),
    )

    assert bad_metric.status_code == 400
    assert bad_metric.json()["detail"] == "Invalid ranking metric"
    assert bad_direction.status_code == 400
    assert bad_direction.json()["detail"] == "Invalid ranking direction"


def test_missing_and_non_completed_model_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(running, model_run_id=999999),
    )
    non_completed = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(running),
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_strategy_experiment_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run = seed_experiment_dataset(db_session)

    response = client.post(
        "/api/strategy/experiments/compare",
        json=compare_payload(run),
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
