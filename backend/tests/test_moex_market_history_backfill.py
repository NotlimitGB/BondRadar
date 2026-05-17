from datetime import date
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.services.moex_iss_client import MoexIssClient, MoexIssClientError
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


BACKFILL_URL = "/api/market-data/moex/bonds/history/backfill"


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


class FakeHistoryClient:
    def __init__(
        self,
        pages_by_secid: dict[str, list[list[dict[str, Any]]]] | None = None,
        *,
        errors_by_secid: dict[str, Exception] | None = None,
        warnings_by_secid: dict[str, list[str]] | None = None,
    ) -> None:
        self.pages_by_secid = pages_by_secid or {}
        self.errors_by_secid = errors_by_secid or {}
        self.warnings_by_secid = warnings_by_secid or {}
        self.calls: list[dict[str, Any]] = []

    def fetch_bond_market_history(
        self,
        secid: str,
        *,
        board: str,
        date_from: date,
        date_to: date,
        start: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        self.calls.append(
            {
                "secid": secid,
                "board": board,
                "date_from": date_from,
                "date_to": date_to,
                "start": start,
                "limit": limit,
            }
        )
        if secid in self.errors_by_secid:
            raise self.errors_by_secid[secid]
        page_index = start // limit
        pages = self.pages_by_secid.get(secid, [])
        rows = pages[page_index] if page_index < len(pages) else []
        warnings = self.warnings_by_secid.get(secid, []) if page_index == 0 else []
        return rows, warnings


def create_company(db: Session, ticker: str = "MH") -> Company:
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
    secid: str | None = "RU000HIST001",
    isin: str | None = "RU000HIST001",
) -> Bond:
    bond = Bond(
        company_id=company.id,
        secid=secid,
        isin=isin,
        name=f"History Bond {secid or isin}",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def backfill_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "board": "TQCB",
        "page_size": 100,
        "max_pages_per_bond": 100,
        "rebuild_existing": False,
        "skip_bonds_without_secid": True,
        "source": "moex",
    }
    payload.update(overrides)
    return payload


def history_row(
    secid: str,
    trade_date: str = "2026-01-10",
    close_price: str | None = "101.00",
    **overrides: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "secid": secid,
        "trade_date": trade_date,
        "close_price": close_price,
        "market_price": None,
        "weighted_average_price": "100.50",
        "last_price": None,
        "legal_close_price": "100.00",
        "yield_to_maturity": "12.50",
        "duration": "365",
        "volume": "1000000",
        "value": "1000000",
        "num_trades": "42",
        "currency": "RUB",
        "board": "TQCB",
        "raw": {"SECID": secid, "TRADEDATE": trade_date, "CLOSE": close_price},
    }
    row.update(overrides)
    return row


def test_client_parses_moex_history_table_into_canonical_rows() -> None:
    payload = {
        "history": {
            "columns": [
                "SECID",
                "TRADEDATE",
                "CLOSE",
                "MARKETPRICE2",
                "WAPRICE",
                "YIELDCLOSE",
                "DURATION",
                "VOLUME",
                "VALUE",
                "NUMTRADES",
                "BOARDID",
                "FACEUNIT",
            ],
            "data": [
                [
                    "RU000HIST001",
                    "2026-01-10",
                    "101.00",
                    "100.80",
                    "100.50",
                    "12.5",
                    "365",
                    "1000",
                    "100000",
                    "5",
                    "TQCB",
                    "RUB",
                ]
            ],
        }
    }
    http_client = FakeHttpClient(payload)
    moex_client = MoexIssClient(http_client=http_client)

    rows, warnings = moex_client.fetch_bond_market_history(
        "RU000HIST001",
        board="TQCB",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
        start=50,
        limit=25,
    )

    assert warnings == []
    assert rows[0]["secid"] == "RU000HIST001"
    assert rows[0]["trade_date"] == "2026-01-10"
    assert rows[0]["close_price"] == "101.00"
    assert rows[0]["market_price"] == "100.80"
    assert rows[0]["weighted_average_price"] == "100.50"
    assert rows[0]["yield_to_maturity"] == "12.5"
    assert rows[0]["num_trades"] == "5"
    assert rows[0]["raw"]["CLOSE"] == "101.00"
    assert http_client.calls[0]["params"]["start"] == 50
    assert http_client.calls[0]["params"]["limit"] == 25


def test_backfill_creates_market_snapshots(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH1")
    bond = create_bond(db_session, company)
    fake_client = FakeHistoryClient(
        {
            bond.secid: [
                [
                    history_row(bond.secid, "2026-01-10"),
                    history_row(bond.secid, "2026-01-11", close_price="102.00"),
                ],
                [],
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshots_created"] == 2
    assert payload["rows_fetched"] == 2
    snapshots = list(db_session.execute(select(BondMarketSnapshot)).scalars())
    assert len(snapshots) == 2
    assert snapshots[0].source == "moex"
    assert snapshots[0].price == Decimal("101.000000")
    assert snapshots[0].duration_years == Decimal("1.000")
    assert snapshots[0].raw_payload["canonical"]["num_trades"] == "42"


def test_repeated_backfill_skips_existing_by_default(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH2")
    bond = create_bond(db_session, company, secid="RU000HIST002", isin="RU000HIST002")
    fake_client = FakeHistoryClient({bond.secid: [[history_row(bond.secid)], []]})
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    first = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))
    second = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["snapshots_created"] == 1
    assert second.json()["snapshots_skipped"] == 1
    assert len(db_session.execute(select(BondMarketSnapshot)).scalars().all()) == 1


def test_rebuild_existing_updates_safe_fields_without_null_overwrite(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH3")
    bond = create_bond(db_session, company, secid="RU000HIST003", isin="RU000HIST003")
    existing = BondMarketSnapshot(
        bond_id=bond.id,
        trade_date=date(2026, 1, 10),
        price=Decimal("99.00"),
        yield_to_maturity=Decimal("9.00"),
        source="moex",
    )
    db_session.add(existing)
    db_session.commit()
    fake_client = FakeHistoryClient(
        {
            bond.secid: [
                [
                    history_row(
                        bond.secid,
                        close_price="103.00",
                        yield_to_maturity=None,
                    )
                ],
                [],
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        BACKFILL_URL,
        json=backfill_payload(bond_ids=[bond.id], rebuild_existing=True),
    )

    assert response.status_code == 200
    assert response.json()["snapshots_updated"] == 1
    snapshot = db_session.execute(select(BondMarketSnapshot)).scalar_one()
    assert snapshot.price == Decimal("103.000000")
    assert snapshot.yield_to_maturity == Decimal("9.000")


def test_scope_by_secids_works(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH4")
    bond = create_bond(db_session, company, secid="RU000HIST004", isin="RU000HIST004")
    fake_client = FakeHistoryClient({bond.secid: [[history_row(bond.secid)], []]})
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(BACKFILL_URL, json=backfill_payload(secids=[bond.secid]))

    assert response.status_code == 200
    assert response.json()["bonds_requested"] == 1
    assert response.json()["snapshots_created"] == 1
    assert fake_client.calls[0]["secid"] == bond.secid


def test_missing_secid_can_skip_or_fail(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH5")
    bond = create_bond(db_session, company, secid=None, isin=None)
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: FakeHistoryClient(),
    )

    skipped = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))
    failed = client.post(
        BACKFILL_URL,
        json=backfill_payload(
            bond_ids=[bond.id],
            skip_bonds_without_secid=False,
        ),
    )

    assert skipped.status_code == 200
    assert skipped.json()["bonds_skipped"] == 1
    assert skipped.json()["bond_results"][0]["status"] == "skipped"
    assert failed.status_code == 200
    assert failed.json()["bonds_failed"] == 1
    assert failed.json()["errors"][0]["message"] == "Bond secid is missing"


def test_invalid_optional_values_warn_and_do_not_become_zero(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH6")
    bond = create_bond(db_session, company, secid="RU000HIST006", isin="RU000HIST006")
    fake_client = FakeHistoryClient(
        {
            bond.secid: [
                [
                    history_row(
                        bond.secid,
                        close_price="bad-number",
                        yield_to_maturity="",
                        volume="100",
                    )
                ],
                [],
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshots_created"] == 1
    assert any("Invalid numeric value" in item["message"] for item in payload["warnings"])
    snapshot = db_session.execute(select(BondMarketSnapshot)).scalar_one()
    assert snapshot.price is None
    assert snapshot.volume == Decimal("100.00")


def test_missing_or_invalid_trade_date_skips_row(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH7")
    bond = create_bond(db_session, company, secid="RU000HIST007", isin="RU000HIST007")
    fake_client = FakeHistoryClient(
        {bond.secid: [[history_row(bond.secid, trade_date="bad-date")], []]}
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    assert response.json()["snapshots_skipped"] == 1
    assert response.json()["errors"][0]["message"] == (
        "MOEX history row trade date is missing or invalid"
    )
    assert db_session.execute(select(BondMarketSnapshot)).scalars().all() == []


def test_pagination_stops_on_empty_page(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH8")
    bond = create_bond(db_session, company, secid="RU000HIST008", isin="RU000HIST008")
    fake_client = FakeHistoryClient(
        {
            bond.secid: [
                [history_row(bond.secid, "2026-01-10")],
                [history_row(bond.secid, "2026-01-11")],
                [],
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        BACKFILL_URL,
        json=backfill_payload(bond_ids=[bond.id], page_size=1),
    )

    assert response.status_code == 200
    assert response.json()["snapshots_created"] == 2
    assert [call["start"] for call in fake_client.calls] == [0, 1, 2]


def test_max_pages_per_bond_warning(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH9")
    bond = create_bond(db_session, company, secid="RU000HIST009", isin="RU000HIST009")
    fake_client = FakeHistoryClient({bond.secid: [[history_row(bond.secid)]]})
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        BACKFILL_URL,
        json=backfill_payload(bond_ids=[bond.id], max_pages_per_bond=1),
    )

    assert response.status_code == 200
    assert any(
        warning["message"] == "MOEX history pagination stopped by max_pages_per_bond"
        for warning in response.json()["warnings"]
    )


def test_client_error_fails_one_bond_and_continues_next(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH10")
    failed_bond = create_bond(
        db_session,
        company,
        secid="RU000HIST010",
        isin="RU000HIST010",
    )
    completed_bond = create_bond(
        db_session,
        company,
        secid="RU000HIST011",
        isin="RU000HIST011",
    )
    fake_client = FakeHistoryClient(
        {completed_bond.secid: [[history_row(completed_bond.secid)], []]},
        errors_by_secid={
            failed_bond.secid: MoexIssClientError("MOEX request failed")
        },
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        BACKFILL_URL,
        json=backfill_payload(bond_ids=[failed_bond.id, completed_bond.id]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["bonds_failed"] == 1
    assert payload["bonds_processed"] == 1
    assert payload["snapshots_created"] == 1
    assert payload["bond_results"][0]["status"] == "failed"
    assert payload["bond_results"][1]["status"] == "completed"


def test_validation_errors_return_400(client: TestClient) -> None:
    cases = [
        (
            backfill_payload(date_from="2026-02-01", date_to="2026-01-01"),
            "Invalid date range",
        ),
        (
            backfill_payload(date_from="2010-01-01", date_to="2026-01-01"),
            "date range must not exceed 3660 days",
        ),
        (backfill_payload(page_size=0), "page_size must be between 1 and 500"),
        (backfill_payload(page_size=501), "page_size must be between 1 and 500"),
        (
            backfill_payload(max_pages_per_bond=0),
            "max_pages_per_bond must be between 1 and 10000",
        ),
        (
            backfill_payload(max_pages_per_bond=10001),
            "max_pages_per_bond must be between 1 and 10000",
        ),
        (
            backfill_payload(bond_ids=[1], secids=["RU000HIST012"]),
            "Use bond_ids or secids, not both",
        ),
        (backfill_payload(source=""), "source must not be empty"),
    ]

    for payload, detail in cases:
        response = client.post(BACKFILL_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_response_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MH11")
    bond = create_bond(db_session, company, secid="RU000HIST013", isin="RU000HIST013")
    fake_client = FakeHistoryClient({bond.secid: [[history_row(bond.secid)], []]})
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(BACKFILL_URL, json=backfill_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
