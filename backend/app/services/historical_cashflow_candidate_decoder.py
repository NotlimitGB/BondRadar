"""Bounded pair-preserving offline decoder. No transport or source writes."""
from decimal import Decimal
import hashlib
import json
from app.schemas.historical_cashflow_feasibility import Request, Candidate
from app.schemas.historical_cashflow_qualification import DecodedView, DuplicateException
from app.schemas.historical_cashflow_source_forensics import DuplicateFinding
from app.services.historical_cashflow_diagnostic_client import Pairs
from app.services.historical_cashflow_feasibility_service import map_event
from app.services.historical_identity_feasibility_service import IdentityError, require
from app.services.historical_identity_probe_service import encode
from app.services.historical_evidence_normalization import sha

SECIDS = ("RU000A0JQAM6", "SU24020RMFS8")
TABLES = ("coupons", "amortizations", "redemptions", "offers")


def typed_tree(value):
    """Type tags prevent canonical Decimal strings colliding with source strings."""
    if type(value) is Pairs:
        return ("pairs", tuple((k, typed_tree(v)) for k, v in value.members))
    if type(value) is dict:
        return ("object", tuple((k, typed_tree(v)) for k, v in sorted(value.items())))
    if type(value) is list:
        return ("array", tuple(typed_tree(v) for v in value))
    return (type(value).__name__, value)


def strict_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is Pairs:
        a, b = {}, {}
        for key, value in left.members: a.setdefault(key, []).append(value)
        for key, value in right.members: b.setdefault(key, []).append(value)
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if type(left) is list:
        return len(left) == len(right) and all(strict_equal(a, b) for a, b in zip(left, right))
    return left == right


def table_rows(block):
    require(type(block) is dict, "SOURCE_TABLE_INVALID")
    cols, data = block.get("columns"), block.get("data")
    require(type(cols) is list and 0 < len(cols) <= 1024 and
            all(type(c) is str and c for c in cols) and len({c.upper() for c in cols}) == len(cols) and
            type(data) is list and len(data) <= 100 and
            all(type(r) is list and len(r) == len(cols) for r in data), "SOURCE_TABLE_INVALID")
    require(all(v is None or type(v) in (str, int, bool, Decimal) for r in data for v in r), "SOURCE_CELL_INVALID")
    return cols, tuple(dict(zip(cols, r)) for r in data)


def cursor_values(block, offset):
    _, rows = table_rows(block)
    require(len(rows) == 1, "CURSOR_INVALID")
    row = {k.upper(): v for k, v in rows[0].items()}
    require(set(row) == {"INDEX", "TOTAL", "PAGESIZE"}, "CURSOR_INVALID")
    index, total, size = (row[k] for k in ("INDEX", "TOTAL", "PAGESIZE"))
    require(all(type(v) is int for v in (index, total, size)) and index == offset and
            total >= index >= 0 and 1 <= size <= 100, "CURSOR_INVALID")
    return index, total, size


def decode_candidate(*, body, request, candidate):
    require(type(request) is Request and type(candidate) is Candidate and
            request.secid in SECIDS and request.table in TABLES and type(request.offset) is int and request.offset >= 0 and
            (request.offset == 0 or request.table == "amortizations") and
            candidate.secid == request.secid, "QUALIFICATION_SCOPE_INVALID")
    require(type(body) is bytes and len(body) <= 8 * 1024**2, "SOURCE_BODY_LIMIT")
    base = dict(requested_table=request.table, requested_secid=request.secid,
                accepted_current_isin_context=candidate.accepted_current_isin,
                raw_body_sha256=hashlib.sha256(body).hexdigest())
    try:
        def number(token):
            require(len(token) <= 1024, "JSON_NUMBER_LIMIT")
            result = Decimal(token)
            require(result.is_finite(), "NONFINITE_SOURCE_NUMBER")
            return result
        def integer(token):
            require(len(token) <= 1024, "JSON_NUMBER_LIMIT")
            return int(token)
        root = json.loads(body, parse_float=number, parse_int=integer,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
                          object_pairs_hook=lambda p: Pairs(tuple(p)))
        require(type(root) is Pairs, "SOURCE_OBJECT_REQUIRED")
        duplicates = []; nodes = 0
        def visit(value, depth, path="/"):
            nonlocal nodes
            nodes += 1
            require(nodes <= 100000 and depth <= 64, "JSON_RESOURCE_LIMIT")
            if type(value) is Pairs:
                groups = {}
                counters = {}
                for key, child in value.members:
                    groups.setdefault(key, []).append(child)
                    counters[key] = counters.get(key, 0) + 1
                    display = "".join(c if 32 <= ord(c) < 127 else "?" for c in key)[:128]
                    component = display + "#" + hashlib.sha256(key.encode()).hexdigest()[:12] + "@" + str(counters[key])
                    visit(child, depth + 1, path + component + "/")
                for key, values in groups.items():
                    if len(values) > 1:
                        require(len(duplicates) < 512, "DUPLICATE_DIAGNOSTIC_LIMIT")
                        equal = all(strict_equal(values[0], v) for v in values[1:])
                        finding = DuplicateFinding(key_display="".join(c if 32 <= ord(c) < 127 else "?" for c in key)[:128],
                            key_sha256=hashlib.sha256(key.encode()).hexdigest(), occurrences=len(values), location=path,
                            area="ROOT" if depth == 0 else "OTHER", comparison="STRUCTURALLY_IDENTICAL" if equal else "DIFFERENT",
                            value_sha256s=tuple(sha(typed_tree(v)) for v in values))
                        duplicates.append((key, depth, equal, finding))
                        base["duplicate_findings"] = tuple(d[3] for d in duplicates)
            elif type(value) is list:
                for index, child in enumerate(value):
                    visit(child, depth + 1, path + str(index) + "/")
        visit(root, 0)
        require(all(depth == 0 and key == "coupons.cursor" and finding.occurrences == 2 and equal
                    for key, depth, equal, finding in duplicates), "DUPLICATE_NOT_ALLOWED")
        def ordinary(value):
            if type(value) is Pairs:
                result = {}
                for key, child in value.members:
                    if key in result:
                        require(value is root and key == "coupons.cursor" and duplicates, "DUPLICATE_NOT_ALLOWED")
                        # Explicitly approved identical occurrence, never a fallback.
                        continue
                    result[key] = ordinary(child)
                return result
            if type(value) is list:
                return [ordinary(child) for child in value]
            return value
        payload = ordinary(root)
        require(not any(k != request.table and k.lower() == request.table for k in payload), "AMBIGUOUS_TABLE_NAME")
        if duplicates:
            cursor_values(payload["coupons.cursor"], 0)
        if request.table not in payload:
            return DecodedView(**base, structural_status="REQUESTED_TABLE_MISSING", diagnostics=("REQUESTED_TABLE_MISSING",))
        cols, rows = table_rows(payload[request.table])
        if duplicates and request.table == "coupons":
            index, total, size = cursor_values(payload["coupons.cursor"], request.offset)
            require(len(rows) == min(size, total - index), "CURSOR_INVALID")
        events = tuple(map_event(row, candidate, request.table, request.offset, i, base["raw_body_sha256"]) for i, row in enumerate(rows))
        require(not any(e.source_identity_conflict or any(d.startswith("SOURCE_") and d.endswith("_INVALID")
                    for d in e.diagnostics) for e in events), "SOURCE_IDENTITY_CONFLICT")
        cursor = payload.get(request.table + ".cursor")
        completeness = "COMPLETENESS_UNPROVEN"; index = total = size = None
        if request.table + ".cursor" in payload:
            try:
                index, total, size = cursor_values(cursor, request.offset)
                require(len(rows) == min(size, total - index), "CURSOR_INVALID")
                completeness = "COMPLETE" if index + size >= total else "PAGINATION_REQUIRED"
            except IdentityError:
                completeness = "CURSOR_INVALID"; index = total = size = None
        unit_names = {"UNIT", "UNITS", "AMOUNTUNIT", "AMOUNT_UNIT", "VALUEUNIT"}
        claims = {str(v) for row in rows for k, v in row.items() if k.upper() in unit_names and v is not None and str(v).strip()}
        metadata = payload[request.table].get("metadata")
        money = {"VALUE", "AMOUNT", "COUPONVALUE", "COUPON_VALUE", "AMORTVALUE", "AMORTIZATION_VALUE", "REDEMPTIONVALUE", "REDEMPTION_VALUE"}
        if type(metadata) is dict:
            claims.update(str(v) for k, fields in metadata.items() if k.upper() in money and type(fields) is dict
                          for name, v in fields.items() if name.upper() in unit_names and v is not None and str(v).strip())
        diagnostics = {"MONETARY_UNIT_AUTHORITY_UNPROVEN", "ACTUAL_PAYMENTS_UNPROVEN", "HISTORICAL_PUBLICATION_UNPROVEN"}
        if request.table == "offers": diagnostics.add("OFFER_NOT_AUTOMATIC_REDEMPTION")
        return DecodedView(**base, structural_status="STRUCTURALLY_RECOVERABLE", requested_table_present=True,
            repaired_view_sha256=sha(typed_tree(payload)), duplicate_exception=DuplicateException() if duplicates else None,
            columns=tuple(cols), metadata_json=encode(typed_tree(metadata)).decode() if metadata is not None else None,
            source_rows_json=tuple(encode(typed_tree(row)).decode() for row in rows),
            source_row_sha256s=tuple(sha(typed_tree(row)) for row in rows),
            cursor_json=encode(typed_tree(cursor)).decode() if cursor is not None else None,
            cursor_index=index, cursor_total=total, cursor_page_size=size, completeness=completeness,
            unit_claims=tuple(sorted(claims)), unit_status="CONFLICTING" if len(claims) > 1 else "PARTIAL" if claims else "UNPROVEN",
            events=events, could_become_economic_after_qualification=tuple(
                e.status == "SCHEDULED_OBSERVATION" and request.table != "offers" for e in events),
            diagnostics=tuple(sorted(diagnostics)))
    except (IdentityError, ValueError, TypeError, RecursionError, UnicodeError, ArithmeticError) as exc:
        return DecodedView(**base, structural_status="STRUCTURALLY_REJECTED",
                           diagnostics=(exc.code if isinstance(exc, IdentityError) else "SOURCE_JSON_INVALID",))
