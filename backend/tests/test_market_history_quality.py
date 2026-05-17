from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.enums import AnalysisSignal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


AUDIT_URL = "/api/data-quality/market-history/audit"


def create_company(db: Session, ticker: str = "MHQ") -> Company:
    company = Company(
        name=f"{ticker} Company",
        ticker=ticker,
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
    secid: str | None = None,
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000MHQ{index:03d}",
        secid=secid if secid is not None else f"MHQ{index:03d}",
        name=f"History Quality Bond {index}",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def add_snapshot(
    db: Session,
    bond: Bond,
    trade_date: date,
    *,
    price: Decimal | None = Decimal("100.000000"),
    yield_to_maturity: Decimal | None = Decimal("12.000"),
    volume: Decimal | None = Decimal("1000000.00"),
    source: str = "moex",
) -> None:
    db.add(
        BondMarketSnapshot(
            bond_id=bond.id,
            trade_date=trade_date,
            price=price,
            clean_price=price,
            yield_to_maturity=yield_to_maturity,
            duration_years=Decimal("2.000"),
            volume=volume,
            source=source,
            raw_payload={"source": source},
        )
    )
    db.flush()


def weekdays(date_from: date, date_to: date) -> list[date]:
    result: list[date] = []
    current = date_from
    while current <= date_to:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def audit_payload(**overrides) -> dict:
    payload = {
        "date_from": "2026-01-05",
        "date_to": "2026-01-09",
        "source": "moex",
        "expected_date_mode": "business_days",
        "minimum_snapshot_count": 1,
        "minimum_coverage_ratio": "0.70",
        "maximum_gap_days": 14,
        "require_price": True,
        "require_yield": False,
        "require_volume": False,
        "include_bond_rows": True,
        "include_gap_details": True,
        "limit": 100,
        "offset": 0,
    }
    payload.update(overrides)
    return payload


def count_rows(db: Session, model) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def test_empty_db_returns_stable_response(client: TestClient) -> None:
    response = client.post(AUDIT_URL, json=audit_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["overview"]["selected_bond_count"] == 0
    assert payload["bond_rows"] == []
    assert any("weekday approximation" in item["message"] for item in payload["warnings"])


def test_ready_bond_with_full_business_day_coverage(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQA")
    bond = create_bond(db_session, company, 1)
    for trade_date in weekdays(date(2026, 1, 5), date(2026, 1, 9)):
        add_snapshot(db_session, bond, trade_date)
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            minimum_snapshot_count=5,
            minimum_coverage_ratio="1.0",
            maximum_gap_days=0,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    row = payload["bond_rows"][0]
    assert row["status"] == "ready"
    assert row["coverage_ratio"] == "1"
    assert payload["overview"]["ready_bond_count"] == 1


def test_bond_without_snapshots_is_not_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQB")
    bond = create_bond(db_session, company, 2)
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "not_ready"
    assert "no_snapshots" in row["issues"]


def test_low_coverage_and_long_gap_are_detected(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQC")
    bond = create_bond(db_session, company, 3)
    add_snapshot(db_session, bond, date(2026, 1, 5))
    add_snapshot(db_session, bond, date(2026, 1, 30))
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            date_from="2026-01-05",
            date_to="2026-01-30",
            minimum_snapshot_count=1,
            minimum_coverage_ratio="0.80",
            maximum_gap_days=5,
        ),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert "low_coverage" in row["issues"]
    assert "long_gap" in row["issues"]
    assert row["longest_gap_days"] > 5


def test_missing_price_required_makes_not_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQD")
    bond = create_bond(db_session, company, 4)
    for trade_date in weekdays(date(2026, 1, 5), date(2026, 1, 9)):
        add_snapshot(db_session, bond, trade_date, price=None)
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            minimum_snapshot_count=5,
            minimum_coverage_ratio="1.0",
        ),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "not_ready"
    assert "missing_price" in row["issues"]


def test_missing_yield_and_volume_can_be_warning_when_required(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQE")
    bond = create_bond(db_session, company, 5)
    for trade_date in weekdays(date(2026, 1, 5), date(2026, 1, 9)):
        add_snapshot(
            db_session,
            bond,
            trade_date,
            yield_to_maturity=None,
            volume=None,
        )
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            minimum_snapshot_count=5,
            minimum_coverage_ratio="1.0",
            require_yield=True,
            require_volume=True,
        ),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "warning"
    assert "missing_yield" in row["issues"]
    assert "missing_volume" in row["issues"]


def test_suspicious_numeric_values_are_reported(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQF")
    bond = create_bond(db_session, company, 6)
    add_snapshot(
        db_session,
        bond,
        date(2026, 1, 7),
        price=Decimal("0"),
        yield_to_maturity=Decimal("-1.000"),
        volume=Decimal("-10.00"),
    )
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            expected_date_mode="observed_market_dates",
            require_yield=True,
            require_volume=True,
        ),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert "non_positive_price" in row["issues"]
    assert "negative_yield" in row["issues"]
    assert "negative_volume" in row["issues"]
    assert response.json()["issue_summary"]["non_positive_price_count"] == 1


def test_observed_market_dates_mode_uses_observed_dates(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQG")
    first = create_bond(db_session, company, 7)
    second = create_bond(db_session, company, 8)
    add_snapshot(db_session, first, date(2026, 1, 5))
    add_snapshot(db_session, second, date(2026, 1, 8))
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            expected_date_mode="observed_market_dates",
            minimum_snapshot_count=1,
        ),
    )

    assert response.status_code == 200
    assert response.json()["overview"]["expected_date_count"] == 2


def test_scope_by_company_ids_bond_ids_and_secids(
    client: TestClient,
    db_session: Session,
) -> None:
    first_company = create_company(db_session, "MHQH")
    second_company = create_company(db_session, "MHQI")
    first = create_bond(db_session, first_company, 9, secid="MHQ009")
    second = create_bond(db_session, second_company, 10, secid="MHQ010")
    add_snapshot(db_session, first, date(2026, 1, 5))
    add_snapshot(db_session, second, date(2026, 1, 5))
    db_session.commit()

    by_bond = client.post(AUDIT_URL, json=audit_payload(bond_ids=[first.id]))
    by_company = client.post(
        AUDIT_URL,
        json=audit_payload(company_ids=[second_company.id]),
    )
    by_secid = client.post(AUDIT_URL, json=audit_payload(secids=[" mhq009 "]))

    assert by_bond.status_code == 200
    assert by_company.status_code == 200
    assert by_secid.status_code == 200
    assert [row["bond_id"] for row in by_bond.json()["bond_rows"]] == [first.id]
    assert [row["bond_id"] for row in by_company.json()["bond_rows"]] == [second.id]
    assert [row["bond_id"] for row in by_secid.json()["bond_rows"]] == [first.id]


def test_duplicate_selectors_warn_and_dedupe(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQJ")
    bond = create_bond(db_session, company, 11)
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id, bond.id]))

    assert response.status_code == 200
    assert response.json()["total_bond_rows"] == 1
    assert any(
        warning["message"] == "Duplicate selectors were ignored"
        for warning in response.json()["warnings"]
    )


def test_pagination_and_include_flags_work(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQK")
    bonds = [create_bond(db_session, company, index) for index in (12, 13, 14)]
    for bond in bonds:
        add_snapshot(db_session, bond, date(2026, 1, 5))
    db_session.commit()

    paginated = client.post(
        AUDIT_URL,
        json=audit_payload(limit=1, offset=1, include_gap_details=False),
    )
    hidden_rows = client.post(
        AUDIT_URL,
        json=audit_payload(include_bond_rows=False),
    )

    assert paginated.status_code == 200
    assert paginated.json()["total_bond_rows"] == 3
    assert len(paginated.json()["bond_rows"]) == 1
    assert paginated.json()["bond_rows"][0]["gaps"] == []
    assert paginated.json()["bond_rows"][0]["gap_count"] > 0
    assert hidden_rows.status_code == 200
    assert hidden_rows.json()["bond_rows"] == []
    assert hidden_rows.json()["overview"]["selected_bond_count"] == 3


def test_invalid_requests_return_400(client: TestClient) -> None:
    cases = [
        (
            audit_payload(date_from="2026-01-10", date_to="2026-01-05"),
            "Invalid date range",
        ),
        (
            audit_payload(date_from="2010-01-01", date_to="2026-01-01"),
            "date range must not exceed 3660 days",
        ),
        (
            audit_payload(bond_ids=[1], company_ids=[1]),
            "Use only one selector type: bond_ids, company_ids, or secids",
        ),
        (
            audit_payload(expected_date_mode="calendar"),
            "Invalid expected date mode",
        ),
        (
            audit_payload(minimum_snapshot_count=0),
            "minimum_snapshot_count must be positive",
        ),
        (
            audit_payload(minimum_coverage_ratio="-0.1"),
            "minimum_coverage_ratio must be between 0 and 1",
        ),
        (
            audit_payload(minimum_coverage_ratio="1.1"),
            "minimum_coverage_ratio must be between 0 and 1",
        ),
        (
            audit_payload(maximum_gap_days=-1),
            "maximum_gap_days must be non-negative",
        ),
        (audit_payload(limit=0), "limit must be between 1 and 500"),
        (audit_payload(limit=501), "limit must be between 1 and 500"),
        (audit_payload(offset=-1), "offset must be non-negative"),
        (
            audit_payload(source=""),
            "source must not be empty when provided",
        ),
    ]

    for payload, detail in cases:
        response = client.post(AUDIT_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_audit_does_not_write_db_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQL")
    bond = create_bond(db_session, company, 15)
    add_snapshot(db_session, bond, date(2026, 1, 5))
    db_session.commit()
    before = {
        "companies": count_rows(db_session, Company),
        "bonds": count_rows(db_session, Bond),
        "snapshots": count_rows(db_session, BondMarketSnapshot),
    }

    response = client.post(AUDIT_URL, json=audit_payload())

    after = {
        "companies": count_rows(db_session, Company),
        "bonds": count_rows(db_session, Bond),
        "snapshots": count_rows(db_session, BondMarketSnapshot),
    }
    assert response.status_code == 200
    assert after == before


def test_response_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "MHQM")
    bond = create_bond(db_session, company, 16)
    add_snapshot(db_session, bond, date(2026, 1, 5))
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload())

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
