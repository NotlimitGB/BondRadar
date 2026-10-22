"""Strict v2 exception matrix; all response bytes synthetic."""
from decimal import Decimal, getcontext
import hashlib
import json
import pytest
from app.schemas.historical_cashflow_feasibility import Request
from app.services.historical_cashflow_candidate_decoder import decode_candidate
from app.services.historical_cashflow_probe_client import parse_page
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_cashflow_feasibility import candidate

SID = "SU24020RMFS8"
ISIN = "RU000A100QS2"
CURSOR = '{"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[0,1,100]]}'
BLOCK = '{"columns":["SECID","ISIN","DATE","VALUE","CURRENCY"],"data":[["SU24020RMFS8",null,"2026-01-01",2.123456789,"RUB"]]}'


def decode(body, table="coupons"):
    return decode_candidate(body=body.encode() if type(body) is str else body,
        request=Request(secid=SID, table=table, offset=0), candidate=candidate(SID, ISIN))


def response(table="coupons", cursor=None, duplicate=False, block=BLOCK):
    result = '{"' + table + '":' + block
    if cursor is not None:
        result += ',"coupons.cursor":' + cursor
        if duplicate: result += ',"coupons.cursor":' + cursor
    return result + '}'


def test_narrow_exception_preserves_raw_and_decimal_and_v1_refuses():
    body = response(cursor=CURSOR, duplicate=True)
    before = getcontext().copy()
    result = decode(body)
    assert result.structural_status == "STRUCTURALLY_RECOVERABLE"
    assert result.completeness == "COMPLETE" and result.duplicate_exception is not None
    assert result.raw_body_sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert result.repaired_view_sha256 != result.raw_body_sha256
    e = result.events[0]
    assert e.amount == Decimal("2.123456789") and "STORAGE_UNREPRESENTABLE" in e.diagnostics
    assert e.returned_source_isin is None and e.accepted_current_isin_context == ISIN
    assert result.unit_status == "UNPROVEN" and not result.actual_payment_confirmed
    assert result.could_become_economic_after_qualification == (False,)
    assert str(getcontext()) == str(before)
    assert decode(body) == result
    with pytest.raises(IdentityError): parse_page(body.encode(), Request(secid=SID, table="coupons", offset=0))
    with pytest.raises(ValueError): result.completeness = "COMPLETE"
    with pytest.raises(ValueError): type(result).model_validate(dict(result, extra=True))
    assert type(result).model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("body", [
    response(cursor=CURSOR, duplicate=True)[:-1] + ',"coupons.cursor":' + CURSOR + '}',
    response(cursor=CURSOR)[:-1] + ',"coupons.cursor":' + CURSOR.replace('0,1,100','0,2,100') + '}',
    response(cursor=CURSOR)[:-1] + ',"coupons.cursor":' + CURSOR.replace('0,1,100','false,1,100') + '}',
    response(cursor=CURSOR)[:-1] + ',"coupons.cursor":' + CURSOR.replace('0,1,100','0.0,1,100') + '}',
    response()[:-1] + ',"other":1,"other":1}',
    response(block=BLOCK[:-1] + ',"x":{"coupons.cursor":1,"coupons.cursor":1}}'),
    response(block=BLOCK.replace('"data":', '"data":[],"data":')),
    response(block=BLOCK.replace('2.123456789', '{"VALUE":1,"VALUE":1}')),
    response()[:-1] + ',"coupons":' + BLOCK + '}',
    response(cursor='{"columns":["INDEX"],"data":[[0]]}', duplicate=True),
    response(cursor=CURSOR.replace('0,1,100', '0,2,100'), duplicate=True),
    response(block=BLOCK.replace(SID, "OTHER")),
    response(block=BLOCK.replace('null', '"OTHERISIN"')),
    response(block=BLOCK.replace('"ISIN"', '"SECID"')),
    response(block=BLOCK.replace('2.123456789', 'NaN')),
    response()[:-1] + ',"COUPONS":' + BLOCK + '}',
    '[' * 70 + '0' + ']' * 70,
])
def test_unsafe_duplicates_identity_shapes_and_resources_fail_closed(body):
    result = decode(body)
    assert result.structural_status == "STRUCTURALLY_REJECTED"
    assert not result.events and result.repaired_view_sha256 is None


@pytest.mark.parametrize("table,cursor,expected", [
    ("coupons", None, "COMPLETENESS_UNPROVEN"),
    ("coupons", CURSOR, "COMPLETE"),
    ("coupons", CURSOR.replace('0,1,100','0,101,1'), "PAGINATION_REQUIRED"),
    ("coupons", CURSOR.replace('0,1,100','1,1,100'), "CURSOR_INVALID"),
    ("coupons", CURSOR.replace('0,1,100','0,2,100'), "CURSOR_INVALID"),
    ("redemptions", CURSOR, "COMPLETENESS_UNPROVEN"),
    ("offers", None, "COMPLETENESS_UNPROVEN"),
])
def test_table_specific_cursor_and_first_page_only(table, cursor, expected):
    result = decode(response(table, cursor), table)
    assert result.completeness == expected
    assert len(result.events) == 1
    assert not result.historical_backtest_ready and not result.pit_ready


def test_redemption_duplicate_coupon_cursor_and_empty_tables():
    result = decode(response("redemptions", CURSOR, True), "redemptions")
    assert result.duplicate_exception and result.requested_table == "redemptions"
    assert result.completeness == "COMPLETENESS_UNPROVEN"
    for table in ("coupons", "offers"):
        empty = decode(response(table, block=BLOCK.replace('[["SU24020RMFS8",null,"2026-01-01",2.123456789,"RUB"]]', '[]')), table)
        assert not empty.events and empty.completeness == "COMPLETENESS_UNPROVEN"
    assert decode('{"redemptions":' + BLOCK + '}').structural_status == "REQUESTED_TABLE_MISSING"


def test_amount_context_no_offer_payment_and_unit_claims():
    body = json.dumps({"offers": {"columns": ["SECID", "DATE", "PRICE", "VALUEPRC", "FACEVALUE", "CURRENCY", "UNIT"],
        "data": [[SID, "2026-01-01", "101", "2", "1000", "RUB", "per bond"]]}})
    result = decode(body, "offers")
    assert result.unit_status == "PARTIAL" and result.events[0].amount is None
    assert "OFFER_NOT_AUTOMATIC_REDEMPTION" in result.diagnostics
    assert not result.total_return_execution_ready and not result.historical_publication_proven
    assert "PRICE" in result.source_rows_json[0] and "FACEVALUE" in result.source_rows_json[0]


def test_strict_equality_objects_can_reorder_but_array_or_type_changes_fail():
    a = CURSOR
    b = '{"data":[[0,1,100]],"columns":["INDEX","TOTAL","PAGESIZE"]}'
    assert decode(response(cursor=a)[:-1] + ',"coupons.cursor":' + b + '}').duplicate_exception
    body = response(cursor=a)[:-1] + ',"coupons.cursor":' + a.replace('0,1,100', '1,0,100') + '}'
    assert decode(body).structural_status == "STRUCTURALLY_REJECTED"


def test_rejected_duplicate_findings_preserve_all_locations_and_types():
    body = response()[:-1] + ',"x":1,"x":true,"nested":{"v":2,"v":2}}'
    result = decode(body)
    assert result.structural_status == "STRUCTURALLY_REJECTED"
    assert len(result.duplicate_findings) == 2
    assert {f.comparison for f in result.duplicate_findings} == {"DIFFERENT", "STRUCTURALLY_IDENTICAL"}
    assert {f.key_display for f in result.duplicate_findings} == {"x", "v"}
    assert all(f.location and len(f.key_sha256) == 64 for f in result.duplicate_findings)


@pytest.mark.parametrize("amount", ["true", "-1", '"malformed"', '"NaN"', '"Infinity"', "null"])
def test_invalid_economics_remain_diagnostic_without_structural_promotion(amount):
    result = decode(response(block=BLOCK.replace('2.123456789', amount)))
    assert result.structural_status == "STRUCTURALLY_RECOVERABLE"
    assert result.events[0].status == "EVENT_INVALID"
    assert result.could_become_economic_after_qualification == (False,)
    assert result.unit_status == "UNPROVEN"
