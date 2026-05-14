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
from app.services.moex_iss_client import (
    MoexHistoryResult,
    MoexIssClient,
    MoexIssClientError,
)


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeHttpClient:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, params: dict[str, Any]):
        self.calls.append({"path": path, "params": params})
        payload = self.payloads.pop(0)
        return FakeResponse(payload)


class FakeMoexClient:
    def __init__(
        self,
        rows_by_secid: dict[str, list[dict[str, Any]]] | None = None,
        *,
        error_by_secid: dict[str, Exception] | None = None,
        warnings_by_secid: dict[str, list[str]] | None = None,
    ) -> None:
        self.rows_by_secid = rows_by_secid or {}
        self.error_by_secid = error_by_secid or {}
        self.warnings_by_secid = warnings_by_secid or {}

    def fetch_history(
        self,
        *,
        secid: str,
        board: str,
        date_from: date,
        date_to: date,
    ) -> MoexHistoryResult:
        if secid in self.error_by_secid:
            raise self.error_by_secid[secid]
        return MoexHistoryResult(
            rows=self.rows_by_secid.get(secid, []),
            warnings=self.warnings_by_secid.get(secid, []),
        )


def moex_payload(rows: list[list[Any]]) -> dict[str, Any]:
    return {
        "history": {
            "columns": [
                "TRADEDATE",
                "LEGALCLOSEPRICE",
                "CLOSE",
                "ACCRUEDINT",
                "YIELDATWAPRICE",
                "DURATION",
                "VOLUME",
            ],
            "data": rows,
        }
    }


def create_company(db: Session, ticker: str = "MOEX") -> Company:
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
    isin: str | None = "RU000MOEX001",
    secid: str | None = "RU000MOEX001",
) -> Bond:
    bond = Bond(
        company_id=company.id,
        isin=isin,
        secid=secid,
        name=f"MOEX Bond {secid or isin}",
        currency="RUB",
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def sync_payload(
    *,
    bond_ids: list[int] | None,
    rebuild_existing: bool = False,
) -> dict[str, Any]:
    return {
        "bond_ids": bond_ids,
        "date_from": "2026-01-01",
        "date_to": "2026-01-31",
        "board": "TQCB",
        "rebuild_existing": rebuild_existing,
    }


def test_moex_client_parses_json_table_and_paginates() -> None:
    http_client = FakeHttpClient(
        [
            moex_payload([["2026-01-10", 100.5, 101.0, 2.1, 12.5, 730, 1000]]),
            moex_payload([]),
        ]
    )
    client = MoexIssClient(http_client=http_client)

    result = client.fetch_history(
        secid="RU000MOEX001",
        board="TQCB",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 31),
    )

    assert result.rows == [
        {
            "TRADEDATE": "2026-01-10",
            "LEGALCLOSEPRICE": 100.5,
            "CLOSE": 101.0,
            "ACCRUEDINT": 2.1,
            "YIELDATWAPRICE": 12.5,
            "DURATION": 730,
            "VOLUME": 1000,
        }
    ]
    assert [call["params"]["start"] for call in http_client.calls] == [0, 1]
    assert http_client.calls[0]["path"].endswith(
        "/iss/history/engines/stock/markets/bonds/boards/TQCB/securities/RU000MOEX001.json"
    )


def test_moex_sync_creates_market_snapshots(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MS1")
    bond = create_bond(db_session, company)
    fake_client = FakeMoexClient(
        {
            bond.secid: [
                {
                    "TRADEDATE": "2026-01-10",
                    "LEGALCLOSEPRICE": "100.50",
                    "CLOSE": "101.00",
                    "ACCRUEDINT": "2.10",
                    "YIELDATWAPRICE": "12.50",
                    "DURATION": "730",
                    "VOLUME": "1000000",
                }
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["processed_bonds"] == 1
    snapshot = db_session.execute(select(BondMarketSnapshot)).scalar_one()
    assert snapshot.source == "moex"
    assert snapshot.trade_date == date(2026, 1, 10)
    assert snapshot.price == Decimal("101.000000")
    assert snapshot.duration_years == Decimal("2.000")
    assert snapshot.raw_payload["mapping_notes"] == [
        "DURATION looked like days and was divided by 365"
    ]


def test_moex_sync_upserts_existing_snapshots_without_duplicates(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MS2")
    bond = create_bond(db_session, company, isin="RU000MOEX002", secid="RU000MOEX002")
    fake_client = FakeMoexClient(
        {
            bond.secid: [
                {"TRADEDATE": "2026-01-10", "CLOSE": "101.00"},
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    first = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))
    second = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["created"] == 1
    assert second.json()["skipped"] == 1
    assert db_session.execute(select(BondMarketSnapshot)).scalars().all().__len__() == 1


def test_moex_sync_updates_existing_when_rebuild_true(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MS3")
    bond = create_bond(db_session, company, isin="RU000MOEX003", secid="RU000MOEX003")
    fake_client = FakeMoexClient(
        {
            bond.secid: [
                {"TRADEDATE": "2026-01-10", "CLOSE": "101.00"},
            ]
        }
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )
    client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))
    fake_client.rows_by_secid[bond.secid] = [
        {"TRADEDATE": "2026-01-10", "CLOSE": "102.00"}
    ]

    response = client.post(
        "/api/market-data/moex/sync",
        json=sync_payload(bond_ids=[bond.id], rebuild_existing=True),
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    snapshot = db_session.execute(select(BondMarketSnapshot)).scalar_one()
    assert snapshot.price == Decimal("102.000000")


def test_moex_sync_missing_secid_is_item_error(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MS4")
    bond = create_bond(db_session, company, isin=None, secid=None)
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: FakeMoexClient(),
    )

    response = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["skipped_bonds"] == 1
    assert payload["errors"][0]["message"] == "Bond secid is missing"


def test_moex_sync_unknown_bond_id_is_item_error(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: FakeMoexClient(),
    )

    response = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[999]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_bonds"] == 1
    assert payload["skipped_bonds"] == 1
    assert payload["errors"][0]["message"] == "Bond not found"


def test_moex_sync_invalid_date_range_returns_400(client: TestClient) -> None:
    response = client.post(
        "/api/market-data/moex/sync",
        json={
            "date_from": "2026-02-01",
            "date_to": "2026-01-01",
            "board": "TQCB",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid date range"


def test_moex_unavailable_is_item_error(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MS5")
    bond = create_bond(db_session, company, isin="RU000MOEX005", secid="RU000MOEX005")
    fake_client = FakeMoexClient(
        error_by_secid={bond.secid: MoexIssClientError("MOEX request failed")}
    )
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["skipped_bonds"] == 1
    assert payload["errors"][0]["message"] == "MOEX request failed"


def test_empty_moex_response_adds_warning(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, "MS6")
    bond = create_bond(db_session, company, isin="RU000MOEX006", secid="RU000MOEX006")
    monkeypatch.setattr(
        "app.services.moex_market_data_service.MoexIssClient",
        lambda: FakeMoexClient({bond.secid: []}),
    )

    response = client.post("/api/market-data/moex/sync", json=sync_payload(bond_ids=[bond.id]))

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed_bonds"] == 1
    assert any("No MOEX history rows found" in warning for warning in payload["warnings"])


def test_no_ml_dependencies_added() -> None:
    requirements = open("backend/requirements.txt", encoding="utf-8").read().lower()
    forbidden = ("pandas", "numpy", "xgboost", "catboost", "tensorflow", "torch")

    assert all(package not in requirements for package in forbidden)
