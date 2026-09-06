from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.services.cbr_bank_financial_evidence import publication_availability_audit as audit

NOW = datetime(2026, 9, 4, 17, tzinfo=timezone.utc)
HTTP_DATE = "Fri, 04 Sep 2026 17:00:00 GMT"
LAST_MODIFIED = "Wed, 26 Aug 2026 09:05:00 GMT"
ARTIFACT = "https://www.cbr.ru/vfs/credit/forms/101-20260801.rar"


class NeverRead(httpx.SyncByteStream):
    def __init__(self):
        self.closed = False

    def __iter__(self):
        pytest.fail("artifact body was read")

    def close(self):
        self.closed = True


def catalog(*, omit=(), extra=""):
    return "<html><body><p>Последнее обновление страницы: 26.08.2026</p>" + "".join(
        f'<div><a href="/vfs/credit/forms/{form.short_code}-{day:%Y%m%d}.rar" '
        f'data-zoom-title="Данные на {day.isoformat()}">на {day.isoformat()}</a></div>'
        for day in audit.REPORT_DATES for form in audit.FORMS
        if (day, form) not in omit
    ) + extra + "</body></html>"


def mock_source(*, html=None, headers=None, artifact_status=200, fail_artifact=False):
    calls, streams = [], []
    page = catalog() if html is None else html

    def handler(request):
        calls.append((request.method, str(request.url)))
        if str(request.url) == audit.SOURCE_PAGE:
            return httpx.Response(200, text=page, headers={"content-type": "text/html; charset=utf-8"})
        if request.url.path.endswith(".rar"):
            if fail_artifact:
                raise httpx.ConnectError("postgresql://secret:password@private", request=request)
            stream = NeverRead()
            streams.append(stream)
            return httpx.Response(artifact_status, stream=stream, headers=headers if headers is not None else {
                "date": HTTP_DATE, "last-modified": LAST_MODIFIED, "etag": '"opaque-version"',
                "content-type": "application/rar", "content-length": "999999999",
            })
        assert str(request.url) in {url for url, _ in audit.RESEARCH_SOURCES}
        return httpx.Response(200, text="<html>LastUpdate справочника. GetFormsMaxDate. RSS публикация страницы.</html>",
                              headers={"content-type": "text/html"})

    return handler, calls, streams


def run_source(**kwargs):
    handler, calls, streams = mock_source(**kwargs)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = audit.OfficialTransport(client, clock=lambda: NOW, sleep=lambda _: None)
        report = audit.run_audit(transport, observed_at=NOW)
    return report, calls, streams


def assert_safety(report):
    for field in ("database_accessed", "database_mutation_executed", "database_persistence", "filesystem_write",
                  "normalization", "scoring", "pit_ready", "publication_backfill_executed", "historical_availability_proven"):
        assert report[field] is False
    assert report["production_actions"] == "NONE"
    assert report["publication_status"] == "UNKNOWN" and report["publication_at"] is None
    assert report["artifact_bodies_read"] == 0
    assert len(report["artifacts"]) == 104
    assert all(row["explicit_publication_at"] is row["explicit_publication_date"] is None for row in report["artifacts"])


def test_full_catalog_metadata_counts_order_context_and_no_body():
    report, calls, streams = run_source()
    assert_safety(report)
    assert report["status"] == "complete"
    assert report["expected_report_dates"] == 26
    assert len(set(report["report_dates"])) == 26
    assert report["catalog_artifacts_found"] == report["artifact_metadata_checked"] == 104
    assert report["catalog_artifacts_missing"] == report["artifact_metadata_failures"] == 0
    assert report["last_modified_count"] == report["etag_count"] == 104
    assert report["historical_availability_proven_artifacts"] == 0
    assert report["historical_availability_unproven_artifacts"] == report["pit_not_usable_artifacts"] == 104
    assert report["explicit_publication_timestamp_count"] == report["explicit_publication_date_count"] == 0
    assert report["pit_conservative_bound_artifacts"] == 0
    assert report["overall_historical_availability_status"] == "HISTORICAL_AVAILABILITY_NOT_PROVEN"
    assert report["recommendation"] == "REQUIRES_EXTERNAL_ARCHIVAL_EVIDENCE"
    assert report["coverage_by_form"] == {form.value: 26 for form in audit.FORMS}
    assert report["coverage_by_year"] == {"2023": 8, "2024": 16, "2025": 48, "2026": 32}
    assert sum(url == audit.SOURCE_PAGE for _, url in calls) == 1
    assert all(method == "GET" for method, _ in calls)
    assert len(streams) == 104 and all(stream.closed for stream in streams)
    assert report["logical_requests"] == 105 + len(audit.RESEARCH_SOURCES)
    row = report["artifacts"][0]
    assert row["http_date"] == "2026-09-04T17:00:00Z"
    assert row["last_modified"] == "2026-08-26T09:05:00Z"
    assert row["content_length"] == 999999999  # headers-only, not a download cap
    assert row["raw_headers"]["last-modified"] == LAST_MODIFIED
    assert "на 2023-07-01" in row["catalog_context"]["anchor_text"]
    assert row["version_binding_proven"] is False
    assert all(item["official_source_url"].startswith("https://www.cbr.ru/") for item in row["evidence"])


def test_deterministic_report_and_reversed_catalog():
    first, _, _ = run_source()
    html = catalog()
    start, end = html.index("<div>"), html.rindex("</div>") + len("</div>")
    anchors = html[start:end].split("</div>")[:-1]
    reversed_html = html[:start] + "".join(part + "</div>" for part in reversed(anchors)) + html[end:]
    second, _, _ = run_source(html=reversed_html)
    # Exact source page bytes and adjacent context legitimately change with order.
    for report in (first, second):
        report.pop("catalog_content_sha256")
        for row in report["artifacts"]:
            row["catalog_context"].pop("adjacent_text")
    assert first == second
    repeat, _, _ = run_source()
    original, _, _ = run_source()
    assert json.dumps(repeat, sort_keys=True) == json.dumps(original, sort_keys=True)


def test_missing_artifact_is_recorded_without_synthesizing_url():
    report, calls, _ = run_source(html=catalog(omit={(audit.REPORT_DATES[0], audit.FORMS[0])}))
    assert report["status"] == "complete"
    assert report["catalog_artifacts_found"] == 103 and report["catalog_artifacts_missing"] == 1
    row = report["artifacts"][0]
    assert row["source_url"] is None and row["source_href"] is None
    assert row["error_code"] == "CATALOG_ARTIFACT_MISSING"
    assert report["artifact_metadata_checked"] == 103
    assert len(calls) == 104 + len(audit.RESEARCH_SOURCES)
    assert_safety(report)


@pytest.mark.parametrize("extra", [
    '<a href="/vfs/credit/forms/101-20230701.rar">duplicate</a>',
    '<a href="/other/101-20230701.rar">conflict</a>',
    '<a href="https://evil.example/101-20230701.rar">foreign</a>',
    '<a href="http://www.cbr.ru/101-20230701.rar">http</a>',
])
def test_invalid_catalog_fails_before_any_artifact_request(extra):
    report, calls, _ = run_source(html=catalog(extra=extra))
    assert report["status"] == "incomplete"
    assert report["error_code"] == "INVALID_CATALOG_REFERENCES"
    assert report["catalog_artifacts_found"] is report["catalog_artifacts_missing"] is None
    assert report["artifact_metadata_unassessed"] == 104
    assert len(calls) == 1
    assert_safety(report)


@pytest.mark.parametrize("headers,code", [
    ({"last-modified": "not a date"}, "MALFORMED_METADATA"),
    ({"date": "invalid"}, "MALFORMED_METADATA"),
    ({"content-length": "-1"}, "MALFORMED_METADATA"),
    ({"content-length": "abc"}, "MALFORMED_METADATA"),
    ({"content-type": "text/html"}, "INVALID_ARTIFACT_CONTENT_TYPE"),
    ({"date": HTTP_DATE, "last-modified": "Sat, 05 Sep 2026 17:00:00 GMT"}, "MALFORMED_METADATA"),
])
def test_malformed_headers_are_not_publication(headers, code):
    report, _, _ = run_source(headers=headers)
    assert report["status"] == "incomplete" and report["artifact_metadata_failures"] == 104
    assert all(row["error_code"] == code for row in report["artifacts"])
    assert_safety(report)


def test_missing_headers_and_404_are_measured_not_publication():
    report, _, _ = run_source(headers={})
    assert report["status"] == "complete"
    assert report["last_modified_count"] == report["etag_count"] == 0
    assert all(row["content_length"] is row["last_modified"] is row["etag"] is None for row in report["artifacts"])
    missing, _, _ = run_source(artifact_status=404, headers={})
    assert missing["status"] == "complete" and missing["artifact_metadata_checked"] == 104
    assert all(row["metadata_state"] == "NOT_FOUND" for row in missing["artifacts"])
    assert_safety(report)
    assert_safety(missing)


def request_with(handler, *, url=ARTIFACT, read_page=False):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = audit.OfficialTransport(client, clock=lambda: NOW, sleep=lambda _: None)
        return transport.get(url, read_page=read_page)


@pytest.mark.parametrize("url", ["http://www.cbr.ru/file", "https://evil.example/file", "https://user:secret@cbr.ru/file", "https://cbr.ru:444/file", "https://cbr.ru/file#fragment"])
def test_unsafe_urls_and_redirect_targets_are_never_requested(url):
    with pytest.raises(audit.AuditError, match="UNSAFE_SOURCE_URL"):
        request_with(lambda _: pytest.fail("unsafe network request"), url=url)
    calls = []
    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": url})
    with pytest.raises(audit.AuditError, match="UNSAFE_SOURCE_URL"):
        request_with(redirect)
    assert calls == [ARTIFACT]


def test_safe_redirect_metadata_and_limit():
    calls = []
    def redirect(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(302, headers={"location": "https://cbr.ru/file.rar"})
        return httpx.Response(200, stream=NeverRead())
    result = request_with(redirect)
    assert result.final_url == "https://cbr.ru/file.rar" and len(result.redirects) == 1
    with pytest.raises(audit.AuditError, match="INVALID_REDIRECT"):
        request_with(lambda _: httpx.Response(302, headers={"location": "/loop"}))


@pytest.mark.parametrize("failure", ["timeout", 429, 500, 503, 599])
def test_bounded_transient_retries(failure):
    calls = []
    def handler(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("private secret", request=request)
        return httpx.Response(failure, stream=NeverRead())
    with pytest.raises(audit.AuditError) as caught:
        request_with(handler)
    assert len(calls) == 3
    assert caught.value.code in {"TIMEOUT", "RATE_LIMITED", "SOURCE_ERROR"}
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 405])
def test_permanent_http_status_does_not_retry(status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, stream=NeverRead())
    assert request_with(handler).status == status
    assert len(calls) == 1


def test_connect_error_no_retry_and_no_exception_leak():
    report, calls, _ = run_source(fail_artifact=True)
    assert report["status"] == "incomplete"
    assert report["artifact_metadata_failures"] == 104
    # The two R1 gaps receive one isolated HEAD and one streamed-GET recovery attempt.
    assert len(calls) == 109 + len(audit.RESEARCH_SOURCES)
    assert "password" not in json.dumps(report)
    assert_safety(report)


def test_transport_bounds(monkeypatch):
    monkeypatch.setattr(audit, "MAX_PAGE_BYTES", 8)
    with pytest.raises(audit.AuditError, match="PAGE_TOO_LARGE"):
        request_with(lambda _: httpx.Response(200, content=b"x" * 9), read_page=True)
    with pytest.raises(audit.AuditError, match="PAGE_TOO_LARGE"):
        request_with(lambda _: httpx.Response(200, stream=httpx.ByteStream(b"x" * 9)), read_page=True)
    monkeypatch.setattr(audit, "MAX_HEADER_BYTES", 8)
    with pytest.raises(audit.AuditError, match="HEADERS_TOO_LARGE"):
        request_with(lambda _: httpx.Response(200, headers={"etag": "x" * 9}, stream=NeverRead()))


def assertion(**changes):
    return replace(audit.PublicationAssertion(
        evidence_type=audit.EvidenceType.TIMESTAMP.value, source_url="https://www.cbr.ru/release/",
        artifact_url=ARTIFACT, observed_at=NOW, raw_value="2026-08-26T12:05:00+03:00",
        semantics_url="https://www.cbr.ru/documented-version-history/", semantics_excerpt="Exact version first publication",
        version_identity="official-immutable-version-123", semantics_documented=True, version_binding_proven=True,
    ), **changes)


def test_explicit_reviewed_timestamp_and_date_only_classifier():
    result = audit.classify_publication(assertion(), artifact_url=ARTIFACT)
    assert result == {"explicit_publication_at": "2026-08-26T09:05:00Z", "explicit_publication_date": None,
                      "historical_availability_proven": True, "pit_evidence_class": "PROVEN_EXACT"}
    result = audit.classify_publication(assertion(evidence_type=audit.EvidenceType.DATE, raw_value="2026-08-26"), artifact_url=ARTIFACT)
    assert result["explicit_publication_at"] is None and result["explicit_publication_date"] == "2026-08-26"
    assert result["pit_evidence_class"] == "PROVEN_DATE_ONLY"


@pytest.mark.parametrize("changes", [
    {"semantics_documented": False}, {"version_binding_proven": False}, {"version_identity": ""},
    {"semantics_excerpt": ""}, {"artifact_url": "https://www.cbr.ru/another.rar"},
    {"source_url": "https://third-party.example/release"}, {"semantics_url": "http://www.cbr.ru/doc"},
    {"raw_value": "2026-08-26T12:00:00"}, {"raw_value": "not a date"}, {"raw_value": "2027-01-01T00:00:00Z"},
    {"observed_at": NOW.replace(tzinfo=None)}, {"evidence_type": "UNKNOWN_FUTURE_TYPE"},
    {"evidence_type": audit.EvidenceType.LAST_MODIFIED}, {"evidence_type": audit.EvidenceType.VERSION},
    {"evidence_type": audit.EvidenceType.PERIOD}, {"evidence_type": audit.EvidenceType.CATALOG},
    {"evidence_type": "HTTP_DATE"}, {"evidence_type": "FILENAME_DATE"}, {"evidence_type": "DISCOVERED_AT"},
    {"evidence_type": "RETRIEVED_AT"}, {"evidence_type": "INGESTED_AT"}, {"evidence_type": "DBF_TIMESTAMP"},
])
def test_unproven_or_weak_evidence_never_becomes_publication(changes):
    result = audit.classify_publication(assertion(**changes), artifact_url=ARTIFACT)
    assert result["historical_availability_proven"] is False
    assert result["explicit_publication_at"] is result["explicit_publication_date"] is None
    assert result["pit_evidence_class"] == "UNKNOWN"


def test_adjacent_publication_candidate_and_page_date_are_not_automatically_accepted():
    html = catalog().replace('data-zoom-title="Данные на 2023-07-01"',
        'data-publication-date="2023-07-15" data-zoom-title="Опубликовано 2023-07-15"', 1)
    report, _, _ = run_source(html=html)
    row = report["artifacts"][0]
    assert row["catalog_context"]["anchor_attributes"]["data-publication-date"] == "2023-07-15"
    assert "26.08.2026" in report["catalog_page_timestamp_context"][0]
    assert_safety(report)


def test_duplicate_headers_fail_closed():
    row = audit._slot(audit.REPORT_DATES[0], audit.FORMS[0], audit._utc(NOW))
    response = request_with(lambda _: httpx.Response(200, headers=[("etag", '"a"'), ("etag", '"b"')], stream=NeverRead()))
    audit._record_metadata(row, response)
    assert row["error_code"] == "MALFORMED_METADATA"
    assert not row["historical_availability_proven"]


def test_source_failure_reports_all_unassessed_slots():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403, stream=NeverRead()))) as client:
        report = audit.run_audit(audit.OfficialTransport(client, clock=lambda: NOW), observed_at=NOW)
    assert report["status"] == "incomplete"
    assert report["catalog_artifacts_found"] is None
    assert all(row["catalog_present"] is None for row in report["artifacts"])
    assert_safety(report)


def test_cli_success_contract_and_no_db_or_filesystem(monkeypatch, capsys):
    import builtins
    import sqlalchemy
    handler, _, _ = mock_source()
    configuration = []
    def factory(**kwargs):
        configuration.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **k: pytest.fail("DB engine"))
    monkeypatch.setattr(builtins, "open", lambda *a, **k: pytest.fail("filesystem access"))
    assert audit.main([], clock=lambda: NOW, client_factory=factory) == 0
    captured = capsys.readouterr()
    assert len(captured.out.splitlines()) == 1 and captured.err == ""
    assert_safety(json.loads(captured.out))
    assert configuration[0]["trust_env"] is False and configuration[0]["follow_redirects"] is False
    timeout = configuration[0]["timeout"]
    assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (5, 30, 10, 5)


@pytest.mark.parametrize("args", [["--database-url", "postgresql://secret"], ["--output", "file"], ["--from-date", "2023-01-01"], ["--help"]])
def test_cli_invalid_arguments_no_network(args, capsys):
    assert audit.main(args, clock=lambda: NOW, client_factory=lambda **k: pytest.fail("network")) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["error_code"] == "INVALID_ARGUMENTS"
    assert report["network_accessed"] is False
    assert "secret" not in captured.out and captured.err == ""
    assert_safety(report)


def test_cli_sanitizes_runtime_exception(capsys):
    def factory(**kwargs):
        raise RuntimeError("password=private DSN")
    assert audit.main([], clock=lambda: NOW, client_factory=factory) == 1
    captured = capsys.readouterr()
    assert "private" not in captured.out and captured.err == ""
    report = json.loads(captured.out)
    assert report["error_code"] == "AUDIT_RUNTIME_FAILURE"
    assert_safety(report)


def test_module_has_no_persistence_or_financial_processing_surfaces():
    source = Path(audit.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(any(part in name for part in ("sqlalchemy", "store", "lexical", "archive", "models", "db.session")) for name in imports)
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"commit", "flush", "persist_bundle", "execute_apply", "write_text", "write_bytes", "mkdir"}
    assert len(audit.RESEARCH_SOURCES) <= audit.MAX_ALTERNATIVE_SOURCES == 12


def test_isolated_retry_uses_head_then_streamed_get_without_body_read():
    calls, streams = [], []
    def handler(request):
        calls.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(405)
        stream = NeverRead()
        streams.append(stream)
        return httpx.Response(200, stream=stream, headers={"date": HTTP_DATE, "last-modified": LAST_MODIFIED,
            "content-type": "application/rar", "content-length": "123"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = audit.OfficialTransport(client, clock=lambda: NOW, sleep=lambda _: None)
        result = audit.retry_artifact_metadata(transport, source_url=ARTIFACT,
                                               artifact_filename="101-20241001.rar")
    assert calls == ["HEAD", "GET"]
    assert result["status"] == "CHECKED" and result["selected_method"] == "GET"
    assert result["classification"] == "UNSUPPORTED_REQUEST_METHOD"
    assert result["metadata"]["last_modified"] == "2026-08-26T09:05:00Z"
    assert streams and all(stream.closed for stream in streams)


def test_isolated_retry_represents_persistent_failure_honestly():
    calls = []
    def handler(request):
        calls.append(request.method)
        raise httpx.ReadTimeout("secret", request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = audit.OfficialTransport(client, clock=lambda: NOW, sleep=lambda _: None)
        result = audit.retry_artifact_metadata(transport, source_url=ARTIFACT,
                                               artifact_filename="123-20250701.rar")
    assert calls == ["HEAD"] * 3 + ["GET"] * 3
    assert result["status"] == "FAILED" and result["classification"] == "PERSISTENT_SOURCE_FAILURE"
    assert result["metadata"] is None
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("title,expected", [
    ("Архив 101-20260801.rar опубликован", "EXACT_ARTIFACT_MATCH"),
    ("Форма 0409123 кредитных организаций", "EXACT_FORM_DATASET_MATCH"),
    ("Обзор банковского сектора", "BROADER_BANKING_STATISTICS_ONLY"),
    ("Форма 123", "AMBIGUOUS"),
    ("Курс иностранной валюты", "UNRELATED_PUBLICATION"),
])
def test_calendar_candidate_exact_broader_ambiguous_and_unrelated(title, expected):
    result = audit.classify_calendar_candidate(title=title, planned_vs_actual="PLANNED")
    assert result["relation"] == expected
    assert result["pit_usable"] is False


def test_calendar_planned_and_actual_remain_distinct_and_version_unbound():
    planned = audit.classify_calendar_candidate(title="Форма 0409101", publication_date="01.09.2026",
                                                planned_vs_actual="PLANNED")
    actual = audit.classify_calendar_candidate(title="Форма 0409101", publication_date="01.09.2026",
                                               planned_vs_actual="ACTUAL")
    assert planned["pit_classification"] == "NOT_USABLE_FOR_HISTORICAL_PIT"
    assert actual["pit_classification"] == "POTENTIAL_PUBLICATION_EVIDENCE_REQUIRES_VERSION_BINDING"
    assert not planned["pit_usable"] and not actual["pit_usable"]


def test_disclosure_policy_boundary_is_not_publication_at():
    html = " ".join(("0409101", "0409102", "0409123", "0409135",
                     "начиная с отчетности по состоянию на 01.06.2023", "не ранее 18-го рабочего дня"))
    result = audit._policy_review(html, "DISCLOSURE_POLICY_2023_RESUMPTION")
    assert result["conclusions"] == ["PROVES_DISCLOSURE_POLICY_ONLY",
        "PROVES_EARLIEST_ELIGIBLE_REPORT_DATE", "PROVES_NOTHING_ARTIFACT_SPECIFIC"]
    assert result["artifact_release_date_proven"] is result["publication_at_proven"] is False
    assert result["pit_usable"] is False


def test_last_modified_diagnostic_is_descriptive_only_and_detects_clusters():
    rows = [
        {"form": "0409101", "report_date": "2023-07-01", "last_modified": "2024-01-01T00:00:00Z"},
        {"form": "0409101", "report_date": "2023-10-01", "last_modified": "2023-11-01T00:00:00Z"},
        {"form": "0409102", "report_date": "2023-10-01", "last_modified": "2024-01-01T00:00:00Z"},
    ]
    result = audit.last_modified_diagnostic(rows)
    assert result["count"] == 3 and result["distinct_timestamp_count"] == 2
    assert result["same_timestamp_clusters"] == [{"timestamp": "2024-01-01T00:00:00Z", "count": 2}]
    assert result["timestamps_more_than_90_days_after_report_date"] == 2
    assert result["non_monotonic_forms"] == ["0409101"]
    assert result["pit_usable"] is False
    assert result["semantics"] == "UNDOCUMENTED_HTTP_RESOURCE_METADATA_NOT_PUBLICATION"


def test_completed_negative_audit_can_retain_two_persistent_metadata_gaps():
    report, _, _ = run_source()
    for filename in audit.R1_RETRY_FILENAMES:
        row = next(item for item in report["artifacts"] if item["artifact_filename"] == filename)
        row.update(metadata_state="FAILED", error_code="SOURCE_ERROR", failure_classification="PERSISTENT_SOURCE_FAILURE",
                   http_status=None, last_modified=None, etag=None)
    report = audit._aggregate(report)
    assert report["status"] == "complete" and report["source_audit_complete"] is True
    assert report["artifact_metadata_checked"] == 102 and report["artifact_metadata_failures"] == 2
    assert report["last_modified_count"] == 102
    assert report["overall_historical_availability_status"] == "HISTORICAL_AVAILABILITY_NOT_PROVEN"
    assert report["official_source_only_pit_reconstruction"] == "UNSUPPORTED"


def test_partial_transient_live_coverage_is_complete_but_never_reported_as_full():
    report, _, _ = run_source()
    row = report["artifacts"][0]
    row.update(metadata_state="FAILED", error_code="TIMEOUT", failure_classification="TRANSIENT_SOURCE_FAILURE",
               http_status=None, last_modified=None, etag=None)
    report = audit._aggregate(report)
    assert report["status"] == "complete" and report["source_audit_complete"] is True
    assert report["artifact_metadata_checked"] == 103 and report["artifact_metadata_failures"] == 1
    assert report["last_modified_count"] == 103
    assert report["historical_availability_unproven_artifacts"] == 104
