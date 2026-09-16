from dataclasses import replace
from datetime import datetime, timezone, date, timedelta
import json
from urllib.parse import parse_qs

import httpx
import pytest
from sqlalchemy import select

from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact
from app.services.credit_risk_evidence.contracts import (
    SourceProvider, SourceKind, RatingAgency, SourceArtifactInput, CreditRiskEvidenceError,
    CreditRiskEvidenceCollision,
)
from app.services.credit_risk_evidence.service import CreditRiskEvidenceStore
from app.services.credit_risk_evidence.cbr_ratings.contracts import (
    SourceResponse, RepositoryError, ITEM_FIELDS, AGENCIES, BASE,
    is_russian_legal_entity_inn, eligible_issuer_inns,
)
from app.services.credit_risk_evidence.cbr_ratings.client import CbrRatingsClient, search_fields
from app.services.credit_risk_evidence.cbr_ratings.parser import parse_search, envelope, source_date, derive
from test_credit_risk_evidence import _seed_issuer, _rating_input, _artifact_input

NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
INN = "7707083893"
ISIN = "RU000A123456"
UNIVERSE = {"issuer_inns": [INN], "bond_isins": [ISIN]}
EMPTY_SEARCH_INNS = ("0261012138", "0273086494", "0274051582")
EMPTY_SEARCH_BYTES = b'{\n "status": "error", "data": null, "errors": [{"code": 0, "message": "Array"}]\n}'


def row(**updates):
    result = dict.fromkeys(ITEM_FIELDS, "")
    result.update(objectId="123", objectName="Issuer", inn=INN, ratingValue="AAA(RU)",
        kraName="АКРА (АО)", releaseDate="02.06.2026", prediction="NA – не предусмотрен методологией",
        ratingAction="NW – первичное присвоение кредитного рейтинга")
    result.update(updates)
    return result


def json_bytes(data):
    return json.dumps({"status": "success", "data": data, "errors": []}, ensure_ascii=False).encode("utf-8")


def search_bytes(rows=None, *, count=None, number=1):
    rows = [row()] if rows is None else rows
    count = len(rows) if count is None else count
    return json_bytes(dict(itemCount=count, itemFields=list(ITEM_FIELDS), itemList=rows, pageCount=(count+24)//25,
        pageNumber=number, pageSize=25, sortingDirection="ascending", sortingField="objectName"))


def response(action, fields, payload):
    return SourceResponse(action, tuple(sorted((k, str(v)) for k, v in fields.items())), payload, NOW)


def fixture_responses(rows=None, histories=None):
    rows = [row()] if rows is None else rows
    histories = histories or {r["objectId"]: [dict(ratingValue=r["ratingValue"], kraName=r["kraName"], releaseDate=r["releaseDate"]),
        dict(ratingValue="AA(RU)", kraName=r["kraName"], releaseDate="01.01.2025")] for r in rows}
    result = [response("searchRating", search_fields(INN), search_bytes(rows))]
    for obj in sorted(histories, key=int):
        result.append(response("searchObjectHistory", {"objectId": obj}, json_bytes({"title": "History", "table": histories[obj]})))
    return tuple(result)


def empty_search_responses():
    first, empty, last = EMPTY_SEARCH_INNS
    return (
        response("searchRating", search_fields(first), search_bytes([row(inn=first)])),
        response("searchRating", search_fields(empty), EMPTY_SEARCH_BYTES),
        response("searchRating", search_fields(last), search_bytes([row(inn=last, objectId="125")])),
        response("searchObjectHistory", {"objectId": "123"}, json_bytes({"title": "History", "table": []})),
        response("searchObjectHistory", {"objectId": "125"}, json_bytes({"title": "History", "table": []})),
    )


def test_explicit_empty_search_page_raw_bytes_and_strict_generic_envelope():
    page = parse_search(EMPTY_SEARCH_BYTES, action="searchRating")
    assert (page.item_count, page.page_count, page.page_number, page.page_size) == (0, 0, 0, 25)
    assert (page.sorting_field, page.sorting_direction, page.rows) == ("objectName", "ascending", ())
    with pytest.raises(RepositoryError, match="BITRIX_SOURCE_ERROR"):
        envelope(EMPTY_SEARCH_BYTES)
    candidates, counts, retained = derive(empty_search_responses(),
        {"issuer_inns": list(EMPTY_SEARCH_INNS), "bond_isins": []})
    assert counts["issuer_queries_attempted"] == counts["issuer_queries_succeeded"] == 3
    assert counts["issuer_queries_no_results"] == 1
    assert {c.value.source_issuer_inn for c in candidates} == {EMPTY_SEARCH_INNS[0], EMPTY_SEARCH_INNS[2]}
    assert len(candidates) == 2 and retained == {"123", "125"}
    # Ordinary successful zero-row pages are not the explicit error-envelope diagnostic.
    candidates, counts, _ = derive(fixture_responses(rows=[]), UNIVERSE)
    assert not candidates and counts["issuer_queries_no_results"] == 0
    assert counts["issuer_queries_succeeded"] == 1


@pytest.mark.parametrize("changes", [
    {"status": "Error"}, {"status": "success"}, {"data": {}}, {"data": []},
    {"extra": None}, {"errors": []}, {"errors": {}}, {"errors": [None]},
    {"errors": [{"message": "Array"}]}, {"errors": [{"code": 0}]},
    {"errors": [{"code": 0, "message": "Array", "extra": None}]},
    {"errors": [{"code": 0, "message": "Array"}, {"code": 0, "message": "Other"}]},
    *({"errors": [{"code": code, "message": "Array"}]} for code in (1, "0", 0.0, False, None)),
    *({"errors": [{"code": 0, "message": message}]} for message in ("array", " Array ", "Other", None)),
])
def test_empty_search_signature_deviations_fail_closed(changes):
    payload = json.loads(EMPTY_SEARCH_BYTES)
    payload.update(changes)
    with pytest.raises(RepositoryError, match="BITRIX_SOURCE_ERROR"):
        parse_search(json.dumps(payload).encode(), action="searchRating")


@pytest.mark.parametrize("payload, code", [
    (b'{"status":"error","errors":[{"code":0,"message":"Array"}]}', "BITRIX_SOURCE_ERROR"),
    (b'{"status":"error","status":"error","data":null,"errors":[{"code":0,"message":"Array"}]}', "DUPLICATE_JSON_KEY"),
    (b'{"status":"error","data":null,"errors":[{"code":NaN,"message":"Array"}]}', "INVALID_SOURCE_JSON"),
    (b'{"status":"error","data":null,"errors":[{"code":1e999,"message":"Array"}]}', "INVALID_SOURCE_JSON"),
    (b'{"status":"error","data":null,"errors":[{"code":0,"message":"Array"}],"captchaResult":false}', "CAPTCHA_REQUIRED"),
    (b'{"status":"error","data":null,"errors":[{"code":0,"message":"Array"}],"sessid":"secret"}', "SECRET_BEARING_RESPONSE"),
    (b'not json', "INVALID_SOURCE_JSON"),
])
def test_search_specific_path_keeps_json_and_secret_guards(payload, code):
    with pytest.raises(RepositoryError, match=code):
        parse_search(payload, action="searchRating")


@pytest.mark.parametrize("label", list(AGENCIES))
def test_four_agencies_scale_absence_and_richer_current(label):
    candidates, counts, retained = derive(fixture_responses([row(kraName=label)]), UNIVERSE)
    assert len(candidates) == 2 and retained == {"123"}
    current = next(c.value for c in candidates if c.value.event_date == date(2026, 6, 2))
    assert current.agency == AGENCIES[label]
    assert current.rating_scale_raw is None and current.publication_at is None
    assert current.rating_outlook_raw == row()["prediction"]
    assert counts["current_history_duplicates"] == 1


def test_identifiers_object_join_and_unknown_agency_fail_closed():
    rows = [row(), row(objectId="124", isin=ISIN)]
    candidates, _, _ = derive(fixture_responses(rows), UNIVERSE)
    assert {c.value.target.value for c in candidates} == {"BOND", "LEGAL_ISSUER"}
    with pytest.raises(RepositoryError, match="UNKNOWN_RATING_AGENCY"):
        parse_search(search_bytes([row(kraName="acra")]))
    with pytest.raises(RepositoryError, match="FOREIGN_ISSUER_INN"):
        derive(fixture_responses([row(inn="7707654321")]), UNIVERSE)
    with pytest.raises(RepositoryError, match="OBJECT_IDENTITY_COLLISION"):
        derive(fixture_responses([row(), row(isin=ISIN)]), UNIVERSE)
    with pytest.raises(CreditRiskEvidenceError):
        derive(fixture_responses([row(isin="bad")]), UNIVERSE)


def test_withdrawal_date_only_and_exact_raw_strings():
    candidates, _, _ = derive(fixture_responses([row(ratingValue="Рейтинг отозван", ratingAction="WD", releaseDate="02.06.2026 12:30:00")]), UNIVERSE)
    current = next(c.value for c in candidates if c.value.event_date.year == 2026)
    assert current.rating_value_raw == "Рейтинг отозван" and current.rating_action_raw == "WD"
    assert current.publication_date == date(2026, 6, 2) and current.publication_at is None
    with pytest.raises(RepositoryError):
        source_date("31.02.2026")


@pytest.mark.parametrize("payload", [b'{"status":"error","errors":[],"data":{}}',
    b'{"status":"success","status":"success","errors":[],"data":{}}',
    b'{"status":"success","errors":[],"data":{"x":NaN}}'])
def test_false_success_duplicate_and_nonfinite_json(payload):
    with pytest.raises(RepositoryError):
        envelope(payload)


def test_captcha_and_secret_payloads_are_not_frozen():
    with pytest.raises(RepositoryError, match="CAPTCHA_REQUIRED"):
        envelope(json_bytes({"captchaResult": False}))
    with pytest.raises(RepositoryError, match="SECRET_BEARING_RESPONSE"):
        envelope(json_bytes({"sessid": "secret"}))
    bad = json.loads(search_bytes()); bad["data"]["pageSize"] = True
    with pytest.raises(RepositoryError):
        parse_search(json.dumps(bad).encode())


def make_client(handler):
    tick = [0.0]
    def sleep(seconds):
        tick[0] += seconds
    return CbrRatingsClient(client=httpx.Client(transport=httpx.MockTransport(handler)),
        clock=lambda: NOW, monotonic=lambda: tick[0], sleep=sleep), tick


@pytest.mark.parametrize("value, expected", [
    ("7707083893", True), ("7736050003", True), ("0010000025", False),
    ("7707083894", False), ("770708389", False), ("77070838933", False),
    ("770708389X", False), ("７７０７０８３８９３", False), ("770708389٣", False),
    (" 7707083893", False), (7707083893, False), (True, False), (None, False),
])
def test_source_specific_russian_inn_checksum(value, expected):
    assert is_russian_legal_entity_inn(value) is expected
    from app.services.credit_risk_evidence.contracts import canonical_inn
    assert canonical_inn("0010000025") == "0010000025"


@pytest.mark.parametrize("with_eligible", [True, False])
def test_client_queries_only_eligible_full_universe_and_empty_scope(with_eligible):
    universe = {"issuer_inns": sorted(["0010000025"] + ([INN] if with_eligible else [])), "bond_isins": []}
    original = json.loads(json.dumps(universe))
    searches = []
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="'+'a'*32+'">',
                                  headers={"content-type": "text/html"})
        assert request.url.params["action"] == "searchRating"
        searches.append(parse_qs(request.content.decode())["fields[inn]"][0])
        return httpx.Response(200, content=search_bytes([]), headers={"content-type": "application/json"})
    client, _ = make_client(handler)
    try:
        responses = client.collect(universe)
        candidates, counts, retained = derive(responses, universe)
        assert searches == ([INN] if with_eligible else [])
        assert eligible_issuer_inns(reversed(universe["issuer_inns"])) == tuple(searches)
        assert client.requests == 2 + len(searches)
        assert counts["issuer_query_eligible_count"] == len(searches)
        assert counts["issuer_query_ineligible_count"] == 1
        assert counts["issuer_queries_attempted"] == counts["issuer_queries_succeeded"] == len(searches)
        assert not candidates and not retained and universe == original
    finally:
        client.close()


def test_client_continues_after_empty_search_and_preserves_session_payload():
    searches, histories = [], []
    expected = empty_search_responses()
    payloads = {dict(r.fields)["inn"]: r.content for r in expected[:3]}
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="'+'a'*32+'">',
                headers={"content-type": "text/html", "set-cookie": "session=test; Path=/"})
        assert "session=test" in request.headers["cookie"]
        fields = parse_qs(request.content.decode(), keep_blank_values=True)
        assert fields["sessid"] == ["a"*32]
        if request.url.params["action"] == "searchRating":
            inn = fields["fields[inn]"][0]; searches.append(inn)
            payload = payloads[inn]
        else:
            assert request.url.params["action"] == "searchObjectHistory"
            histories.append(fields["fields[objectId]"][0])
            payload = json_bytes({"title": "History", "table": []})
        return httpx.Response(200, content=payload, headers={"content-type": "application/json"})
    client, _ = make_client(handler)
    universe = {"issuer_inns": sorted(["0010000025", *EMPTY_SEARCH_INNS]), "bond_isins": []}
    try:
        responses = client.collect(universe)
        assert tuple(searches) == EMPTY_SEARCH_INNS and histories == ["123", "125"]
        assert client.requests == 7 and responses[1].content == EMPTY_SEARCH_BYTES
        assert all("sessid" not in dict(r.fields) for r in responses)
        candidates, counts, _ = derive(responses, universe)
        assert counts["issuer_queries_attempted"] == counts["issuer_queries_succeeded"] == 3
        assert counts["issuer_queries_no_results"] == 1 and len(candidates) == 2
        assert counts["issuer_query_ineligible_count"] == 1
    finally:
        client.close()


@pytest.mark.parametrize("action, fields", [
    ("searchRatingNavigation", {"pageSize": 25, "pageNumber": 2, "sortingField": "objectName", "sortingDirection": "ascending"}),
    ("searchObjectHistory", {"objectId": "123"}),
])
def test_empty_signature_is_fatal_for_navigation_and_history_client_and_offline(action, fields):
    with pytest.raises(RepositoryError, match="BITRIX_SOURCE_ERROR"):
        if action == "searchRatingNavigation":
            parse_search(EMPTY_SEARCH_BYTES, action=action)
        else:
            envelope(EMPTY_SEARCH_BYTES)
    source = empty_search_responses()
    failing = response(action, fields, EMPTY_SEARCH_BYTES)
    responses = (source[0], failing) if action == "searchRatingNavigation" else (*source[:3], failing)
    with pytest.raises(RepositoryError, match="BITRIX_SOURCE_ERROR"):
        derive(responses, {"issuer_inns": list(EMPTY_SEARCH_INNS), "bond_isins": []})
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="'+'a'*32+'">',
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, content=EMPTY_SEARCH_BYTES, headers={"content-type": "application/json"})
    client, _ = make_client(handler)
    try:
        client.bootstrap()
        with pytest.raises(RepositoryError, match="BITRIX_SOURCE_ERROR"):
            client.action(action, fields)
    finally:
        client.close()


@pytest.mark.parametrize("status, code", [(400, "SOURCE_ACCESS_BLOCKED"), (503, "TRANSIENT_SOURCE_FAILURE")])
def test_empty_signature_never_normalizes_http_failure(status, code):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="'+'a'*32+'">',
                                  headers={"content-type": "text/html"})
        return httpx.Response(status, content=EMPTY_SEARCH_BYTES, headers={"content-type": "application/json"})
    client, _ = make_client(handler)
    try:
        with pytest.raises(RepositoryError, match=code):
            client.collect(UNIVERSE)
    finally:
        client.close()


def test_missing_eligible_and_recorded_ineligible_search_fail_closed():
    assert is_russian_legal_entity_inn("7736050003")
    with pytest.raises(RepositoryError, match="INCOMPLETE_DISCOVERY"):
        derive(fixture_responses(), {"issuer_inns": sorted([INN, "7736050003"]), "bond_isins": [ISIN]})
    with pytest.raises(RepositoryError, match="SEARCH_CONTEXT_CONFLICT"):
        derive((response("searchRating", search_fields("0010000025"), search_bytes([])),),
               {"issuer_inns": ["0010000025", INN], "bond_isins": []})


def test_valid_russian_inn_bitrix_error_is_still_fatal():
    searches = []
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="'+'a'*32+'">',
                                  headers={"content-type": "text/html"})
        searches.append(parse_qs(request.content.decode())["fields[inn]"][0])
        return httpx.Response(200, content=b'{"status":"error","data":null,"errors":[{"code":123,"message":"Internal source failure"}]}',
                              headers={"content-type": "application/json"})
    client, _ = make_client(handler)
    try:
        with pytest.raises(RepositoryError, match="BITRIX_SOURCE_ERROR"):
            client.collect({"issuer_inns": ["0010000025", INN], "bond_isins": []})
        assert searches == [INN] and client.requests == 3
    finally:
        client.close()


def test_wire_session_pagination_and_outside_bond_history_skipped():
    calls = []
    def handler(request):
        calls.append((request, parse_qs(request.content.decode(), keep_blank_values=True)))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="' + 'a'*32 + '">',
                                 headers={"content-type": "text/html", "set-cookie": "session=test; Path=/"})
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        assert "session=test" in request.headers["cookie"]
        fields = calls[-1][1]
        assert fields["sessid"] == ["a"*32]
        action = request.url.params["action"]
        if action == "searchRating":
            assert fields["fields[captchaCode]"] == [""]
            return httpx.Response(200, content=search_bytes([row(isin="RU000A999999")]*25, count=26), headers={"content-type":"application/json"})
        if action == "searchRatingNavigation":
            assert fields["fields[pageNumber]"] == ["2"]
            return httpx.Response(200, content=search_bytes([row(objectId="124")], count=26, number=2), headers={"content-type":"application/json"})
        assert fields["fields[objectId]"] == ["124"]
        return httpx.Response(200, content=json_bytes({"title":"History", "table":[]}), headers={"content-type":"application/json"})
    client, tick = make_client(handler)
    responses = client.collect(UNIVERSE)
    assert [r.action for r in responses] == ["searchRating", "searchRatingNavigation", "searchObjectHistory"]
    assert client.requests == 5 and tick[0] >= 4
    assert all("sessid" not in dict(r.fields) for r in responses)
    client.close(); assert client.sessid is None and not client.client.cookies


def test_captcha_halts_next_request_and_robots_disallow():
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.method == "GET":
            return httpx.Response(200, text='<input name="sessid" value="'+'a'*32+'">', headers={"content-type":"text/html"})
        return httpx.Response(200, content=json_bytes({"captchaResult":False}), headers={"content-type":"application/json"})
    client, _ = make_client(handler)
    with pytest.raises(RepositoryError, match="CAPTCHA_REQUIRED"):
        client.collect(UNIVERSE)
    before = client.requests
    with pytest.raises(RepositoryError, match="SOURCE_RUN_STOPPED"):
        client.action("searchRating", search_fields(INN))
    assert client.requests == before
    blocked, _ = make_client(lambda _: httpx.Response(200, text="User-agent: *\nDisallow: /"))
    with pytest.raises(RepositoryError, match="ROBOTS_DISALLOW"):
        blocked.collect(UNIVERSE)
    assert blocked.requests == 1


def test_transport_redirect_retry_budget_and_tls(monkeypatch):
    client, _ = make_client(lambda _: httpx.Response(302, headers={"location":"https://foreign.invalid/"}))
    with pytest.raises(RepositoryError, match="UNSAFE_SOURCE_URL"):
        client.bootstrap()
    client, _ = make_client(lambda _: httpx.Response(503, headers={"retry-after":"1000"}))
    with pytest.raises(RepositoryError, match="TRANSIENT_SOURCE_FAILURE"):
        client.bootstrap()
    assert client.requests == 3 and client._delay("1000", 0) == 30
    monkeypatch.setattr("app.services.credit_risk_evidence.cbr_ratings.client.MAX_REQUESTS", 1)
    client, _ = make_client(lambda r: httpx.Response(404))
    with pytest.raises(RepositoryError, match="REQUEST_LIMIT"):
        client.bootstrap()
    def tls(request):
        raise httpx.ConnectError("certificate verify failed secret", request=request)
    client, _ = make_client(tls)
    with pytest.raises(RepositoryError, match="SOURCE_TRANSPORT_FAILURE"):
        client.bootstrap()
    assert client.requests == 1


@pytest.mark.parametrize("rating_agency", list(RatingAgency))
def test_cbr_store_reobservation_versioning_and_resolution_collision(db_session, rating_agency):
    issuer = _seed_issuer(db_session)
    store = CreditRiskEvidenceStore(db_session)
    def artifact(content):
        return store.persist_source_artifact(SourceArtifactInput(SourceProvider.CBR_RATINGS,
            SourceKind.RATING_REPOSITORY_RESPONSE, BASE+"/response", "application/json", content, NOW)).row
    first = artifact(b'{"first":1}')
    value = _rating_input(agency=rating_agency, rating_scale_raw=None)
    initial = store.persist_rating_event(first, value)
    new = artifact(b'{"later":2}')
    repeated = store.persist_rating_event(new, value)
    assert initial.inserted and not repeated.inserted and repeated.row.artifact_id == first.id
    assert repeated.row.rating_scale_raw is None
    changed = store.persist_rating_event(new, replace(value, rating_value_raw="BBB(RU)"))
    assert changed.inserted
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(new, replace(value, agency="MOEX"))
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(new, replace(value, agency="CBR_RATINGS"))
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(new, replace(value, rating_scale_raw=""))
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(new, replace(value, rating_scale_raw="inferred national"))
    issuer.resolution_state = "conflict"; db_session.flush()
    with pytest.raises(CreditRiskEvidenceCollision):
        store.persist_rating_event(new, value)


def test_direct_agency_validation_and_cbr_provider_remain_separate(db_session):
    _seed_issuer(db_session)
    store = CreditRiskEvidenceStore(db_session)
    artifact = store.persist_source_artifact(_artifact_input()).row
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(artifact, _rating_input(agency=RatingAgency.EXPERT_RA))
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(artifact, _rating_input(rating_scale_raw=None))
    moex = store.persist_source_artifact(_artifact_input(SourceProvider.MOEX)).row
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(moex, _rating_input())


def test_nonfinite_exponent_and_required_captcha_fail_closed():
    from app.services.credit_risk_evidence.cbr_ratings.parser import envelope
    with pytest.raises(RepositoryError, match="INVALID_SOURCE_JSON"):
        envelope(b'{"status":"success","errors":[],"data":{"extra":1e999}}')
    with pytest.raises(RepositoryError, match="CAPTCHA_REQUIRED"):
        envelope(b'{"status":"success","errors":[],"data":{"captchaRequired":true}}')
