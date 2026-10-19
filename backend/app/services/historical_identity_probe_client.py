"""Task308A fixed-route, one-attempt HTTP transport and offline table parser."""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re
import httpx
from app.schemas.historical_identity_feasibility import ProbeRequest
from app.services.historical_identity_feasibility_service import IdentityError, require
from app.services.historical_evidence_normalization import safe_json


TRANSIENT_HTTP = {408,429,500,502,503,504}


class SourceFailure(Exception):
    def __init__(self, category, *, retryable=False, received_bytes=0, response_sha256=None, retry_after=0):
        self.category,self.retryable=category,retryable
        self.received_bytes,self.response_sha256=received_bytes,response_sha256
        self.retry_after=retry_after
        super().__init__(category)


@dataclass(frozen=True)
class ParsedPage:
    rows: tuple
    complete: bool
    next_offset: int | None
    cursor_total: int | None
    cursor_page_size: int | None
    completion_basis: str


def request_for(secid, source, offset=0):
    require(type(secid) is str and re.fullmatch(r"[A-Za-z0-9._-]{1,64}",secid) is not None,"SECID_NOT_REQUESTABLE")
    require(source in ("REFERENCE","DESCRIPTION") and type(offset) is int and offset>=0,"PROBE_REQUEST_INVALID")
    if source=="REFERENCE":
        path="/iss/securities.json"
        params={"q":secid,"start":str(offset),"limit":"100","iss.meta":"on","iss.only":"securities,securities.cursor"}
    else:
        require(offset==0,"DESCRIPTION_OFFSET_INVALID")
        path=f"/iss/securities/{secid}.json"
        params={"iss.meta":"on","iss.only":"description"}
    return ProbeRequest(source=source,secid=secid,offset=offset,path=path,params=tuple(sorted(params.items())))


def parse_page(body, request):
    require(request==request_for(request.secid,request.source,request.offset),"PROBE_REQUEST_INVALID")
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError("DUPLICATE_SOURCE_KEY")
            result[key]=value
        return result
    try:
        payload=json.loads(body,parse_float=Decimal,object_pairs_hook=pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        safe_json(payload)
        require(type(payload) is dict,"SOURCE_OBJECT_REQUIRED")
        def table(name):
            block=payload.get(name)
            require(type(block) is dict,"SOURCE_TABLE_MISSING")
            columns,data=block.get("columns"),block.get("data")
            require(type(columns) is list and len(columns)<=1024 and all(type(c) is str for c in columns) and
                len({c.upper() for c in columns})==len(columns) and type(data) is list,"SOURCE_TABLE_INVALID")
            require(all(type(r) is list and len(r)==len(columns) for r in data),"SOURCE_TABLE_INVALID")
            rows=tuple(dict(zip(columns,r)) for r in data)
            require(all(v is None or type(v) in (str,int,bool,Decimal) for r in rows for v in r.values()),"SOURCE_CELL_INVALID")
            safe_json(rows)
            return rows
        if request.source=="DESCRIPTION":
            require("description.cursor" not in payload,"DESCRIPTION_CURSOR_UNEXPECTED")
            fields={}
            for row in table("description"):
                lowered={k.lower():v for k,v in row.items()}
                require("name" in lowered and "value" in lowered and type(lowered["name"]) is str,"DESCRIPTION_FIELDS_INVALID")
                key=lowered["name"].upper()
                require(key not in fields,"DESCRIPTION_DUPLICATE_FIELD")
                fields[key]=lowered["value"]
            safe_json(fields)
            return ParsedPage((fields,) if fields else (),True,None,None,None,"SINGLE_SPECIFICATION")
        rows=table("securities")
        require(len(rows)<=100,"REFERENCE_PAGE_TOO_LARGE")
        if "securities.cursor" in payload:
            cursor=table("securities.cursor")
            require(len(cursor)==1,"SOURCE_CURSOR_INVALID")
            values={k.upper():v for k,v in cursor[0].items()}
            index,total,size=(values.get(k) for k in ("INDEX","TOTAL","PAGESIZE"))
            require(all(type(v) is int for v in (index,total,size)) and index==request.offset and total>=index and 1<=size<=100 and
                len(rows)==min(size,total-index),"SOURCE_CURSOR_INVALID")
            nxt=None if index+size>=total else index+size
            return ParsedPage(rows,nxt is None,nxt,total,size,"CURSOR")
        return ParsedPage(rows,not rows,request.offset+len(rows) if rows else None,None,None,"EMPTY_TERMINATOR")
    except IdentityError:raise
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        raise IdentityError("SOURCE_JSON_OR_TABLE_INVALID") from None


class HistoricalIdentityProbeClient:
    def __init__(self, *, transport=None):
        self.http=httpx.Client(transport=transport,verify=True,trust_env=False,follow_redirects=False,timeout=30)

    def close(self):self.http.close()

    def fetch(self, request, *, response_limit, bytes_remaining, seconds_remaining, check_time):
        require(request==request_for(request.secid,request.source,request.offset),"PROBE_REQUEST_INVALID")
        body=bytearray()
        try:
            with self.http.stream("GET","https://iss.moex.com"+request.path,params=dict(request.params),
                headers={"Accept-Encoding":"identity"},timeout=min(30,seconds_remaining),follow_redirects=False) as response:
                if response.status_code!=200:
                    retry=response.status_code in TRANSIENT_HTTP
                    try:delay=min(60,max(0,int(response.headers.get("Retry-After","0"))))
                    except ValueError:delay=0
                    raise SourceFailure("TRANSIENT_HTTP" if retry else "HTTP_REJECTED",retryable=retry,retry_after=delay)
                if response.headers.get("Content-Encoding","identity").lower()!="identity":
                    raise SourceFailure("CONTENT_ENCODING_REJECTED")
                cap=min(response_limit,bytes_remaining)
                size=min(4096,cap)
                require(size>0,"RESPONSE_BYTE_BUDGET")
                blocks=iter(response.iter_bytes(chunk_size=size))
                while True:
                    check_time()
                    # Refuse before pulling a chunk that could cross the decoded-byte budget.
                    # At an exact full-chunk boundary, completeness is deliberately unproven.
                    if len(body)+size>cap:
                        raise SourceFailure("RESPONSE_BYTE_BUDGET",received_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest())
                    try:block=next(blocks)
                    except StopIteration:break
                    body.extend(block)
                    if len(block)<size:break  # HTTPX emits a short final chunk only at EOF.
            check_time()
            return bytes(body)
        except SourceFailure:raise
        except IdentityError as exc:
            raise SourceFailure(exc.code,received_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest()) from None
        except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError):
            raise SourceFailure("TRANSIENT_TRANSPORT",retryable=True,received_bytes=len(body)) from None
        except Exception:
            raise SourceFailure("TRANSPORT_REJECTED",received_bytes=len(body)) from None
