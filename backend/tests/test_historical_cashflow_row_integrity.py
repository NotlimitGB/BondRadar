"""Independent source-row verification, including typed-object representation."""
from datetime import datetime, timezone
from decimal import Decimal, getcontext
import json
import pytest
from app.schemas.historical_cashflow_feasibility import Request
from app.schemas.historical_cashflow_qualification import QualifiedObservation
from app.services.historical_cashflow_candidate_decoder import decode_candidate
from app.services.historical_cashflow_row_integrity import decode_typed_object, verify_retained_rows
from app.services.historical_identity_feasibility_service import IdentityError
from app.services.historical_identity_probe_service import encode
from app.services.historical_evidence_normalization import sha
from test_historical_cashflow_feasibility import candidate

SID = "SU24020RMFS8"
ISIN = "RU000A100QS2"


def observation(body, *, secid=SID, table="coupons", profile="BASELINE", stamp=None):
    view = decode_candidate(body=body, request=Request(secid=secid, table=table, offset=0),
                            candidate=candidate(secid, ISIN if secid == SID else "RU000A0JQAM6"))
    return QualifiedObservation(requested_secid=secid, table=table, profile=profile,
        request_id=sha((view.raw_body_sha256, profile, secid, table)),
        observed_at=stamp or datetime(2026, 1, 1, tzinfo=timezone.utc), original_disposition="ACCEPTED", view=view)


BODY = b'{"coupons":{"columns":["z","DATE","VALUE","a","nil","boolean","integer"],"data":[["s","2026-01-01",1.2300,"1.2300",null,true,1],["t","2026-02-01",0,"0",null,false,2]]}}'


def test_all_rows_keyed_types_hash_domains_and_decimal_context():
    obs = observation(BODY)
    before = str(getcontext())
    finding, rows = verify_retained_rows(body=BODY, observation=obs)
    assert finding.status == "PASS" and finding.retained_rows_checked == 2
    assert list(rows[0]) != list(decode_typed_object(obs.view.source_rows_json[0]))
    assert rows[0]["VALUE"].as_tuple() == Decimal("1.2300").as_tuple()
    assert type(rows[0]["a"]) is str and type(rows[0]["boolean"]) is bool and rows[0]["nil"] is None
    assert finding.typed_row_sha256s != finding.legacy_event_row_sha256s
    assert str(getcontext()) == before
    assert type(finding).model_validate_json(finding.model_dump_json()) == finding


@pytest.mark.parametrize("change", ["amount", "string", "bool", "null", "sha", "order", "count", "legacy", "columns", "body"])
def test_actual_mismatch_any_row_is_rejected(change):
    obs = observation(BODY); body = BODY
    rows = list(obs.view.source_rows_json)
    if change in ("amount", "string", "bool", "null"):
        tree = json.loads(rows[1]); fields = dict(tree[1])
        key, replacement = {"amount": ("VALUE", ["int", 9]), "string": ("a", ["Decimal", "0"]),
                            "bool": ("boolean", ["int", 0]), "null": ("nil", ["str", ""])}[change]
        fields[key] = replacement; tree[1] = list(fields.items()); rows[1] = json.dumps(tree)
        obs = obs.model_copy(update={"view": obs.view.model_copy(update={"source_rows_json": tuple(rows)})})
    elif change == "sha": obs = obs.model_copy(update={"view": obs.view.model_copy(update={"source_row_sha256s": ("0" * 64,) * 2})})
    elif change == "order": obs = obs.model_copy(update={"view": obs.view.model_copy(update={"source_rows_json": tuple(reversed(rows))})})
    elif change == "count": obs = obs.model_copy(update={"view": obs.view.model_copy(update={"source_rows_json": tuple(rows[:1])})})
    elif change == "legacy":
        events = list(obs.view.events); events[1] = events[1].model_copy(update={"row_sha256": "0" * 64})
        obs = obs.model_copy(update={"view": obs.view.model_copy(update={"events": tuple(events)})})
    elif change == "columns": obs = obs.model_copy(update={"view": obs.view.model_copy(update={"columns": tuple(reversed(obs.view.columns))})})
    else: body = BODY + b" "
    with pytest.raises((IdentityError, ValueError)): verify_retained_rows(body=body, observation=obs)


@pytest.mark.parametrize("tree", [
    ["array", []], ["object", [["v", ["int", True]]]], ["object", [["v", ["bool", 1]]]],
    ["object", [["v", ["Decimal", 1]]]], ["object", [["v", ["Decimal", "Infinity"]]]],
    ["object", [["v", ["Decimal", "malformed"]]]],
    ["object", [["v", ["float", 1]]]], ["object", [["v", ["NoneType", "null"]]]],
    ["object", [["v", ["str", "x"]], ["v", ["str", "x"]]]],
])
def test_typed_tree_rejects_ambiguous_or_coerced_types(tree):
    with pytest.raises((IdentityError, ValueError)): decode_typed_object(json.dumps(tree))


def test_missing_table_and_allowed_root_duplicate_are_not_row_corruption():
    cursor = b'{"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[0,2,100]]}'
    body = BODY[:-1] + b',"coupons.cursor":' + cursor + b',"coupons.cursor":' + cursor + b'}'
    finding, rows = verify_retained_rows(body=body, observation=observation(body))
    assert finding.retained_rows_checked == len(rows) == 2
    missing = observation(body, table="redemptions")
    assert missing.view.structural_status == "REQUESTED_TABLE_MISSING"
    finding, rows = verify_retained_rows(body=body, observation=missing)
    assert finding.status == "PASS" and not rows


def test_source_identity_mismatch_remains_rejected_not_maturity():
    body = b'{"amortizations":{"columns":["SECID","ISIN","amortdate","value","data_source"],"data":[["OTHER","OTHER","2026-01-01",1000,"maturity"]]}}'
    obs = observation(body, table="amortizations")
    assert obs.view.structural_status == "STRUCTURALLY_REJECTED" and "SOURCE_IDENTITY_CONFLICT" in obs.view.diagnostics
    finding, rows = verify_retained_rows(body=body, observation=obs)
    assert finding.status == "PASS" and finding.retained_rows_checked == 0 and not rows


def test_invalid_decimal_tag_does_not_mutate_caller_context():
    before = str(getcontext())
    with pytest.raises(IdentityError): decode_typed_object('["object",[["value",["Decimal","malformed"]]]]')
    assert str(getcontext()) == before
