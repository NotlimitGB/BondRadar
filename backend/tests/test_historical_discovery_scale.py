from datetime import timedelta
import io
import tracemalloc
from app.schemas.historical_discovery_checkpoint import DiscoveryLimits
from app.schemas.historical_evidence_foundation import HistoricalSourceQuery
from app.services import historical_discovery_checkpoint as checkpoint
from app.services.historical_audit_canonical_json import write_json
from test_historical_discovery_checkpoint import POLICY,DAY,client,response,market_row,execute


def test_scale_200_dates_2000_pages_resume_streaming(tmp_path,monkeypatch,record_property):
    metadata=tuple(checkpoint.discovery_queries(POLICY))[:3]
    dates=tuple(DAY+timedelta(days=i) for i in range(200))
    queries=metadata+tuple(HistoricalSourceQuery(family="MARKET",table="history",trade_date=d) for d in dates)
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))
    calls=[0]
    def handler(request):
        calls[0]+=1
        day_text=request.url.params.get("date")
        if not day_text:return response(request)
        day=__import__("datetime").date.fromisoformat(day_text);offset=int(request.url.params["start"])
        base=(day-DAY).days*50
        rows=[market_row(day,base+i,ISIN="RU"+str(base+i).zfill(10)+"X"*180) for i in range(offset,offset+5)]
        return response(request,rows=rows,cursor=[offset,50,5])
    source,_=client(handler)
    limits=DiscoveryLimits(max_requests=400,max_pages=400,max_seconds=3600)
    tracemalloc.start()
    executions=0
    while True:
        result=execute(tmp_path,source,limits);executions+=1
        assert result.status in ("BUDGET_STOP","COMPLETE")
        if result.status=="COMPLETE":break
        assert executions<10
    acquisition_peak=tracemalloc.get_traced_memory()[1]
    assert calls[0]==2003 and result.accepted_page_count==2003 and executions==6
    tracemalloc.reset_peak()
    store=checkpoint.CheckpointStore(tmp_path,POLICY)
    manifest=checkpoint.finalize(store)
    aggregation_peak=tracemalloc.get_traced_memory()[1]
    tracemalloc.reset_peak()
    # A counting sink verifies streaming without retaining the serialized result.
    class Sink:
        count=0
        def write(self,value):self.count+=len(value)
    sink=Sink();write_json(manifest,sink)
    serialization_peak=tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert manifest.identity_count==10000 and manifest.exact_binding_count==10000
    assert manifest.observed_dates==dates and manifest.source_page_count==2003
    assert manifest.observed_identity_count==10000 and manifest.listing_identity_count==0
    assert len(manifest.index_sha256s)==6 and sink.count<100000
    assert acquisition_peak<64*1024*1024 and aggregation_peak<64*1024*1024 and serialization_peak<64*1024*1024
    for name,value in (("acquisition_peak_bytes",acquisition_peak),("aggregation_peak_bytes",aggregation_peak),
                       ("serialization_peak_bytes",serialization_peak),("accepted_pages",2003)):
        record_property(name,value)
        print(f"{name}={value}")
