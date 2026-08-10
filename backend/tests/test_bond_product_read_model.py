from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.bond_score import BondScore
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.company_score import CompanyScore
from app.models.enums import AnalysisSignal
from app.schemas.bond import BondRead
from app.services.bond_product_read_service import BondProductReadService
from app.services.bond_risk_assessment_service import BondRiskAssessmentService
from app.services.bond_score_service import BondScoreService
from app.services.company_credit_health_service import CompanyCreditHealthService
from app.services.moex_iss_client import MoexIssClient


def create_company(db: Session, ticker: str, *, signal: str = "neutral") -> Company:
    company = Company(
        name=f"{ticker} Issuer",
        ticker=ticker,
        sector="Industrials",
        inn=None,
        country="RU",
        credit_rating="A",
        signal=signal,
    )
    db.add(company)
    db.commit()
    db.refresh(company)
    return company


def create_bond(
    db: Session,
    company_id: int,
    *,
    suffix: int,
    name: str,
    signal: str = "neutral",
) -> Bond:
    bond = Bond(
        company_id=company_id,
        isin=f"RU{suffix:010d}",
        secid=f"BOND{suffix}",
        name=name,
        currency="RUB",
        nominal_value=Decimal("1000.00"),
        current_price=Decimal("90.000"),
        coupon_rate=Decimal("8.500"),
        yield_to_maturity=Decimal("10.000"),
        duration_years=Decimal("3.000"),
        volume=Decimal("100000.00"),
        maturity_date=date(2030, 1, 1),
        offer_date=date(2028, 1, 1),
        is_floating_coupon=False,
        is_subordinated=False,
        is_perpetual=False,
        amortization=False,
        liquidity_score=40,
        signal=signal,
        risk_notes="Existing bond note",
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def create_market(
    db: Session,
    bond: Bond,
    *,
    trade_date: date,
    source: str,
    price: str | None,
    yield_to_maturity: str | None = "12.000",
    duration_years: str | None = "2.000",
    volume: str | None = "900000.00",
    liquidity_score: int | None = 80,
) -> BondMarketSnapshot:
    snapshot = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=trade_date,
        source=source,
        price=Decimal(price) if price is not None else None,
        clean_price=Decimal("101.000000"),
        dirty_price=Decimal("102.000000"),
        nkd=Decimal("1.500000"),
        yield_to_maturity=(
            Decimal(yield_to_maturity) if yield_to_maturity is not None else None
        ),
        duration_years=(Decimal(duration_years) if duration_years is not None else None),
        volume=Decimal(volume) if volume is not None else None,
        liquidity_score=liquidity_score,
        spread_to_ofz=Decimal("0.012500"),
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def create_risk(
    db: Session,
    bond: Bond,
    *,
    as_of_date: date,
    assessment_score: int,
) -> BondRiskAssessment:
    assessment = BondRiskAssessment(
        bond_id=bond.id,
        company_id=bond.company_id,
        as_of_date=as_of_date,
        assessment_score=assessment_score,
        decision_status="watchlist",
        risk_level="medium",
        required_risk_premium=Decimal("0.015000"),
        yield_to_maturity=Decimal("18.000"),
        coupon_rate=bond.coupon_rate,
        duration_years=Decimal("5.000"),
        liquidity_score=55,
        volume=Decimal("700000.00"),
        company_credit_status="credit_watchlist",
        company_credit_health_score=65,
        company_score=Decimal("70.00"),
        bond_score=Decimal("68.00"),
        gates={"credit_gate": "warning"},
        warnings=["Synthetic warning"],
        blocking_reasons=[],
        positive_factors=["Synthetic positive factor"],
        negative_factors=["Synthetic negative factor"],
        missing_data=[],
        explanation={"summary": "Synthetic risk context"},
    )
    db.add(assessment)
    db.commit()
    db.refresh(assessment)
    return assessment


def as_decimal(value: str | int | float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def test_unified_bond_product_read_synthetic_smoke(
    client: TestClient,
    db_session: Session,
) -> None:
    issuer_a = create_company(db_session, "AAA")
    issuer_b = create_company(db_session, "BBB", signal="increased_risk")
    complete = create_bond(db_session, issuer_a.id, suffix=1, name="Alpha")
    market_only = create_bond(db_session, issuer_a.id, suffix=2, name="Beta")
    risk_only = create_bond(db_session, issuer_b.id, suffix=3, name="Gamma")
    neither = create_bond(db_session, issuer_b.id, suffix=4, name="Omega")

    create_market(
        db_session,
        complete,
        trade_date=date(2026, 1, 1),
        source="moex",
        price="95.000000",
    )
    create_market(
        db_session,
        complete,
        trade_date=date(2026, 2, 1),
        source="manual",
        price="96.000000",
    )
    winning_market = create_market(
        db_session,
        complete,
        trade_date=date(2026, 2, 1),
        source="moex",
        price="103.500000",
    )
    partial_market = create_market(
        db_session,
        market_only,
        trade_date=date(2026, 2, 2),
        source="moex",
        price="104.000000",
        yield_to_maturity=None,
        duration_years="1.500",
        volume=None,
        liquidity_score=None,
    )
    create_risk(
        db_session,
        complete,
        as_of_date=date(2026, 1, 1),
        assessment_score=50,
    )
    winning_risk = create_risk(
        db_session,
        complete,
        as_of_date=date(2026, 2, 1),
        assessment_score=77,
    )
    create_risk(
        db_session,
        risk_only,
        as_of_date=date(2026, 2, 1),
        assessment_score=66,
    )

    response = client.get("/api/bonds")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert [row["name"] for row in payload] == ["Alpha", "Beta", "Gamma", "Omega"]
    by_id = {row["id"]: row for row in payload}
    legacy_fields = set(BondRead.model_fields)
    assert all(legacy_fields <= row.keys() for row in payload)

    complete_payload = by_id[complete.id]
    assert complete_payload["issuer"] == {
        "id": issuer_a.id,
        "name": issuer_a.name,
        "ticker": issuer_a.ticker,
        "sector": issuer_a.sector,
        "inn": issuer_a.inn,
        "country": issuer_a.country,
        "credit_rating": issuer_a.credit_rating,
        "signal": issuer_a.signal,
    }
    assert complete_payload["latest_market"]["id"] == winning_market.id
    assert complete_payload["latest_market"]["trade_date"] == "2026-02-01"
    assert complete_payload["latest_market"]["source"] == "moex"
    assert as_decimal(complete_payload["latest_market"]["clean_price"]) == Decimal(
        "101.000000"
    )
    assert as_decimal(complete_payload["latest_market"]["dirty_price"]) == Decimal(
        "102.000000"
    )
    assert as_decimal(complete_payload["latest_market"]["nkd"]) == Decimal("1.500000")
    assert as_decimal(complete_payload["latest_market"]["spread_to_ofz"]) == Decimal(
        "0.012500"
    )
    assert as_decimal(complete_payload["current_price"]) == Decimal("103.500000")
    assert as_decimal(complete_payload["yield_to_maturity"]) == Decimal("12.000")
    assert as_decimal(complete_payload["duration_years"]) == Decimal("2.000")
    assert as_decimal(complete_payload["volume"]) == Decimal("900000.00")
    assert complete_payload["liquidity_score"] == 80
    assert as_decimal(complete_payload["coupon_rate"]) == complete.coupon_rate
    assert as_decimal(complete_payload["nominal_value"]) == complete.nominal_value
    assert complete_payload["latest_risk"]["id"] == winning_risk.id
    assert complete_payload["latest_risk"]["assessment_score"] == 77
    assert as_decimal(complete_payload["latest_risk"]["yield_to_maturity"]) == Decimal(
        "18.000"
    )
    assert complete_payload["latest_risk"]["explanation"] == {
        "summary": "Synthetic risk context"
    }

    partial_payload = by_id[market_only.id]
    assert partial_payload["latest_market"]["id"] == partial_market.id
    assert as_decimal(partial_payload["current_price"]) == Decimal("104.000000")
    assert as_decimal(partial_payload["yield_to_maturity"]) == market_only.yield_to_maturity
    assert as_decimal(partial_payload["duration_years"]) == Decimal("1.500")
    assert as_decimal(partial_payload["volume"]) == market_only.volume
    assert partial_payload["liquidity_score"] == market_only.liquidity_score
    assert partial_payload["latest_risk"] is None

    risk_only_payload = by_id[risk_only.id]
    assert risk_only_payload["latest_market"] is None
    assert risk_only_payload["latest_risk"]["assessment_score"] == 66
    assert as_decimal(risk_only_payload["current_price"]) == risk_only.current_price
    assert as_decimal(risk_only_payload["yield_to_maturity"]) == risk_only.yield_to_maturity
    assert as_decimal(risk_only_payload["duration_years"]) == risk_only.duration_years
    assert as_decimal(risk_only_payload["volume"]) == risk_only.volume
    assert risk_only_payload["liquidity_score"] == risk_only.liquidity_score

    neither_payload = by_id[neither.id]
    assert neither_payload["latest_market"] is None
    assert neither_payload["latest_risk"] is None

    detail = client.get(f"/api/bonds/{complete.id}")
    assert detail.status_code == 200
    assert detail.json() == complete_payload


def test_market_and_risk_latest_selection_is_deterministic(
    client: TestClient,
    db_session: Session,
) -> None:
    issuer = create_company(db_session, "DET")
    market_bond = create_bond(db_session, issuer.id, suffix=10, name="Market tie")
    create_market(
        db_session,
        market_bond,
        trade_date=date(2026, 3, 1),
        source="manual",
        price="101.000000",
    )
    greatest_non_moex_id = create_market(
        db_session,
        market_bond,
        trade_date=date(2026, 3, 1),
        source="vendor",
        price="102.000000",
    )
    risk_bond = create_bond(db_session, issuer.id, suffix=11, name="Risk date")
    create_risk(
        db_session,
        risk_bond,
        as_of_date=date(2026, 3, 1),
        assessment_score=41,
    )
    latest_risk = create_risk(
        db_session,
        risk_bond,
        as_of_date=date(2026, 3, 2),
        assessment_score=81,
    )

    payload = {row["id"]: row for row in client.get("/api/bonds").json()}

    assert payload[market_bond.id]["latest_market"]["id"] == greatest_non_moex_id.id
    assert payload[risk_bond.id]["latest_risk"]["id"] == latest_risk.id

    for dialect in (sqlite.dialect(), postgresql.dialect()):
        sql = " ".join(
            str(
                BondProductReadService._latest_risk_statement([risk_bond.id]).compile(
                    dialect=dialect,
                    compile_kwargs={"literal_binds": True},
                )
            )
            .lower()
            .split()
        )
        ordering = (
            "order by bond_risk_assessments.as_of_date desc, "
            "bond_risk_assessments.created_at desc, bond_risk_assessments.id desc"
        )
        assert ordering in sql


def test_filters_detail_404_and_orphan_contract(
    client: TestClient,
    db_session: Session,
) -> None:
    first = create_company(db_session, "FLT1")
    second = create_company(db_session, "FLT2")
    alpha = create_bond(db_session, first.id, suffix=20, name="Alpha", signal="neutral")
    create_bond(
        db_session,
        first.id,
        suffix=21,
        name="Beta",
        signal="increased_risk",
    )
    create_bond(db_session, second.id, suffix=22, name="Gamma", signal="neutral")

    company_rows = client.get(f"/api/bonds?company_id={first.id}").json()
    assert [row["name"] for row in company_rows] == ["Alpha", "Beta"]
    signal_rows = client.get("/api/bonds?signal=increased_risk").json()
    assert [row["name"] for row in signal_rows] == ["Beta"]
    page = client.get("/api/bonds?skip=1&limit=1").json()
    assert [row["name"] for row in page] == ["Beta"]
    assert client.get(f"/api/bonds/{alpha.id}").status_code == 200

    missing = client.get("/api/bonds/999999")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Bond not found."

    orphan = create_bond(db_session, 999999, suffix=23, name="Orphan")
    orphan_detail = client.get(f"/api/bonds/{orphan.id}")
    assert orphan_detail.status_code == 500
    assert orphan_detail.json()["detail"] == "Bond issuer not found."
    orphan_list = client.get("/api/bonds")
    assert orphan_list.status_code == 500
    assert orphan_list.json()["detail"] == "Bond issuer not found."


def test_get_paths_are_read_only_and_query_count_is_bounded(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    issuer = create_company(db_session, "READ")
    bonds = [
        create_bond(db_session, issuer.id, suffix=30 + index, name=f"Bond {index}")
        for index in range(5)
    ]
    create_market(
        db_session,
        bonds[0],
        trade_date=date(2026, 4, 1),
        source="moex",
        price="105.000000",
    )
    create_risk(
        db_session,
        bonds[0],
        as_of_date=date(2026, 4, 1),
        assessment_score=72,
    )

    models = (
        Bond,
        BondMarketSnapshot,
        BondRiskAssessment,
        CompanyCreditHealthSnapshot,
        BondScore,
        CompanyScore,
    )
    before_counts = {
        model: db_session.scalar(select(func.count()).select_from(model)) for model in models
    }
    before_bond = {
        field: getattr(bonds[0], field)
        for field in (
            "current_price",
            "yield_to_maturity",
            "duration_years",
            "volume",
            "liquidity_score",
            "updated_at",
        )
    }

    def forbidden(*_args, **_kwargs):
        raise AssertionError("GET product read must not calculate, sync, or access MOEX")

    monkeypatch.setattr(BondRiskAssessmentService, "assess_bond", forbidden)
    monkeypatch.setattr(BondRiskAssessmentService, "recalculate_all", forbidden)
    monkeypatch.setattr(CompanyCreditHealthService, "calculate_for_company", forbidden)
    monkeypatch.setattr(BondScoreService, "calculate_for_bond", forbidden)
    monkeypatch.setattr(MoexIssClient, "fetch_history", forbidden)

    engine = db_session.get_bind()
    selected: list[str] = []

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selected.append(statement)

    event.listen(engine, "before_cursor_execute", count_selects)
    try:
        start = len(selected)
        one = client.get("/api/bonds?limit=1")
        one_bond_selects = len(selected) - start
        start = len(selected)
        many = client.get("/api/bonds?limit=5")
        five_bond_selects = len(selected) - start
        detail = client.get(f"/api/bonds/{bonds[0].id}")
    finally:
        event.remove(engine, "before_cursor_execute", count_selects)

    assert one.status_code == many.status_code == detail.status_code == 200
    assert five_bond_selects <= one_bond_selects + 1
    assert five_bond_selects <= 5

    after_counts = {
        model: db_session.scalar(select(func.count()).select_from(model)) for model in models
    }
    db_session.expire_all()
    persisted_bond = db_session.get(Bond, bonds[0].id)
    assert after_counts == before_counts
    assert persisted_bond is not None
    assert {
        field: getattr(persisted_bond, field) for field in before_bond
    } == before_bond


def test_write_endpoints_remain_bond_read_compatible(
    client: TestClient,
    db_session: Session,
) -> None:
    issuer = create_company(db_session, "WRITE")
    created = client.post(
        "/api/bonds",
        json={
            "company_id": issuer.id,
            "isin": "RU0000000040",
            "name": "Write compatibility",
        },
    )
    assert created.status_code == 201
    assert set(created.json()) == set(BondRead.model_fields)
    assert "issuer" not in created.json()

    bond_id = created.json()["id"]
    updated = client.patch(
        f"/api/bonds/{bond_id}",
        json={"name": "Updated write compatibility"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated write compatibility"
    assert set(updated.json()) == set(BondRead.model_fields)

    deleted = client.delete(f"/api/bonds/{bond_id}")
    assert deleted.status_code == 204
