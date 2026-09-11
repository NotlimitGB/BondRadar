from __future__ import annotations

import ast
import gzip
import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.services.cbr_bank_financial_evidence import external_archival_availability_audit as audit


NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def target(day="2023-07-01", form="0409101", payload=b"rar"):
    short = audit.FORM_SHORT[form]
    filename = f"{short}-{day.replace('-', '')}.rar"
    return audit.TargetArtifact(
        day,
        form,
        f"https://www.cbr.ru/vfs/credit/forms/{filename}",
        filename,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    )


def inventory(binding=audit.CURRENT_BINDING):
    rows = []
    for day in audit.REPORT_DATES:
        for form in audit.FORMS:
            payload = f"{day}:{form.value}".encode()
            rows.append(target(day.isoformat(), form.value, payload))
    return audit.TargetInventory.create(
        binding_target=binding,
        upstream_task258b_batch_sha256=("a" * 64 if binding == audit.FROZEN_BINDING else None),
        artifacts=rows,
    )


def capture(source, target_row, timestamp="20230702120000", *, exact=True):
    digest = target_row.target_content_sha256 if exact else "b" * 64
    return {
        "archive_source": source,
        "capture_at": audit._iso(audit._capture_time(timestamp)),
        "original_url": target_row.source_url,
        "archived_status": "200",
        "archive_index_digest": "INDEX",
        "archive_payload_sha256": digest,
        "target_sha256": target_row.target_content_sha256,
        "payload_exact_match": exact,
        "capture_record_locator": "https://web.archive.org/web/example",
        "evidence_class": (
            "ARCHIVE_PAYLOAD_EXACT_VERSION_MATCH"
            if exact
            else "ARCHIVE_PAYLOAD_VERSION_MISMATCH"
        ),
        "error_code": None,
    }


class FakeArchive:
    def __init__(self, source, matches=()):
        self.source = source
        self.matches = set(matches)

    def inspect(self, item):
        if (item.report_date, item.form) not in self.matches:
            return {"candidates": [], "source_error_code": None}
        stamp = datetime.combine(
            date.fromisoformat(item.report_date) + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        ).strftime("%Y%m%d%H%M%S")
        return {"candidates": [capture(self.source, item, stamp)], "source_error_code": None}


class FailingArchive:
    def inspect(self, item):
        raise audit.AuditError("ARCHIVE_TIMEOUT")


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def test_inventory_contract_hash_scope_and_frozen_binding():
    current = inventory()
    assert len(current.artifacts) == 104
    assert current.binding_target == audit.CURRENT_BINDING
    assert current.upstream_task258b_batch_sha256 is None
    assert audit.TargetInventory.from_dict(current.to_dict()) == current

    frozen = inventory(audit.FROZEN_BINDING)
    assert audit.TargetInventory.from_dict(frozen.to_dict()) == frozen
    assert frozen.upstream_task258b_batch_sha256 == "a" * 64

    bad = current.to_dict()
    bad["target_artifacts"][0]["target_compressed_size"] += 1
    with pytest.raises(audit.AuditError, match="TARGET_INVENTORY_HASH_MISMATCH"):
        audit.TargetInventory.from_dict(bad)

    bad = current.to_dict()
    bad["target_artifacts"] = bad["target_artifacts"][:-1]
    with pytest.raises(audit.AuditError, match="TARGET_INVENTORY_SCOPE_INVALID"):
        audit.TargetInventory.from_dict(bad)


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_url", "http://www.cbr.ru/vfs/credit/forms/101-20230701.rar"),
        ("source_url", "https://127.0.0.1/vfs/credit/forms/101-20230701.rar"),
        ("artifact_filename", "../101-20230701.rar"),
        ("target_content_sha256", "ABC"),
        ("target_compressed_size", 0),
    ],
)
def test_inventory_rejects_unsafe_or_malformed_target(field, value):
    source = inventory()
    rows = list(source.artifacts)
    rows[0] = replace(rows[0], **{field: value})
    with pytest.raises(audit.AuditError):
        audit.TargetInventory.create(
            binding_target=audit.CURRENT_BINDING,
            upstream_task258b_batch_sha256=None,
            artifacts=rows,
        )


def test_wayback_exact_url_index_and_payload_match():
    item = target(payload=b"exact-rar")
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/cdx/search/cdx":
            assert request.url.params["matchType"] == "exact"
            return httpx.Response(200, json=[
                ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
                ["20230702120000", item.source_url, "application/rar", "200", "SHA1", "9"],
            ])
        assert "id_" in request.url.path
        return httpx.Response(200, content=b"exact-rar")

    with mock_client(handler) as client:
        transport = audit.ReadOnlyTransport(client, sleep=lambda _: None)
        result = audit.WaybackClient(transport).inspect(item)
    candidate = result["candidates"][0]
    assert candidate["payload_exact_match"] is True
    assert candidate["archive_payload_sha256"] == item.target_content_sha256
    assert len(calls) == 2


def test_wayback_resume_continues_payload_without_repeating_index():
    item = target(payload=b"exact-rar")
    cdx = [
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["20230702120000", item.source_url, "application/rar", "200", "SHA1", "9"],
    ]

    def first_handler(request):
        if request.url.path == "/cdx/search/cdx":
            return httpx.Response(200, json=cdx)
        raise httpx.ReadTimeout("retryable")

    with mock_client(first_handler) as client:
        first = audit.WaybackClient(
            audit.ReadOnlyTransport(client, sleep=lambda _: None)
        ).inspect(item)
    assert first["source_error_code"] == "ARCHIVE_TIMEOUT"
    assert first["index_cursor"] == 1
    assert first["payload_cursor"] == 0

    calls = []

    def resumed_handler(request):
        calls.append(request.url.path)
        assert request.url.path != "/cdx/search/cdx"
        return httpx.Response(200, content=b"exact-rar")

    with mock_client(resumed_handler) as client:
        resumed = audit.WaybackClient(
            audit.ReadOnlyTransport(client, sleep=lambda _: None)
        ).inspect(item, prior=first)
    assert len(calls) == 1
    assert resumed["source_error_code"] is None
    assert resumed["payload_pending"] is False
    assert resumed["candidates"][0]["payload_exact_match"] is True


def test_wayback_no_capture_redirect_capture_and_exact_url_guard():
    item = target()
    assert audit._wayback_rows(b"[]", item.source_url) == []
    rows = json.dumps([
        ["timestamp", "original", "mimetype", "statuscode", "digest", "length"],
        ["20230702120000", item.source_url, "text/html", "302", "D", "1"],
    ]).encode()
    assert audit._wayback_rows(rows, item.source_url)[0]["statuscode"] == "302"
    mismatch = rows.replace(item.source_url.encode(), b"https://www.cbr.ru/other")
    assert audit._wayback_rows(mismatch, item.source_url) == []


def warc(payload=b"exact-rar", *, target_url=None, record_type="response", status=200):
    target_url = target_url or target(payload=payload).source_url
    if record_type == "response":
        content = (
            f"HTTP/1.1 {status} OK\r\nContent-Type: application/rar\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n"
        ).encode() + payload
    else:
        content = b""
    header = (
        "WARC/1.0\r\n"
        f"WARC-Type: {record_type}\r\n"
        "WARC-Date: 2023-07-02T12:00:00Z\r\n"
        f"WARC-Target-URI: {target_url}\r\n"
        "WARC-Payload-Digest: sha1:ABC\r\n"
        f"Content-Length: {len(content)}\r\n\r\n"
    ).encode()
    return gzip.compress(header + content)


def test_commoncrawl_warc_response_exact_payload_and_guards():
    item = target(payload=b"exact-rar")
    parsed = audit.parse_warc_range(warc(b"exact-rar", target_url=item.source_url), expected_url=item.source_url)
    assert parsed["record_type"] == "response"
    assert parsed["http_status"] == 200
    assert parsed["payload"] == b"exact-rar"
    revisit = audit.parse_warc_range(warc(b"", target_url=item.source_url, record_type="revisit"), expected_url=item.source_url)
    assert revisit["record_type"] == "revisit" and revisit["payload"] is None
    with pytest.raises(audit.AuditError, match="COMMONCRAWL_WARC_TARGET_MISMATCH"):
        audit.parse_warc_range(warc(target_url="https://www.cbr.ru/other"), expected_url=item.source_url)
    with pytest.raises(audit.AuditError, match="COMMONCRAWL_WARC_INVALID"):
        audit.parse_warc_range(b"not gzip", expected_url=item.source_url)


def test_commoncrawl_exact_index_range_and_payload_match():
    item = target(payload=b"exact-rar")
    row = {
        "timestamp": "20230702120000",
        "url": item.source_url,
        "status": "200",
        "mime": "application/rar",
        "digest": "ABC",
        "length": "200",
        "offset": "10",
        "filename": "crawl-data/CC-MAIN-X/one.warc.gz",
    }

    def handler(request):
        if request.url.path == "/collinfo.json":
            return httpx.Response(200, json=[
                {
                    "id": "CC-MAIN-OLD",
                    "cdx-api": "https://index.commoncrawl.org/CC-MAIN-OLD-index",
                    "from": "2023-06-01T00:00:00Z",
                    "to": "2023-06-30T23:59:59Z",
                },
                {
                    "id": "CC-MAIN-X",
                    "cdx-api": "https://index.commoncrawl.org/CC-MAIN-X-index",
                    "from": "2023-07-01T00:00:00Z",
                    "to": "2023-07-31T23:59:59Z",
                },
            ])
        if request.url.host == "index.commoncrawl.org":
            assert request.url.path == "/CC-MAIN-X-index"
            assert request.url.params["matchType"] == "exact"
            return httpx.Response(200, content=json.dumps(row).encode())
        assert request.headers["range"] == "bytes=10-209"
        return httpx.Response(206, content=warc(b"exact-rar", target_url=item.source_url))

    with mock_client(handler) as client:
        transport = audit.ReadOnlyTransport(client, sleep=lambda _: None)
        result = audit.CommonCrawlClient(transport, observed_at=NOW).inspect(item)
    assert result["candidates"][0]["payload_exact_match"] is True


def test_commoncrawl_revisit_resolves_only_from_exact_linked_payload():
    item = target(payload=b"exact-rar")
    rows = [
        {
            "timestamp": "20230702120000", "url": item.source_url, "status": "200",
            "mime": "warc/revisit", "digest": "SAME", "length": "100", "offset": "0",
            "filename": "crawl-data/CC-MAIN-X/revisit.warc.gz",
        },
        {
            "timestamp": "20230703120000", "url": item.source_url, "status": "200",
            "mime": "application/rar", "digest": "SAME", "length": "200", "offset": "100",
            "filename": "crawl-data/CC-MAIN-X/response.warc.gz",
        },
    ]

    def handler(request):
        if request.url.path == "/collinfo.json":
            return httpx.Response(200, json=[{
                "id": "CC-MAIN-X", "cdx-api": "https://index.commoncrawl.org/CC-MAIN-X-index",
                "from": "2023-07-01T00:00:00Z", "to": "2023-07-31T23:59:59Z",
            }])
        if request.url.host == "index.commoncrawl.org":
            return httpx.Response(200, content="\n".join(json.dumps(row) for row in rows).encode())
        if request.url.path.endswith("response.warc.gz"):
            return httpx.Response(206, content=warc(b"exact-rar", target_url=item.source_url))
        return httpx.Response(206, content=warc(b"", target_url=item.source_url, record_type="revisit"))

    with mock_client(handler) as client:
        result = audit.CommonCrawlClient(
            audit.ReadOnlyTransport(client, sleep=lambda _: None), observed_at=NOW
        ).inspect(item)
    assert [candidate["payload_exact_match"] for candidate in result["candidates"]] == [True, True]
    assert result["candidates"][0]["capture_at"] < result["candidates"][1]["capture_at"]


def test_cdxj_rejects_foreign_target_and_invalid_range_metadata():
    item = target()
    row = {
        "timestamp": "20230702120000", "url": "https://evil.example/a", "status": "200",
        "digest": "A", "length": "1", "offset": "0", "filename": "crawl-data/x.warc.gz",
    }
    with pytest.raises(audit.AuditError, match="COMMONCRAWL_EXACT_URL_MISMATCH"):
        audit._parse_cdxj(json.dumps(row).encode(), item.source_url)


def test_transport_retries_caps_redirects_and_disables_foreign_redirect():
    attempts = 0

    def retry_handler(request):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"ok")

    with mock_client(retry_handler) as client:
        transport = audit.ReadOnlyTransport(client, sleep=lambda _: None)
        assert transport.get("https://web.archive.org/index", hosts=audit.WAYBACK_HOSTS, max_bytes=2).body == b"ok"
    assert attempts == 3

    def redirect_handler(request):
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    with mock_client(redirect_handler) as client:
        with pytest.raises(audit.AuditError, match="UNSAFE_ARCHIVE_URL"):
            audit.ReadOnlyTransport(client).get(
                "https://web.archive.org/index", hosts=audit.WAYBACK_HOSTS, max_bytes=10
            )

    with mock_client(lambda request: httpx.Response(200, content=b"abc")) as client:
        with pytest.raises(audit.AuditError, match="ARCHIVE_RESPONSE_OVERSIZE"):
            audit.ReadOnlyTransport(client).get(
                "https://web.archive.org/index", hosts=audit.WAYBACK_HOSTS, max_bytes=2
            )

    with mock_client(lambda request: (_ for _ in ()).throw(httpx.RemoteProtocolError("secret"))) as client:
        with pytest.raises(audit.AuditError, match="ARCHIVE_CONNECTION_ERROR"):
            audit.ReadOnlyTransport(client).get(
                "https://web.archive.org/index", hosts=audit.WAYBACK_HOSTS, max_bytes=2
            )


def test_retry_after_and_bounded_transport_retry():
    sleeps = []
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, content=b"ok")

    with mock_client(handler) as client:
        transport = audit.ReadOnlyTransport(
            client, sleep=sleeps.append, clock=lambda: NOW
        )
        result = transport.get(
            "https://web.archive.org/index",
            hosts=audit.WAYBACK_HOSTS,
            max_bytes=2,
        )
    assert result.body == b"ok"
    assert attempts == 2
    assert sleeps == [7.0]


def _terminalize(checkpoint, source, state=audit.INDEX_CONFIRMED_NO_CAPTURE):
    for artifact in checkpoint["artifacts"]:
        entry = artifact["sources"][source]
        entry.update(
            state=state,
            candidates=[],
            index_rows=[],
            index_cursor=0,
            payload_cursor=0,
            source_error_code=None,
        )
    audit._seal_checkpoint(checkpoint)


def test_checkpoint_round_trip_integrity_and_atomic_write(tmp_path):
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    path = tmp_path / "checkpoint.json"
    audit.write_checkpoint(path, checkpoint, updated_at=NOW)
    loaded, loaded_inventory = audit.load_checkpoint(path)
    assert loaded_inventory == source
    assert loaded["checkpoint_evidence_sha256"] == checkpoint["checkpoint_evidence_sha256"]
    assert not list(tmp_path.glob("*.tmp"))

    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["schema"] = "wrong"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(audit.AuditError, match="CHECKPOINT_INCOMPATIBLE"):
        audit.load_checkpoint(path)


def test_checkpoint_inventory_and_evidence_mismatch_rejected(tmp_path):
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    checkpoint["target_inventory_sha256"] = "f" * 64
    audit._seal_checkpoint(checkpoint)
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(audit.AuditError, match="CHECKPOINT_INCOMPATIBLE"):
        audit.load_checkpoint(path)

    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    checkpoint["artifacts"][0]["sources"][audit.WAYBACK]["state"] = (
        audit.EXACT_VERSION_VERIFIED
    )
    checkpoint["checkpoint_evidence_sha256"] = "0" * 64
    path.write_text(json.dumps(checkpoint), encoding="utf-8")
    with pytest.raises(audit.AuditError, match="CHECKPOINT_INCOMPATIBLE"):
        audit.load_checkpoint(path)


class CountingArchive:
    def __init__(self, result=None, error=None):
        self.result = result or {"candidates": [], "source_error_code": None}
        self.error = error
        self.calls = 0

    def inspect(self, item):
        self.calls += 1
        if self.error:
            raise audit.AuditError(self.error)
        return self.result


def test_resume_skips_verified_and_confirmed_no_capture(tmp_path):
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    _terminalize(checkpoint, audit.COMMON_CRAWL)
    _terminalize(checkpoint, audit.WAYBACK)
    first = checkpoint["artifacts"][0]["sources"][audit.WAYBACK]
    first["state"] = audit.EXACT_VERSION_VERIFIED
    first["candidates"] = [capture(audit.WAYBACK, source.artifacts[0])]
    audit._seal_checkpoint(checkpoint)
    path = tmp_path / "checkpoint.json"
    audit.write_checkpoint(path, checkpoint, updated_at=NOW)
    client = CountingArchive(error="ARCHIVE_TIMEOUT")
    report = audit.execute_resumable_audit(
        checkpoint,
        source,
        wayback=client,
        commoncrawl=CountingArchive(),
        selected_sources=(audit.WAYBACK,),
        checkpoint_path=path,
        clock=lambda: NOW,
        resumed_from_checkpoint=True,
    )
    assert client.calls == 0
    assert report["reused_completed_checks"] == 104
    assert report["wayback_exact_version_matches"] == 1
    assert first["state"] == audit.EXACT_VERSION_VERIFIED


@pytest.mark.parametrize("initial", [audit.PAYLOAD_PENDING, audit.TECHNICAL_FAILURE_FINAL_FOR_RUN])
def test_resume_retries_only_incomplete_and_can_verify_payload(tmp_path, initial):
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    _terminalize(checkpoint, audit.WAYBACK)
    _terminalize(checkpoint, audit.COMMON_CRAWL)
    entry = checkpoint["artifacts"][0]["sources"][audit.WAYBACK]
    entry["state"] = initial
    entry["source_error_code"] = "ARCHIVE_TIMEOUT" if "TECHNICAL" in initial else None
    audit._seal_checkpoint(checkpoint)
    path = tmp_path / "checkpoint.json"
    audit.write_checkpoint(path, checkpoint, updated_at=NOW)
    exact = {"candidates": [capture(audit.WAYBACK, source.artifacts[0])], "source_error_code": None}
    client = CountingArchive(result=exact)
    report = audit.execute_resumable_audit(
        checkpoint,
        source,
        wayback=client,
        commoncrawl=CountingArchive(),
        selected_sources=(audit.WAYBACK,),
        checkpoint_path=path,
        clock=lambda: NOW,
        resumed_from_checkpoint=True,
    )
    assert client.calls == 1
    assert entry["state"] == audit.EXACT_VERSION_VERIFIED
    assert report["newly_completed_checks"] == 1
    assert report["unresolved_technical_findings"] == 0


def test_technical_failure_never_becomes_no_capture(tmp_path):
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    _terminalize(checkpoint, audit.WAYBACK)
    _terminalize(checkpoint, audit.COMMON_CRAWL)
    entry = checkpoint["artifacts"][0]["sources"][audit.WAYBACK]
    entry["state"] = audit.TECHNICAL_FAILURE_RETRYABLE
    audit._seal_checkpoint(checkpoint)
    path = tmp_path / "checkpoint.json"
    audit.write_checkpoint(path, checkpoint, updated_at=NOW)
    report = audit.execute_resumable_audit(
        checkpoint,
        source,
        wayback=CountingArchive(error="ARCHIVE_TIMEOUT"),
        commoncrawl=CountingArchive(),
        selected_sources=(audit.WAYBACK,),
        checkpoint_path=path,
        clock=lambda: NOW,
        resumed_from_checkpoint=True,
    )
    assert entry["state"] == audit.TECHNICAL_FAILURE_FINAL_FOR_RUN
    assert entry["source_error_code"] == "ARCHIVE_TIMEOUT"
    assert report["status"] == "incomplete"
    assert report["artifacts"][0]["pit_evidence_class"] == "UNKNOWN"
    assert "TECHNICAL_FAILURE_IS_NOT_NO_CAPTURE" in report["artifacts"][0]["notes"]


def test_commoncrawl_rate_limit_breaker_preserves_remaining_pending(tmp_path):
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    _terminalize(checkpoint, audit.WAYBACK)
    path = tmp_path / "checkpoint.json"
    audit.write_checkpoint(path, checkpoint, updated_at=NOW)

    class RateLimited(CountingArchive):
        rate_limited_for_run = False

    client = RateLimited(error="ARCHIVE_RATE_LIMITED")
    report = audit.execute_resumable_audit(
        checkpoint,
        source,
        wayback=CountingArchive(),
        commoncrawl=client,
        selected_sources=(audit.COMMON_CRAWL,),
        checkpoint_path=path,
        clock=lambda: NOW,
        resumed_from_checkpoint=True,
    )
    states = [row["sources"][audit.COMMON_CRAWL]["state"] for row in checkpoint["artifacts"]]
    assert client.calls == 1
    assert states[0] == audit.TECHNICAL_FAILURE_FINAL_FOR_RUN
    assert states[1:] == [audit.PENDING] * 103
    assert report["commoncrawl_source_audit_complete"] is False


def test_fresh_and_resumed_checkpoint_reports_are_semantically_equivalent(tmp_path):
    source = inventory()
    reports = []
    for index in range(2):
        checkpoint = audit.create_checkpoint(
            source, observed_at=NOW, created_at=NOW + timedelta(seconds=index)
        )
        path = tmp_path / f"checkpoint-{index}.json"
        audit.write_checkpoint(path, checkpoint, updated_at=NOW)
        report = audit.execute_resumable_audit(
            checkpoint,
            source,
            wayback=CountingArchive(),
            commoncrawl=CountingArchive(),
            selected_sources=(audit.WAYBACK, audit.COMMON_CRAWL),
            checkpoint_path=path,
            clock=lambda: NOW,
            resumed_from_checkpoint=bool(index),
        )
        reports.append(report)
    assert reports[0]["semantic_evidence_sha256"] == reports[1]["semantic_evidence_sha256"]
    assert reports[0]["status"] == reports[1]["status"] == "complete"
    assert reports[0]["filesystem_write_scope"] == "LOCAL_OPERATIONAL_CHECKPOINT_ONLY"


def test_resolution_selects_earliest_bound_without_publication_promotion():
    source = inventory()
    key = (source.artifacts[0].report_date, source.artifacts[0].form)
    report = audit.run_audit(
        source,
        wayback=FakeArchive(audit.WAYBACK, {key}),
        commoncrawl=FakeArchive(audit.COMMON_CRAWL, {key}),
        observed_at=NOW,
    )
    assert report["status"] == "complete"
    assert report["artifacts_with_conservative_bound"] == 1
    assert report["artifacts_without_conservative_bound"] == 103
    assert report["coverage_ratio"] == "0.009615"
    assert report["overall_archival_status"] == "ARCHIVAL_EVIDENCE_PARTIAL"
    assert report["publication_time_proven"] is report["pit_ready"] is False
    assert report["publication_at"] is None
    assert report["frozen_task258b_binding"] is False
    assert report["frozen_batch_vds_run_required"] is True
    assert report["wayback_index_hits"] == report["wayback_payload_available"] == 1
    assert report["commoncrawl_exact_version_matches"] == 1
    assert len(report["artifacts"]) == 104


def test_all_artifacts_exact_is_only_sufficient_threshold():
    source = inventory()
    all_keys = {(item.report_date, item.form) for item in source.artifacts}
    report = audit.run_audit(
        source,
        wayback=FakeArchive(audit.WAYBACK, all_keys),
        commoncrawl=FakeArchive(audit.COMMON_CRAWL),
        observed_at=NOW,
    )
    assert report["artifacts_with_conservative_bound"] == 104
    assert report["coverage_ratio"] == "1.000000"
    assert report["overall_archival_status"] == "ARCHIVAL_EVIDENCE_SUFFICIENT_FOR_NEXT_PIT_CONTRACT"
    assert report["pit_ready"] is False


def test_no_capture_is_complete_negative_evidence_not_nonpublication():
    source = inventory(audit.FROZEN_BINDING)
    report = audit.run_audit(
        source,
        wayback=FakeArchive(audit.WAYBACK),
        commoncrawl=FakeArchive(audit.COMMON_CRAWL),
        observed_at=NOW,
    )
    assert report["status"] == "complete"
    assert report["overall_archival_status"] == "ARCHIVAL_EVIDENCE_INSUFFICIENT"
    assert report["artifacts_with_conservative_bound"] == 0
    assert report["wayback_index_hits"] == report["commoncrawl_index_hits"] == 0
    assert all("NO_CAPTURE_DOES_NOT_PROVE_NON_PUBLICATION" in row["notes"] for row in report["artifacts"])
    assert report["frozen_task258b_binding"] is True
    assert report["frozen_batch_vds_run_required"] is False


def test_temporal_anomaly_and_source_failure_are_fail_closed():
    source = inventory()
    first = source.artifacts[0]

    class Anomaly:
        def inspect(self, item):
            if item is first:
                return {"candidates": [capture(audit.WAYBACK, item, "20230630120000")], "source_error_code": None}
            return {"candidates": [], "source_error_code": None}

    report = audit.run_audit(source, wayback=Anomaly(), commoncrawl=FailingArchive(), observed_at=NOW)
    assert report["status"] == "incomplete"
    assert report["overall_archival_status"] == "UNKNOWN"
    assert report["artifacts"][0]["temporal_anomaly"] is True
    assert report["artifacts"][0]["historical_availability_proven_by_archive"] is False
    assert report["pit_ready"] is False


def test_deterministic_report_and_safety_contract():
    source = inventory()
    kwargs = dict(
        inventory=source,
        wayback=FakeArchive(audit.WAYBACK),
        commoncrawl=FakeArchive(audit.COMMON_CRAWL),
        observed_at=NOW,
    )
    first = audit.run_audit(**kwargs)
    second = audit.run_audit(**kwargs)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    for field in (
        "publication_time_proven", "pit_ready", "database_accessed",
        "database_mutation_executed", "database_persistence", "filesystem_write",
        "archive_write_executed", "publication_backfill_executed", "normalization", "scoring",
    ):
        assert first[field] is False
    assert first["production_actions"] == "NONE"


def test_cli_invalid_arguments_and_sanitized_unreadable_inventory(capsys):
    assert audit.main([], clock=lambda: NOW) == 2
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["error_code"] == "INVALID_ARGUMENTS"
    assert audit.main(["--target-inventory", "postgresql://user:secret@host/db"], clock=lambda: NOW) == 1
    failed = json.loads(capsys.readouterr().out)
    assert failed["error_code"] == "TARGET_INVENTORY_UNREADABLE"
    assert "secret" not in json.dumps(failed)


def test_module_has_no_db_archive_write_or_save_page_surface():
    path = Path(audit.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith(("sqlalchemy", "app.models", "alembic")) for name in imports)
    text = path.read_text(encoding="utf-8").lower()
    assert "save page now" not in text
    assert "requests.post" not in text
    assert "publication_at\": none" in text


def semantic_projection(*, rows=("row-a",), schema="schema-a", member="member-a"):
    return {
        "semantic_observations": list(rows),
        "form_structural_schema_fingerprint": schema,
        "member_schema_fingerprints": [["VALUE.DBF", schema]],
        "value_member_schema_fingerprint": schema,
        "member_content_sha256": [["VALUE.DBF", member]],
    }


def test_semantic_mismatch_container_only_and_metadata_only():
    archived = semantic_projection()
    current = semantic_projection()
    result = audit.classify_semantic_difference(archived, current)
    assert result["semantic_classification"] == "CONTAINER_ONLY_DIFFERENCE"
    assert result["raw_lexical_equal"] is True

    current = semantic_projection(member="member-b")
    result = audit.classify_semantic_difference(archived, current)
    assert result["semantic_classification"] == "METADATA_ONLY_DIFFERENCE"
    assert result["raw_data_difference"] is False


def test_semantic_mismatch_raw_value_and_row_set_are_raw_data():
    archived = semantic_projection(rows=("row-a", "row-b"))
    changed = semantic_projection(rows=("row-a", "row-c"))
    result = audit.classify_semantic_difference(archived, changed)
    assert result["semantic_classification"] == "RAW_DATA_DIFFERENCE"
    assert result["added_rows"] == result["removed_rows"] == 1
    assert result["changed_raw_values"] == 1

    added = semantic_projection(rows=("row-a", "row-b", "row-c"))
    result = audit.classify_semantic_difference(archived, added)
    assert result["semantic_classification"] == "RAW_DATA_DIFFERENCE"
    assert result["added_rows"] == 1 and result["removed_rows"] == 0


def test_semantic_mismatch_schema_change_is_structural_when_rows_equal():
    archived = semantic_projection()
    current = semantic_projection(schema="schema-b", member="member-b")
    result = audit.classify_semantic_difference(archived, current)
    assert result["semantic_classification"] == "STRUCTURAL_SCHEMA_DIFFERENCE"
    assert result["schema_difference"] is True
    assert result["value_member_schema_difference"] is True


def test_semantic_mismatch_raw_change_has_precedence_over_schema_change():
    archived = semantic_projection(rows=("old",))
    current = semantic_projection(rows=("new",), schema="schema-b")
    result = audit.classify_semantic_difference(archived, current)
    assert result["semantic_classification"] == "RAW_DATA_DIFFERENCE"
    assert result["schema_difference"] is True


def test_semantic_audit_checksum_failure_is_unknown_without_checkpoint_write():
    source = inventory()
    checkpoint = audit.create_checkpoint(source, observed_at=NOW, created_at=NOW)
    _terminalize(checkpoint, audit.WAYBACK)
    for target_row, artifact in zip(source.artifacts[:3], checkpoint["artifacts"][:3]):
        stamp = "20240731120000"
        candidate = capture(audit.WAYBACK, target_row, stamp, exact=False)
        candidate["archive_payload_sha256"] = hashlib.sha256(b"expected").hexdigest()
        candidate["capture_record_locator"] = (
            f"https://web.archive.org/web/{stamp}id_/{target_row.source_url}"
        )
        artifact["sources"][audit.WAYBACK]["state"] = audit.VERSION_MISMATCH
        artifact["sources"][audit.WAYBACK]["candidates"] = [candidate]
    audit._seal_checkpoint(checkpoint)

    with mock_client(lambda request: httpx.Response(200, content=b"changed")) as client:
        report = audit.run_wayback_mismatch_semantic_audit(
            checkpoint,
            source,
            transport=audit.ReadOnlyTransport(client, sleep=lambda _: None),
            observed_at=NOW,
        )
    assert report["status"] == "incomplete"
    assert report["classification_counts"]["UNKNOWN"] == 3
    assert {row["error_code"] for row in report["artifacts"]} == {
        "WAYBACK_ARCHIVED_PAYLOAD_CHANGED"
    }
    assert report["checkpoint_mutated"] is False
    assert report["common_crawl_continued"] is False


def test_task259b_r3_final_audit_contract_is_partial_and_pit_safe():
    document = (
        Path(__file__).parents[2]
        / "docs"
        / "audits"
        / "TASK259B_CBR_BANK_EXTERNAL_ARCHIVAL_AVAILABILITY_AUDIT.md"
    ).read_text(encoding="utf-8")
    normalized = " ".join(document.split())

    required = (
        "STATUS=PASS",
        "LIVE_ARCHIVAL_AUDIT=PARTIAL_COMPLETE_BY_DESIGN",
        "OVERALL_ARCHIVAL_STATUS=ARCHIVAL_EVIDENCE_PARTIAL",
        "WAYBACK_COMPLETE=104/104",
        "WAYBACK_EXACT_VERSION=22",
        "WAYBACK_NO_CAPTURE=79",
        "WAYBACK_VERSION_MISMATCH=3",
        "WAYBACK_UNRESOLVED=0",
        "COMMON_CRAWL_SOURCE_AUDIT_COMPLETE=false",
        "COMMON_CRAWL_COMPLETE=10/104",
        "COMMON_CRAWL_NO_CAPTURE=10",
        "COMMON_CRAWL_EXACT_VERSION=0",
        "COMMON_CRAWL_UNRESOLVED=94",
        "COMMON_CRAWL_DEFERRED=true",
        "RAW_DATA_DIFFERENCES=3",
        "UNKNOWN_DIFFERENCES=0",
        "FROZEN_TASK258B_BINDING=PROVEN",
        "EVIDENCE_ORIGIN=OPERATOR_OBSERVED_READ_ONLY_VDS_BINDING",
        "HISTORICAL_REVISION_RISK_PROVEN=true",
        "CURRENT_CBR_HISTORICAL_ARTIFACTS_POINT_IN_TIME_FAITHFUL=DISPROVEN",
        "PRODUCTION_RAW_LAYER_CONTAINS_LATER_REVISIONS=PROVEN_FOR_3_IDENTIFIED_ARTIFACTS",
        "PUBLICATION_TIME_PROVEN=false",
        "PIT_READY=false",
        "NEXT_RECOMMENDED_TASK=260A_HISTORICAL_ARTIFACT_VERSIONING_AND_PIT_SELECTION_CONTRACT",
    )
    assert all(value in document for value in required)
    assert "Historical Revision Discovery" in document
    assert "Frozen Task258B Binding" in document
    assert "Impact on BondRadar Backtesting" in document
    assert "It does not prove that all 104 artifacts were revised" in normalized
    assert "Codex did not independently execute those VDS commands" in normalized
    assert "no archive capture != proof of non-publication" in document
    assert "archive_capture_at != publication_at" in document
    assert "TASK259B_COMMIT_ALLOWED=false" not in document
