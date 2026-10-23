"""Pure independent positional-to-keyed comparison after frozen R2 replay."""
from decimal import Decimal, Context, localcontext, ROUND_HALF_EVEN
import hashlib
import json
from app.schemas.historical_cashflow_qualification import QualifiedObservation
from app.schemas.historical_cashflow_semantics import RowIntegrityFinding
from app.services.historical_cashflow_diagnostic_client import Pairs
from app.services.historical_cashflow_candidate_decoder import typed_tree, table_rows
from app.services.historical_identity_feasibility_service import require
from app.services.historical_evidence_normalization import sha


def decode_typed_object(serialized):
    require(type(serialized) is str and len(serialized) <= 8 * 1024**2, "TYPED_ROW_LIMIT")
    tree = json.loads(serialized, parse_float=Decimal, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    require(type(tree) is list and len(tree) == 2 and tree[0] == "object" and type(tree[1]) is list and
            len(tree[1]) <= 1024, "TYPED_OBJECT_REQUIRED")
    result = {}
    for pair in tree[1]:
        require(type(pair) is list and len(pair) == 2 and type(pair[0]) is str and pair[0] not in result, "TYPED_FIELD_INVALID")
        key, node = pair
        require(type(node) is list and len(node) == 2 and type(node[0]) is str, "TYPED_VALUE_INVALID")
        tag, value = node
        if tag == "Decimal":
            require(type(value) is str and len(value) <= 1024, "TYPED_DECIMAL_INVALID")
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                try:
                    value = Decimal(value)
                except ArithmeticError:
                    require(False, "TYPED_DECIMAL_INVALID")
            require(value.is_finite(), "TYPED_DECIMAL_INVALID")
        else:
            types = {"str": str, "int": int, "bool": bool, "NoneType": type(None)}
            require(tag in types and type(value) is types[tag], "TYPED_VALUE_INVALID")
        result[key] = value
    return result


def exact_row_equal(left, right):
    if left.keys() != right.keys(): return False
    for key, value in left.items():
        other = right[key]
        if type(value) is not type(other): return False
        if type(value) is Decimal:
            if value.as_tuple() != other.as_tuple(): return False
        elif value != other: return False
    return True


def verify_retained_rows(*, body, observation):
    """Returns one finding and transient keyed rows; never changes R2 serialization."""
    require(type(observation) is QualifiedObservation and type(body) is bytes and len(body) <= 8 * 1024**2,
            "ROW_INTEGRITY_INPUT_INVALID")
    view = observation.view
    require(hashlib.sha256(body).hexdigest() == view.raw_body_sha256, "RAW_BODY_SHA_MISMATCH")
    base = dict(request_id=observation.request_id, secid=observation.requested_secid, table=observation.table,
                raw_body_sha256=view.raw_body_sha256, structural_status=view.structural_status)
    if view.structural_status == "STRUCTURALLY_REJECTED":
        require(not view.source_rows_json and not view.source_row_sha256s and not view.events, "REJECTED_RETAINED_ROWS")
        return RowIntegrityFinding(**base, retained_rows_checked=0, typed_row_sha256s=(), legacy_event_row_sha256s=()), ()
    def number(token):
        require(len(token) <= 1024, "SOURCE_NUMBER_LIMIT")
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            value = Decimal(token)
        require(value.is_finite(), "SOURCE_NUMBER_INVALID")
        return value
    def integer(token):
        require(len(token) <= 1024, "SOURCE_NUMBER_LIMIT")
        return int(token)
    root = json.loads(body, parse_float=number, parse_int=integer, object_pairs_hook=lambda p: Pairs(tuple(p)),
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    require(type(root) is Pairs, "SOURCE_OBJECT_REQUIRED")
    blocks = [v for key, v in root.members if key == observation.table]
    if view.structural_status == "REQUESTED_TABLE_MISSING":
        require(not blocks and not view.source_rows_json and not view.events and not view.source_row_sha256s, "MISSING_TABLE_MISMATCH")
        return RowIntegrityFinding(**base, retained_rows_checked=0, typed_row_sha256s=(), legacy_event_row_sha256s=()), ()
    require(len(blocks) == 1, "REQUESTED_BLOCK_COUNT_MISMATCH")
    nodes = 0
    def ordinary(value, depth=0):
        nonlocal nodes
        nodes += 1
        require(nodes <= 100000 and depth <= 64, "SOURCE_ROW_RESOURCE_LIMIT")
        if type(value) is Pairs:
            result = {}
            for key, child in value.members:
                require(key not in result, "SOURCE_BLOCK_DUPLICATE")
                result[key] = ordinary(child, depth + 1)
            return result
        if type(value) is list: return [ordinary(v, depth + 1) for v in value]
        return value
    columns, rows = table_rows(ordinary(blocks[0]))
    require(tuple(columns) == view.columns, "SOURCE_COLUMNS_MISMATCH")
    require(len(rows) == len(view.source_rows_json) == len(view.source_row_sha256s) == len(view.events), "SOURCE_ROW_COUNT_MISMATCH")
    typed_hashes = []; legacy_hashes = []
    for index, row in enumerate(rows):
        decoded = decode_typed_object(view.source_rows_json[index])
        require(exact_row_equal(row, decoded), "SOURCE_TYPED_ROW_MISMATCH")
        typed_digest, legacy_digest = sha(typed_tree(row)), sha(row)
        require(typed_digest == view.source_row_sha256s[index] == sha(typed_tree(decoded)), "SOURCE_ROW_SHA_MISMATCH")
        event = view.events[index]
        require(event.row_index == index and event.row_sha256 == legacy_digest and event.page_sha256 == view.raw_body_sha256,
                "LEGACY_EVENT_ROW_SHA_MISMATCH")
        typed_hashes.append(typed_digest); legacy_hashes.append(legacy_digest)
    return RowIntegrityFinding(**base, retained_rows_checked=len(rows), typed_row_sha256s=tuple(typed_hashes),
                               legacy_event_row_sha256s=tuple(legacy_hashes)), rows
