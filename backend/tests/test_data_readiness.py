from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport


DATE_FROM = date(2025, 1, 1)
DATE_TO = date(2025, 1, 10)
HORIZON_DAYS = 30


def dt(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 12, 0, tzinfo=timezone.utc)


def base_payload(**overrides):
    payload = {
        "date_from": DATE_FROM.isoformat(),
        "date_to": DATE_TO.isoformat(),
        "horizon_days": HORIZON_DAYS,
        "return_method": "risk_adjusted",
        "min_rows": 4,
        "min_positive_rows": 2,
        "min_negative_rows": 2,
        "max_insufficient_ratio": "0.30",
    }
    payload.update(overrides)
    return payload


def create_company(db: Session, ticker: str = "READY") -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
        inn=f"77{abs(hash(ticker)) % 100000000:08d}",
        country="RU",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(company)
    db.flush()
    return company


def create_bond(
    db: Session,
    company: Company,
    index: int,
    *,
    has_secid: bool = True,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000RDY{index:03d}"[:12],
        secid=f"RDY{index:03d}" if has_secid else None,
        name=f"{company.ticker} Bond {index}",
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        volume=Decimal("1000000.00"),
        maturity_date=date(2030, 1, 1),
        liquidity_score=80,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def seed_readiness_dataset(
    db: Session,
    *,
    labels: list[str] | None = None,
    include_labels: bool = True,
    include_cashflows: bool = True,
    report_published_at: datetime | None = dt(2024, 12, 31),
    secids: tuple[bool, bool] = (True, True),
    return_method: str = "risk_adjusted",
) -> tuple[Company, list[Bond]]:
    labels = labels or [
        "positive_return",
        "negative_return",
        "positive_return",
        "negative_return",
    ]
    company = create_company(db)
    report = FinancialReport(
        company_id=company.id,
        period_year=2024,
        period_quarter=0,
        revenue=Decimal("1000.00"),
        ebitda=Decimal("250.00"),
        net_debt=Decimal("300.00"),
        total_debt=Decimal("400.00"),
        cash=Decimal("100.00"),
        equity=Decimal("600.00"),
        short_term_debt=Decimal("100.00"),
        operating_cash_flow=Decimal("150.00"),
        net_profit=Decimal("120.00"),
        interest_expense=Decimal("50.00"),
        source="readiness-test",
        published_at=report_published_at,
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(report)
    db.flush()
    bonds = [
        create_bond(db, company, 1, has_secid=secids[0]),
        create_bond(db, company, 2, has_secid=secids[1]),
    ]
    db.add(
        CompanyCreditHealthSnapshot(
            company_id=company.id,
            as_of_date=DATE_TO,
            financial_report_id=report.id,
            credit_health_score=82,
            credit_status="credit_stable",
            risk_level="low",
            data_quality_level="high",
            debt_to_ebitda=Decimal("1.200000"),
            interest_coverage=Decimal("5.000000"),
            cash_to_short_term_debt=Decimal("1.000000"),
            ocf_to_total_debt=Decimal("0.300000"),
            debt_to_equity=Decimal("0.600000"),
            net_profit_margin=Decimal("0.120000"),
            risk_factors=[],
            positive_factors=["Stable financial profile"],
            missing_data=[],
            explanation={"test": True},
        )
    )
    for bond in bonds:
        db.add(
            BondRiskAssessment(
                bond_id=bond.id,
                company_id=company.id,
                as_of_date=DATE_TO,
                assessment_score=80,
                decision_status="eligible_for_analysis",
                risk_level="low",
                required_risk_premium=Decimal("0.010000"),
                yield_to_maturity=Decimal("12.000"),
                coupon_rate=Decimal("10.000"),
                duration_years=Decimal("2.000"),
                liquidity_score=80,
                volume=Decimal("1000000.00"),
                company_credit_status="credit_stable",
                company_credit_health_score=82,
                gates={},
                warnings=[],
                blocking_reasons=[],
                positive_factors=[],
                negative_factors=[],
                missing_data=[],
                explanation={"test": True},
            )
        )
        if include_cashflows:
            db.add(
                BondCashflowEvent(
                    bond_id=bond.id,
                    event_date=DATE_FROM + timedelta(days=5),
                    event_type="coupon",
                    amount=Decimal("20.000000"),
                    currency="RUB",
                    source="readiness-test",
                    raw_payload={"test": True},
                )
            )

    for index, label in enumerate(labels):
        bond = bonds[index % len(bonds)]
        as_of_date = DATE_FROM + timedelta(days=index)
        db.add(
            BondMarketSnapshot(
                bond_id=bond.id,
                trade_date=as_of_date,
                price=Decimal("100.000000"),
                clean_price=Decimal("100.000000"),
                dirty_price=Decimal("101.000000"),
                nkd=Decimal("10.000000"),
                yield_to_maturity=Decimal("12.000"),
                duration_years=Decimal("2.000"),
                volume=Decimal("1000000.00"),
                liquidity_score=80,
                source="readiness-test",
                raw_payload={"test": True},
            )
        )
        db.add(
            BondFeatureSnapshot(
                bond_id=bond.id,
                company_id=company.id,
                as_of_date=as_of_date,
                financial_report_id=report.id,
                yield_to_maturity=Decimal("12.000"),
                duration_years=Decimal("2.000"),
                liquidity_score=80,
                volume=Decimal("1000000.00"),
                missing_data_count=0,
                features_json={"test": True},
            )
        )
        if include_labels:
            label_binary = None
            future_return = None
            if label == "positive_return":
                label_binary = 1
                future_return = Decimal("0.020000")
            elif label == "negative_return":
                label_binary = 0
                future_return = Decimal("-0.020000")
            db.add(
                BondReturnLabel(
                    bond_id=bond.id,
                    as_of_date=as_of_date,
                    horizon_days=HORIZON_DAYS,
                    return_method=return_method,
                    future_return=future_return,
                    price_return=future_return,
                    net_total_return=future_return,
                    risk_adjusted_excess_return=future_return,
                    label=label,
                    label_binary=label_binary,
                )
            )
    db.commit()
    return company, bonds


def gate(payload: dict, name: str) -> dict:
    return next(item for item in payload["gates"] if item["name"] == name)


def test_no_selected_bonds_returns_not_ready(client: TestClient) -> None:
    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert gate(payload, "selected_bonds")["status"] == "fail"
    assert payload["summary"]["ready_for_ml_training"] is False


def test_ready_dataset_returns_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session)

    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["summary"]["ready_for_ml_training"] is True
    assert payload["summary"]["selected_bonds_count"] == 2
    assert payload["summary"]["positive_label_count"] == 2
    assert payload["summary"]["negative_label_count"] == 2
    assert payload["summary"]["joined_feature_label_row_count"] == 4
    assert all(item["status"] == "pass" for item in payload["gates"])


def test_missing_secid_fail_and_warning_behavior(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session, secids=(False, False))
    all_missing = client.post("/api/data-readiness/check", json=base_payload())

    assert all_missing.status_code == 200
    assert all_missing.json()["status"] == "not_ready"
    assert gate(all_missing.json(), "moex_secid_coverage")["status"] == "fail"


def test_partial_missing_secid_is_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session, secids=(True, False))

    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "warning"
    assert gate(payload, "moex_secid_coverage")["status"] == "warning"
    assert payload["summary"]["bonds_without_secid_count"] == 1


def test_future_published_report_does_not_count(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session, report_published_at=dt(2025, 2, 1))

    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["summary"]["financial_report_count"] == 0
    assert gate(payload, "financial_report_coverage")["status"] == "fail"


def test_missing_cashflows_warning_by_default_and_fail_when_required(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session, include_cashflows=False)

    warning_response = client.post("/api/data-readiness/check", json=base_payload())
    fail_response = client.post(
        "/api/data-readiness/check",
        json=base_payload(require_cashflows=True),
    )

    assert warning_response.status_code == 200
    assert warning_response.json()["status"] == "warning"
    assert gate(warning_response.json(), "cashflow_coverage")["status"] == "warning"
    assert fail_response.status_code == 200
    assert fail_response.json()["status"] == "not_ready"
    assert gate(fail_response.json(), "cashflow_coverage")["status"] == "fail"


def test_no_labels_fails_label_gate(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session, include_labels=False)

    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert gate(payload, "label_rows")["status"] == "fail"


def test_one_class_labels_fail_class_balance(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session, labels=["positive_return"] * 4)

    response = client.post(
        "/api/data-readiness/check",
        json=base_payload(min_negative_rows=1),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert gate(payload, "class_balance")["status"] == "fail"
    assert payload["summary"]["negative_label_count"] == 0


def test_too_many_insufficient_labels_fails_ratio(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(
        db_session,
        labels=[
            "positive_return",
            "negative_return",
            "positive_return",
            "negative_return",
            "insufficient_data",
            "insufficient_data",
            "insufficient_data",
            "insufficient_data",
            "insufficient_data",
            "insufficient_data",
        ],
    )

    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert gate(payload, "insufficient_ratio")["status"] == "fail"
    assert Decimal(payload["summary"]["insufficient_ratio"]) == Decimal("0.6")


def test_invalid_requests_return_400(client: TestClient) -> None:
    cases = [
        (base_payload(date_from="2025-02-01", date_to="2025-01-01"), "Invalid date range"),
        (base_payload(horizon_days=0), "horizon_days must be positive"),
        (base_payload(return_method="not_real"), "Invalid return method"),
        (base_payload(min_rows=0), "min_rows must be positive"),
        (
            base_payload(max_insufficient_ratio="1.5"),
            "max_insufficient_ratio must be between 0 and 1",
        ),
        (base_payload(max_bond_issues=0), "max_bond_issues must be between 1 and 500"),
    ]

    for payload, detail in cases:
        response = client.post("/api/data-readiness/check", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_readiness_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    seed_readiness_dataset(db_session)

    response = client.post("/api/data-readiness/check", json=base_payload())

    assert response.status_code == 200
    payload = json.dumps(response.json()).lower()
    forbidden = [
        "buy",
        "sell",
        "hold",
        "strong_buy",
        "strong_sell",
        "must_buy",
        "must_sell",
        "покупать",
        "продавать",
    ]
    assert all(word not in payload for word in forbidden)
