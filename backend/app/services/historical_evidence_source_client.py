"""Allowlisted MOEX acquisition, bounded bodies and sanitized failure categories."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import time
import httpx
from app.schemas.historical_evidence_foundation import HistoricalSourcePage, HistoricalSourceQuery
from app.services.historical_evidence_normalization import LIMIT, safe_json, sha, signed


class HistoricalSourceError(Exception):
    def __init__(self,code,*,retryable=False,attempts=1):
        self.code=code;self.retryable=retryable;self.attempts=attempts
        super().__init__(code)


class HistoricalEvidenceSourceClient:
    def __init__(self,http_client: httpx.Client,*,sleeper=time.sleep,monotonic=time.monotonic,
                 clock=lambda:datetime.now(timezone.utc)):
        self.client=http_client;self.sleep=sleeper;self.monotonic=monotonic;self.clock=clock;self.last_attempt=None

    @staticmethod
    def _route(query):
        root="https://iss.moex.com/iss";history=root+"/history/engines/stock/markets/bonds"
        routes={"DATES":history+"/dates.json","COLUMNS":history+"/securities/columns.json",
                "LISTING":history+"/listing.json","MARKET":history+"/securities.json",
                "DESCRIPTION":root+f"/securities/{query.secid}.json",
                "REFERENCE":root+"/securities.json","CASHFLOWS":root+f"/statistics/engines/stock/markets/bonds/bondization/{query.secid}.json"}
        params={"iss.meta":"on","start":query.offset,"limit":100,"iss.only":query.table+","+query.table+".cursor"}
        if query.family=="MARKET":params.update(date=query.trade_date.isoformat(),sort_column="SECID",sort_order="asc",numtrades=0)
        if query.family=="LISTING":params["status"]="all"
        if query.family=="REFERENCE":params.update(q=query.secid,engine="stock",market="bonds")
        if query.family=="CASHFLOWS":params[query.table+".start"]=query.offset
        return routes[query.family],params

    def fetch_page(self,query):
        if type(query) is not HistoricalSourceQuery: raise ValueError("SOURCE_QUERY_REQUIRED")
        query=HistoricalSourceQuery.model_validate({k:getattr(query,k) for k in type(query).model_fields})
        url,params=self._route(query);transient=0
        for attempt in range(1,4):
            wait=0 if self.last_attempt is None else max(0,0.5-(self.monotonic()-self.last_attempt))
            if wait:self.sleep(wait)
            self.last_attempt=self.monotonic();retry_after=0
            try:
                with self.client.stream("GET",url,params=params,follow_redirects=False,timeout=30) as response:
                    if response.status_code>=300:
                        retryable=response.status_code in (408,429,500,502,503,504)
                        try:retry_after=min(60,max(0,int(response.headers.get("Retry-After","0"))))
                        except ValueError:pass
                        raise HistoricalSourceError("TRANSIENT_HTTP" if retryable else "SOURCE_REQUEST_REJECTED",retryable=retryable)
                    body=bytearray()
                    for chunk in response.iter_bytes():
                        if len(body)+len(chunk)>LIMIT: raise HistoricalSourceError("SOURCE_BODY_TOO_LARGE")
                        body.extend(chunk)
                try:
                    def pairs(values):
                        result={}
                        for key,value in values:
                            if key in result:raise ValueError("DUPLICATE_JSON_KEY")
                            result[key]=value
                        return result
                    payload=json.loads(body,parse_float=Decimal,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()),object_pairs_hook=pairs)
                except (ValueError,TypeError,RecursionError):raise HistoricalSourceError("MALFORMED_SOURCE_JSON") from None
                rows,next_offset,total,page_size=self._table(payload,query)
                return signed(HistoricalSourcePage,"page_sha256",query=query,rows=rows,observed_at=self.clock(),
                    complete=next_offset is None,next_offset=next_offset,cursor_total=total,cursor_page_size=page_size,
                    attempts=attempt,transient_failures=transient)
            except (httpx.TimeoutException,httpx.NetworkError,httpx.RemoteProtocolError):
                error=HistoricalSourceError("TRANSIENT_TRANSPORT",retryable=True)
            except httpx.HTTPError:error=HistoricalSourceError("SOURCE_TRANSPORT_REJECTED")
            except HistoricalSourceError as exc:error=exc
            except Exception:error=HistoricalSourceError("SOURCE_UNEXPECTED_FAILURE")
            if not error.retryable or attempt==3:
                raise HistoricalSourceError(error.code,retryable=error.retryable,attempts=attempt) from None
            transient+=1;self.sleep(max((0.25,0.50)[attempt-1],retry_after))
        raise HistoricalSourceError("SOURCE_REQUEST_FAILED")

    @staticmethod
    def _table(payload,query):
        def table(name):
            block=payload.get(name) if type(payload) is dict else None
            if type(block) is not dict:raise HistoricalSourceError("SOURCE_TABLE_MISSING")
            columns=block.get("columns");data=block.get("data")
            if type(columns) is not list or len(columns)>1024 or any(type(k) is not str for k in columns) or len(set(columns))!=len(columns) or type(data) is not list:
                raise HistoricalSourceError("MALFORMED_SOURCE_TABLE")
            if any(type(row) is not list or len(row)!=len(columns) for row in data):raise HistoricalSourceError("MALFORMED_SOURCE_TABLE")
            if any(cell is not None and type(cell) not in (str,int,bool,Decimal) for row in data for cell in row):
                raise HistoricalSourceError("NON_SCALAR_SOURCE_CELL")
            try:
                return tuple(safe_json(dict(zip(columns,row))) for row in data)
            except ValueError:
                raise HistoricalSourceError("UNSAFE_SOURCE_FIELDS") from None
        rows=table(query.table)
        if len(rows)>100:raise HistoricalSourceError("SOURCE_PAGE_LIMIT_EXCEEDED")
        name=query.table+".cursor"
        if name in payload:
            cursor=table(name)
            if len(cursor)!=1:raise HistoricalSourceError("INVALID_SOURCE_CURSOR")
            index,total,size=(cursor[0].get(k) for k in ("INDEX","TOTAL","PAGESIZE"))
            if any(type(v) is not int for v in (index,total,size)) or index!=query.offset or total<index or not 1<=size<=100 or len(rows)!=min(size,total-index):
                raise HistoricalSourceError("INVALID_SOURCE_CURSOR")
            return rows,None if index+size>=total else index+size,total,size
        return rows,None if len(rows)<100 else query.offset+len(rows),None,None
