from datetime import date, datetime, timezone
import json
from pathlib import Path
import httpx
import pytest
from app.schemas.historical_evidence_foundation import HistoricalEvidencePolicy, HistoricalSourceQuery
from app.schemas.historical_discovery_checkpoint import DiscoveryLimits, DiscoveryReadAuthorization
from app.services import historical_discovery_checkpoint as checkpoint
from app.services.historical_evidence_source_client import HistoricalEvidenceSourceClient
from app.services.historical_evidence_normalization import sha, signed

POLICY=HistoricalEvidencePolicy(cutoff=date(2026,9,30))
DAY=date(2020,9,1)


@pytest.fixture
def scope(monkeypatch):
    queries=tuple(checkpoint.discovery_queries(POLICY))[:5]
    monkeypatch.setattr(checkpoint,"discovery_queries",lambda policy:iter(queries))
    return queries


def client(handler, *, clock=None):
    sleeps=[]
    return HistoricalEvidenceSourceClient(httpx.Client(transport=httpx.MockTransport(handler)),sleeper=sleeps.append,
        monotonic=clock or (lambda:0),clock=lambda:datetime(2026,10,8,tzinfo=timezone.utc)),sleeps


def response(request, *, rows=None, cursor=None):
    path=request.url.path
    table="dates" if path.endswith("/dates.json") else "history" if path.endswith("/columns.json") or path.endswith("/securities.json") else "securities"
    rows=rows or []
    columns=list(rows[0]) if rows else []
    payload={table:{"columns":columns,"data":[list(r.values()) for r in rows]}}
    if cursor is not None:payload[table+".cursor"]={"columns":["INDEX","TOTAL","PAGESIZE"],"data":[cursor]}
    return httpx.Response(200,json=payload)


def market_row(day, i=0, **updates):
    return {"SECID":f"EXTINCT_{i}","BOARDID":"OLD_BOARD","TRADEDATE":str(day),"ISIN":f"RU_{i}","CLOSE":"90","ACCINT":"1","NUMTRADES":0,**updates}


def execute(root,source,limits):
    auth=checkpoint.authorize(policy=POLICY,evidence_root=root,limits=limits)
    return checkpoint.run_discovery(source_client=source,policy=POLICY,evidence_root=root,authorization=auth,limits=limits)


def test_frozen_full_calendar_and_strict_authorization(tmp_path):
    queries=list(checkpoint.discovery_queries(POLICY))
    assert len(queries)==(POLICY.cutoff-POLICY.history_start).days+4
    assert queries[3].trade_date==POLICY.history_start and queries[-1].trade_date==POLICY.cutoff
    with pytest.raises(ValueError):checkpoint.authorize(policy=POLICY.model_copy(update={"cutoff":DAY}),evidence_root=tmp_path)
    auth=checkpoint.authorize(policy=POLICY,evidence_root=tmp_path)
    with pytest.raises(ValueError):DiscoveryReadAuthorization.model_validate({**auth.model_dump(),"explicit_authorization":1})
    calls=[];source,_=client(lambda r:calls.append(r) or response(r))
    result=checkpoint.run_discovery(source_client=source,policy=POLICY,evidence_root=tmp_path,
        authorization=auth.model_copy(update={"scope_sha256":"wrong"}))
    assert result.status=="AUTHORIZATION_FAILED" and not calls and not (tmp_path/"scope.json").exists()


def test_resume_budget_finalize_and_exact_population(tmp_path,scope):
    calls=[]
    def handler(r):
        calls.append((r.url.path,dict(r.url.params)))
        if r.url.params.get("date")==str(DAY):
            offset=int(r.url.params["start"])
            rows=[market_row(DAY,i) for i in range(offset,min(150,offset+100))]
            return response(r,rows=rows,cursor=[offset,150,100])
        if r.url.path.endswith("columns.json"):
            return response(r,rows=[{"name":f"COL_{i}"} for i in range(150)])
        return response(r)
    source,sleeps=client(handler);small=DiscoveryLimits(max_requests=4,max_pages=4)
    first=execute(tmp_path,source,small)
    assert first.status=="BUDGET_STOP" and first.accepted_page_count==4 and len(calls)==4
    source,_=client(handler);second=execute(tmp_path,source,small)
    assert second.status=="COMPLETE" and len(calls)==6 and second.absent_partition_count==1
    assert calls[4][1]["start"]=="100"
    assert second.http_attempt_count==6 and second.retry_count==0
    assert not second.db_mutation and not second.pit_ready and not second.acquisition_ready
    assert all("marketprice_board" not in params for _,params in calls)
    metadata=[params for path,params in calls if path.endswith(("dates.json","columns.json"))]
    assert all("start" not in params and "limit" not in params for params in metadata)
    store=checkpoint.CheckpointStore(tmp_path,POLICY)
    result=checkpoint.finalize(store)
    assert result.identity_count==150 and result.exact_binding_count==150 and result.board_count==1
    assert result.observed_dates==(DAY,) and result.unresolved_binding_count==0
    assert checkpoint.VerifiedDiscoveryIndex(tmp_path,POLICY).manifest==result
    assert checkpoint.finalize(store)==result
    store.recheck(sha(scope[3]))
    with pytest.raises(ValueError):checkpoint.VerifiedDiscoveryIndex(tmp_path,POLICY)
    assert (tmp_path/sha(scope[3])/"generation-0.json").exists()


@pytest.mark.parametrize("problem",["total","size","missing","overlap","date","board","malformed"])
def test_failed_partition_preserves_pages_and_is_not_empty(tmp_path,scope,problem):
    def handler(r):
        if r.url.params.get("date")!=str(DAY):return response(r)
        offset=int(r.url.params["start"])
        if offset==0:return response(r,rows=[market_row(DAY,i) for i in range(100)],cursor=[0,150,100])
        if problem=="malformed":return httpx.Response(200,json={"history":{"columns":["A","A"],"data":[]}})
        rows=[market_row(DAY,i) for i in range(100,150)]
        if problem=="overlap":rows[0]=market_row(DAY,0)
        if problem=="date":rows[0]["TRADEDATE"]="2020-09-02"
        if problem=="board":rows[0]["BOARDID"]=None
        cursor=None if problem=="missing" else [100,151,100] if problem=="total" else [100,150,50] if problem=="size" else [100,150,100]
        if problem=="total":rows.append(market_row(DAY,150))
        return response(r,rows=rows,cursor=cursor)
    source,_=client(handler);result=execute(tmp_path,source,DiscoveryLimits())
    assert result.status in ("SOURCE_FAILED","EVIDENCE_INCONSISTENT")
    assert result.failed_partition_count==1 and result.absent_partition_count==0 and result.accepted_page_count==4
    assert result.http_attempt_count==5
    with pytest.raises(ValueError):checkpoint.finalize(checkpoint.CheckpointStore(tmp_path,POLICY))


def test_transient_request_budget_and_runtime_are_exact(tmp_path,scope):
    requests=[]
    source,sleeps=client(lambda r:requests.append(r) or httpx.Response(503,text="password=SECRET",headers={"Retry-After":"60"}))
    result=execute(tmp_path,source,DiscoveryLimits(max_requests=2,max_seconds=120))
    assert result.status=="BUDGET_STOP" and len(requests)==2 and result.http_attempt_count==2
    assert result.retry_count==1 and "SECRET" not in repr(result)
    assert max(sleeps)<=60
    now=[0]
    def handler(r):now[0]=121;return response(r)
    source,_=client(handler,clock=lambda:now[0]);result=execute(tmp_path,source,DiscoveryLimits())
    assert result.status=="BUDGET_STOP" and result.execution_http_attempt_count==1 and result.accepted_page_count==0


def test_corrupt_checkpoint_crash_orphan_and_tampered_manifest(tmp_path,scope,monkeypatch):
    source,_=client(response)
    original=checkpoint.CheckpointStore.save_state
    def crash(self,q,state):raise OSError("SECRET")
    monkeypatch.setattr(checkpoint.CheckpointStore,"save_state",crash)
    result=execute(tmp_path,source,DiscoveryLimits())
    assert result.status=="CHECKPOINT_INVALID" and result.execution_http_attempt_count==1
    monkeypatch.setattr(checkpoint.CheckpointStore,"save_state",original)
    result=execute(tmp_path,source,DiscoveryLimits())
    assert result.status=="COMPLETE" and result.accepted_page_count==5
    store=checkpoint.CheckpointStore(tmp_path,POLICY);manifest=checkpoint.finalize(store)
    tampered=signed(type(manifest),"source_manifest_sha256",**{k:getattr(manifest,k) for k in type(manifest).model_fields if k!="source_manifest_sha256" and k!="identity_count"},identity_count=999)
    checkpoint.atomic_json(tmp_path/"manifest.json",tampered)
    with pytest.raises(ValueError,match="REPLAY"):checkpoint.VerifiedDiscoveryIndex(tmp_path,POLICY)
    checkpoint.atomic_json(tmp_path/"manifest.json",manifest)
    state=store.state(scope[0]);directory=store.directory(scope[0])/str(state["generation"])
    receipt=checkpoint.read_json(directory/"0.json");(directory/(receipt["page_sha256"]+".json")).write_text("{}")
    calls=[];source,_=client(lambda r:calls.append(r) or response(r))
    result=execute(tmp_path,source,DiscoveryLimits())
    assert result.status=="CHECKPOINT_INVALID" and not calls


def test_probe_no_filter_population_and_overlap_failure(tmp_path,scope):
    params=[]
    def handler(r):
        params.append(dict(r.url.params))
        return response(r,rows=[market_row(DAY,0,NUMTRADES=None)] if r.url.params["start"]=="0" else [])
    source,_=client(handler);auth=checkpoint.authorize(policy=POLICY,evidence_root=tmp_path,probe_dates=(DAY,))
    result=checkpoint.probe(source_client=source,policy=POLICY,evidence_root=tmp_path,authorization=auth)
    assert result.status=="COMPLETE" and result.equal_population_dates==(DAY,)
    assert "numtrades" in params[0] and "numtrades" not in params[2]
    assert result.numtrades_filter_semantics_verified=="NOT_VERIFIED"
    assert checkpoint.run_discovery(source_client=source,policy=POLICY,evidence_root=tmp_path,authorization=auth).status=="AUTHORIZATION_FAILED"
    def repeated(r):
        offset=int(r.url.params["start"])
        return response(r,rows=[market_row(DAY,i) for i in range(100)],cursor=[offset,200,100])
    source,_=client(repeated);result=checkpoint.probe(source_client=source,policy=POLICY,evidence_root=tmp_path,authorization=auth)
    assert result.status=="SOURCE_FAILED" and not result.compared_dates


def test_symlink_root_and_concurrent_writer_are_blocked(tmp_path,scope):
    store=checkpoint.CheckpointStore(tmp_path,POLICY,create=True)
    with store.lock():
        with pytest.raises(ValueError,match="LOCK"):
            with store.lock():pass
    link=tmp_path/"link"
    try:link.symlink_to(tmp_path,target_is_directory=True)
    except OSError:pytest.skip("OS does not permit unprivileged symlinks")
    with pytest.raises(ValueError,match="UNSAFE"):checkpoint.safe_path(link/"child")


def test_indexed_acquisition_plan_blocks_unreviewed_population_without_db(tmp_path,scope):
    from app.services.historical_evidence_plan_service import HistoricalEvidencePlanService
    source,_=client(response);assert execute(tmp_path,source,DiscoveryLimits()).status=="COMPLETE"
    checkpoint.finalize(checkpoint.CheckpointStore(tmp_path,POLICY))
    def forbidden():raise AssertionError("DB must not be opened")
    result=HistoricalEvidencePlanService(forbidden).plan_acquisition(discovery=checkpoint.VerifiedDiscoveryIndex(tmp_path,POLICY))
    assert result.status=="BLOCKED" and result.blockers==("DISCOVERY_SOURCE_POPULATION_NOT_VERIFIED",)


def test_non_cursor_short_pages_zero_trades_missing_prices_and_identity_conflicts(tmp_path,scope):
    calls=[]
    def handler(r):
        if r.url.params.get("date")!=str(DAY):return response(r)
        calls.append(int(r.url.params["start"]))
        if calls[-1]:return response(r)
        rows=[market_row(DAY,0,CLOSE=None,ACCINT=None,NUMTRADES=0),
              market_row(DAY,0,ISIN="RU_OTHER",BOARDID="OTHER_BOARD",NUMTRADES=None)]
        return response(r,rows=rows)
    source,_=client(handler);result=execute(tmp_path,source,DiscoveryLimits())
    assert result.status=="COMPLETE" and calls==[0,2]
    manifest=checkpoint.finalize(checkpoint.CheckpointStore(tmp_path,POLICY))
    assert manifest.exact_binding_count==2 and manifest.board_count==2 and manifest.identity_conflict_secid_count==1
    assert manifest.market_row_count==2 and manifest.zero_trade_row_count==1 and manifest.unknown_trade_row_count==1
    assert manifest.usable_price_row_count==1


def test_content_identity_excludes_observation_time(tmp_path,scope):
    def handler(r):return response(r,rows=[market_row(DAY,0)] if r.url.params.get("date")==str(DAY) and r.url.params["start"]=="0" else [])
    source,_=client(handler);execute(tmp_path,source,DiscoveryLimits())
    store=checkpoint.CheckpointStore(tmp_path,POLICY);before=checkpoint.finalize(store)
    store.recheck(sha(scope[3]))
    source,_=client(handler);source.clock=lambda:datetime(2026,10,9,tzinfo=timezone.utc)
    execute(tmp_path,source,DiscoveryLimits());after=checkpoint.finalize(store)
    assert before.source_content_sha256==after.source_content_sha256
    assert before.source_manifest_sha256!=after.source_manifest_sha256
