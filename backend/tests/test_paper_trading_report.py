from datetime import date
from decimal import Decimal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction


def create_company(db: Session, ticker: str = "PTR") -> Company:
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


def create_bond(db: Session, company: Company, index: int = 1) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000PTR{index:03d}",
        secid=f"PTR{index:03d}",
        name=f"Report Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def create_portfolio(
    db: Session,
    *,
    current_value: Decimal = Decimal("1000.000000"),
    cash_balance: Decimal = Decimal("1000.000000"),
) -> PaperPortfolio:
    portfolio = PaperPortfolio(
        name="Report portfolio",
        description=None,
        status="active",
        base_currency="RUB",
        initial_capital=Decimal("1000.000000"),
        cash_balance=cash_balance,
        current_value=current_value,
        model_run_id=None,
        return_method="risk_adjusted",
        horizon_days=30,
        params_json={},
        summary_json={},
        warnings_json=[],
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def add_snapshot(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    as_of_date: date,
    value: Decimal,
    cash: Decimal = Decimal("100.000000"),
    period_return: Decimal | None = Decimal("0.0000000000"),
) -> PaperPortfolioSnapshot:
    allocated = value - cash
    snapshot = PaperPortfolioSnapshot(
        portfolio_id=portfolio.id,
        as_of_date=as_of_date,
        portfolio_value=value,
        cash_balance=cash,
        allocated_value=allocated,
        allocated_weight=allocated / value if value > 0 else Decimal("0"),
        unallocated_weight=cash / value if value > 0 else Decimal("0"),
        positions_count=1,
        active_positions_count=1,
        cumulative_return=value / portfolio.initial_capital - Decimal("1"),
        period_return=period_return,
        metrics_json={},
        warnings_json=[],
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def add_position(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    bond: Bond,
    is_active: bool = True,
    current_amount: Decimal = Decimal("500.000000"),
    weight: Decimal = Decimal("0.5000000000"),
) -> PaperPortfolioPosition:
    position = PaperPortfolioPosition(
        portfolio_id=portfolio.id,
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=date(2026, 1, 1),
        allocation_weight=weight,
        allocation_amount=current_amount,
        current_amount=current_amount,
        probability_positive=Decimal("0.8000000000"),
        predicted_label="predicted_positive_return",
        yield_to_maturity=Decimal("12.000"),
        liquidity_score=90,
        decision_status="eligible_for_analysis",
        risk_level="low",
        is_active=is_active,
        source_model_run_id=None,
        source_prediction_id=None,
        source_details_json={},
    )
    db.add(position)
    db.commit()
    db.refresh(position)
    return position


def add_transaction(
    db: Session,
    *,
    portfolio: PaperPortfolio,
    transaction_type: str,
    as_of_date: date,
    amount_delta: Decimal,
    bond: Bond | None = None,
    fee_amount: Decimal | None = None,
) -> PaperPortfolioTransaction:
    transaction = PaperPortfolioTransaction(
        portfolio_id=portfolio.id,
        bond_id=None if bond is None else bond.id,
        transaction_type=transaction_type,
        as_of_date=as_of_date,
        amount_delta=amount_delta,
        weight_delta=None,
        fee_amount=fee_amount,
        portfolio_value_before=None,
        portfolio_value_after=None,
        details_json={},
    )
    db.add(transaction)
    db.commit()
    db.refresh(transaction)
    return transaction


def test_performance_report_for_portfolio_with_snapshots(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(
        db_session,
        current_value=Decimal("1100.000000"),
        cash_balance=Decimal("100.000000"),
    )
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 1, 1),
        value=Decimal("1000.000000"),
    )
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 2, 1),
        value=Decimal("1100.000000"),
        period_return=Decimal("0.1000000000"),
    )

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["portfolio_id"] == portfolio.id
    assert payload["name"] == "Report portfolio"
    assert payload["status"] == "active"
    assert payload["metrics"]["snapshot_count"] == 2
    assert payload["equity_curve"]
    assert Decimal(str(payload["metrics"]["cumulative_return"])) == Decimal("0.1")


def test_equity_curve_calculates_drawdown(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(
        db_session,
        current_value=Decimal("1100.000000"),
        cash_balance=Decimal("100.000000"),
    )
    values = [
        Decimal("1000.000000"),
        Decimal("1200.000000"),
        Decimal("900.000000"),
        Decimal("1100.000000"),
    ]
    for offset, value in enumerate(values):
        add_snapshot(
            db_session,
            portfolio=portfolio,
            as_of_date=date(2026, 1 + offset, 1),
            value=value,
        )

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
    )

    assert response.status_code == 200
    payload = response.json()
    assert Decimal(str(payload["metrics"]["max_drawdown"])) == Decimal("0.25")
    assert Decimal(str(payload["equity_curve"][2]["drawdown"])) == Decimal("0.25")


def test_annualized_return_is_null_with_one_snapshot(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 1, 1),
        value=Decimal("1000.000000"),
    )

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
    )

    assert response.status_code == 200
    assert response.json()["metrics"]["annualized_return"] is None


def test_fee_and_transaction_amount_metrics(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)
    company = create_company(db_session)
    bond = create_bond(db_session, company)
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 1, 1),
        value=Decimal("1000.000000"),
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="rebalance_fee",
        as_of_date=date(2026, 1, 1),
        amount_delta=Decimal("-2.000000"),
        fee_amount=Decimal("2.000000"),
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="period_return",
        as_of_date=date(2026, 1, 31),
        amount_delta=Decimal("40.000000"),
        bond=bond,
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="allocation_increase",
        as_of_date=date(2026, 1, 1),
        amount_delta=Decimal("300.000000"),
        bond=bond,
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="allocation_decrease",
        as_of_date=date(2026, 2, 1),
        amount_delta=Decimal("-50.000000"),
        bond=bond,
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="allocation_removed",
        as_of_date=date(2026, 3, 1),
        amount_delta=Decimal("-100.000000"),
        bond=bond,
    )

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
    )

    assert response.status_code == 200
    metrics = response.json()["metrics"]
    assert Decimal(str(metrics["total_fee_amount"])) == Decimal("2.000000")
    assert Decimal(str(metrics["total_period_return_amount"])) == Decimal("40.000000")
    assert Decimal(str(metrics["total_allocation_increase_amount"])) == Decimal("300.000000")
    assert Decimal(str(metrics["total_allocation_decrease_amount"])) == Decimal("50.000000")
    assert Decimal(str(metrics["total_removed_amount"])) == Decimal("100.000000")


def test_contributions_group_by_bond(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)
    company = create_company(db_session)
    bond = create_bond(db_session, company)
    add_position(db_session, portfolio=portfolio, bond=bond)
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="allocation_increase",
        as_of_date=date(2026, 1, 1),
        amount_delta=Decimal("500.000000"),
        bond=bond,
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="period_return",
        as_of_date=date(2026, 1, 31),
        amount_delta=Decimal("30.000000"),
        bond=bond,
    )

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/contributions"
    )

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["bond_id"] == bond.id
    assert item["bond_name"] == bond.name
    assert item["company_name"] == company.name
    assert Decimal(str(item["period_return_amount"])) == Decimal("30.000000")
    assert Decimal(str(item["allocation_increase_amount"])) == Decimal("500.000000")
    assert Decimal(str(item["net_amount_delta"])) == Decimal("530.000000")
    assert item["is_active"] is True


def test_contributions_support_inactive_filter(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)
    company = create_company(db_session)
    bond = create_bond(db_session, company)
    add_position(db_session, portfolio=portfolio, bond=bond, is_active=False)
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="allocation_removed",
        as_of_date=date(2026, 2, 1),
        amount_delta=Decimal("-500.000000"),
        bond=bond,
    )

    included = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/contributions"
    )
    excluded = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/contributions"
        "?include_inactive=false"
    )

    assert included.status_code == 200
    assert excluded.status_code == 200
    assert len(included.json()["items"]) == 1
    assert excluded.json()["items"] == []
    assert excluded.json()["warnings"]


def test_date_filters_snapshots_and_transactions(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)
    company = create_company(db_session)
    bond = create_bond(db_session, company)
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 1, 1),
        value=Decimal("1000.000000"),
    )
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 2, 1),
        value=Decimal("1050.000000"),
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="period_return",
        as_of_date=date(2026, 1, 1),
        amount_delta=Decimal("10.000000"),
        bond=bond,
    )
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="period_return",
        as_of_date=date(2026, 2, 1),
        amount_delta=Decimal("20.000000"),
        bond=bond,
    )

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
        "?date_from=2026-02-01&date_to=2026-02-01"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metrics"]["snapshot_count"] == 1
    assert payload["metrics"]["transaction_count"] == 1
    assert Decimal(str(payload["metrics"]["total_period_return_amount"])) == Decimal("20.000000")


def test_empty_snapshots_return_stable_warning_response(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)

    response = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["equity_curve"] == []
    assert payload["metrics"]["snapshot_count"] == 0
    assert payload["warnings"]


def test_invalid_filters_return_400(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)

    invalid_range = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
        "?date_from=2026-02-01&date_to=2026-01-01"
    )
    low_limit = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/contributions?limit=0"
    )
    high_limit = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/contributions?limit=501"
    )

    assert invalid_range.status_code == 400
    assert invalid_range.json()["detail"] == "Invalid date range"
    assert low_limit.status_code == 400
    assert low_limit.json()["detail"] == "limit must be between 1 and 500"
    assert high_limit.status_code == 400
    assert high_limit.json()["detail"] == "limit must be between 1 and 500"


def test_missing_portfolio_returns_404(client: TestClient) -> None:
    response = client.get("/api/paper-trading/portfolios/999999/performance")

    assert response.status_code == 404
    assert response.json()["detail"] == "Paper portfolio not found"


def test_report_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    portfolio = create_portfolio(db_session)
    company = create_company(db_session)
    bond = create_bond(db_session, company)
    add_snapshot(
        db_session,
        portfolio=portfolio,
        as_of_date=date(2026, 1, 1),
        value=Decimal("1000.000000"),
    )
    add_position(db_session, portfolio=portfolio, bond=bond)
    add_transaction(
        db_session,
        portfolio=portfolio,
        transaction_type="period_return",
        as_of_date=date(2026, 1, 31),
        amount_delta=Decimal("10.000000"),
        bond=bond,
    )

    performance = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/performance"
    ).json()
    curve = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/equity-curve"
    ).json()
    contributions = client.get(
        f"/api/paper-trading/portfolios/{portfolio.id}/contributions"
    ).json()

    assert_no_forbidden_investment_vocabulary([performance, curve, contributions])

