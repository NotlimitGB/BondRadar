import json
from datetime import datetime,timezone,date
import httpx
import pytest
from app.schemas.historical_evidence_foundation import HistoricalSourceQuery
from app.services.historical_evidence_source_client import HistoricalEvidenceSourceClient,HistoricalSourceError
from app.services.historical_evidence_discovery import pages


def client(handler):
    sleeps=[]
    return HistoricalEvidenceSourceClient(httpx.Client(transport=httpx.MockTransport(handler)),sleeper=sleeps.append,
        monotonic=lambda:0,clock=lambda:datetime(2026,10,7,tzinfo=timezone.utc)),sleeps


def test_exact_market_route_and_decimal_preservation():
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(200,content=b'{"history":{"columns":["SECID","VALUE"],"data":[["OLD",1.234567890123456789]]}}')
    source,_=client(handler);query=HistoricalSourceQuery(family="MARKET",table="history",trade_date=date(2020,9,1))
    p=source.fetch_page(query)
    assert p.complete and p.rows[0]["VALUE"]=="1.234567890123456789"
    assert seen[0].url.path=="/iss/history/engines/stock/markets/bonds/securities.json"
    assert seen[0].url.params["date"]=="2020-09-01" and "marketprice_board" not in seen[0].url.params


@pytest.mark.parametrize("status,retried",[(408,True),(429,True),(500,True),(502,True),(503,True),(504,True),(400,False),(401,False),(403,False)])
def test_retry_categories_and_sanitization(status,retried):
    calls=[]
    def handler(request):calls.append(1);return httpx.Response(status,text="password=SECRET",headers={"Retry-After":"999"})
    source,sleeps=client(handler)
    with pytest.raises(HistoricalSourceError) as error:source.fetch_page(HistoricalSourceQuery(family="LISTING",table="securities"))
    assert len(calls)==(3 if retried else 1)
    assert error.value.attempts==len(calls) and "SECRET" not in repr(error.value) and error.value.__cause__ is None
    assert all(s<=60 for s in sleeps)


def test_cursor_100_plus_50_and_source_absence():
    def handler(request):
        offset=int(request.url.params["start"]);rows=[[str(i)] for i in range(offset,min(150,offset+100))]
        return httpx.Response(200,json={"securities":{"columns":["SECID"],"data":rows},"securities.cursor":{"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[offset,150,100]]}})
    source,_=client(handler);result=tuple(pages(source,HistoricalSourceQuery(family="LISTING",table="securities")))
    assert len(result)==2 and sum(len(p.rows) for p in result)==150 and result[-1].complete
    source,_=client(lambda r:httpx.Response(200,json={"securities":{"columns":["SECID"],"data":[]}}))
    assert tuple(pages(source,HistoricalSourceQuery(family="LISTING",table="securities")))[0].complete


@pytest.mark.parametrize("payload",[
    {},{"securities":{"columns":["SECID","SECID"],"data":[]}},
    {"securities":{"columns":["SECID"],"data":[[]]}},
    {"securities":{"columns":["SECID"],"data":[]},"securities.cursor":{"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[True,0,100]]}},
    {"securities":{"columns":["token"],"data":[["SECRET"]]}},
])
def test_malformed_response_not_retried(payload):
    calls=[]
    def handler(r):calls.append(1);return httpx.Response(200,json=payload)
    source,_=client(handler)
    with pytest.raises((ValueError,HistoricalSourceError)):source.fetch_page(HistoricalSourceQuery(family="LISTING",table="securities"))
    assert len(calls)==1


def test_transport_retry_body_limit_and_duplicate_keys():
    calls=[]
    def handler(r):
        calls.append(1)
        if len(calls)<3:raise httpx.ReadTimeout("token=SECRET")
        return httpx.Response(200,json={"dates":{"columns":[],"data":[]}})
    source,_=client(handler);p=source.fetch_page(HistoricalSourceQuery(family="DATES",table="dates"))
    assert p.attempts==3 and p.transient_failures==2
    for body in (b'x'*(8388608+1),b'{"dates":{},"dates":{}}'):
        source,_=client(lambda r:httpx.Response(200,content=body))
        with pytest.raises(HistoricalSourceError):source.fetch_page(HistoricalSourceQuery(family="DATES",table="dates"))
