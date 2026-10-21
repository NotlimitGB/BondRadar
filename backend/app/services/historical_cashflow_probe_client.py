"""Fixed-route, one-attempt bondization adapter; no legacy pagination/defaults."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
import httpx
from app.schemas.historical_cashflow_feasibility import Request, TABLES
from app.schemas.historical_evidence_foundation import HistoricalSourceQuery
from app.services.historical_evidence_source_client import HistoricalEvidenceSourceClient
from app.services.historical_identity_feasibility_service import require, IdentityError
from app.services.historical_identity_probe_client import SourceFailure, TRANSIENT_HTTP
from app.services.historical_evidence_normalization import safe_json, sha


def route(request):
    require(type(request) is Request, "REQUEST_INVALID")
    Request.model_validate(request)
    require(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", request.secid) is not None and request.table in TABLES, "REQUEST_INVALID")
    return HistoricalEvidenceSourceClient._route(HistoricalSourceQuery(
        family="CASHFLOWS", table=request.table, secid=request.secid, offset=request.offset))


@dataclass(frozen=True)
class Page:
    rows: tuple
    complete: bool
    next_offset: int | None
    total: int | None
    size: int | None
    diagnostic: str
    content_sha256: str


def parse_page(body, request):
    route(request)
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_SOURCE_KEY")
            result[key] = value
        return result
    try:
        payload = json.loads(body, parse_float=Decimal, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        safe_json(payload)
        require(type(payload) is dict, "SOURCE_OBJECT_REQUIRED")
        def table(name):
            block = payload.get(name)
            require(type(block) is dict, "SOURCE_TABLE_MISSING")
            cols, rows = block.get("columns"), block.get("data")
            require(type(cols) is list and 0 < len(cols) <= 1024 and
                all(type(c) is str and c for c in cols) and len({c.upper() for c in cols}) == len(cols)
                and type(rows) is list and all(type(r) is list and len(r) == len(cols) for r in rows), "SOURCE_TABLE_INVALID")
            result = tuple(dict(zip(cols, row)) for row in rows)
            require(all(v is None or type(v) in (str, int, bool, Decimal) for r in result for v in r.values()), "SOURCE_CELL_INVALID")
            return result
        rows = table(request.table)
        require(len(rows) <= 100, "SOURCE_PAGE_TOO_LARGE")
        content = sha(tuple(sorted(sha(row) for row in rows)))
        if request.table + ".cursor" not in payload:
            return Page(rows, False, None, None, None, "COMPLETENESS_UNPROVEN", content)
        try:
            cursor = table(request.table + ".cursor")
            require(len(cursor) == 1, "SOURCE_CURSOR_INVALID")
            c = {k.upper(): v for k, v in cursor[0].items()}
            index, total, size = (c.get(k) for k in ("INDEX", "TOTAL", "PAGESIZE"))
            require(all(type(v) is int for v in (index, total, size)) and index == request.offset and total >= index
                and 1 <= size <= 100 and len(rows) == min(size, total-index), "SOURCE_CURSOR_INVALID")
        except IdentityError:
            # Preserve every schema-valid economic row even when completion proof fails.
            return Page(rows,False,None,None,None,"SOURCE_CURSOR_INVALID",content)
        complete = index + size >= total
        return Page(rows, complete, None if complete else index+size, total, size,
            "SOURCE_TABLE_EMPTY_COMPLETE" if total == 0 else "CURSOR_COMPLETE" if complete else "CURSOR_CONTINUES", content)
    except IdentityError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise IdentityError("SOURCE_JSON_OR_TABLE_INVALID") from None


class HistoricalCashflowProbeClient:
    def __init__(self, *, transport=None):
        self.http = httpx.Client(transport=transport, verify=True, trust_env=False, follow_redirects=False, timeout=30)

    def close(self):
        self.http.close()

    def fetch(self, request, *, response_limit, bytes_remaining, seconds_remaining, check_time):
        url, params = route(request)
        body = bytearray()
        try:
            check_time()
            with self.http.stream("GET", url, params=params, headers={"Accept-Encoding": "identity"},
                timeout=min(30, seconds_remaining), follow_redirects=False) as response:
                if response.status_code != 200:
                    try:
                        delay = min(60, max(0, int(response.headers.get("Retry-After", "0"))))
                    except ValueError:
                        delay = 0
                    retry = response.status_code in TRANSIENT_HTTP
                    raise SourceFailure("TRANSIENT_HTTP" if retry else "HTTP_REJECTED", retryable=retry, retry_after=delay)
                if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    raise SourceFailure("CONTENT_ENCODING_REJECTED")
                cap = min(response_limit, bytes_remaining)
                size = min(4096, cap)
                require(size > 0, "RESPONSE_BYTE_BUDGET")
                blocks = iter(response.iter_bytes(chunk_size=size))
                while True:
                    check_time()
                    if len(body)+size > cap:
                        raise SourceFailure("RESPONSE_BYTE_BUDGET", received_bytes=len(body))
                    try:
                        block = next(blocks)
                    except StopIteration:
                        break
                    body.extend(block)
                    if len(block) < size:
                        break
            check_time()
            return bytes(body)
        except SourceFailure:
            raise
        except IdentityError as exc:
            raise SourceFailure(exc.code, received_bytes=len(body)) from None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
            raise SourceFailure("TRANSIENT_TRANSPORT", retryable=True, received_bytes=len(body)) from None
        except Exception:
            raise SourceFailure("TRANSPORT_REJECTED", received_bytes=len(body)) from None
