from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.services.moex_iss_client import (
    MoexIssClient,
    MoexIssClientError,
    MoexSecurityReferenceCandidate,
)
from app.services.moex_issuer_identity_source_service import (
    MoexIssuerIdentitySourceService,
    resolve_security_reference,
)


def test_task296c2_transient_recovery_exhaustion_and_safe_errors():
    isin = "RU000A10FWR5"
    calls, delays = [], []
    columns = list(MoexIssClient.SECURITY_REFERENCE_COLUMNS)
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("Authorization=secret-must-not-leak", request=request)
        return httpx.Response(200, json={"securities": {"columns": columns,
            "data": [[isin, isin, "Bond", "Bond", "TQCB", "17788", "Issuer", "9703246300", None]]}})
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        service = MoexIssuerIdentitySourceService(MoexIssClient(http_client=http), sleeper=delays.append)
        row = service.lookup_isins(source_isins=[isin])[0]
        assert row.resolution.security_match_status.value == "EXACT_ISIN_RECOVERED"
        assert row.resolution.matched_isin == row.resolution.matched_secid == isin
        assert row.resolution.primary_board == "TQCB"
        assert row.acquisition.attempt_count == 2 and row.acquisition.transient_failure_count == 1
        assert row.acquisition.source_query_count == 1 and delays == [0.25]
    calls.clear()
    delays.clear()
    def exhausted(request):
        calls.append(request)
        return httpx.Response(503, text="secret-response-body")
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(exhausted)) as http:
        result = MoexIssuerIdentitySourceService(MoexIssClient(http_client=http), sleeper=delays.append).lookup(requested_secid=None, expected_isin=isin)
        assert result.security_match_status == "SOURCE_ERROR" and result.matched_isin is None
        assert result.acquisition.attempt_count == result.acquisition.transient_failure_count == 3
        assert delays == [0.25, 0.50] and len(calls) == 3
        assert "secret" not in repr(result)
        with pytest.raises(MoexIssClientError) as exc:
            MoexIssClient(http_client=http).fetch_security_reference_candidates(isin)
        assert "secret" not in str(exc.value) and exc.value.__cause__ is None and exc.value.__suppress_context__


@pytest.mark.parametrize("status,retryable", [(408, True), (429, True), (500, True), (502, True), (503, True), (504, True), (401, False), (403, False), (404, False), (501, False)])
def test_task296c2_http_classification(status, retryable):
    calls, delays = [], []
    def handler(request):
        calls.append(1)
        return httpx.Response(status, text="token=not-for-output")
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        result = MoexIssuerIdentitySourceService(MoexIssClient(http_client=http), sleeper=delays.append).lookup(requested_secid="RU000A10FWR5")
    assert len(calls) == (3 if retryable else 1)
    assert result.security_match_status == "SOURCE_ERROR"
    assert result.acquisition.transient_failure_count == (3 if retryable else 0)


def test_task296c2_semantics_fallback_pagination_and_batch_validation():
    isin = "RU000A10FWR5"
    fake = FakeReferenceClient({isin: [candidate(secid=isin, isin=isin)]})
    service = MoexIssuerIdentitySourceService(fake, sleeper=lambda _: pytest.fail("semantic retry"))
    assert service.lookup(requested_secid="OLD", expected_isin=isin).security_match_status == "EXACT_ISIN_RECOVERED"
    assert fake.calls == ["OLD", isin]
    fake.calls.clear()
    assert service.lookup(requested_secid="ABSENT").security_match_status == "SECURITY_NOT_FOUND"
    assert fake.calls == ["ABSENT"]
    fake.candidates[isin] = [candidate(secid=isin, isin="CONTRADICTION")]
    fake.calls.clear()
    assert service.lookup(requested_secid=isin, expected_isin=isin).security_match_status == "SECURITY_IDENTIFIER_CONFLICT"
    assert fake.calls == [isin]
    for invalid in ("ISIN", {isin}, iter([isin]), [isin, isin], [False], [" "]):
        before = list(fake.calls)
        with pytest.raises(ValueError):
            service.lookup_isins(source_isins=invalid)
        assert fake.calls == before
    delays, calls = [], []
    class FallbackClient:
        def fetch_security_reference_candidates(self, query):
            calls.append(query)
            if query == "OLD":
                return []
            if calls.count(isin) == 1:
                raise MoexIssClientError("sanitized", category="TIMEOUT", retryable=True)
            return [candidate(secid=isin, isin=isin)]
    recovered = MoexIssuerIdentitySourceService(FallbackClient(), sleeper=delays.append).lookup(requested_secid="OLD", expected_isin=isin)
    assert calls == ["OLD", isin, isin] and delays == [0.25]
    assert recovered.acquisition.attempt_count == 3 and recovered.source_query_count == 2
    columns = list(MoexIssClient.SECURITY_REFERENCE_COLUMNS)
    def full_page(request):
        return httpx.Response(200, json={"securities": {"columns": columns, "data": [[isin, isin, None, None, "TQCB", None, None, None, None]] * 100}})
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(full_page)) as http:
        client = MoexIssClient(http_client=http, max_pages=1)
        failed = MoexIssuerIdentitySourceService(client, sleeper=lambda _: pytest.fail("pagination retry")).lookup(requested_secid=isin)
        assert failed.security_match_status == "SOURCE_ERROR" and failed.acquisition.attempt_count == 1


@pytest.mark.parametrize("payload", [[], {}, {"securities": {"columns": [], "data": "bad"}}])
def test_task296c2_malformed_response_is_not_retried(payload):
    calls = []
    def handler(request):
        calls.append(1)
        return httpx.Response(200, json=payload)
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        result = MoexIssuerIdentitySourceService(MoexIssClient(http_client=http), sleeper=lambda _: pytest.fail("malformed retry")).lookup(requested_secid="RU000A10FWR5")
    assert result.security_match_status == "SOURCE_ERROR" and len(calls) == 1


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError])
def test_task296c2_transport_errors_are_classified_without_leak(error_type):
    calls = []
    def handler(request):
        calls.append(1)
        raise error_type("Authorization=NEVER_SERIALIZE", request=request)
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        result = MoexIssuerIdentitySourceService(MoexIssClient(http_client=http), sleeper=lambda _: None).lookup(requested_secid="RU000A10FWR5")
    assert len(calls) == 3 and result.acquisition.transient_failure_count == 3
    assert "NEVER_SERIALIZE" not in json.dumps(result.__dict__)


def test_task296c2_retry_discards_partial_pages_and_bounds_two_queries():
    isin = "RU000A10FWR5"
    columns = list(MoexIssClient.SECURITY_REFERENCE_COLUMNS)
    calls = []
    def handler(request):
        calls.append(int(request.url.params["start"]))
        if len(calls) == 2:
            raise httpx.ConnectError("sanitized by boundary", request=request)
        old = len(calls) == 1
        row = [isin, isin, "Bond", "Bond", "TQCB", "OLD" if old else "NEW", "Issuer", "9703246300", None]
        return httpx.Response(200, json={"securities": {"columns": columns, "data": [row] * (100 if old else 1)}})
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        result = MoexIssuerIdentitySourceService(MoexIssClient(http_client=http), sleeper=lambda _: None).lookup(requested_secid=None, expected_isin=isin)
    assert result.issuer_id == "NEW" and calls == [0, 100, 0]
    assert result.acquisition.attempt_count == 2
    stages, delays = [], []
    class TwoQueries:
        def fetch_security_reference_candidates(self, query):
            stages.append(query)
            if query == "OLD" and stages.count("OLD") == 3:
                return []
            raise MoexIssClientError("safe", category="TIMEOUT", retryable=True)
    failed = MoexIssuerIdentitySourceService(TwoQueries(), sleeper=delays.append).lookup(requested_secid="OLD", expected_isin=isin)
    assert stages == ["OLD"] * 3 + [isin] * 3
    assert failed.acquisition.attempt_count == 6 and failed.acquisition.transient_failure_count == 5
    assert failed.security_match_status == "SOURCE_ERROR" and delays == [0.25, 0.50, 0.25, 0.50]
    ambiguous = FakeReferenceClient({isin: [candidate(secid=isin, isin=isin, issuer_id="1"), candidate(secid=isin, isin=isin, issuer_id="2")]})
    result = MoexIssuerIdentitySourceService(ambiguous, sleeper=lambda _: pytest.fail("semantic retry")).lookup(requested_secid=None, expected_isin=isin)
    assert result.security_match_status == "SECURITY_AMBIGUOUS" and ambiguous.calls == [isin]


def load_probe_module() -> Any:
    path = (
        Path(__file__).parents[2]
        / "scripts"
        / "moex_issuer_identity_source_probe.py"
    )
    spec = importlib.util.spec_from_file_location(
        "moex_issuer_identity_source_probe",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeHttpClient:
    def __init__(self, payloads: list[dict[str, Any]]) -> None:
        self.payloads = list(payloads)
        self.calls: list[dict[str, Any]] = []

    def get(self, path: str, params: dict[str, Any]) -> FakeResponse:
        self.calls.append({"path": path, "params": dict(params)})
        return FakeResponse(self.payloads.pop(0))


class FakeReferenceClient:
    def __init__(
        self,
        candidates: dict[str, list[MoexSecurityReferenceCandidate]],
        *,
        errors: set[str] | None = None,
    ) -> None:
        self.candidates = candidates
        self.errors = errors or set()
        self.calls: list[str] = []

    def fetch_security_reference_candidates(
        self,
        query: str,
    ) -> list[MoexSecurityReferenceCandidate]:
        self.calls.append(query)
        if query in self.errors:
            raise RuntimeError("credential=must-not-leak")
        return list(self.candidates.get(query, []))


def candidate(
    *,
    secid: str = "RU000A104511",
    isin: str | None = "RU000A104511",
    issuer_id: str | None = "1234",
    issuer_title: str | None = "Issuer Legal Name",
    issuer_inn: str | None = "7700000001",
    issuer_okpo: str | None = "12345678",
) -> MoexSecurityReferenceCandidate:
    return MoexSecurityReferenceCandidate(
        secid=secid,
        isin=isin,
        short_name="Issuer Bond",
        full_name="Issuer Bond 2029",
        primary_board="TQCB",
        issuer_id=issuer_id,
        issuer_title=issuer_title,
        issuer_inn=issuer_inn,
        issuer_okpo=issuer_okpo,
    )


def test_reference_client_uses_named_projection_and_strict_shape() -> None:
    columns = list(MoexIssClient.SECURITY_REFERENCE_COLUMNS)
    payload = {
        "securities": {
            "columns": columns,
            "data": [
                [
                    " ru000a104511 ",
                    " ru000a104511 ",
                    " Short ",
                    " Full ",
                    " tqcb ",
                    1234,
                    " Issuer ",
                    " 7700000001 ",
                    " 12345678 ",
                ]
            ],
        }
    }
    http = FakeHttpClient([payload])

    result = MoexIssClient(http_client=http).fetch_security_reference_candidates(
        " ru000a104511 "
    )

    assert result == [
        MoexSecurityReferenceCandidate(
            secid="RU000A104511",
            isin="RU000A104511",
            short_name="Short",
            full_name="Full",
            primary_board="TQCB",
            issuer_id="1234",
            issuer_title="Issuer",
            issuer_inn="7700000001",
            issuer_okpo="12345678",
        )
    ]
    assert http.calls == [
        {
            "path": "/iss/securities.json",
            "params": {
                "q": "RU000A104511",
                "iss.meta": "off",
                "iss.only": "securities",
                "start": 0,
                "limit": 100,
                "securities.columns": ",".join(columns),
            },
        }
    ]

    for malformed in (
        {},
        {"securities": {"columns": columns[:-1], "data": []}},
        {"securities": {"columns": columns, "data": [["too-short"]]}},
        {"securities": {"columns": columns, "data": "bad"}},
    ):
        with pytest.raises(MoexIssClientError):
            MoexIssClient(
                http_client=FakeHttpClient([malformed])
            ).fetch_security_reference_candidates("RU000A104511")


def test_matching_is_exact_order_independent_and_fail_closed() -> None:
    exact = candidate()
    unrelated = candidate(secid="RU000A999999", isin="RU000A999999")
    result = resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin="RU000A104511",
        candidates=[unrelated, exact],
    )
    assert result.security_match_status == "EXACT_SECID_ISIN_CORROBORATED"
    assert result.issuer_metadata_status == "ISSUER_COMPLETE"
    assert result.issuer_inn == "7700000001"

    assert resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin="RU000A000000",
        candidates=[exact],
    ).security_match_status == "SECURITY_IDENTIFIER_CONFLICT"
    assert resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin=None,
        candidates=[unrelated],
    ).security_match_status == "SECURITY_NOT_FOUND"

    compatible = candidate(issuer_title=None)
    merged_a = resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin=None,
        candidates=[compatible, exact],
    )
    merged_b = resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin=None,
        candidates=[exact, compatible],
    )
    assert merged_a == merged_b
    assert merged_a.security_match_status == "EXACT_SECID"
    assert merged_a.issuer_title == "Issuer Legal Name"

    ambiguous = resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin=None,
        candidates=[exact, candidate(issuer_inn="7700000002")],
    )
    assert ambiguous.security_match_status == "SECURITY_AMBIGUOUS"
    assert ambiguous.issuer_inn is None


def test_isin_recovery_and_issuer_completeness_are_separate() -> None:
    partial_foreign = candidate(
        secid="XS0000000001",
        isin="XS0000000001",
        issuer_inn=None,
        issuer_okpo=None,
    )
    recovered = resolve_security_reference(
        requested_secid="MISSINGSECID",
        expected_isin="XS0000000001",
        candidates=[partial_foreign],
    )
    assert recovered.security_match_status == "EXACT_ISIN_RECOVERED"
    assert recovered.issuer_metadata_status == "ISSUER_PARTIAL"
    assert recovered.issuer_inn is None

    missing = resolve_security_reference(
        requested_secid="RU000A104511",
        expected_isin=None,
        candidates=[
            candidate(
                issuer_id=None,
                issuer_title=None,
                issuer_inn=None,
                issuer_okpo=None,
            )
        ],
    )
    assert missing.issuer_metadata_status == "ISSUER_MISSING"


def test_lookup_fallback_and_source_failure_are_sanitized() -> None:
    foreign = candidate(secid="XS0000000001", isin="XS0000000001")
    client = FakeReferenceClient(
        {"LOCAL": [], "XS0000000001": [foreign]},
    )
    recovered = MoexIssuerIdentitySourceService(client).lookup(
        requested_secid="LOCAL",
        expected_isin="XS0000000001",
    )
    assert recovered.security_match_status == "EXACT_ISIN_RECOVERED"
    assert recovered.source_query_count == 2
    assert client.calls == ["LOCAL", "XS0000000001"]

    failed = MoexIssuerIdentitySourceService(
        FakeReferenceClient({}, errors={"RU000A104511"})
    ).lookup(requested_secid="RU000A104511")
    assert failed.security_match_status == "SOURCE_ERROR"
    assert "credential" not in json.dumps(failed.__dict__)


def test_explicit_probe_is_deterministic_and_does_not_import_db() -> None:
    probe = load_probe_module()
    sys.modules.pop("app.db.session", None)
    client = FakeReferenceClient(
        {
            "RU000A104511": [candidate()],
            "RU000A107G55": [
                candidate(
                    secid="RU000A107G55",
                    isin="RU000A107G55",
                    issuer_inn=None,
                )
            ],
        }
    )
    generated = datetime(2026, 8, 28, 12, tzinfo=timezone.utc)

    report = probe.build_explicit_probe_report(
        client,
        ["ru000a107g55", "RU000A104511"],
        generated_at=generated,
    )

    assert [row["requested_secid"] for row in report["results"]] == [
        "RU000A104511",
        "RU000A107G55",
    ]
    assert report["summary"]["exact_security_matches"] == 2
    assert report["database_accessed"] is False
    assert "app.db.session" not in sys.modules
    assert probe.serialize_report(report, "json") == probe.serialize_report(
        report, "json"
    )
    assert "Issuer fields are observed official-source facts" in (
        probe.serialize_report(report, "markdown")
    )


def test_explicit_cli_validation_deduplication_and_optional_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = load_probe_module()
    client = FakeReferenceClient({"RU000A104511": [candidate()]})
    monkeypatch.setattr(probe, "MoexIssClient", lambda: client)
    output = tmp_path / "probe.json"

    result = probe.main(
        [
            "--secid",
            "ru000a104511",
            "--secid",
            "RU000A104511",
            "--output",
            str(output),
        ]
    )

    assert result == 0
    assert client.calls == ["RU000A104511"]
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "completed"
    with pytest.raises(ValueError):
        probe.normalize_secids([f"SECID{index}" for index in range(101)])
    with pytest.raises(ValueError):
        probe.parse_args(["--secid", "bad secid"])


def test_db_coverage_is_one_select_bounded_and_immutable(db_session: Session) -> None:
    probe = load_probe_module()
    company = Company(
        name="Unknown issuer for RU000A104511",
        ticker="UNKNOWN-RU000A104511",
        inn=None,
        country="RU",
    )
    bond = Bond(
        company=company,
        secid="RU000A104511",
        isin="RU000A104511",
        name="Synthetic Bond",
        currency="RUB",
    )
    profile = CompanyIdentityProfile(
        company=company,
        inn="7700000999",
        issuer_role="unknown",
        identity_status="weak",
        identity_source="existing_company",
        review_status="pending",
    )
    db_session.add_all([company, bond, profile])
    db_session.commit()
    before = (
        db_session.scalar(select(func.count()).select_from(Bond)),
        db_session.scalar(select(func.count()).select_from(Company)),
        db_session.scalar(select(func.count()).select_from(CompanyIdentityProfile)),
    )
    statements: list[str] = []

    def record_statement(
        conn: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        report = probe.build_db_coverage_report(
            db_session,
            FakeReferenceClient({"RU000A104511": [candidate()]}),
            sample_limit=1,
            generated_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)

    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")
    assert report["summary"]["total_bonds"] == 1
    assert report["summary"]["placeholder_companies"] == 1
    assert report["summary"]["moex_profile_inn_mismatches"] == 1
    assert len(report["samples"]["placeholder_recovery_candidates"]) == 1
    after = (
        db_session.scalar(select(func.count()).select_from(Bond)),
        db_session.scalar(select(func.count()).select_from(Company)),
        db_session.scalar(select(func.count()).select_from(CompanyIdentityProfile)),
    )
    assert after == before
    assert report["database_mutation_executed"] is False
    assert report["identity_applied"] is False


def test_postgresql_read_only_enforcement_and_sanitized_cli_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    probe = load_probe_module()

    class Result:
        def scalar_one(self) -> str:
            return "on"

    class Dialect:
        name = "postgresql"

    class Bind:
        dialect = Dialect()

    class FakeSession:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def get_bind(self) -> Bind:
            return Bind()

        def execute(self, statement: Any) -> Result:
            self.statements.append(str(statement))
            return Result()

    session = FakeSession()
    assert probe.enforce_read_only_transaction(session) is True
    assert session.statements == [
        "SET TRANSACTION READ ONLY",
        "SHOW transaction_read_only",
    ]

    monkeypatch.setattr(
        probe,
        "MoexIssClient",
        lambda: (_ for _ in ()).throw(RuntimeError("token=secret")),
    )
    assert probe.main(["--secid", "RU000A104511"]) == 1
    error = capsys.readouterr().err
    assert json.loads(error) == {
        "error": "moex_issuer_identity_source_probe_failed",
        "schema": "bondradar.moex_issuer_identity_source_probe.v1",
        "status": "failed",
    }
    assert "secret" not in error
