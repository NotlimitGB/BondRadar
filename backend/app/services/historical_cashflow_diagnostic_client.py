"""Lossless JSON forensics and a fixed-route, one-attempt diagnostic transport."""
from dataclasses import dataclass
from decimal import Decimal, Context, localcontext, ROUND_HALF_EVEN
import hashlib
import json
import httpx
from app.schemas.historical_cashflow_feasibility import Request
from app.schemas.historical_cashflow_source_forensics import (
    DiagnosticRequest, DuplicateFinding, Inspection, TableFinding,
)
from app.services.historical_cashflow_probe_client import route as b2_route, parse_page
from app.services.historical_identity_probe_client import SourceFailure, TRANSIENT_HTTP
from app.services.historical_identity_feasibility_service import require, IdentityError
from app.services.historical_identity_probe_service import encode
from app.services.historical_evidence_normalization import sha, safe_json


@dataclass(frozen=True)
class Pairs:
    members: tuple


def route(request):
    require(type(request) is DiagnosticRequest,"DIAGNOSTIC_REQUEST_REQUIRED")
    DiagnosticRequest.model_validate(request)
    require(type(request.offset) is int and request.offset==0,"OFFSET_NOT_AUTHORIZED")
    url,params=b2_route(Request(secid=request.secid,table=request.table,offset=0))
    if request.profile=="TABLE_ONLY":params["iss.only"]=request.table
    return url,params


def _display(key):
    return "".join(c if 32<=ord(c)<127 else "?" for c in key)[:128]


def _tree(value):
    if isinstance(value,Pairs):return ("OBJECT",tuple((k,_tree(v)) for k,v in value.members))
    if isinstance(value,list):return ("ARRAY",tuple(_tree(v) for v in value))
    return (type(value).__name__,value)


def inspect_body(*, body, request, expected_isin=None):
    route(request)
    return _inspect(body,request,2*1024**2,expected_isin)


def inspect_original_body(*, body, request, expected_isin=None):
    require(type(request) is Request,"ORIGINAL_REQUEST_REQUIRED")
    b2_route(request)
    return _inspect(body,request,8*1024**2,expected_isin)


def _inspect(body, request, limit, expected_isin):
    """Never reduce an object with duplicate members to a normal dict."""
    require(type(body) is bytes and len(body)<=limit,"DIAGNOSTIC_BODY_LIMIT")
    body_sha=hashlib.sha256(body).hexdigest()
    duplicates=[];nodes=0
    try:
        with localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
            root=json.loads(body,parse_float=Decimal,object_pairs_hook=lambda p:Pairs(tuple(p)),
                parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
        def visit(value,path,depth,area="OTHER"):
            nonlocal nodes
            nodes+=1
            require(nodes<=100000 and depth<=64,"JSON_INSPECTION_LIMIT")
            if isinstance(value,Pairs):
                grouped={}
                for key,child in value.members:grouped.setdefault(key,[]).append(child)
                for key,values in grouped.items():
                    if len(values)>1:
                        require(len(duplicates)<512,"DUPLICATE_DIAGNOSTIC_LIMIT")
                        hashes=tuple(sha(_tree(v)) for v in values)
                        duplicates.append(DuplicateFinding(key_display=_display(key),key_sha256=hashlib.sha256(key.encode()).hexdigest(),
                            occurrences=len(values),location=path or "/",area="ROOT" if not path else "METADATA" if key=="metadata" else "DATA" if key=="data" else area,
                            comparison="STRUCTURALLY_IDENTICAL" if len(set(hashes))==1 else "DIFFERENT",value_sha256s=hashes))
                counters={}
                for key,child in value.members:
                    counters[key]=counters.get(key,0)+1
                    child_area="METADATA" if key=="metadata" else "DATA" if key=="data" else area
                    # Exact key hashes and occurrence ordinals disambiguate sanitized paths.
                    component=_display(key)+"#"+hashlib.sha256(key.encode()).hexdigest()[:12]+"@"+str(counters[key])
                    visit(child,path+"/"+component,depth+1,child_area)
            elif isinstance(value,list):
                for index,child in enumerate(value):visit(child,path+"/"+str(index),depth+1,area)
        visit(root,"",0)
        if duplicates:
            return Inspection(body_sha256=body_sha,byte_count=len(body),disposition="QUARANTINE",duplicates=tuple(duplicates),
                diagnostics=("DUPLICATE_SOURCE_KEY","QUARANTINE_NOT_FINANCIAL_EVIDENCE"))
        def ordinary(value):
            if isinstance(value,Pairs):return {k:ordinary(v) for k,v in value.members}
            if isinstance(value,list):return [ordinary(v) for v in value]
            return value
        payload=ordinary(root)
        safe_json(payload)
        require(type(payload) is dict,"SOURCE_OBJECT_REQUIRED")
        findings=[];claims=[];diagnostics={"NO_ACTUAL_PAYMENT_PROOF","HISTORICAL_PUBLICATION_UNPROVEN"}
        # Only the requested block is interpreted; unexpected blocks remain visible in raw bytes.
        block=payload.get(request.table)
        require(type(block) is dict,"SOURCE_TABLE_MISSING")
        columns,rows=block.get("columns"),block.get("data")
        require(type(columns) is list and all(type(c) is str for c in columns) and type(rows) is list
            and all(type(row) is list and len(row)==len(columns) for row in rows),"SOURCE_TABLE_INVALID")
        metadata=block.get("metadata")
        unit_keys={"UNIT","UNITS","AMOUNTUNIT","AMOUNT_UNIT","VALUEUNIT"}
        monetary_keys={"VALUE","AMOUNT","COUPONVALUE","COUPON_VALUE","REDEMPTIONVALUE","REDEMPTION_VALUE","AMORTVALUE","AMORTIZATION_VALUE"}
        for row in rows:
            claims.extend(str(v) for k,v in zip(columns,row) if k.upper() in unit_keys and v is not None and str(v).strip())
        if type(metadata) is dict:
            for field,value in metadata.items():
                if field.upper() in monetary_keys and type(value) is dict:
                    claims.extend(str(v) for k,v in value.items() if k.upper() in unit_keys and v is not None and str(v).strip())
        def fields(names):return tuple(c for c in columns if c.upper() in names)
        def ids(names,label):
            result=[]
            for source_row in rows:
                supplied=[v for k,v in zip(columns,source_row) if k.upper() in names and v is not None and not (type(v) is str and not v)]
                if any(type(v) is not str for v in supplied):diagnostics.add(label+"_INVALID");result.append(None)
                elif len(set(supplied))>1:diagnostics.add(label+"_ALIAS_CONFLICT");result.append(None)
                else:result.append(supplied[0] if supplied else None)
            return tuple(result)
        returned_secids=ids({"SECID","SECURITYID"},"RETURNED_SECID")
        returned_isins=ids({"ISIN"},"RETURNED_ISIN")
        if any(v is not None and v!=request.secid for v in returned_secids) or (expected_isin is not None and any(v is not None and v!=expected_isin for v in returned_isins)):
            diagnostics.add("SOURCE_IDENTITY_CONFLICT")
        if any(v is None for v in returned_isins):diagnostics.add("RETURNED_ISIN_NOT_SUPPLIED")
        if any(not any(k.upper() in monetary_keys and v is not None and not (type(v) is str and not v.strip()) for k,v in zip(columns,r)) for r in rows):
            diagnostics.add("MONETARY_AMOUNT_NOT_SUPPLIED")
        findings.append(TableFinding(table=request.table,columns=tuple(columns),row_count=len(rows),
            metadata_json=encode(metadata).decode() if metadata is not None else None,rows_json=tuple(encode(r).decode() for r in rows),
            cursor_present=request.table+".cursor" in payload,cursor_json=encode(payload[request.table+".cursor"]).decode() if request.table+".cursor" in payload else None,
            currency_fields=fields({"CURRENCY","CURRENCYID","FACEUNIT"}),amount_fields=fields({"VALUE","AMOUNT","COUPONVALUE","COUPON_VALUE","REDEMPTIONVALUE","REDEMPTION_VALUE","AMORTVALUE","AMORTIZATION_VALUE"}),
            percentage_fields=fields({"VALUEPRC","COUPONPERCENT","REDEMPTIONPERCENT","PRICE","OFFERPRICE"}),
            nominal_fields=fields({"FACEVALUE","NOMINAL","ISSUEVALUE"}),date_fields=tuple(c for c in columns if "DATE" in c.upper()),unit_claims=tuple(sorted(set(claims))),
            returned_secids=returned_secids,returned_isins=returned_isins))
        disposition="ACCEPTED";complete=False
        try:
            page=parse_page(body,Request(secid=request.secid,table=request.table,offset=request.offset))
            complete=page.complete;diagnostics.add(page.diagnostic)
        except (IdentityError,ValueError):
            disposition="QUARANTINE";diagnostics.add("V1_SCHEMA_REJECTED")
        if disposition=="QUARANTINE":
            diagnostics.add("QUARANTINE_NOT_FINANCIAL_EVIDENCE")
            findings=[f.model_copy(update={"rows_json":(),"returned_secids":(),"returned_isins":(),"unit_claims":()}) for f in findings]
            claims=[]
        if request.table=="offers":diagnostics.add("OFFER_NOT_AUTOMATIC_REDEMPTION")
        if not claims:diagnostics.add("AMOUNT_UNIT_AUTHORITY_UNPROVEN")
        return Inspection(body_sha256=body_sha,byte_count=len(body),disposition=disposition,tables=tuple(findings),
            completeness="COMPLETE" if complete and disposition=="ACCEPTED" else "INCOMPLETE",
            unit_authority="UNIT_AUTHORITY_CONFLICTING" if len(set(claims))>1 else "UNIT_AUTHORITY_PARTIAL" if claims else "UNIT_AUTHORITY_UNPROVEN",
            diagnostics=tuple(sorted(diagnostics)))
    except (IdentityError,ValueError,TypeError,RecursionError,UnicodeError,ArithmeticError):
        return Inspection(body_sha256=body_sha,byte_count=len(body),disposition="QUARANTINE",
            diagnostics=("JSON_INSPECTION_UNAVAILABLE","DUPLICATE_LOCATION_UNKNOWN","QUARANTINE_NOT_FINANCIAL_EVIDENCE"))


class HistoricalCashflowDiagnosticClient:
    def __init__(self, *, transport=None):
        self.http=httpx.Client(transport=transport,verify=True,trust_env=False,follow_redirects=False,timeout=30)

    def close(self):self.http.close()

    def fetch(self, request, *, response_limit, bytes_remaining, seconds_remaining, check_time):
        url,params=route(request);body=bytearray()
        try:
            check_time()
            self.http.cookies.clear()
            with self.http.stream("GET",url,params=params,headers={"Accept-Encoding":"identity"},timeout=min(30,seconds_remaining),follow_redirects=False) as response:
                if response.status_code!=200:
                    try:delay=min(60,max(0,int(response.headers.get("Retry-After","0"))))
                    except ValueError:delay=0
                    retry=response.status_code in TRANSIENT_HTTP
                    raise SourceFailure("TRANSIENT_HTTP" if retry else "HTTP_REJECTED",retryable=retry,retry_after=delay)
                if response.headers.get("Content-Encoding","identity").lower()!="identity":raise SourceFailure("CONTENT_ENCODING_REJECTED")
                cap=min(response_limit,bytes_remaining);size=min(4096,cap)
                require(size>0,"RESPONSE_BYTE_BUDGET")
                blocks=iter(response.iter_bytes(chunk_size=size))
                while True:
                    check_time()
                    if len(body)+size>cap:raise SourceFailure("RESPONSE_BYTE_BUDGET",received_bytes=len(body))
                    try:block=next(blocks)
                    except StopIteration:break
                    body.extend(block)
                    if len(block)<size:break
            check_time();return bytes(body)
        except SourceFailure:raise
        except IdentityError as exc:raise SourceFailure(exc.code,received_bytes=len(body)) from None
        except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError):
            raise SourceFailure("TRANSIENT_TRANSPORT",retryable=True,received_bytes=len(body)) from None
        except Exception:raise SourceFailure("TRANSPORT_REJECTED",received_bytes=len(body)) from None
