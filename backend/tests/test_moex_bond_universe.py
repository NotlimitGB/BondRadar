from datetime import date
from decimal import Decimal
from typing import Any
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.enums import AnalysisSignal
from app.services.moex_iss_client import MoexIssClient
from app.services.moex_normalization import canonicalize_moex_currency


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
        return FakeResponse(self.payloads.pop(0))


class FakeBondUniverseClient:
    def __init__(
        self,
        *,
        pages: list[list[dict[str, Any]]] | None = None,
        descriptions: dict[str, dict[str, Any]] | None = None,
        description_warnings: dict[str, list[str]] | None = None,
    ) -> None:
        self.pages = pages or []
        self.descriptions = descriptions or {}
        self.description_warnings = description_warnings or {}
        self.universe_calls: list[dict[str, Any]] = []
        self.description_calls: list[dict[str, Any]] = []

    def fetch_bond_universe(
        self,
        board: str,
        start: int = 0,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        self.universe_calls.append({"board": board, "start": start, "limit": limit})
        index = len(self.universe_calls) - 1
        if index >= len(self.pages):
            return [], []
        return self.pages[index], []

    def fetch_bond_description(
        self,
        secid: str,
        board: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        self.description_calls.append({"secid": secid, "board": board})
        return (
            dict(self.descriptions.get(secid, {"secid": secid})),
            list(self.description_warnings.get(secid, [])),
        )


def description(
    secid: str = "RU000A100001",
    *,
    isin: str | None = "RU000A100001",
    issuer_name: str | None = "Demo Issuer",
    issuer_inn: str | None = "7700000001",
    name: str = "Demo Bond",
    coupon_rate: str | None = "12.5",
    maturity_date: str | None = "2030-01-01",
    nominal_value: str | None = "1000",
    currency: Any = "RUB",
    is_traded: Any = 1,
) -> dict[str, Any]:
    return {
        "secid": secid,
        "isin": isin,
        "name": name,
        "shortname": name[:20],
        "issuer_name": issuer_name,
        "issuer_inn": issuer_inn,
        "currency": currency,
        "nominal_value": nominal_value,
        "coupon_rate": coupon_rate,
        "maturity_date": maturity_date,
        "offer_date": None,
        "has_amortization": None,
        "is_subordinated": False,
        "is_perpetual": False,
        "is_traded": is_traded,
    }


def sync_payload(**overrides) -> dict[str, Any]:
    payload = {
        "secids": ["RU000A100001"],
        "board": "TQCB",
        "create_missing_companies": True,
        "rebuild_existing": False,
    }
    payload.update(overrides)
    return payload


def create_company(
    db: Session,
    *,
    ticker: str = "MOEXU",
    name: str = "Demo Issuer",
    inn: str | None = "7700000001",
) -> Company:
    company = Company(
        name=name,
        ticker=ticker,
        inn=inn,
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
    secid: str = "RU000A100001",
    isin: str | None = "RU000A100001",
    name: str = "Old Bond",
    currency: str = "RUB",
) -> Bond:
    bond = Bond(
        company_id=company.id,
        secid=secid,
        isin=isin,
        name=name,
        currency=currency,
        nominal_value=Decimal("1000.00"),
        coupon_rate=Decimal("5.000"),
        maturity_date=date(2028, 1, 1),
        current_price=Decimal("99.000"),
        yield_to_maturity=Decimal("10.000"),
        volume=Decimal("1000.00"),
        signal=AnalysisSignal.NEUTRAL.value,
    )
    db.add(bond)
    db.commit()
    db.refresh(bond)
    return bond


def count(db: Session, model) -> int:
    return int(db.execute(select(func.count()).select_from(model)).scalar_one())


def test_moex_currency_normalizer_is_explicit_and_fail_closed() -> None:
    assert canonicalize_moex_currency("SUR") == "RUB"
    assert canonicalize_moex_currency("sur") == "RUB"
    assert canonicalize_moex_currency("RUB") == "RUB"
    assert canonicalize_moex_currency("usd") == "USD"
    assert canonicalize_moex_currency("CNY") == "CNY"
    assert canonicalize_moex_currency(None) is None
    assert canonicalize_moex_currency("") is None
    assert canonicalize_moex_currency("RUBLE") is None
    assert canonicalize_moex_currency("12X") is None
    assert canonicalize_moex_currency("RUR") == "RUR"


def test_client_parses_universe_table() -> None:
    http_client = FakeHttpClient(
        [
            {
                "securities": {
                    "columns": [
                        "SECID",
                        "ISIN",
                        "SHORTNAME",
                        "SECNAME",
                        "FACEVALUE",
                        "CURRENCYID",
                        "FACEUNIT",
                    ],
                    "data": [
                        [
                            "RU000A100001",
                            "RU000A100001",
                            "Short",
                            "Full Bond Name",
                            "1000",
                            "SUR",
                            "USD",
                        ]
                    ],
                }
            }
        ]
    )
    client = MoexIssClient(http_client=http_client)

    rows, warnings = client.fetch_bond_universe("TQCB", start=0, limit=50)

    assert warnings == []
    assert rows[0]["secid"] == "RU000A100001"
    assert rows[0]["isin"] == "RU000A100001"
    assert rows[0]["name"] == "Full Bond Name"
    assert rows[0]["nominal_value"] == "1000"
    assert rows[0]["currency"] == "USD"
    assert rows[0]["raw"]["CURRENCYID"] == "SUR"
    assert rows[0]["raw"]["FACEUNIT"] == "USD"
    assert http_client.calls[0]["path"].endswith(
        "/iss/engines/stock/markets/bonds/boards/TQCB/securities.json"
    )
    assert http_client.calls[0]["params"]["start"] == 0
    assert http_client.calls[0]["params"]["limit"] == 50


def test_client_nominal_currency_uses_faceunit_without_trading_fallback() -> None:
    foreign = MoexIssClient._normalize_bond_metadata_row(
        {"SECID": "FOREIGN", "CURRENCYID": "SUR", "FACEUNIT": "CNY"}
    )
    rub = MoexIssClient._normalize_bond_metadata_row(
        {"SECID": "RUB", "CURRENCYID": "USD", "FACEUNIT": "SUR"}
    )
    trading_only = MoexIssClient._normalize_bond_metadata_row(
        {"SECID": "TRADING", "CURRENCYID": "SUR"}
    )

    assert foreign["currency"] == "CNY"
    assert rub["currency"] == "SUR"
    assert trading_only["currency"] is None
    assert trading_only["raw"] == {"SECID": "TRADING", "CURRENCYID": "SUR"}


def test_client_parses_description_alternate_columns() -> None:
    http_client = FakeHttpClient(
        [
            {
                "description": {
                    "columns": ["name", "title", "value"],
                    "data": [
                        ["SECID", "Code", "RU000A100002"],
                        ["ISINCODE", "ISIN", "RU000A100002"],
                        ["EMITENT_TITLE", "Issuer", "Alternate Issuer"],
                        ["EMITENT_INN", "INN", "7700000002"],
                        ["CURRENCYID", "Trading currency", "SUR"],
                        ["FACEUNIT", "Nominal currency", "USD"],
                        ["COUPONPERCENT", "Coupon", "9.75"],
                        ["MATDATE", "Maturity", "2031-05-20"],
                    ],
                }
            }
        ]
    )
    client = MoexIssClient(http_client=http_client)

    metadata, warnings = client.fetch_bond_description("RU000A100002", board="TQCB")

    assert warnings == []
    assert metadata["secid"] == "RU000A100002"
    assert metadata["isin"] == "RU000A100002"
    assert metadata["issuer_name"] == "Alternate Issuer"
    assert metadata["issuer_inn"] == "7700000002"
    assert metadata["currency"] == "USD"
    assert metadata["raw"]["CURRENCYID"] == "SUR"
    assert metadata["raw"]["FACEUNIT"] == "USD"
    assert metadata["coupon_rate"] == "9.75"
    assert metadata["maturity_date"] == "2031-05-20"


def test_explicit_secids_sync_creates_company_and_bond(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        descriptions={"RU000A100001": description()}
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["requested_securities"] == 1
    assert payload["processed_securities"] == 1
    assert payload["companies_created"] == 1
    assert payload["bonds_created"] == 1
    company = db_session.execute(select(Company)).scalar_one()
    identity = db_session.execute(select(CompanyIdentityProfile)).scalar_one()
    bond = db_session.execute(select(Bond)).scalar_one()
    assert company.inn == "7700000001"
    assert company.signal == AnalysisSignal.INSUFFICIENT_DATA.value
    assert identity.company_id == company.id
    assert identity.legal_name == "Demo Issuer"
    assert identity.inn == "7700000001"
    assert identity.identity_status == "matched"
    assert identity.identity_source == "moex_iss"
    assert bond.company_id == company.id
    assert bond.secid == "RU000A100001"
    assert bond.isin == "RU000A100001"
    assert bond.name == "Demo Bond"
    assert bond.coupon_rate == Decimal("12.500")
    assert bond.maturity_date == date(2030, 1, 1)


def test_repeated_sync_skips_existing_by_default(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        descriptions={"RU000A100001": description()}
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    first = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())
    second = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert first.json()["bonds_created"] == 1
    assert second.json()["bonds_skipped"] == 1
    assert second.json()["companies_skipped"] == 1
    assert count(db_session, Company) == 1
    assert count(db_session, Bond) == 1


def test_universe_sync_records_source_evidence_idempotently(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    metadata = description()
    metadata["raw"] = {
        "FACEUNIT": "SUR",
        "nominal_value": "1000",
        "coupon_rate": "12.5",
        "maturity_date": "2030-01-01",
        "is_floating_coupon": False,
        "has_amortization": False,
        "is_subordinated": False,
        "is_perpetual": False,
    }
    metadata["__moex_board_observed"] = True
    fake_client = FakeBondUniverseClient(
        descriptions={"RU000A100001": metadata}
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    first = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())
    evidence_count = count(db_session, BondSecurityMasterEvidence)
    second = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert first.status_code == 200 and second.status_code == 200
    profile = db_session.execute(select(BondSecurityMasterProfile)).scalar_one()
    assert profile.currency_code == "RUB"
    assert profile.coupon_structure == "fixed"
    assert profile.amortization_structure == "bullet"
    assert profile.subordination_structure == "senior"
    assert profile.perpetual_structure == "dated"
    assert profile.trading_board == "TQCB"
    assert count(db_session, BondSecurityMasterEvidence) == evidence_count
    assert {
        row.source
        for row in db_session.execute(select(BondSecurityMasterEvidence)).scalars()
    } == {"moex_description"}


def test_universe_and_description_use_faceunit_for_legacy_and_security_master(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    secid = "RU000A100113"
    universe = description(
        secid=secid,
        isin=secid,
        issuer_inn="7700000113",
        currency="USD",
    )
    universe["raw"] = {
        "SECID": secid,
        "ISIN": secid,
        "CURRENCYID": "SUR",
        "FACEUNIT": "USD",
    }
    description_metadata = description(
        secid=secid,
        isin=secid,
        issuer_inn="7700000113",
        currency="USD",
    )
    description_metadata["raw"] = {
        "SECID": secid,
        "ISIN": secid,
        "FACEUNIT": "USD",
    }
    fake_client = FakeBondUniverseClient(
        pages=[[universe], []],
        descriptions={secid: description_metadata},
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=None),
    )

    assert response.status_code == 200
    assert response.json()["bonds_created"] == 1
    bond = db_session.execute(select(Bond)).scalar_one()
    profile = db_session.execute(select(BondSecurityMasterProfile)).scalar_one()
    currency_evidence = list(
        db_session.execute(
            select(BondSecurityMasterEvidence).where(
                BondSecurityMasterEvidence.field_name == "currency_code"
            )
        ).scalars()
    )
    assert bond.currency == "USD"
    assert profile.currency_state == "verified"
    assert profile.currency_code == "USD"
    assert len(currency_evidence) == 2
    assert {row.source for row in currency_evidence} == {
        "moex_universe",
        "moex_description",
    }
    assert all(
        row.raw_value_json == {"source_field": "FACEUNIT", "value": "USD"}
        for row in currency_evidence
    )


def test_rebuild_existing_updates_safe_metadata(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session)
    create_bond(db_session, company)
    fake_client = FakeBondUniverseClient(
        descriptions={
            "RU000A100001": description(
                name="Updated Bond",
                coupon_rate="11.25",
                maturity_date="2032-02-02",
            )
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(rebuild_existing=True),
    )

    assert response.status_code == 200
    assert response.json()["bonds_updated"] == 1
    bond = db_session.execute(select(Bond)).scalar_one()
    assert bond.name == "Updated Bond"
    assert bond.coupon_rate == Decimal("11.250")
    assert bond.maturity_date == date(2032, 2, 2)
    assert bond.current_price == Decimal("99.000")
    assert bond.yield_to_maturity == Decimal("10.000")
    assert bond.volume == Decimal("1000.00")


def test_company_matching_by_inn_prevents_duplicates(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    existing = create_company(db_session, ticker="EXISTINN", name="Curated Name")
    fake_client = FakeBondUniverseClient(
        descriptions={
            "RU000A100001": description(issuer_name="MOEX Name", issuer_inn=existing.inn)
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert response.status_code == 200
    assert response.json()["companies_skipped"] == 1
    assert count(db_session, Company) == 1
    bond = db_session.execute(select(Bond)).scalar_one()
    assert bond.company_id == existing.id


def test_company_matching_by_normalized_name_prevents_duplicates(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    existing = create_company(
        db_session,
        ticker="EXISTNAME",
        name="Demo    Issuer",
        inn=None,
    )
    fake_client = FakeBondUniverseClient(
        descriptions={
            "RU000A100001": description(issuer_name="demo issuer", issuer_inn=None)
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert response.status_code == 200
    assert response.json()["companies_skipped"] == 1
    assert count(db_session, Company) == 1
    assert db_session.execute(select(Bond)).scalar_one().company_id == existing.id


def test_create_missing_companies_false_skips_unresolved_issuer(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        descriptions={
            "RU000A100001": description(issuer_name=None, issuer_inn=None)
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(create_missing_companies=False),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["bonds_skipped"] == 1
    assert payload["errors"][0]["message"] == "Company could not be resolved"
    assert count(db_session, Company) == 0
    assert count(db_session, Bond) == 0


def test_missing_moex_issuer_metadata_creates_weak_identity(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        descriptions={
            "RU000A100001": description(issuer_name=None, issuer_inn=None)
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert response.status_code == 200
    profile = db_session.execute(select(CompanyIdentityProfile)).scalar_one()
    assert profile.identity_status == "unknown"
    assert profile.identity_source == "moex_iss"
    messages = {warning["message"] for warning in response.json()["warnings"]}
    assert "issuer_name_missing" in messages
    assert "issuer_inn_missing" in messages
    assert "company_identity_created_weak" in messages


def test_moex_sync_does_not_overwrite_verified_identity(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    company = create_company(db_session, name="Curated Issuer", inn="7700000001")
    db_session.add(
        CompanyIdentityProfile(
            company_id=company.id,
            legal_name="Verified Legal Name",
            inn="7700000001",
            issuer_role="legal_issuer",
            identity_status="verified",
            identity_source="manual_review",
            review_status="accepted",
        )
    )
    db_session.commit()
    fake_client = FakeBondUniverseClient(
        descriptions={
            "RU000A100001": description(issuer_name="MOEX Name", issuer_inn=company.inn)
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert response.status_code == 200
    profile = db_session.execute(select(CompanyIdentityProfile)).scalar_one()
    assert profile.legal_name == "Verified Legal Name"
    assert profile.identity_status == "verified"


def test_missing_and_invalid_values_are_item_warnings_or_errors(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        pages=[
            [
                description(secid=None, isin="RU000A100003"),
                description(
                    secid="RU000A100004",
                    isin=None,
                    nominal_value="bad-decimal",
                    maturity_date="bad-date",
                    is_traded=1,
                ),
                description(secid="RU000A100005", isin="RU000A100005", is_traded=0),
            ]
        ],
        descriptions={
            "RU000A100004": description(
                secid="RU000A100004",
                isin=None,
                nominal_value="bad-decimal",
                maturity_date="bad-date",
            ),
            "RU000A100005": description(secid="RU000A100005", is_traded=0),
        },
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=None),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["processed_securities"] == 3
    assert payload["bonds_created"] == 1
    assert payload["bonds_skipped"] == 2
    assert any(error["message"] == "Bond secid is missing" for error in payload["errors"])
    messages = {warning["message"] for warning in payload["warnings"]}
    assert "Bond isin is missing" in messages
    assert "Bond nominal_value is invalid and was ignored" in messages
    assert "Bond maturity_date is invalid and was ignored" in messages
    assert "MOEX security is inactive and was skipped" in messages


def test_sync_canonicalizes_sur_preserves_foreign_and_rejects_unknown_currency(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    currencies = {
        "RU000A100101": "SUR",
        "RU000A100102": "USD",
        "RU000A100103": "CNY",
        "RU000A100104": "EUR",
        "RU000A100105": None,
        "RU000A100106": "RUBLE",
        "RU000A100107": "12X",
    }
    fake_client = FakeBondUniverseClient(
        descriptions={
            secid: description(secid=secid, isin=secid, currency=currency)
            for secid, currency in currencies.items()
        }
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=list(currencies)),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["bonds_created"] == 4
    assert payload["bonds_skipped"] == 3
    assert [error["message"] for error in payload["errors"]] == [
        "bond_currency_unresolved",
        "bond_currency_unresolved",
        "bond_currency_unresolved",
    ]
    stored = {
        bond.secid: bond.currency
        for bond in db_session.execute(select(Bond).order_by(Bond.secid)).scalars()
    }
    assert stored == {
        "RU000A100101": "RUB",
        "RU000A100102": "USD",
        "RU000A100103": "CNY",
        "RU000A100104": "EUR",
    }


def test_unresolved_currency_creates_nothing_and_does_not_corrupt_existing_bond(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    missing_secid = "RU000A100111"
    missing_metadata = description(
        secid=missing_secid,
        isin=missing_secid,
        currency=None,
    )
    missing_metadata["raw"] = {"CURRENCYID": "SUR"}
    missing_client = FakeBondUniverseClient(
        descriptions={missing_secid: missing_metadata}
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: missing_client,
    )

    missing_response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=[missing_secid]),
    )
    assert missing_response.status_code == 200
    assert missing_response.json()["errors"][0]["message"] == (
        "bond_currency_unresolved"
    )
    assert count(db_session, Company) == 0
    assert count(db_session, Bond) == 0

    company = create_company(db_session, ticker="MOEXCUR", inn="7700000111")
    existing = create_bond(
        db_session,
        company,
        secid="RU000A100112",
        isin="RU000A100112",
        currency="USD",
    )
    rebuild_metadata = description(
        secid=existing.secid,
        isin=existing.isin,
        issuer_name=company.name,
        issuer_inn=company.inn,
        currency=None,
    )
    rebuild_metadata["raw"] = {"CURRENCYID": "SUR"}
    rebuild_client = FakeBondUniverseClient(
        descriptions={existing.secid: rebuild_metadata}
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: rebuild_client,
    )

    rebuild_response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=[existing.secid], rebuild_existing=True),
    )
    db_session.refresh(existing)
    assert rebuild_response.status_code == 200
    assert existing.currency == "USD"
    assert "bond_currency_unresolved" in {
        warning["message"] for warning in rebuild_response.json()["warnings"]
    }
    security_master = db_session.execute(
        select(BondSecurityMasterProfile).where(
            BondSecurityMasterProfile.bond_id == existing.id
        )
    ).scalar_one()
    assert security_master.currency_state == "unknown"
    assert security_master.currency_code is None
    assert security_master.nominal_state == "verified"
    assert security_master.maturity_state == "verified"


def test_pagination_stops_on_empty_page(
    client: TestClient,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        pages=[[description(secid="RU000A100006", isin="RU000A100006")], []],
        descriptions={
            "RU000A100006": description(secid="RU000A100006", isin="RU000A100006")
        },
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=None, page_size=1),
    )

    assert response.status_code == 200
    assert response.json()["bonds_created"] == 1
    assert [call["start"] for call in fake_client.universe_calls] == [0, 1]


def test_max_pages_warning(
    client: TestClient,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        pages=[[description(secid="RU000A100007", isin="RU000A100007")]],
        descriptions={
            "RU000A100007": description(secid="RU000A100007", isin="RU000A100007")
        },
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post(
        "/api/market-data/moex/bonds/sync",
        json=sync_payload(secids=None, max_pages=1),
    )

    assert response.status_code == 200
    assert response.json()["bonds_created"] == 1
    assert any(
        warning["message"] == "MOEX bond universe pagination max_pages reached"
        for warning in response.json()["warnings"]
    )


def test_validation_errors(client: TestClient) -> None:
    cases = [
        (sync_payload(secids=[""]), "secids cannot contain empty values"),
        (sync_payload(max_pages=0), "max_pages must be between 1 and 500"),
        (sync_payload(page_size=0), "page_size must be between 1 and 500"),
    ]

    for payload, detail in cases:
        response = client.post("/api/market-data/moex/bonds/sync", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_response_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    monkeypatch,
) -> None:
    fake_client = FakeBondUniverseClient(
        descriptions={"RU000A100001": description()}
    )
    monkeypatch.setattr(
        "app.services.moex_bond_universe_service.MoexIssClient",
        lambda: fake_client,
    )

    response = client.post("/api/market-data/moex/bonds/sync", json=sync_payload())

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())

