from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.schemas.cashflow import BondTotalReturnLabelBuildRequest
from app.schemas.ml_dataset import BondMarketSnapshotCreate
from app.services.market_snapshot_service import MarketSnapshotService
from app.services.moex_iss_client import MoexCashflowScheduleResult, MoexIssClient
from app.services.total_return_label_service import TotalReturnLabelService


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeHttpClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, params: dict[str, Any]):
        self.calls.append({"path": path, "params": params})
        return FakeResponse(self.payload)


class FakeCashflowClient:
    def __init__(
        self,
        schedules: dict[str, MoexCashflowScheduleResult] | None = None,
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.schedules = schedules or {}
        self.errors = errors or {}

    def fetch_bond_cashflows(self, secid: str) -> MoexCashflowScheduleResult:
        if secid in self.errors:
            raise self.errors[secid]
        return self.schedules.get(secid, MoexCashflowScheduleResult())


def create_company(db: Session, ticker: str = "MCF") -> Company:
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
    isin: str | None = "RUCF0000001",
    secid: str | None = "RUCF0000001",
    currency: str = "RUB",
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        secid=secid,
        name=f"MOEX Cashflow Bond {secid or isin}",
        currency=currency,
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("10.000"),
        yield_to_maturity=Decimal("12.000"),
        duration_years=Decimal("2.000"),
        liquidity_score=80,
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def sync_payload(
    *,
    bond_ids: list[int] | None,
    date_from: str | None = None,
    date_to: str | None = None,
    rebuild_existing: bool = False,
) -> dict[str, Any]:
    return {
        "bond_ids": bond_ids,
        "date_from": date_from,
        "date_to": date_to,
        "rebuild_existing": rebuild_existing,
    }


def schedule(
    *,
    coupons: list[dict[str, Any]] | None = None,
    amortizations: list[dict[str, Any]] | None = None,
    offers: list[dict[str, Any]] | None = None,
    redemptions: list[dict[str, Any]] | None = None,
) -> MoexCashflowScheduleResult:
    return MoexCashflowScheduleResult(
        coupons=coupons or [],
        amortizations=amortizations or [],
        offers=offers or [],
        redemptions=redemptions or [],
    )


def test_client_parses_moex_payload_into_normalized_tables() -> None:
    payload = {
        "coupons": {
            "columns": ["coupondate", "value", "currencyid"],
            "data": [["2026-01-15", "12.50", "RUB"]],
        },
        "amortization_schedule": {
            "columns": ["amortdate", "valueprc"],
            "data": [["2026-02-15", "5.0"]],
        },
        "offers": {
            "columns": ["offerdate", "price"],
            "data": [["2026-03-15", "100.0"]],
        },
        "maturity": {
            "columns": ["date", "value"],
            "data": [["2026-04-15", "1000.0"]],
        },
    }
    http_client = FakeHttpClient(payload)
    client = MoexIssClient(http_client=http_client)

    result = client.fetch_bond_cashflows("RUCF0000001")

    assert result.coupons[0]["coupondate"] == "2026-01-15"
    assert result.coupons[0]["__moex_source_table"] == "coupons"
    assert result.amortizations[0]["__moex_source_table"] == "amortization_schedule"
    assert result.offers[0]["__moex_source_table"] == "offers"
    assert result.redemptions[0]["__moex_source_table"] == "maturity"
    assert http_client.calls[0]["path"].endswith(
        "/iss/statistics/engines/stock/markets/bonds/bondization/RUCF0000001.json"
    )


def test_sync_creates_coupon_cashflow_event(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF1")
    bond = create_bond(db_session, company)
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[{"coupondate": "2026-01-15", "value": "12.50"}]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1
    event = db_session.execute(select(BondCashflowEvent)).scalar_one()
    assert event.event_type == "coupon"
    assert event.source == "moex"
    assert event.amount == Decimal("12.500000")
    assert event.raw_payload["normalized_table"] == "coupons"


def test_sync_creates_amortization_and_offer_events(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF2")
    bond = create_bond(db_session, company, isin="RUCF0000002", secid="RUCF0000002")
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                amortizations=[{"amortdate": "2026-02-15", "valueprc": "5.0"}],
                offers=[{"offerdate": "2026-03-15", "price": "100.0"}],
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )

    assert response.status_code == 200
    assert response.json()["created"] == 2
    events = list(
        db_session.execute(
            select(BondCashflowEvent).order_by(BondCashflowEvent.event_date)
        ).scalars()
    )
    assert [event.event_type for event in events] == [
        "amortization",
        "offer_redemption",
    ]


def test_repeated_sync_skips_or_updates_existing_events(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF3")
    bond = create_bond(db_session, company, isin="RUCF0000003", secid="RUCF0000003")
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[{"coupondate": "2026-01-15", "value": "10.00"}]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    first = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )
    second = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )
    fake_client.schedules[bond.secid] = schedule(
        coupons=[{"coupondate": "2026-01-15", "value": "11.00"}]
    )
    third = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id], rebuild_existing=True),
    )

    assert first.json()["created"] == 1
    assert second.json()["skipped"] == 1
    assert third.json()["updated"] == 1
    events = list(db_session.execute(select(BondCashflowEvent)).scalars())
    assert len(events) == 1
    assert events[0].amount == Decimal("11.000000")


def test_missing_secid_and_unknown_bond_are_item_errors(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF4")
    bond = create_bond(db_session, company, isin=None, secid=None)
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: FakeCashflowClient(),
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id, 999]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_bonds"] == 2
    assert payload["skipped_bonds"] == 2
    assert {error["message"] for error in payload["errors"]} == {
        "Bond not found",
        "Bond secid is missing",
    }


def test_invalid_rows_are_warnings_not_500(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF5")
    bond = create_bond(db_session, company, isin="RUCF0000005", secid="RUCF0000005")
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[
                    {"coupondate": "bad-date", "value": "10.00"},
                    {"coupondate": "2026-01-15", "value": "bad-amount"},
                    {"coupondate": "2026-01-16"},
                ]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 0
    assert payload["skipped"] == 3
    assert len(payload["warnings"]) == 3


def test_cashflow_currency_is_canonical_shared_and_fail_closed(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCFCUR1")
    bond = create_bond(
        db_session,
        company,
        isin="RUCF0000101",
        secid="RUCF0000101",
        currency="USD",
    )
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[
                    {
                        "coupondate": "2026-01-15",
                        "value": "10.00",
                        "currencyid": "SUR",
                    },
                    {"coupondate": "2026-02-15", "value": "11.00"},
                    {
                        "coupondate": "2026-03-15",
                        "value": "12.00",
                        "currencyid": "CNY",
                    },
                    {
                        "coupondate": "2026-04-15",
                        "value": "13.00",
                        "currencyid": "12X",
                    },
                ]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 3
    assert payload["skipped"] == 1
    events = list(
        db_session.execute(
            select(BondCashflowEvent).order_by(BondCashflowEvent.event_date)
        ).scalars()
    )
    assert [event.currency for event in events] == ["RUB", "USD", "CNY"]
    assert payload["warnings"][-1]["message"] == (
        "MOEX coupons row skipped: bond_currency_unresolved"
    )


def test_cashflow_missing_currency_skips_when_bond_currency_is_unresolved(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCFCUR2")
    bond = create_bond(
        db_session,
        company,
        isin="RUCF0000102",
        secid="RUCF0000102",
        currency="12X",
    )
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[{"coupondate": "2026-01-15", "value": "10.00"}]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 0
    assert payload["skipped"] == 1
    assert payload["warnings"][0]["message"] == (
        "MOEX coupons row skipped: bond_currency_unresolved"
    )
    assert db_session.scalar(select(func.count()).select_from(BondCashflowEvent)) == 0


def test_date_filter_imports_only_matching_events(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF6")
    bond = create_bond(db_session, company, isin="RUCF0000006", secid="RUCF0000006")
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[
                    {"coupondate": "2026-01-15", "value": "10.00"},
                    {"coupondate": "2026-03-15", "value": "12.00"},
                ]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(
            bond_ids=[bond.id],
            date_from="2026-03-01",
            date_to="2026-03-31",
        ),
    )

    assert response.status_code == 200
    assert response.json()["created"] == 1
    event = db_session.execute(select(BondCashflowEvent)).scalar_one()
    assert event.event_date == date(2026, 3, 15)


def test_complete_schedule_updates_structure_before_date_filter(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCFSTRUCT")
    bond = create_bond(
        db_session,
        company,
        isin="RUCF0000100",
        secid="RUCF0000100",
    )
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                amortizations=[
                    {
                        "amortdate": "2030-02-15",
                        "valueprc": "5.0",
                        "__moex_source_table": "amortizations",
                    }
                ],
                offers=[
                    {
                        "offerdate": "2030-03-15",
                        "price": "100.0",
                        "__moex_source_table": "offers",
                    }
                ],
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(
            bond_ids=[bond.id],
            date_from="2026-01-01",
            date_to="2026-12-31",
        ),
    )

    assert response.status_code == 200
    assert response.json()["created"] == 0
    profile = db_session.execute(
        select(BondSecurityMasterProfile).where(
            BondSecurityMasterProfile.bond_id == bond.id
        )
    ).scalar_one()
    assert profile.amortization_structure == "amortizing"
    assert profile.offer_structure == "present"
    assert db_session.scalar(select(func.count()).select_from(BondCashflowEvent)) == 0


def test_invalid_date_range_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(
            bond_ids=None,
            date_from="2026-04-01",
            date_to="2026-03-01",
        ),
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid date range"


def test_moex_coupon_sync_feeds_total_return_labels(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MCF7")
    bond = create_bond(db_session, company, isin="RUCF0000007", secid="RUCF0000007")
    MarketSnapshotService(db_session).create_or_update(
        BondMarketSnapshotCreate(
            bond_id=bond.id,
            trade_date=date(2026, 1, 10),
            price=Decimal("100.00"),
            clean_price=Decimal("100.00"),
            dirty_price=Decimal("100.00"),
            nkd=Decimal("0.00"),
            yield_to_maturity=Decimal("12.00"),
            duration_years=Decimal("2.00"),
            volume=Decimal("1000000.00"),
            liquidity_score=80,
            source="manual",
        )
    )
    MarketSnapshotService(db_session).create_or_update(
        BondMarketSnapshotCreate(
            bond_id=bond.id,
            trade_date=date(2026, 2, 9),
            price=Decimal("99.00"),
            clean_price=Decimal("99.00"),
            dirty_price=Decimal("99.00"),
            nkd=Decimal("0.00"),
            yield_to_maturity=Decimal("12.00"),
            duration_years=Decimal("2.00"),
            volume=Decimal("1000000.00"),
            liquidity_score=80,
            source="manual",
        )
    )
    fake_client = FakeCashflowClient(
        {
            bond.secid: schedule(
                coupons=[{"coupondate": "2026-01-25", "value": "25.00"}]
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_cashflow_service.MoexIssClient",
        lambda: fake_client,
    )

    sync_response = client.post(
        "/api/market-data/moex/cashflows/sync",
        json=sync_payload(bond_ids=[bond.id]),
    )
    build_result = TotalReturnLabelService(db_session).build_labels(
        BondTotalReturnLabelBuildRequest(
            as_of_date_from=date(2026, 1, 10),
            as_of_date_to=date(2026, 1, 10),
            horizon_days=30,
            bond_ids=[bond.id],
            return_method="total_return",
            rebuild_existing=True,
        )
    )

    assert sync_response.status_code == 200
    assert build_result.created == 1
    label = db_session.execute(select(BondReturnLabel)).scalar_one()
    assert label.return_method == "total_return"
    assert label.coupon_return is not None
    assert label.coupon_return > 0
