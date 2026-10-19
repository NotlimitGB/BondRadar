"""Task308A hermetic transport, authorization, evidence replay and failure tests."""
from datetime import datetime, timezone
import ast
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import httpx
import pytest
from app.schemas.historical_identity_feasibility import ProbeLimits
from app.services.historical_identity_feasibility_service import IdentityError
from app.services.historical_identity_probe_client import HistoricalIdentityProbeClient, request_for, parse_page
from app.services.historical_identity_probe_service import HistoricalIdentityProbeService, encode, save_state, load_state
from test_historical_identity_feasibility import plan, runner


ISIN="RU000A0JS3W6"


class Timer:
    def __init__(self):self.value=0.;self.delays=[]
    def sleep(self,seconds):self.delays.append(seconds);self.value+=seconds
    def now(self):return self.value


@pytest.fixture
def setup_probe(tmp_path,monkeypatch):
    monkeypatch.setattr("app.services.historical_identity_probe_service.shutil.disk_usage",lambda _:SimpleNamespace(free=30*1024**3))
    timer=Timer();service=HistoricalIdentityProbeService(sleeper=timer.sleep,monotonic=timer.now,clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc))
    root=tmp_path/"evidence";root.mkdir();p=plan(4)
    def auth(**overrides):
        args=dict(plan=p,expected_plan_sha256=p.plan_sha256,secids=[p.samples[0].secid],sources=["REFERENCE","DESCRIPTION"],destination=root,explicit_authorization=True)
        args.update(overrides);return service.authorize(**args)
    return p,root,timer,service,auth


def response(request, *, isin=ISIN, secid=None):
    sid=request.url.params.get("q") or request.url.path.split("/")[-1][:-5]
    sid=sid if secid is None else secid
    if request.url.path=="/iss/securities.json":
        data=[] if request.url.params["start"]!="0" else [[sid,isin,"stock_bonds"]]
        return httpx.Response(200,json={"securities":{"columns":["secid","isin","type"],"data":data}})
    return httpx.Response(200,json={"description":{"columns":["name","value"],"data":[["SECID",sid],["ISIN",isin]]}})


def run_probe(parts,handler,**kwargs):
    p,root,timer,service,auth=parts;a=kwargs.pop("authorization",None) or auth()
    client=HistoricalIdentityProbeClient(transport=httpx.MockTransport(handler))
    try:return a,service.probe(plan=p,authorization=a,expected_plan_sha256=p.plan_sha256,destination=root,confirm_network=True,source_client=client,**kwargs)
    finally:client.close()


def test_authorized_probe_replay_resume_no_repeat(setup_probe):
    p,root,timer,service,auth=setup_probe;calls=[]
    def handler(r):calls.append(r);return response(r)
    a,m=run_probe(setup_probe,handler)
    assert m.status=="COMPLETE" and m.attempts==3 and m.accepted_pages==3
    assert all(r.method=="GET" and r.url.host=="iss.moex.com" for r in calls)
    assert dict(calls[0].url.params)=={"q":a.secids[0],"start":"0","limit":"100","iss.meta":"on","iss.only":"securities,securities.cursor"}
    assert all(not ({"engine","market","is_trading","marketprice_board"}&set(r.url.params)) for r in calls)
    before={f.name:f.read_bytes() for f in root.iterdir() if f.name!=".task308a.lock"}
    result=service.evaluate(plan=p,authorization=a,evidence_root=root)
    assert result.feasibility=="FEASIBLE_ON_OBSERVED_SAMPLE"
    unit=next(u for u in result.units if u.approved)
    assert unit.recovered_current_isin==ISIN and all(r.current_binding_observed for r in unit.results)
    assert not result.pit_ready and all(r.historical_decision_usability=="UNPROVEN" for r in unit.results)
    assert encode(result)==encode(service.evaluate(plan=p,authorization=a,evidence_root=root))
    assert before=={f.name:f.read_bytes() for f in root.iterdir() if f.name!=".task308a.lock"}
    _,again=run_probe(setup_probe,lambda _:pytest.fail("accepted request repeated"),authorization=a)
    assert again==m and len(calls)==3 and timer.delays==[.5,.5,.5]


@pytest.mark.parametrize("change",["confirm","sha","subset","duplicates","sources","too_many","bool_limit","destination"])
def test_authorization_refusals_before_network(setup_probe,change,tmp_path):
    p,root,timer,service,auth=setup_probe
    with pytest.raises((IdentityError,ValueError)):
        if change=="confirm":auth(explicit_authorization=False)
        elif change=="sha":auth(expected_plan_sha256="0"*64)
        elif change=="subset":auth(secids=["NOT_SELECTED"])
        elif change=="duplicates":auth(secids=[p.samples[0].secid]*2)
        elif change=="sources":auth(sources=["ARBITRARY"])
        elif change=="too_many":auth(secids=[f"X{i}" for i in range(13)])
        elif change=="bool_limit":auth(limits=ProbeLimits(max_requests=True))
        else:
            a=auth();other=tmp_path/"other";other.mkdir()
            service.probe(plan=p,authorization=a,expected_plan_sha256=p.plan_sha256,destination=other,confirm_network=True,source_client=object())
    assert not list(root.iterdir())
    with pytest.raises(IdentityError,match="CONFIRM_NETWORK"):
        service.probe(plan=p,authorization=auth(),expected_plan_sha256=p.plan_sha256,destination=root,source_client=object())


@pytest.mark.parametrize("code,retries",[(429,3),(503,3),(408,3),(401,1),(404,1)])
def test_sanitized_http_failures_and_bounded_retries(setup_probe,code,retries):
    p,root,timer,service,auth=setup_probe;a=auth(sources=["REFERENCE"]);calls=[]
    def handler(r):calls.append(r);return httpx.Response(code,text="SECRET_TOKEN BODY",headers={"Retry-After":"0"})
    _,m=run_probe(setup_probe,handler,authorization=a)
    assert m.status=="INCOMPLETE" and len(calls)==retries
    report=service.evaluate(plan=p,authorization=a,evidence_root=root)
    assert "SECRET_TOKEN" not in encode(report).decode() and not any(f.suffix=="json" and b"SECRET_TOKEN" in f.read_bytes() for f in root.iterdir())
    if retries==3:assert .25 in timer.delays and timer.delays.count(.5)>=4


def test_transient_success_does_not_repeat_accepted_page(setup_probe):
    p,root,timer,service,auth=setup_probe;calls=[]
    def handler(r):
        calls.append(r)
        if len(calls)==1:raise httpx.ReadTimeout("secret URL credential",request=r)
        return response(r)
    a,m=run_probe(setup_probe,handler)
    assert m.status=="COMPLETE" and len(calls)==4
    assert .25 in timer.delays
    assert service.evaluate(plan=p,authorization=a,evidence_root=root).feasibility=="FEASIBLE_ON_OBSERVED_SAMPLE"


@pytest.mark.parametrize("body",[b'{"securities":{}}',b'{"x":1,"x":2}',b'{"x":NaN}',b'[]',b'{"securities":{"columns":["secid","SECID"],"data":[]}}'])
def test_malformed_response_is_not_empty_success(setup_probe,body):
    p,root,timer,service,auth=setup_probe;a=auth(sources=["REFERENCE"])
    _,m=run_probe(setup_probe,lambda _:httpx.Response(200,content=body),authorization=a)
    assert m.status=="INCOMPLETE" and m.attempts==1
    result=service.evaluate(plan=p,authorization=a,evidence_root=root)
    assert next(u for u in result.units if u.approved).status=="SOURCE_INVALID"


def test_description_does_not_inject_secid_and_cross_source_conflict(setup_probe):
    p,root,timer,service,auth=setup_probe
    def handler(r):return response(r,isin="US0378331005" if r.url.path!="/iss/securities.json" else ISIN)
    a,m=run_probe(setup_probe,handler)
    result=service.evaluate(plan=p,authorization=a,evidence_root=root)
    assert next(u for u in result.units if u.approved).status=="EXACT_CURRENT_SOURCE_BINDING_CONFLICT"
    page=parse_page(b'{"description":{"columns":["name","value"],"data":[["ISIN","RU000A0JS3W6"]]}}',request_for("X","DESCRIPTION"))
    assert "SECID" not in page.rows[0]


@pytest.mark.parametrize("damage",["offset","total","size","disappear","overlap"])
def test_cursor_and_overlap_fail_closed(setup_probe,damage):
    p,root,timer,service,auth=setup_probe;a=auth(sources=["REFERENCE"])
    def handler(r):
        offset=int(r.url.params["start"]);sid=a.secids[0]
        row=[sid if offset==0 or damage=="overlap" else "OTHER",ISIN]
        block={"securities":{"columns":["secid","isin"],"data":[row]}}
        if not(offset and damage=="disappear"):
            block["securities.cursor"]={"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[offset+int(offset>0 and damage=="offset"),3 if offset and damage=="total" else 2,0 if offset and damage=="size" else 1]]}
        return httpx.Response(200,json=block)
    _,m=run_probe(setup_probe,handler,authorization=a)
    assert m.status=="INCOMPLETE" and m.attempts==2 and m.accepted_pages==1


def test_durable_interruption_blocks_automatic_repeat(setup_probe):
    p,root,timer,service,auth=setup_probe;a=auth()
    class Interrupted:
        def fetch(self,*args,**kwargs):raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        service.probe(plan=p,authorization=a,expected_plan_sha256=p.plan_sha256,destination=root,confirm_network=True,source_client=Interrupted())
    assert load_state(root,a)["pending"]==1
    with pytest.raises(IdentityError,match="UNRECONCILED"):
        run_probe(setup_probe,lambda _:pytest.fail("must not repeat ambiguous request"),authorization=a)


def test_tampered_raw_response_refused(setup_probe):
    p,root,timer,service,auth=setup_probe;a,m=run_probe(setup_probe,response)
    f=next(root.glob("*.response.json"));f.write_bytes(f.read_bytes()+b" ")
    with pytest.raises(IdentityError):service.evaluate(plan=p,authorization=a,evidence_root=root)


def test_deadline_survives_interrupted_process_without_new_attempt(setup_probe):
    p,root,timer,service,auth=setup_probe;a=auth()
    def interrupt_after_first_pause(seconds):
        if timer.delays:raise KeyboardInterrupt()
        timer.sleep(seconds)
    service.sleep=interrupt_after_first_pause
    with pytest.raises(KeyboardInterrupt):run_probe(setup_probe,response,authorization=a)
    assert len(load_state(root,a)["receipt_sha256s"])==1 and load_state(root,a)["pending"] is None
    # An expired wall-clock scope cannot gain a fresh budget after process restart.
    service.clock=lambda:datetime(2026,10,20,tzinfo=timezone.utc)
    service.sleep=timer.sleep
    _,again=run_probe(setup_probe,lambda _:pytest.fail("expired authorization requested network"),authorization=a)
    assert again.attempts==1
    assert load_state(root,a)["elapsed_seconds"]==a.limits.max_seconds


@pytest.mark.parametrize("gate",["requests","pages","time","body","disk"])
def test_resource_budgets(setup_probe,gate,monkeypatch):
    p,root,timer,service,auth=setup_probe
    limits=ProbeLimits(**{"requests":{"max_requests":1},"pages":{"max_pages":1},"time":{"max_seconds":1},"body":{"response_bytes":10},"disk":{}}[gate])
    a=auth(limits=limits)
    if gate=="disk":
        monkeypatch.setattr("app.services.historical_identity_probe_service.shutil.disk_usage",lambda _:SimpleNamespace(free=1))
        with pytest.raises(IdentityError,match="FREE_SPACE"):run_probe(setup_probe,response,authorization=a)
        return
    _,m=run_probe(setup_probe,response,authorization=a)
    assert m.status=="INCOMPLETE" and m.attempts<=1


def test_cli_four_modes_explicit_decisions(setup_probe,tmp_path):
    p,root,timer,service,auth=setup_probe;out=tmp_path/"out";out.mkdir();path=tmp_path/"plan.json";path.write_bytes(encode(p))
    module=runner();base=["--sample-plan",str(path),"--evidence-dir",str(root),"--output-dir",str(out)]
    explicit=["--expected-plan-sha256",p.plan_sha256]
    def invoke(args,**kw):return module.main(args,stdout=io.StringIO(),stderr=io.StringIO(),probe_service=service,**kw)
    assert invoke(["--mode","PROBE_AUTHORIZE",*base,*explicit,"--secids",p.samples[0].secid])==2
    assert invoke(["--mode","PROBE_AUTHORIZE",*base,*explicit,"--secids",p.samples[0].secid,"--confirm-authorize"])==0
    binding=["--authorization",str(out/"task308a-probe-authorization.json")]
    assert invoke(["--mode","PROBE",*base,*explicit,*binding])==2
    client=HistoricalIdentityProbeClient(transport=httpx.MockTransport(response))
    try:assert invoke(["--mode","PROBE",*base,*explicit,*binding,"--confirm-network"],source_client=client)==0
    finally:client.close()
    assert invoke(["--mode","PROBE_EVALUATE",*base,*binding])==0
    assert (out/"task308a-feasibility-report.json").exists()


def test_static_isolation():
    root=Path(__file__).resolve().parents[1]/"app/services"
    for name in ("historical_identity_feasibility_service.py","historical_identity_probe_service.py","historical_identity_probe_client.py"):
        tree=ast.parse((root/name).read_text())
        imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any("sqlalchemy" in m or "app.models" in m for m in imports)
        calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        assert not {"commit","execute","getenv","sync","record_assertion"}&set(calls)
