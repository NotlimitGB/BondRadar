from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine, event, insert
from sqlalchemy.orm import Session

from app.models.legal_issuer import LegalIssuer
from backend.app.services.cbr_legal_issuer_bridge.contracts import (
    CbrBridgeError,
    CbrBridgeSourceStatus,
    CbrBridgeState,
    CbrCreditOrganizationRegistryRecord,
    CbrCreditOrganizationRegistrySnapshot,
    FinOrgRecord,
    FinOrgSearchResult,
    LegalIssuerCandidate,
    canonical_inn,
    canonical_ogrn,
    canonical_regn,
    identifier_set_sha256,
)
from backend.app.services.cbr_legal_issuer_bridge.finorg import (
    LAST_UPDATE_ACTION,
    SEARCH_ACTION,
    CbrFinOrgClient,
    build_search_by_ogrns_request,
    parse_get_last_update_response,
    parse_search_by_ogrns_response,
)
from backend.app.services.cbr_legal_issuer_bridge.fullcolist import (
    parse_fullcolist_html,
)
from backend.app.services.cbr_legal_issuer_bridge.service import (
    CbrLegalIssuerBridgeService,
    LegalIssuerInnResolver,
)
from backend.app.services.cbr_legal_issuer_bridge.transport import (
    CbrIdentityHttpTransport,
)
from scripts.cbr_legal_issuer_bridge_probe import (
    build_probe_report,
    build_task251_fixture_snapshot,
    main,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cbr_legal_issuer_bridge"
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
REGISTRY_DATE = date(2026, 8, 30)
LAST_UPDATE = datetime(2026, 8, 30, 1, 30, tzinfo=timezone.utc)


def _registry_record(
    regn: str = "1481",
    ogrn: str | None = "1027700132195",
    name: str = "CBR title",
) -> CbrCreditOrganizationRegistryRecord:
    return CbrCreditOrganizationRegistryRecord(
        regn=regn,
        ogrn=ogrn,
        name=name,
        organization_type=None,
        legal_form="ПАО",
        registration_date=date(1991, 6, 20),
        license_status="Действующая",
        location=None,
        registry_as_of=REGISTRY_DATE,
        retrieved_at=NOW,
    )


def _registry_snapshot(
    records=(_registry_record(),),
    *,
    ambiguous=(),
    conflicts=(),
) -> CbrCreditOrganizationRegistrySnapshot:
    return CbrCreditOrganizationRegistrySnapshot(
        records=tuple(records),
        registry_as_of=REGISTRY_DATE,
        retrieved_at=NOW,
        ambiguous_regns=tuple(ambiguous),
        conflicting_ogrns=tuple(conflicts),
    )


def _finorg_record(
    ogrn: str = "1027700132195",
    inn: str | None = "7707083893",
    *,
    inn_status: str = "VALID",
    error_text: str | None = None,
    name: str | None = "FinOrg title",
) -> FinOrgRecord:
    return FinOrgRecord(
        source_id="10",
        ogrn=ogrn,
        inn=inn,
        inn_status=inn_status,
        name=name,
        status="Действующая",
        error_text=error_text,
    )


class _RegistryClient:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def fetch(self, *, retrieved_at):
        assert retrieved_at == NOW
        return self.snapshot


class _FinOrgClient:
    def __init__(self, records=(_finorg_record(),), source_error=None):
        self.records = tuple(records)
        self.source_error = source_error

    def get_last_update(self):
        return LAST_UPDATE

    def search_by_ogrns(self, ogrns):
        return FinOrgSearchResult(
            requested_ogrns=tuple(ogrns),
            records=self.records,
            source_error=self.source_error,
        )


class _Resolver:
    def __init__(self, candidates):
        self.candidates = tuple(candidates)

    def resolve(self, inns):
        return {inn: tuple(item for item in self.candidates if item.issuer_inn == inn) for inn in inns}


def _candidate(state="verified", title="different title", issuer_id=1):
    return LegalIssuerCandidate(
        legal_issuer_id=issuer_id,
        issuer_inn="7707083893",
        resolution_state=state,
        source_issuer_id=f"moex-{issuer_id}",
        issuer_title=title,
    )


def _service(registry=None, finorg_records=(_finorg_record(),), source_error=None):
    return CbrLegalIssuerBridgeService(
        fullcolist_client=_RegistryClient(registry or _registry_snapshot()),
        finorg_client=_FinOrgClient(finorg_records, source_error),
    )


def test_identifier_and_fullcolist_contract_is_exact_and_conflict_aware() -> None:
    assert canonical_regn("001481") == "1481"
    assert canonical_ogrn("1027700132195") == "1027700132195"
    assert canonical_inn("7707083893") == "7707083893"
    for value in ("0", "-1", "1.0", 1481):
        with pytest.raises(ValueError):
            canonical_regn(value)  # type: ignore[arg-type]
    for function, value in ((canonical_ogrn, "123"), (canonical_inn, "123")):
        with pytest.raises(ValueError):
            function(value)

    html = (FIXTURE_ROOT / "fullcolist.html").read_bytes()
    parsed = parse_fullcolist_html(html, retrieved_at=NOW)
    assert parsed.registry_as_of == REGISTRY_DATE
    assert parsed.records[0].regn == "999"
    assert {item.regn for item in parsed.records} == {"999", "1481"}
    bank = next(item for item in parsed.records if item.regn == "1481")
    assert bank.ogrn == "1027700132195"
    assert bank.name == "Банк А"
    assert bank.license_status == "Действующая"
    assert bank.location == "Москва"

    duplicate = html.replace(b"</table>", html.split(b"<tr><td>1", 1)[1].split(b"</tr>", 1)[0].join((b"<tr><td>1", b"</tr></table>")))
    # A byte-identical row must not create a second logical record.
    assert len(parse_fullcolist_html(duplicate, retrieved_at=NOW).records) == 2

    conflict_regn = html.replace(
        b"</table>",
        b"<tr><td>3</td><td></td><td>1481</td><td>1027700132196</td><td>Other</td><td></td><td></td><td></td><td></td></tr></table>",
    )
    assert parse_fullcolist_html(conflict_regn, retrieved_at=NOW).ambiguous_regns == ("1481",)
    conflict_ogrn = html.replace(
        b"</table>",
        b"<tr><td>3</td><td></td><td>1482</td><td>1027700132195</td><td>Other</td><td></td><td></td><td></td><td></td></tr></table>",
    )
    assert parse_fullcolist_html(conflict_ogrn, retrieved_at=NOW).conflicting_ogrns == ("1027700132195",)

    for broken in (
        html.replace("ОГРН".encode(), "Код".encode()),
        html.replace(b"001481", b"bad"),
        html.replace(b"1027700132195", b"bad"),
        html.replace("по состоянию на 30.08.2026".encode(), "без даты".encode()),
    ):
        with pytest.raises(CbrBridgeError) as exc:
            parse_fullcolist_html(broken, retrieved_at=NOW)
        assert exc.value.code == CbrBridgeSourceStatus.INVALID_CONTENT


def test_finorg_soap_contract_is_deterministic_bounded_and_fail_closed() -> None:
    first = build_search_by_ogrns_request(("1027700132196", "1027700132195"))
    second = build_search_by_ogrns_request(("1027700132195", "1027700132196"))
    assert first == second
    assert first.index(b"1027700132195") < first.index(b"1027700132196")
    assert b"SearchByOGRNs" in first
    with pytest.raises(ValueError):
        build_search_by_ogrns_request(())
    with pytest.raises(ValueError):
        build_search_by_ogrns_request(tuple(f"{index:013d}" for index in range(1, 102)))
    with pytest.raises(ValueError):
        build_search_by_ogrns_request(("bad",))
    with pytest.raises(ValueError):
        CbrFinOrgClient().search_by_ogrns(
            tuple(f"1{index:012d}" for index in range(1, 1002))
        )

    response = (FIXTURE_ROOT / "search_by_ogrns.xml").read_bytes()
    result = parse_search_by_ogrns_response(
        response, requested_ogrns=("1027700132195",)
    )
    assert len(result.records) == 1
    assert result.records[0].ogrn == "1027700132195"
    assert result.records[0].inn == "7707083893"
    assert result.records[0].inn_status == "VALID"
    assert result.records[0].source_id == "10"
    assert parse_get_last_update_response(
        (FIXTURE_ROOT / "get_last_update.xml").read_bytes()
    ) == LAST_UPDATE

    missing_inn = response.replace(b"<INN>7707083893</INN>", b"<INN />")
    assert parse_search_by_ogrns_response(
        missing_inn, requested_ogrns=("1027700132195",)
    ).records[0].inn_status == "MISSING"
    malformed_inn = response.replace(b"7707083893", b"invalid-inn")
    assert parse_search_by_ogrns_response(
        malformed_inn, requested_ogrns=("1027700132195",)
    ).records[0].inn_status == "INVALID"
    duplicate = response.replace(b"</DS>", response.split(b"<Record>", 1)[1].split(b"</Record>", 1)[0].join((b"<Record>", b"</Record></DS>")))
    assert len(parse_search_by_ogrns_response(duplicate, requested_ogrns=("1027700132195",)).records) == 1

    failures = (
        (response.replace(b"<IsSucess>true", b"<IsSucess>false"), CbrBridgeSourceStatus.SOURCE_DECLARED_FAILURE),
        (response.replace(b"<DS>", b"<XX>").replace(b"</DS>", b"</XX>"), CbrBridgeSourceStatus.INVALID_XML),
        (response.replace(b"1027700132195", b"1027700132196"), CbrBridgeSourceStatus.INVALID_CONTENT),
        (b"<!DOCTYPE x [<!ENTITY y SYSTEM 'file:///x'>]><x>&y;</x>", CbrBridgeSourceStatus.INVALID_XML),
        (b"<broken", CbrBridgeSourceStatus.INVALID_XML),
        (b'<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body><soap:Fault /></soap:Body></soap:Envelope>', CbrBridgeSourceStatus.SOAP_FAULT),
    )
    for payload, code in failures:
        with pytest.raises(CbrBridgeError) as exc:
            parse_search_by_ogrns_response(payload, requested_ogrns=("1027700132195",))
        assert exc.value.code == code


def test_http_transport_enforces_methods_retries_hosts_and_limits() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["user-agent"].startswith("BondRadar-Task252")
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    transport = CbrIdentityHttpTransport(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda _delay: None,
    )
    assert transport.get_html("https://www.cbr.ru/banking_sector/credit/FullCoList/") == b"ok"
    assert transport.logical_calls["fullcolist"] == 1
    assert transport.attempt_count == 2

    def soap_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["soapaction"] == f'"{SEARCH_ACTION}"'
        return httpx.Response(200, content=b"ok")

    soap = CbrIdentityHttpTransport(http_client=httpx.Client(transport=httpx.MockTransport(soap_handler)))
    assert soap.post_soap("https://www.cbr.ru/FO_ZoomWS/FinOrg.asmx", action=SEARCH_ACTION, body=b"x") == b"ok"

    scenarios = [
        (lambda _request: httpx.Response(429), CbrBridgeSourceStatus.RATE_LIMITED),
        (lambda _request: httpx.Response(400), CbrBridgeSourceStatus.SOURCE_ERROR),
        (lambda _request: httpx.Response(302, headers={"location": "https://evil.example/x"}), CbrBridgeSourceStatus.INVALID_CONTENT),
        (lambda _request: httpx.Response(200, headers={"content-length": str(5 * 1024 * 1024)}), CbrBridgeSourceStatus.RESPONSE_TOO_LARGE),
    ]
    for handler_fn, code in scenarios:
        failing = CbrIdentityHttpTransport(
            http_client=httpx.Client(transport=httpx.MockTransport(handler_fn)),
            sleep=lambda _delay: None,
        )
        with pytest.raises(CbrBridgeError) as exc:
            failing.get_html("https://www.cbr.ru/banking_sector/credit/FullCoList/")
        assert exc.value.code == code

    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    failing = CbrIdentityHttpTransport(
        http_client=httpx.Client(transport=httpx.MockTransport(timeout)),
        sleep=lambda _delay: None,
    )
    with pytest.raises(CbrBridgeError) as exc:
        failing.get_html("https://www.cbr.ru/banking_sector/credit/FullCoList/")
    assert exc.value.code == CbrBridgeSourceStatus.TIMEOUT


def test_bridge_resolution_states_exact_inn_only_and_current_only() -> None:
    source_only = _service().bridge_regns(("01481",), retrieved_at=NOW)
    assert source_only.bridge_results[0].bridge_state == CbrBridgeState.LEGAL_ISSUER_NOT_EVALUATED
    assert source_only.bridge_results[0].inn == "7707083893"
    assert source_only.legal_issuer_evaluation_performed is False
    assert source_only.pit_status == "CURRENT_ONLY"
    assert source_only.historical_backcast_allowed is False
    assert source_only.registry_as_of == REGISTRY_DATE
    assert source_only.finorg_last_update == LAST_UPDATE
    assert source_only.bridge_results[0].registry_as_of == REGISTRY_DATE
    assert source_only.bridge_results[0].finorg_last_update == LAST_UPDATE
    assert source_only.bridge_results[0].retrieved_at == NOW

    verified = _service().bridge_regns(
        ("1481",), retrieved_at=NOW, legal_issuer_resolver=_Resolver((_candidate(),))
    )
    result = verified.bridge_results[0]
    assert result.bridge_state == CbrBridgeState.VERIFIED
    assert result.legal_issuer_id == 1
    assert "title_mismatch_warning_only" in result.warnings

    cases = [
        (_registry_snapshot(records=()), (), None, None, CbrBridgeState.CBR_REGN_NOT_FOUND),
        (_registry_snapshot(ambiguous=("1481",)), (), None, None, CbrBridgeState.CBR_REGN_AMBIGUOUS),
        (_registry_snapshot(records=(_registry_record(ogrn=None),)), (), None, None, CbrBridgeState.CBR_OGRN_MISSING),
        (_registry_snapshot(conflicts=("1027700132195",)), (), None, None, CbrBridgeState.CBR_OGRN_CONFLICT),
        (_registry_snapshot(), (), None, None, CbrBridgeState.FINORG_NOT_FOUND),
        (_registry_snapshot(), (_finorg_record(error_text="source error"),), None, None, CbrBridgeState.FINORG_SOURCE_ERROR),
        (_registry_snapshot(), (_finorg_record(inn=None, inn_status="MISSING"),), None, None, CbrBridgeState.FINORG_INN_MISSING),
        (_registry_snapshot(), (_finorg_record(inn=None, inn_status="INVALID"),), None, None, CbrBridgeState.FINORG_INN_INVALID),
        (_registry_snapshot(), (_finorg_record(), replace(_finorg_record(), inn="7700000000")), None, None, CbrBridgeState.FINORG_INN_CONFLICT),
        (_registry_snapshot(), (_finorg_record(),), _Resolver(()), None, CbrBridgeState.LEGAL_ISSUER_NOT_FOUND),
        (_registry_snapshot(), (_finorg_record(),), _Resolver((_candidate(), _candidate(issuer_id=2))), None, CbrBridgeState.LEGAL_ISSUER_INN_AMBIGUOUS),
        (_registry_snapshot(), (_finorg_record(),), _Resolver((_candidate(state="observed"),)), None, CbrBridgeState.LEGAL_ISSUER_NOT_VERIFIED),
    ]
    for registry, finorg_rows, resolver, source_error, state in cases:
        snapshot = _service(registry, finorg_rows, source_error).bridge_regns(
            ("1481",), retrieved_at=NOW, legal_issuer_resolver=resolver
        )
        assert snapshot.bridge_results[0].bridge_state == state

    # Matching titles alone cannot create a LegalIssuer identity.
    title_only = _service().bridge_regns(
        ("1481",), retrieved_at=NOW, legal_issuer_resolver=_Resolver(())
    )
    assert title_only.bridge_results[0].bridge_state == CbrBridgeState.LEGAL_ISSUER_NOT_FOUND
    assert identifier_set_sha256(("2", "1")) == identifier_set_sha256(("01", "2"))


def test_legal_issuer_resolver_is_bounded_select_only_without_autoflush() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    LegalIssuer.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(LegalIssuer.__table__),
            [
                {
                    "id": 1,
                    "source_issuer_id": "moex-1",
                    "resolution_state": "verified",
                    "issuer_title": "One",
                    "issuer_inn": "7707083893",
                },
                {
                    "id": 2,
                    "source_issuer_id": "moex-2",
                    "resolution_state": "observed",
                    "issuer_title": "Two",
                    "issuer_inn": "7700000000",
                },
            ],
        )
    statements: list[str] = []
    event.listen(engine, "before_cursor_execute", lambda _c, _u, statement, _p, _x, _m: statements.append(statement))
    with Session(engine) as session:
        event.listen(session, "before_flush", lambda *_args: pytest.fail("resolver flushed"))
        result = LegalIssuerInnResolver(session, batch_size=1).resolve(
            ("7707083893", "7700000000")
        )
    assert result["7707083893"][0].resolution_state == "verified"
    assert result["7700000000"][0].resolution_state == "observed"
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_task251_integration_cli_contract_and_audit_are_contained(monkeypatch, capsys) -> None:
    snapshot = build_task251_fixture_snapshot(date(2026, 8, 1))
    union = {regn for form in snapshot.forms for regn in form.subjects}
    assert len(union) == 353
    assert dict(snapshot.subject_set_hashes) == {
        "0409101": "692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd",
        "0409102": "90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8",
        "0409123": "5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc",
        "0409135": "660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619",
    }
    assert sum(
        isinstance(value, float)
        for form in snapshot.forms
        for record in form.records
        for _name, value in record.source_fields
    ) == 0
    assert any(
        isinstance(record.source_value, Decimal)
        for form in snapshot.forms
        for record in form.records
        if record.source_value is not None
    )

    bridge = _service().bridge_regns(("1481",), retrieved_at=NOW)
    report = build_probe_report(
        bridge,
        report_date=date(2026, 8, 1),
        logical_calls={"fullcolist": 1, "finorg_get_last_update": 1, "finorg_search_by_ogrns": 1},
    )
    assert report["schema"] == "bondradar.cbr_legal_issuer_bridge_probe.v1"
    assert report["legal_issuer_evaluation_performed"] is False
    assert report["database_accessed"] is False
    rendered = json.dumps(report, sort_keys=True)
    assert "CBR title" not in rendered
    assert "source_payload" not in rendered
    assert "database_url" not in rendered.casefold()

    assert main(["--task251-fixture-report-date", "2026-08-01"]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["error_code"] == "INVALID_ARGUMENTS"
    assert "exception" not in failed

    document = Path("docs/audits/TASK252_CBR_REGN_LEGALISSUER_IDENTITY_BRIDGE_V1.md").read_text(encoding="utf-8")
    for required in (
        "STARTING_SHA=d4ada964d717ff19f54b4241831b3d92f48ae997",
        "ALEMBIC_HEAD=202608280002",
        "REGN -> OGRN -> INN -> LegalIssuer",
        "PIT_STATUS=CURRENT_ONLY",
        "HISTORICAL_BACKCAST_ALLOWED=false",
        "DATABASE_PERSISTENCE=false",
        "FUZZY_MATCHING=false",
        "TITLE_MATCHING_USED_FOR_IDENTITY=false",
        "Task253 — CBR REGN → LegalIssuer Production Read-Only Coverage Probe",
        "LIVE_SOURCE_VALIDATION=PASS",
        "TASK251_UNION_REGNS=353",
        "REGN_TO_INN_RESOLVED=353",
        "REGISTRY_CONFLICTS=0",
        "FINORG_CONFLICTS=0",
        "LEGAL_ISSUER_COVERAGE_MEASURED=false",
    ):
        assert required in document
    headings = [line for line in document.splitlines() if line.startswith("## ")]
    assert len(headings) == 32
