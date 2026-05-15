import csv
import json
import re
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.enums import AnalysisSignal


def create_company(db: Session, ticker: str = "TRN") -> Company:
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


def create_bond(
    db: Session,
    company: Company,
    *,
    isin: str = "RU000TR0001",
    secid: str = "TR001",
    nominal_value: Decimal | None = Decimal("1000.00"),
    liquidity_score: int | None = 90,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        secid=secid,
        name=f"Total Return Bond {secid}",
        currency="RUB",
        nominal_value=nominal_value,
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=liquidity_score,
        volume=Decimal("1000000.00"),
        maturity_date=date(2030, 1, 1),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def post_snapshot(
    client: TestClient,
    bond: Bond,
    trade_date: date,
    *,
    clean_price: str = "100.00",
    dirty_price: str | None = None,
    nkd: str | None = "0.00",
    liquidity_score: int = 90,
) -> None:
    response = client.post(
        "/api/market-snapshots",
        json={
            "bond_id": bond.id,
            "trade_date": trade_date.isoformat(),
            "price": clean_price,
            "clean_price": clean_price,
            "dirty_price": dirty_price,
            "nkd": nkd,
            "yield_to_maturity": "12.500",
            "duration_years": "2.000",
            "volume": "1000000.00",
            "liquidity_score": liquidity_score,
            "source": "manual",
        },
    )
    assert response.status_code == 200


def add_feature(db: Session, bond: Bond, company: Company, as_of_date: date) -> None:
    db.add(
        BondFeatureSnapshot(
            bond_id=bond.id,
            company_id=company.id,
            as_of_date=as_of_date,
            bond_score=Decimal("70.00"),
            company_score=Decimal("80.00"),
            yield_to_maturity=Decimal("12.500"),
            duration_years=Decimal("2.000"),
            liquidity_score=80,
            volume=Decimal("1000000.00"),
            missing_data_count=0,
            features_json={},
        )
    )
    db.commit()


def create_risk_assessment(
    db: Session,
    bond: Bond,
    company: Company,
    *,
    as_of_date: date,
    required_risk_premium: Decimal,
) -> BondRiskAssessment:
    assessment = BondRiskAssessment(
        bond_id=bond.id,
        company_id=company.id,
        as_of_date=as_of_date,
        assessment_score=70,
        decision_status="watchlist",
        risk_level="medium",
        required_risk_premium=required_risk_premium,
        company_credit_status="credit_watchlist",
        gates={},
        warnings=[],
        blocking_reasons=[],
        positive_factors=[],
        negative_factors=[],
        missing_data=[],
        explanation={},
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


def build_cashflow_labels(
    client: TestClient,
    as_of_date: date,
    *,
    return_method: str = "total_return",
    rebuild_existing: bool = True,
):
    return client.post(
        "/api/cashflows/labels/build",
        json={
            "as_of_date_from": as_of_date.isoformat(),
            "as_of_date_to": as_of_date.isoformat(),
            "horizon_days": 30,
            "return_method": return_method,
            "rebuild_existing": rebuild_existing,
        },
    )


def test_cashflow_event_create_and_upsert(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "CFE")
    bond = create_bond(db_session, company)
    payload = {
        "bond_id": bond.id,
        "event_date": "2026-02-10",
        "event_type": "coupon",
        "amount": "20.50",
        "currency": "RUB",
        "source": "manual",
    }

    first = client.post("/api/cashflows/events", json=payload)
    second = client.post(
        "/api/cashflows/events",
        json={**payload, "amount": "21.00"},
    )
    listed = client.get(f"/api/cashflows/events?bond_id={bond.id}")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert Decimal(str(second.json()["amount"])) == Decimal("21.000000")
    assert len(listed.json()) == 1


def test_cashflow_amount_percent_conversion(
    client: TestClient, db_session: Session
) -> None:
    company = create_company(db_session, "PCT")
    bond = create_bond(db_session, company, nominal_value=Decimal("1000.00"))

    response = client.post(
        "/api/cashflows/events",
        json={
            "bond_id": bond.id,
            "event_date": "2026-02-10",
            "event_type": "coupon",
            "amount_percent": "2.0",
            "currency": "RUB",
            "source": "manual",
        },
    )

    assert response.status_code == 200
    assert Decimal(str(response.json()["amount"])) == Decimal("20.000000")


def test_total_return_label_includes_coupon_and_can_offset_price_decline(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "CPN")
    bond = create_bond(db_session, company)
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="99.00")
    client.post(
        "/api/cashflows/events",
        json={
            "bond_id": bond.id,
            "event_date": (as_of_date + timedelta(days=15)).isoformat(),
            "event_type": "coupon",
            "amount": "20.00",
            "currency": "RUB",
            "source": "manual",
        },
    )

    response = build_cashflow_labels(client, as_of_date)

    assert response.status_code == 200
    label = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=total_return"
    ).json()[0]
    assert Decimal(str(label["price_return"])) < 0
    assert Decimal(str(label["coupon_return"])) > 0
    assert Decimal(str(label["gross_total_return"])) > 0
    assert label["label"] == "positive_return"


def test_amortization_and_redemption_are_included(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "AMR")
    bond = create_bond(db_session, company, isin="RU000TR0002", secid="TR002")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="100.00")
    for event_type, amount in (("amortization", "30.00"), ("redemption", "50.00")):
        client.post(
            "/api/cashflows/events",
            json={
                "bond_id": bond.id,
                "event_date": (as_of_date + timedelta(days=20)).isoformat(),
                "event_type": event_type,
                "amount": amount,
                "currency": "RUB",
                "source": "manual",
            },
        )

    response = build_cashflow_labels(client, as_of_date)

    assert response.status_code == 200
    label = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=total_return"
    ).json()[0]
    assert Decimal(str(label["amortization_return"])) > 0
    assert Decimal(str(label["redemption_return"])) > 0
    assert Decimal(str(label["gross_total_return"])) > 0


def test_costs_reduce_net_total_return(client: TestClient, db_session: Session) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "CST")
    bond = create_bond(db_session, company, isin="RU000TR0003", secid="TR003")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="102.00")

    response = build_cashflow_labels(client, as_of_date)

    assert response.status_code == 200
    label = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=total_return"
    ).json()[0]
    assert Decimal(str(label["estimated_costs_return"])) > 0
    assert Decimal(str(label["net_total_return"])) < Decimal(
        str(label["gross_total_return"])
    )


def test_risk_adjusted_label_uses_required_premium(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "RAP")
    bond = create_bond(db_session, company, isin="RU000TR0004", secid="TR004")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="101.00")
    create_risk_assessment(
        db_session,
        bond,
        company,
        as_of_date=as_of_date,
        required_risk_premium=Decimal("0.050000"),
    )

    response = build_cashflow_labels(client, as_of_date, return_method="risk_adjusted")

    assert response.status_code == 200
    label = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=risk_adjusted"
    ).json()[0]
    assert Decimal(str(label["risk_adjusted_excess_return"])) <= 0
    assert label["label"] == "negative_return"
    assert label["required_risk_premium"] in ("0.050000", 0.05, "0.05")


def test_missing_end_snapshot_creates_insufficient_label(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "MIS")
    bond = create_bond(db_session, company, isin="RU000TR0005", secid="TR005")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")

    response = build_cashflow_labels(client, as_of_date)

    assert response.status_code == 200
    label = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=total_return"
    ).json()[0]
    assert label["label"] == "insufficient_data"
    assert label["label_binary"] is None
    assert "Start or end price is missing" in label["return_calculation_warnings"]


def test_build_labels_endpoint_creates_total_return_label(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "BLD")
    bond = create_bond(db_session, company, isin="RU000TR0006", secid="TR006")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="101.00")

    response = build_cashflow_labels(client, as_of_date)

    assert response.status_code == 200
    assert response.json()["created"] == 1
    labels = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=total_return"
    )
    assert labels.status_code == 200
    assert labels.json()[0]["return_method"] == "total_return"


def test_existing_price_dataset_build_still_uses_price_method(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "PRC")
    bond = create_bond(db_session, company, isin="RU000TR0007", secid="TR007")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="101.00")

    response = client.post(
        "/api/datasets/build",
        json={
            "as_of_date_from": as_of_date.isoformat(),
            "as_of_date_to": as_of_date.isoformat(),
            "horizon_days": 30,
        },
    )

    assert response.status_code == 200
    label = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=price"
    ).json()[0]
    assert label["return_method"] == "price"
    assert label["future_return"] == label["price_return"]


def test_dataset_export_filters_return_method_and_csv_fields(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "EXP")
    bond = create_bond(db_session, company, isin="RU000TR0008", secid="TR008")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="101.00")
    add_feature(db_session, bond, company, as_of_date)
    db_session.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=30,
            return_method="price",
            future_return=Decimal("0.010000"),
            price_return=Decimal("0.010000"),
            label="positive_return",
            label_binary=1,
        )
    )
    db_session.add(
        BondReturnLabel(
            bond_id=bond.id,
            as_of_date=as_of_date,
            horizon_days=30,
            return_method="total_return",
            future_return=Decimal("0.008500"),
            price_return=Decimal("0.010000"),
            gross_total_return=Decimal("0.010000"),
            estimated_costs_return=Decimal("0.001500"),
            net_total_return=Decimal("0.008500"),
            label="positive_return",
            label_binary=1,
        )
    )
    db_session.commit()

    price_export = client.get("/api/datasets/export?return_method=price")
    total_export = client.get("/api/datasets/export?return_method=total_return")
    csv_response = client.get("/api/datasets/export.csv?return_method=total_return")

    assert price_export.status_code == 200
    assert total_export.status_code == 200
    assert price_export.json()["rows"][0]["return_method"] == "price"
    assert total_export.json()["rows"][0]["return_method"] == "total_return"
    assert "text/csv" in csv_response.headers["content-type"]
    csv_rows = list(csv.DictReader(StringIO(csv_response.text)))
    assert "return_method" in csv_rows[0]
    assert "net_total_return" in csv_rows[0]
    assert "return_calculation_details" not in csv_rows[0]


def test_no_investment_recommendation_vocabulary(
    client: TestClient, db_session: Session
) -> None:
    as_of_date = date(2026, 1, 10)
    company = create_company(db_session, "VOC")
    bond = create_bond(db_session, company, isin="RU000TR0009", secid="TR009")
    post_snapshot(client, bond, as_of_date, clean_price="100.00")
    post_snapshot(client, bond, as_of_date + timedelta(days=30), clean_price="101.00")
    event_payload = client.post(
        "/api/cashflows/events",
        json={
            "bond_id": bond.id,
            "event_date": (as_of_date + timedelta(days=15)).isoformat(),
            "event_type": "coupon",
            "amount": "20.00",
            "currency": "RUB",
            "source": "manual",
        },
    ).json()
    build_payload = build_cashflow_labels(client, as_of_date).json()
    labels_payload = client.get(
        f"/api/datasets/labels?bond_id={bond.id}&return_method=total_return"
    ).json()
    text = json.dumps(
        [event_payload, build_payload, labels_payload],
        ensure_ascii=False,
    ).lower()
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
