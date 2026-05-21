from datetime import date, datetime, timezone
from decimal import Decimal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def create_company(db: Session, ticker: str) -> Company:
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
        isin=f"RU000PC{company.ticker[-2:]}{index:03d}"[:12],
        secid=f"PC{company.ticker[-2:]}{index:03d}",
        name=f"Portfolio Bond {company.ticker} {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
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
    return_method: str = "risk_adjusted",
) -> MLModelRun:
    run = MLModelRun(
        status=status,
        model_type="logistic_regression",
        horizon_days=30,
        features=["bond_score", "company_score", "liquidity_score"],
        target="label_binary",
        train_rows=50,
        test_rows=10,
        positive_rows=30,
        negative_rows=30,
        metrics={"accuracy": 0.75},
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
    assessment_score: int = 80,
    liquidity_score: int | None = 80,
) -> None:
    db.add(
        BondRiskAssessment(
            bond_id=bond.id,
            company_id=bond.company_id,
            as_of_date=as_of_date,
            assessment_score=assessment_score,
            decision_status=decision_status,
            risk_level=risk_level,
            required_risk_premium=Decimal("0.020000"),
            yield_to_maturity=Decimal("12.000"),
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


def add_prediction(
    db: Session,
    *,
    run: MLModelRun,
    bond: Bond,
    as_of_date: date,
    probability: Decimal,
    liquidity_score: int | None = 80,
    yield_to_maturity: Decimal | None = Decimal("12.000"),
) -> None:
    feature = BondFeatureSnapshot(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        bond_score=Decimal("70.00"),
        company_score=Decimal("80.00"),
        yield_to_maturity=yield_to_maturity,
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
            features={"probability": str(probability)},
            created_at=dt(2026, 1, 2),
        )
    )
    db.commit()


def seed_portfolio_dataset(
    db: Session,
) -> tuple[MLModelRun, Company, Company, list[Bond]]:
    run = create_run(db)
    company_a = create_company(db, "PCA")
    company_b = create_company(db, "PCB")
    bonds = [
        create_bond(db, company_a, 1),
        create_bond(db, company_a, 2),
        create_bond(db, company_a, 3),
        create_bond(db, company_b, 4),
        create_bond(db, company_b, 5),
    ]
    early = date(2026, 1, 5)
    latest = date(2026, 2, 5)
    for index, bond in enumerate(bonds):
        add_risk(
            db,
            bond=bond,
            as_of_date=date(2026, 1, 1),
            decision_status="eligible_for_analysis",
            risk_level="low",
            assessment_score=90 - index,
            liquidity_score=95 - index,
        )
    add_prediction(
        db,
        run=run,
        bond=bonds[0],
        as_of_date=early,
        probability=Decimal("0.60"),
        liquidity_score=80,
    )
    probabilities = [
        Decimal("0.95"),
        Decimal("0.90"),
        Decimal("0.70"),
        Decimal("0.40"),
        Decimal("0.65"),
    ]
    liquidity = [95, 88, 70, 75, 92]
    for bond, probability, score in zip(bonds, probabilities, liquidity):
        add_prediction(
            db,
            run=run,
            bond=bond,
            as_of_date=latest,
            probability=probability,
            liquidity_score=score,
        )
    return run, company_a, company_b, bonds


def test_constructs_portfolio_from_latest_predictions(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, _ = seed_portfolio_dataset(db_session)

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id, "max_position_weight": "0.20"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of_date"] == "2026-02-05"
    assert payload["return_method"] == "risk_adjusted"
    assert payload["summary"]["selected_count"] > 0
    assert Decimal(str(payload["summary"]["allocated_weight"])) > 0


def test_exact_as_of_date_uses_only_that_date(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id, "as_of_date": "2026-01-05"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["as_of_date"] == "2026-01-05"
    assert [candidate["bond_id"] for candidate in payload["selected_candidates"]] == [
        bonds[0].id
    ]


def test_probability_minimum_excludes_low_probability_candidates(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id, "min_probability_positive": "0.80"},
    )

    assert response.status_code == 200
    excluded = {
        candidate["bond_id"]: candidate["exclusion_reasons"]
        for candidate in response.json()["excluded_candidates"]
    }
    assert "Probability below minimum" in excluded[bonds[2].id]
    assert "Probability below minimum" in excluded[bonds[3].id]
    assert "Probability below minimum" in excluded[bonds[4].id]


def test_liquidity_filter_excludes_missing_and_low_liquidity(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    company = create_company(db_session, "PCL")
    low = create_bond(db_session, company, 1)
    missing = create_bond(db_session, company, 2)
    for bond, risk_score in [(low, 40), (missing, None)]:
        add_risk(
            db_session,
            bond=bond,
            as_of_date=date(2026, 1, 1),
            liquidity_score=risk_score,
        )
    add_prediction(
        db_session,
        run=run,
        bond=low,
        as_of_date=date(2026, 1, 5),
        probability=Decimal("0.90"),
        liquidity_score=40,
    )
    add_prediction(
        db_session,
        run=run,
        bond=missing,
        as_of_date=date(2026, 1, 5),
        probability=Decimal("0.80"),
        liquidity_score=None,
    )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "min_liquidity_score": 70,
            "exclude_insufficient_credit_data": False,
        },
    )

    assert response.status_code == 200
    excluded = {
        candidate["bond_id"]: candidate["exclusion_reasons"]
        for candidate in response.json()["excluded_candidates"]
    }
    assert "Liquidity score below minimum" in excluded[low.id]
    assert "Liquidity score is missing" in excluded[missing.id]


def test_blocked_risk_filter_excludes_candidate(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)
    add_risk(
        db_session,
        bond=bonds[0],
        as_of_date=date(2026, 2, 5),
        decision_status="blocked_by_risk",
        risk_level="high",
    )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id},
    )

    assert response.status_code == 200
    excluded = {
        candidate["bond_id"]: candidate["exclusion_reasons"]
        for candidate in response.json()["excluded_candidates"]
    }
    assert "Blocked by risk assessment" in excluded[bonds[0].id]
    assert response.json()["summary"]["exclusion_reason_counts"][
        "Blocked by risk assessment"
    ] == 1


def test_zero_position_diagnostics_group_exclusion_reasons(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)
    for bond in bonds:
        add_risk(
            db_session,
            bond=bond,
            as_of_date=date(2026, 2, 5),
            decision_status="blocked_by_risk",
            risk_level="critical",
        )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["selected_count"] == 0
    assert payload["summary"]["excluded_count"] == len(bonds)
    assert payload["summary"]["exclusion_reason_counts"][
        "Blocked by risk assessment"
    ] == len(bonds)
    assert payload["summary"]["financial_data_gap_counts"][
        "financial_report_missing"
    ] == len(bonds)
    assert payload["summary"]["financial_data_gap_counts"][
        "financial_ratios_missing"
    ] == len(bonds)
    assert payload["warnings"][0]["details"]["exclusion_reason_counts"][
        "Blocked by risk assessment"
    ] == len(bonds)
    assert payload["warnings"][0]["details"]["financial_data_gap_counts"][
        "financial_report_missing"
    ] == len(bonds)
    assert "financial_diagnostics" in payload["excluded_candidates"][0]


def test_relaxed_risk_policy_can_select_blocked_candidates_for_analysis(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)
    for bond in bonds:
        add_risk(
            db_session,
            bond=bond,
            as_of_date=date(2026, 2, 5),
            decision_status="blocked_by_risk",
            risk_level="critical",
        )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "exclude_blocked_by_risk": False,
            "exclude_insufficient_credit_data": False,
            "max_high_risk_weight": "1.0",
            "max_position_weight": "0.20",
            "max_issuer_weight": "1.0",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["selected_count"] > 0
    assert Decimal(str(payload["summary"]["allocated_weight"])) > 0


def test_high_risk_cap_limits_relaxed_critical_candidates(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)
    for bond in bonds:
        add_risk(
            db_session,
            bond=bond,
            as_of_date=date(2026, 2, 5),
            decision_status="blocked_by_risk",
            risk_level="critical",
        )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "exclude_blocked_by_risk": False,
            "exclude_insufficient_credit_data": False,
            "max_high_risk_weight": "0.10",
            "max_position_weight": "1.0",
            "max_issuer_weight": "1.0",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["summary"]["high_risk_weight"])) <= Decimal("0.10")
    assert Decimal(str(payload["summary"]["allocated_weight"])) <= Decimal("0.10")


def test_missing_risk_excluded_by_default(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    company = create_company(db_session, "PCM")
    bond = create_bond(db_session, company, 1)
    add_prediction(
        db_session,
        run=run,
        bond=bond,
        as_of_date=date(2026, 1, 5),
        probability=Decimal("0.90"),
    )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id},
    )

    assert response.status_code == 200
    excluded = response.json()["excluded_candidates"][0]
    assert excluded["bond_id"] == bond.id
    assert "Risk assessment is missing" in excluded["exclusion_reasons"]


def test_risk_level_and_decision_status_allow_lists(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)
    add_risk(
        db_session,
        bond=bonds[0],
        as_of_date=date(2026, 2, 5),
        decision_status="watchlist",
        risk_level="medium",
    )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "allowed_risk_levels": ["low"],
            "allowed_decision_statuses": ["eligible_for_analysis"],
            "exclude_blocked_by_risk": False,
            "exclude_insufficient_credit_data": False,
        },
    )

    assert response.status_code == 200
    excluded = {
        candidate["bond_id"]: candidate["exclusion_reasons"]
        for candidate in response.json()["excluded_candidates"]
    }
    assert "Risk level is not allowed" in excluded[bonds[0].id]
    assert "Decision status is not allowed" in excluded[bonds[0].id]


def test_issuer_concentration_cap_limits_allocation(
    client: TestClient,
    db_session: Session,
) -> None:
    run, company_a, _, _ = seed_portfolio_dataset(db_session)

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "top_n": 5,
            "max_position_weight": "0.20",
            "max_issuer_weight": "0.30",
            "max_high_risk_weight": "1",
        },
    )

    assert response.status_code == 200
    selected = response.json()["selected_candidates"]
    company_weight = sum(
        Decimal(str(candidate["allocation_weight"]))
        for candidate in selected
        if candidate["company_id"] == company_a.id
    )
    reasons = [
        reason
        for candidate in selected
        for reason in candidate["selection_reasons"]
    ]
    assert company_weight <= Decimal("0.30")
    assert "Allocation reduced by issuer concentration cap" in reasons


def test_high_risk_cap_limits_allocation(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, bonds = seed_portfolio_dataset(db_session)
    for bond in bonds[:2]:
        add_risk(
            db_session,
            bond=bond,
            as_of_date=date(2026, 2, 5),
            decision_status="eligible_for_analysis",
            risk_level="high",
        )

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "top_n": 3,
            "max_position_weight": "0.20",
            "max_issuer_weight": "1",
            "max_high_risk_weight": "0.30",
        },
    )

    assert response.status_code == 200
    selected = response.json()["selected_candidates"]
    high_risk_weight = sum(
        Decimal(str(candidate["allocation_weight"]))
        for candidate in selected
        if candidate["risk_level"] == "high"
    )
    reasons = [
        reason
        for candidate in selected
        for reason in candidate["selection_reasons"]
    ]
    assert high_risk_weight <= Decimal("0.30")
    assert "Allocation reduced by high-risk cap" in reasons


def test_position_cap_and_unallocated_capital_are_reported(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, _ = seed_portfolio_dataset(db_session)

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": run.id,
            "top_n": 2,
            "max_position_weight": "0.10",
            "max_issuer_weight": "1",
            "capital": "1000",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert all(
        Decimal(str(candidate["allocation_weight"])) <= Decimal("0.10")
        for candidate in payload["selected_candidates"]
    )
    assert Decimal(str(payload["summary"]["unallocated_weight"])) > 0
    assert Decimal(str(payload["summary"]["unallocated_capital"])) > 0


def test_no_prediction_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    with_predictions, _, _, _ = seed_portfolio_dataset(db_session)

    missing_all = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id},
    )
    missing_date = client.post(
        "/api/strategy/portfolio/construct",
        json={
            "model_run_id": with_predictions.id,
            "as_of_date": "2026-03-01",
        },
    )

    assert missing_all.status_code == 400
    assert missing_all.json()["detail"] == "No predictions found for selected model run"
    assert missing_date.status_code == 400
    assert missing_date.json()["detail"] == (
        "No predictions found for selected model run and as_of_date"
    )


def test_missing_and_non_completed_model_run_errors(
    client: TestClient,
    db_session: Session,
) -> None:
    running = create_run(db_session, status="running")

    missing = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": 999999},
    )
    non_completed = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": running.id},
    )

    assert missing.status_code == 404
    assert missing.json()["detail"] == "ML model run not found"
    assert non_completed.status_code == 400
    assert non_completed.json()["detail"] == "ML model run is not completed"


def test_invalid_portfolio_construction_request_returns_400(
    client: TestClient,
    db_session: Session,
) -> None:
    run = create_run(db_session)
    cases = [
        ({"capital": "0"}, "capital must be positive"),
        ({"top_n": 0}, "top_n must be positive"),
        (
            {"min_probability_positive": "1.1"},
            "min_probability_positive must be between 0 and 1",
        ),
        (
            {"max_position_weight": "0"},
            "max_position_weight must be greater than 0 and at most 1",
        ),
        (
            {"max_issuer_weight": "1.1"},
            "max_issuer_weight must be greater than 0 and at most 1",
        ),
        (
            {"max_high_risk_weight": "1.1"},
            "max_high_risk_weight must be between 0 and 1",
        ),
        ({"allowed_risk_levels": ["severe"]}, "Invalid risk level"),
        ({"allowed_decision_statuses": ["manual"]}, "Invalid decision status"),
    ]

    for override, detail in cases:
        response = client.post(
            "/api/strategy/portfolio/construct",
            json={"model_run_id": run.id, **override},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_portfolio_construction_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    run, _, _, _ = seed_portfolio_dataset(db_session)

    response = client.post(
        "/api/strategy/portfolio/construct",
        json={"model_run_id": run.id},
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())

