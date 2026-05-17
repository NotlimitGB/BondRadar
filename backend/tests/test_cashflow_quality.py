from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.company import Company
from app.models.enums import AnalysisSignal
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


AUDIT_URL = "/api/data-quality/cashflows/audit"


def create_company(db: Session, ticker: str = "CFQ") -> Company:
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
    missing_secid: bool = False,
    currency: str = "RUB",
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=f"RU000CFQ{index:03d}",
        secid=None if missing_secid else (secid if secid is not None else f"CFQ{index:03d}"),
        name=f"Cashflow Quality Bond {index}",
        currency=currency,
        nominal_value=Decimal("1000.00"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.flush()
    return bond


def add_event(
    db: Session,
    bond: Bond,
    event_date: date,
    event_type: str,
    *,
    amount: Decimal | None = Decimal("10.000000"),
    amount_percent: Decimal | None = None,
    currency: str = "RUB",
    source: str = "moex",
) -> None:
    db.add(
        BondCashflowEvent(
            bond_id=bond.id,
            event_date=event_date,
            event_type=event_type,
            amount=amount,
            amount_percent=amount_percent,
            currency=currency,
            source=source,
            raw_payload={"source": source},
        )
    )
    db.flush()


def audit_payload(**overrides) -> dict:
    payload = {
        "date_from": "2026-01-01",
        "date_to": "2026-01-05",
        "horizon_days": 365,
        "source": "moex",
        "require_future_cashflows": True,
        "require_coupon_events": False,
        "require_redemption_or_maturity": False,
        "max_duplicate_events_per_bond": 0,
        "maximum_days_without_future_event": 180,
        "include_bond_rows": True,
        "include_event_type_breakdown": True,
        "include_issue_details": True,
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


def test_ready_bond_with_future_coupon_and_redemption(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQA")
    bond = create_bond(db_session, company, 1)
    add_event(db_session, bond, date(2026, 1, 10), "coupon")
    add_event(db_session, bond, date(2026, 2, 10), "redemption", amount=Decimal("1000"))
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            require_coupon_events=True,
            require_redemption_or_maturity=True,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    row = payload["bond_rows"][0]
    assert row["status"] == "ready"
    assert row["future_event_count"] == 2
    assert payload["overview"]["ready_bond_count"] == 1
    assert payload["overview"]["coupon_event_count"] == 1
    assert payload["overview"]["redemption_event_count"] == 1


def test_bond_without_cashflows_is_not_ready(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQB")
    bond = create_bond(db_session, company, 2)
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "not_ready"
    assert "no_cashflows" in row["issues"]


def test_no_future_cashflows_is_not_ready_when_required(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQC")
    bond = create_bond(db_session, company, 3)
    add_event(db_session, bond, date(2026, 1, 2), "coupon")
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "not_ready"
    assert "no_future_cashflows" in row["issues"]


def test_missing_secid_issue(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQD")
    bond = create_bond(db_session, company, 4, missing_secid=True)
    add_event(db_session, bond, date(2026, 1, 10), "coupon")
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "not_ready"
    assert "missing_secid" in row["issues"]


def test_required_coupon_and_redemption_warnings(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQE")
    bond = create_bond(db_session, company, 5)
    add_event(db_session, bond, date(2026, 1, 10), "amortization")
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            require_coupon_events=True,
            require_redemption_or_maturity=True,
        ),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "warning"
    assert "no_coupon_events" in row["issues"]
    assert "no_redemption_or_maturity" in row["issues"]


def test_other_event_type_becomes_other_and_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQF")
    bond = create_bond(db_session, company, 6)
    add_event(db_session, bond, date(2026, 1, 10), "other")
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["other_event_count"] == 1
    assert "invalid_event_type" in row["issues"]
    assert response.json()["overview"]["other_event_count"] == 1


def test_missing_and_zero_amount_warnings(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQG")
    bond = create_bond(db_session, company, 7)
    add_event(db_session, bond, date(2026, 1, 10), "coupon", amount=None)
    add_event(db_session, bond, date(2026, 1, 11), "redemption", amount=Decimal("0"))
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "warning"
    assert "missing_amount" in row["issues"]
    assert "non_positive_amount" in row["issues"]
    assert row["missing_amount_count"] == 1
    assert row["non_positive_amount_count"] == 1


def test_currency_mismatch_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQH")
    bond = create_bond(db_session, company, 8, currency="RUB")
    add_event(db_session, bond, date(2026, 1, 10), "coupon", currency="USD")
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "warning"
    assert "currency_mismatch" in row["issues"]
    assert row["currency_mismatch_count"] == 1


def test_duplicate_events_warning_across_sources(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQI")
    bond = create_bond(db_session, company, 9)
    add_event(db_session, bond, date(2026, 1, 10), "coupon", source="moex")
    add_event(db_session, bond, date(2026, 1, 10), "coupon", source="manual")
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(bond_ids=[bond.id], source=None),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "warning"
    assert "duplicate_events" in row["issues"]
    assert row["duplicate_event_count"] == 1


def test_stale_future_schedule_warning(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQJ")
    bond = create_bond(db_session, company, 10)
    add_event(db_session, bond, date(2026, 8, 1), "coupon")
    db_session.commit()

    response = client.post(
        AUDIT_URL,
        json=audit_payload(
            bond_ids=[bond.id],
            maximum_days_without_future_event=30,
        ),
    )

    assert response.status_code == 200
    row = response.json()["bond_rows"][0]
    assert row["status"] == "warning"
    assert "stale_future_schedule" in row["issues"]


def test_scope_by_company_ids_bond_ids_and_secids(
    client: TestClient,
    db_session: Session,
) -> None:
    first_company = create_company(db_session, "CFQK")
    second_company = create_company(db_session, "CFQL")
    first = create_bond(db_session, first_company, 11, secid="CFQ011")
    second = create_bond(db_session, second_company, 12, secid="CFQ012")
    add_event(db_session, first, date(2026, 1, 10), "coupon")
    add_event(db_session, second, date(2026, 1, 10), "coupon")
    db_session.commit()

    by_bond = client.post(AUDIT_URL, json=audit_payload(bond_ids=[first.id]))
    by_company = client.post(
        AUDIT_URL,
        json=audit_payload(company_ids=[second_company.id]),
    )
    by_secid = client.post(AUDIT_URL, json=audit_payload(secids=[" cfq011 "]))

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
    company = create_company(db_session, "CFQM")
    bond = create_bond(db_session, company, 13)
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
    company = create_company(db_session, "CFQN")
    bonds = [create_bond(db_session, company, index) for index in (14, 15, 16)]
    for bond in bonds:
        add_event(db_session, bond, date(2026, 1, 10), "coupon")
    db_session.commit()

    paginated = client.post(
        AUDIT_URL,
        json=audit_payload(
            limit=1,
            offset=1,
            include_event_type_breakdown=False,
            include_issue_details=False,
        ),
    )
    hidden_rows = client.post(
        AUDIT_URL,
        json=audit_payload(include_bond_rows=False),
    )

    assert paginated.status_code == 200
    assert paginated.json()["total_bond_rows"] == 3
    assert len(paginated.json()["bond_rows"]) == 1
    assert paginated.json()["bond_rows"][0]["event_type_breakdown"] == []
    assert paginated.json()["bond_rows"][0]["issue_details"] == []
    assert hidden_rows.status_code == 200
    assert hidden_rows.json()["bond_rows"] == []
    assert hidden_rows.json()["overview"]["selected_bond_count"] == 3


def test_invalid_requests_return_400(client: TestClient) -> None:
    cases = [
        (
            audit_payload(date_from="2026-02-01", date_to="2026-01-01"),
            "Invalid date range",
        ),
        (
            audit_payload(date_from="2010-01-01", date_to="2026-01-01"),
            "date range must not exceed 3660 days",
        ),
        (audit_payload(horizon_days=0), "horizon_days must be positive"),
        (
            audit_payload(bond_ids=[1], company_ids=[1]),
            "Use only one selector type: bond_ids, company_ids, or secids",
        ),
        (
            audit_payload(max_duplicate_events_per_bond=-1),
            "max_duplicate_events_per_bond must be non-negative",
        ),
        (
            audit_payload(maximum_days_without_future_event=-1),
            "maximum_days_without_future_event must be non-negative",
        ),
        (audit_payload(limit=0), "limit must be between 1 and 500"),
        (audit_payload(limit=501), "limit must be between 1 and 500"),
        (audit_payload(offset=-1), "offset must be non-negative"),
        (audit_payload(source=""), "source must not be empty when provided"),
    ]

    for payload, detail in cases:
        response = client.post(AUDIT_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_audit_does_not_write_db_rows(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQO")
    bond = create_bond(db_session, company, 17)
    add_event(db_session, bond, date(2026, 1, 10), "coupon")
    db_session.commit()
    before = {
        "companies": count_rows(db_session, Company),
        "bonds": count_rows(db_session, Bond),
        "events": count_rows(db_session, BondCashflowEvent),
    }

    response = client.post(AUDIT_URL, json=audit_payload())

    after = {
        "companies": count_rows(db_session, Company),
        "bonds": count_rows(db_session, Bond),
        "events": count_rows(db_session, BondCashflowEvent),
    }
    assert response.status_code == 200
    assert after == before


def test_response_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
) -> None:
    company = create_company(db_session, "CFQP")
    bond = create_bond(db_session, company, 18)
    add_event(db_session, bond, date(2026, 1, 10), "coupon")
    db_session.commit()

    response = client.post(AUDIT_URL, json=audit_payload())

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
