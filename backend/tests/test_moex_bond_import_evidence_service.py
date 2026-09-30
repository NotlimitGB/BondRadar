from __future__ import annotations

from dataclasses import dataclass
import json

import httpx
import pytest

from app.schemas.tinvest_bond_admission_manifest import (
    MoexBondResolutionProjection,
    MoexSecurityMatchStatus,
    TInvestBondAdmissionManifestView,
)
from app.schemas.tinvest_bond_identity_bridge import BondIdentityProjection
from app.schemas.tinvest_instrument_universe import (
    TInvestAvailabilityClass,
    TInvestBondUniverseInstrument,
)
from app.services.moex_bond_import_evidence_service import MoexBondImportEvidenceService
from app.services.moex_iss_client import MoexBondBoardSnapshotResult, MoexIssClient, MoexSecurityReferenceCandidate
from app.services.tinvest_bond_admission_manifest_service import (
    TInvestBondAdmissionManifestService,
)
from app.services.tinvest_bond_identity_bridge_service import (
    TInvestBondIdentityBridgeService,
)
from app.services.tinvest_bond_import_preflight_service import (
    TInvestBondImportPreflightService,
)


def _source(uid: str, isin: str) -> TInvestBondUniverseInstrument:
    return TInvestBondUniverseInstrument(
        uid=uid,
        isin=isin,
        class_code="TQCB",
        currency="rub",
        buy_available=True,
        sell_available=True,
        api_trade_available=True,
        for_qual_investor=False,
        required_tests=(),
        required_tests_state="SOURCE_EMPTY",
        availability_classification=TInvestAvailabilityClass.API_BUY_AVAILABLE,
        source_fields={
            "sector": "financial",
            "currency": "rub",
            "bondType": "BOND_TYPE_CORPORATE",
            "countryOfRisk": "RU",
            "classCode": "TQCB",
        },
    )


def _manifest(count: int = 1) -> TInvestBondAdmissionManifestView:
    sources = tuple(
        _source(f"UID-{i:02}", f"RU000A100{i:04}") for i in range(1, count + 1)
    )
    internal: tuple[BondIdentityProjection, ...] = ()
    bridge = TInvestBondIdentityBridgeService.build(sources, internal, ())
    resolutions = tuple(
        MoexBondResolutionProjection(
            source_isin=row.source_isin,
            security_match_status=MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED,
            matched_secid=f"TASKC1-{row.source_isin[-4:]}",
            matched_isin=row.source_isin,
            candidate_count=1,
            matched_candidate_count=1,
            primary_board="TQCB",
            issuer_metadata_status="ISSUER_COMPLETE",
            issuer_id="issuer",
            issuer_title="Issuer Ltd",
            issuer_inn="7700000000",
        )
        for row in bridge.bridge_rows
        if row.source_isin is not None
    )
    return TInvestBondAdmissionManifestService.build(
        source_bonds=sources,
        identity_bridge=bridge,
        internal_bonds=internal,
        moex_resolutions=resolutions,
        core_m3_complete_bond_ids=(),
    )


@dataclass
class FakeMoexClient:
    references: dict[str, MoexSecurityReferenceCandidate]
    descriptions: dict[str, dict]
    pages: tuple[tuple[list[dict], list[str]], ...]
    max_pages: int = 100
    reference_queries: list[str] | None = None
    board_calls: list[tuple[str, int, int]] | None = None
    description_calls: list[tuple[str, str | None]] | None = None
    reference_error: bool = False
    description_error: bool = False
    board_error: bool = False
    snapshot: MoexBondBoardSnapshotResult | None = None

    def __post_init__(self):
        self.reference_queries = []
        self.board_calls = []
        self.description_calls = []

    def fetch_security_reference_candidates(self, query, *, limit=100):
        self.reference_queries.append(query)
        if self.reference_error:
            raise RuntimeError("sensitive source detail")
        row = self.references.get(query)
        return [row] if row is not None else []

    def fetch_bond_board_snapshot(self, board, *, limit):
        self.board_calls.append((board, 0, limit))
        if self.board_error:
            raise RuntimeError("sensitive board detail")
        if self.snapshot is not None:
            return self.snapshot
        assert len(self.pages) == 1
        rows, warnings = self.pages[0]
        return MoexBondBoardSnapshotResult(rows=tuple(rows), warnings=tuple(warnings),
            completion_status="INCOMPLETE" if warnings else "COMPLETE", request_count=1)

    def fetch_bond_description(self, secid, *, board=None):
        self.description_calls.append((secid, board))
        if self.description_error:
            raise RuntimeError("sensitive description detail")
        return self.descriptions[secid], []


def _client(batch: TInvestBondAdmissionManifestView, **kwargs) -> FakeMoexClient:
    references = {}
    descriptions = {}
    board_rows = []
    for candidate in batch.import_candidate_manifest:
        references[candidate.matched_secid] = MoexSecurityReferenceCandidate(
            secid=candidate.matched_secid,
            isin=candidate.isin,
            short_name="Issuer",
            full_name="Issuer Ltd",
            primary_board="TQCB",
            issuer_id="MOEX-ISSUER",
            issuer_title="Issuer Ltd",
            issuer_inn="7700000000",
            issuer_okpo=None,
        )
        descriptions[candidate.matched_secid] = {
            "secid": candidate.matched_secid,
            "isin": candidate.isin,
            "name": "Issuer corporate bond 2030",
            "shortname": "Issuer 30",
            "currency": "RUB",
            "nominal_value": "1000",
            "maturity_date": "2030-07-15",
            "is_perpetual": False,
            "is_traded": None,
            # Deliberate poison: adapter must not treat these as issuer/board evidence.
            "issuer_name": "FORGED FROM DESCRIPTION",
            "issuer_inn": "BAD",
            "primary_board": "TQRD",
            "__moex_board_observed": True,
            "raw": {"BONDTYPE": "CORPORATE"},
        }
        board_rows.append(
            {
                "secid": candidate.matched_secid,
                "isin": candidate.isin,
                "is_traded": None,
                "status": None,
            }
        )
    return FakeMoexClient(
        references=references,
        descriptions=descriptions,
        pages=((board_rows, []),),
        **kwargs,
    )


def _preflight(batch, client):
    evidence = MoexBondImportEvidenceService(client).build(admission_manifest=batch)
    return TInvestBondImportPreflightService.build(
        admission_manifest=batch,
        moex_evidence=evidence,
        internal_bonds=(),
        company_projections=(),
    ), evidence


def test_complete_sources_are_separate_and_board_scan_is_shared_once() -> None:
    batch = _manifest(2)
    client = _client(batch)
    result, evidence = _preflight(batch, client)
    assert result.summary.ready_count == 2
    assert evidence.board_scan_status == "COMPLETE"
    assert evidence.board_scan_pages_fetched == 1
    assert evidence.board_scan_rows_fetched == 2
    assert len(client.board_calls) == 1
    assert client.board_calls == [("TQCB", 0, 100)]
    assert len(client.reference_queries) == 2
    assert len(client.description_calls) == 2
    assert all(board == "TQCB" for _, board in client.description_calls)
    for row in result.candidate_rows:
        assert row.issuer_name == "Issuer Ltd"
        assert row.issuer_inn == "7700000000"
        assert row.primary_board == "TQCB"
        assert row.board_observed is True
        assert row.moex_description.issuer_name is None
        assert row.moex_description.issuer_inn is None
        assert row.moex_description.primary_board is None
        assert row.moex_description.board_observed is None
        assert "ACTIVE_STATUS_UNKNOWN" in {reason.value for reason in row.reason_codes}


def test_paginated_tqcb_scan_is_reused_and_incomplete_scan_blocks_ready() -> None:
    batch = _manifest(1)
    candidate = batch.import_candidate_manifest[0]
    first_page = [
        {"secid": f"OTHER-{index}", "isin": f"OTHER-ISIN-{index}"}
        for index in range(99)
    ]
    first_page.append(
        {"secid": candidate.matched_secid, "isin": candidate.isin}
    )
    client = _client(batch)
    client.snapshot = MoexBondBoardSnapshotResult(rows=tuple(first_page + [{"secid": "OTHER-LAST", "isin": "OTHER"}]),
        completion_status="COMPLETE", request_count=2, cursor_present=True,
        cursor_index=100, cursor_total=101, cursor_page_size=1)
    result, evidence = _preflight(batch, client)
    assert client.board_calls == [("TQCB", 0, 100)]
    assert evidence.board_scan_pages_fetched == 2
    assert evidence.board_scan_status == "COMPLETE"
    assert result.summary.ready_count == 1

    limited = _client(batch)
    limited.max_pages = 1
    limited.pages = ((first_page, []),)
    limited.snapshot = MoexBondBoardSnapshotResult(rows=tuple(first_page),
        completion_status="INCOMPLETE", request_count=1, warnings=("BOARD_PAGE_BOUND_EXHAUSTED",))
    blocked, partial_evidence = _preflight(batch, limited)
    assert partial_evidence.board_scan_status == "INCOMPLETE"
    assert blocked.candidate_rows[0].status.value == "REVIEW_REQUIRED"
    assert "BOARD_SCAN_INCOMPLETE" in {
        reason.value for reason in blocked.candidate_rows[0].reason_codes
    }


def test_reference_conflict_and_missing_issuer_never_fall_back_to_description_title() -> None:
    batch = _manifest()
    candidate = batch.import_candidate_manifest[0]
    client = _client(batch)
    client.references[candidate.matched_secid] = MoexSecurityReferenceCandidate(
        secid=candidate.matched_secid,
        isin="OTHER-ISIN",
        short_name=None,
        full_name=None,
        primary_board="TQCB",
        issuer_id=None,
        issuer_title=None,
        issuer_inn=None,
        issuer_okpo=None,
    )
    result, evidence = _preflight(batch, client)
    assert result.candidate_rows[0].status.value == "IDENTITY_CONFLICT"
    assert evidence.candidate_evidence[0].issuer.security_match_status == "SECURITY_IDENTIFIER_CONFLICT"
    assert result.candidate_rows[0].issuer_name is None
    assert result.candidate_rows[0].moex_description.issuer_name is None

    client = _client(batch)
    client.references.clear()
    result, evidence = _preflight(batch, client)
    assert result.candidate_rows[0].status.value == "REVIEW_REQUIRED"
    assert "ISSUER_REFERENCE_MISSING" in {
        reason.value for reason in result.candidate_rows[0].reason_codes
    }
    assert result.candidate_rows[0].issuer_name is None
    assert evidence.candidate_evidence[0].issuer.issuer_title is None

    # Exact ISIN with a different SECID is not a recovery: all source identity
    # assertions are checked against the frozen candidate before readiness.
    client = _client(batch)
    candidate = batch.import_candidate_manifest[0]
    client.references[candidate.matched_secid] = MoexSecurityReferenceCandidate(
        secid="OTHER-SECID",
        isin=candidate.isin,
        short_name="Issuer",
        full_name="Issuer Ltd",
        primary_board="TQCB",
        issuer_id="MOEX-ISSUER",
        issuer_title="Issuer Ltd",
        issuer_inn="7700000000",
        issuer_okpo=None,
    )
    result, _ = _preflight(batch, client)
    assert result.candidate_rows[0].status.value == "IDENTITY_CONFLICT"


def test_source_errors_are_sanitized_and_fail_closed() -> None:
    batch = _manifest()
    client = _client(batch, reference_error=True, description_error=True, board_error=True)
    result, evidence = _preflight(batch, client)
    assert result.summary.ready_count == 0
    assert result.candidate_rows[0].status.value == "REVIEW_REQUIRED"
    dump = str(evidence.model_dump(mode="json"))
    assert "sensitive" not in dump
    assert evidence.board_scan_status == "SOURCE_ERROR"
    assert evidence.candidate_evidence[0].issuer.security_match_status == "SOURCE_ERROR"
    assert evidence.candidate_evidence[0].description_status == "SOURCE_ERROR"


def test_description_request_context_and_legacy_marker_cannot_replace_board_row() -> None:
    batch = _manifest()
    candidate = batch.import_candidate_manifest[0]
    client = _client(batch)
    client.pages = (([{"secid": "OTHER-SECID", "isin": "OTHER-ISIN"}], []),)
    result, evidence = _preflight(batch, client)
    row = result.candidate_rows[0]
    assert client.description_calls == [(candidate.matched_secid, "TQCB")]
    assert evidence.candidate_evidence[0].board.observations == ()
    assert row.moex_description is not None
    assert "BOARD_OBSERVATION_MISSING" in {reason.value for reason in row.reason_codes}
    assert row.status.value == "REVIEW_REQUIRED"


def test_invalid_manifest_is_rejected_before_any_source_requests() -> None:
    batch = _manifest()
    invalid = batch.model_copy(update={"pit_ready": True})
    client = _client(batch)
    try:
        MoexBondImportEvidenceService(client).build(admission_manifest=invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid manifest should fail before acquisition")
    assert client.board_calls == []
    assert client.reference_queries == []
    assert client.description_calls == []


def test_description_identity_and_board_activity_conflicts_are_not_silenced() -> None:
    batch = _manifest()
    candidate = batch.import_candidate_manifest[0]
    client = _client(batch)
    client.descriptions[candidate.matched_secid]["isin"] = "OTHER-ISIN"
    result, _ = _preflight(batch, client)
    assert result.candidate_rows[0].status.value == "IDENTITY_CONFLICT"

    client = _client(batch)
    client.pages = (([
        {
            "secid": candidate.matched_secid,
            "isin": candidate.isin,
            "is_traded": 0,
            "status": "active",
        }
    ], []),)
    result, _ = _preflight(batch, client)
    assert result.candidate_rows[0].status.value == "REVIEW_REQUIRED"
    assert "NOT_ACTIVE_OR_NOT_TRADED" in {
        reason.value for reason in result.candidate_rows[0].reason_codes
    }


def test_board_row_with_exact_isin_but_conflicting_secid_is_identity_conflict() -> None:
    batch = _manifest()
    candidate = batch.import_candidate_manifest[0]
    client = _client(batch)
    client.pages = (([
        {"secid": "OTHER-SECID", "isin": candidate.isin},
    ], []),)
    result, evidence = _preflight(batch, client)
    row = result.candidate_rows[0]
    assert row.status.value == "IDENTITY_CONFLICT"
    assert "SECURITY_IDENTITY_CONFLICT" in {reason.value for reason in row.reason_codes}
    assert evidence.candidate_evidence[0].board.observations[0].secid == "OTHER-SECID"
    assert row.board_observed is False


def test_duplicate_board_rows_with_conflicting_isin_block_and_row_order_is_stable() -> None:
    batch = _manifest()
    candidate = batch.import_candidate_manifest[0]
    exact = {"secid": candidate.matched_secid, "isin": candidate.isin}
    wrong = {"secid": candidate.matched_secid, "isin": "OTHER-ISIN"}
    first = _client(batch)
    first.pages = (([exact, wrong], []),)
    conflict, first_evidence = _preflight(batch, first)
    assert conflict.candidate_rows[0].status.value == "IDENTITY_CONFLICT"

    second = _client(batch)
    second.pages = (([wrong, exact], []),)
    conflict_reordered, second_evidence = _preflight(batch, second)
    assert conflict_reordered.model_dump(mode="json") == conflict.model_dump(mode="json")
    assert second_evidence.model_dump(mode="json") == first_evidence.model_dump(mode="json")


def test_adapter_has_no_database_or_mutation_surface() -> None:
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "app" / "services" / "moex_bond_import_evidence_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not ({"sqlalchemy", "requests", "urllib", "os", "pathlib"} & imported)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not ({"Session", "commit", "flush", "rollback", "sync", "add", "delete"} & (names | attributes))


def _board_payload(rows, cursor=None):
    payload = {"securities": {"columns": ["SECID", "ISIN", "STATUS", "IS_TRADED"],
        "data": [[row.get("secid"), row.get("isin"), row.get("status"), row.get("is_traded")] for row in rows]}}
    if cursor is not None:
        payload["securities.cursor"] = {"columns": ["INDEX", "TOTAL", "PAGESIZE"], "data": [list(cursor)]}
    return payload


def _snapshot(payloads, *, max_pages=100):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payloads[min(len(calls) - 1, len(payloads) - 1)])
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        client = MoexIssClient(http_client=http, max_pages=max_pages)
        result = client.fetch_bond_board_snapshot("TQCB", limit=100)
    return result, calls


def _board_rows(count, *, start=0):
    return [{"secid": f"BOARD-{i}", "isin": f"ISIN-{i}"} for i in range(start, start + count)]


def test_task296c3_3033_snapshot_one_request_and_c1_ready_determinism():
    batch = _manifest(2)
    client = _client(batch)
    rows = list(client.pages[0][0]) + _board_rows(3031)
    before = json.dumps(rows, sort_keys=True)
    snapshot, calls = _snapshot([_board_payload(rows)])
    assert snapshot.completion_status == "COMPLETE" and snapshot.request_count == 1
    assert len(snapshot.rows) == 3033 and snapshot.warnings == () and not snapshot.cursor_present
    assert len(calls) == 1 and calls[0].url.params["limit"] == "100"
    assert calls[0].url.params["start"] == "0"
    assert calls[0].url.path.endswith("/boards/TQCB/securities.json")
    client.snapshot = snapshot
    result, evidence = _preflight(batch, client)
    assert result.summary.ready_count == 2
    assert evidence.board_scan_rows_fetched == 3033 and evidence.board_scan_pages_fetched == 1
    assert evidence.board_scan_warning_count == 0
    reasons = {r.value for row in result.candidate_rows for r in row.reason_codes}
    assert not {"BOARD_SCAN_INCOMPLETE", "BOARD_OBSERVATION_MISSING"} & reasons
    reordered, _ = _snapshot([_board_payload(rows[::-1])])
    other = _client(batch)
    other.snapshot = reordered
    result2, evidence2 = _preflight(batch, other)
    assert result2.model_dump() == result.model_dump()
    assert evidence2.model_dump() == evidence.model_dump()
    assert json.dumps(rows, sort_keys=True) == before


def test_task296c3_cursor_pages_and_empty_snapshot():
    result, calls = _snapshot([_board_payload(_board_rows(100), (0, 150, 100)),
        _board_payload(_board_rows(50, start=100), (100, 150, 50))])
    assert result.completion_status == "COMPLETE" and len(result.rows) == 150
    assert result.request_count == 2 and [r.url.params["start"] for r in calls] == ["0", "100"]
    assert (result.cursor_index, result.cursor_total, result.cursor_page_size) == (100, 150, 50)
    for payload in (_board_payload([]), _board_payload([], (0, 0, 100))):
        empty, calls = _snapshot([payload])
        assert empty.completion_status == "COMPLETE" and empty.rows == () and len(calls) == 1
    # A terminal response may retain the original page size.
    terminal, _ = _snapshot([_board_payload(_board_rows(50), (0, 50, 100))])
    assert terminal.completion_status == "COMPLETE"


@pytest.mark.parametrize("cursor", [(-1, 150, 100), (1, 150, 100), (0, -1, 100),
    (0, 150, 0), (0, 150, -1), (False, 150, 100), (0, "150", 100), (0, 150, 99)])
def test_task296c3_invalid_cursor_fails_closed(cursor):
    result, calls = _snapshot([_board_payload(_board_rows(100), cursor)])
    assert result.completion_status == "INCOMPLETE" and result.warnings
    assert len(calls) == 1 and result.rows == ()


@pytest.mark.parametrize("case", ["repeated", "reordered_repeat", "overlap", "total_decrease",
    "total_increase", "nonadvancing", "cursor_missing", "empty_page", "page_bound"])
def test_task296c3_pagination_contradictions_never_accumulate_duplicates(case):
    first = _board_payload(_board_rows(100), (0, 150, 100))
    second = _board_payload(_board_rows(50, start=100), (100, 150, 50))
    if case in {"repeated", "reordered_repeat"}:
        repeated = _board_rows(100)
        second = _board_payload(repeated if case == "repeated" else repeated[::-1], (100, 200, 100))
        first = _board_payload(_board_rows(100), (0, 200, 100))
    elif case == "overlap":
        second = _board_payload(_board_rows(50, start=99), (100, 150, 50))
    elif case == "total_decrease":
        second = _board_payload(_board_rows(40, start=100), (100, 140, 40))
    elif case == "total_increase":
        second = _board_payload(_board_rows(50, start=100), (100, 160, 50))
    elif case == "nonadvancing":
        second = _board_payload(_board_rows(50, start=100), (0, 150, 50))
    elif case == "cursor_missing":
        second = _board_payload(_board_rows(50, start=100))
    elif case == "empty_page":
        second = _board_payload([], (100, 150, 50))
    result, calls = _snapshot([first, second], max_pages=1 if case == "page_bound" else 100)
    assert result.completion_status == "INCOMPLETE" and result.warnings
    assert len(result.rows) == 100 and len(calls) == (1 if case == "page_bound" else 2)
    assert len({json.dumps(row, sort_keys=True) for row in result.rows}) == 100


@pytest.mark.parametrize("payload", [{}, {"securities": {}},
    {"securities": {"columns": ["SECID", "SECID"], "data": [["x", "x"]]}},
    {"securities": {"columns": [1], "data": [["x"]]}},
    {"securities": {"columns": ["SECID"], "data": [["x", "y"]]}},
    {"securities": {"columns": ["SECID"], "data": [{}]}},
    {"securities": {"columns": ["SECID"], "data": "bad"}},
    {**_board_payload([]), "securities.cursor": None},
    {**_board_payload([]), "securities.cursor": {"columns": ["INDEX", "TOTAL", "PAGESIZE"], "data": []}}])
def test_task296c3_malformed_tables_are_not_complete(payload):
    result, calls = _snapshot([payload])
    assert result.completion_status == "INCOMPLETE" and result.warnings and len(calls) == 1


def test_task296c3_deduplicate_only_exact_snapshot_rows_and_warnings_block():
    batch = _manifest()
    client = _client(batch)
    exact = client.pages[0][0][0]
    wrong = {**exact, "isin": "OTHER-ISIN"}
    snapshot, _ = _snapshot([_board_payload([exact, exact, wrong])])
    assert snapshot.completion_status == "COMPLETE" and len(snapshot.rows) == 2
    client.snapshot = snapshot
    result, _ = _preflight(batch, client)
    assert result.candidate_rows[0].status.value == "IDENTITY_CONFLICT"
    client.snapshot = MoexBondBoardSnapshotResult(rows=(exact,), completion_status="COMPLETE",
        request_count=1, warnings=("BOARD_SOURCE_WARNING",))
    result, evidence = _preflight(batch, client)
    assert evidence.board_scan_status == "INCOMPLETE" and result.summary.ready_count == 0
    assert "BOARD_SCAN_INCOMPLETE" in {r.value for r in result.candidate_rows[0].reason_codes}


def test_task296c3_source_failure_is_sanitized_and_legacy_loader_unchanged():
    def handler(request):
        raise httpx.ReadTimeout("secret token and private URL", request=request)
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(handler)) as http:
        result = MoexIssClient(http_client=http).fetch_bond_board_snapshot("TQCB")
    assert result.completion_status == "SOURCE_ERROR" and result.request_count == 1
    assert "secret" not in repr(result)
    calls = []
    def success(request):
        calls.append(request)
        return httpx.Response(200, json=_board_payload(_board_rows(101)))
    with httpx.Client(base_url="https://mock.invalid", transport=httpx.MockTransport(success)) as http:
        rows, warnings = MoexIssClient(http_client=http).fetch_bond_universe("TQCB", start=17, limit=5)
    assert len(rows) == 101 and warnings == [] and len(calls) == 1
    assert calls[0].url.params["start"] == "17" and calls[0].url.params["limit"] == "5"
    assert "iss.only" not in calls[0].url.params


def test_task296c3_missing_board_identifiers_and_no_candidates():
    batch = _manifest()
    candidate = batch.import_candidate_manifest[0]
    for row in ({"secid": candidate.matched_secid}, {"isin": candidate.isin}):
        client = _client(batch)
        client.pages = (([row], []),)
        result, _ = _preflight(batch, client)
        assert result.summary.ready_count == 0
        assert "BOARD_OBSERVATION_MISSING" in {r.value for r in result.candidate_rows[0].reason_codes}
    empty = _manifest(0)
    client = _client(empty)
    evidence = MoexBondImportEvidenceService(client).build(admission_manifest=empty)
    assert client.board_calls == [] and evidence.board_scan_pages_fetched == 0
