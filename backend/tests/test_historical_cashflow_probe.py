"""MockTransport-only strict completeness, retries, evidence and offline replay."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
import httpx
import pytest
from app.schemas.historical_cashflow_feasibility import Request, ProbeState
from app.schemas.historical_identity_feasibility import ProbeLimits
from app.services.historical_cashflow_probe_client import HistoricalCashflowProbeClient, parse_page
from app.services.historical_cashflow_probe_service import HistoricalCashflowProbeService, replay
from app.services.historical_cashflow_feasibility_service import HistoricalCashflowFeasibilityService
from app.services.historical_identity_probe_service import read_model, atomic, encode
from app.services.historical_evidence_normalization import signed
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_cashflow_feasibility import plan_fixture


def body(table="coupons",rows=None,*,cursor=True,index=0,total=None,size=100):
    rows=[] if rows is None else rows
    result={table:{"columns":["SECID","ISIN","DATE","VALUE","CURRENCY","UNIT"],"data":rows}}
    if cursor:result[table+".cursor"]={"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[index,len(rows) if total is None else total,size]]}
    return json.dumps(result).encode()


ROW=["SU24020RMFS8",None,"2025-01-01","2.000000","RUB",None]


@pytest.fixture
def harness(tmp_path,monkeypatch):
    monkeypatch.setattr(shutil,"disk_usage",lambda p:shutil._ntuple_diskusage(100*1024**3,0,100*1024**3))
    class Harness:
        now=datetime(2026,1,1,tzinfo=timezone.utc)
        elapsed=0
        calls=[]
        waits=[]
        root=tmp_path
        plan=plan_fixture()
        def sleep(self,n):
            self.waits.append(n);self.elapsed+=n;self.now+=timedelta(seconds=n)
        def service(self,handler):
            def handle(request):self.calls.append(request);return handler(request)
            return HistoricalCashflowProbeService(client_factory=lambda:HistoricalCashflowProbeClient(transport=httpx.MockTransport(handle)),
                clock=lambda:self.now,monotonic=lambda:self.elapsed,sleeper=self.sleep)
        def auth(self,service,*,tables=("coupons",),limits=None):
            return service.authorize(plan=self.plan,reviewed_plan_sha256=self.plan.plan_sha256,secids=["SU24020RMFS8"],tables=tables,
                limits=limits or ProbeLimits(),evidence_root=self.root,confirm=True)
        def run(self,handler,**kwargs):
            service=self.service(handler);auth=self.auth(service,**kwargs)
            result=service.probe(plan=self.plan,authorization=auth,evidence_root=self.root,confirm_network=True)
            return service,auth,result
    return Harness()


def test_four_tables_exact_route_empty_and_replay(harness):
    h=harness
    service,auth,result=h.run(lambda r:httpx.Response(200,content=body(r.url.params["iss.only"].split(",")[0],[])),tables=("coupons","amortizations","redemptions","offers"))
    assert result.status=="COMPLETE" and result.attempts==4
    for req in h.calls:
        table=req.url.params["iss.only"].split(",")[0]
        assert str(req.url).startswith("https://iss.moex.com/iss/statistics/engines/stock/markets/bonds/bondization/SU24020RMFS8.json?")
        assert dict(req.url.params)=={"iss.meta":"on","start":"0","limit":"100","iss.only":table+","+table+".cursor",table+".start":"0"}
        assert "authorization" not in req.headers and "cookie" not in req.headers
    evaluation=HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)
    assert all(p.execution=="COMPLETE" and p.raw_rows==0 for p in evaluation.partitions)
    assert "SOURCE_TABLE_EMPTY_COMPLETE" in evaluation.partitions[0].diagnostics
    snapshot={p.name:p.read_bytes() for p in h.root.iterdir() if p.is_file()}
    assert service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)==result
    assert len(h.calls)==4 and snapshot=={p.name:p.read_bytes() for p in h.root.iterdir() if p.is_file()}


@pytest.mark.parametrize("rows",[[],[ROW]])
def test_no_cursor_never_empty_success(harness,rows):
    h=harness
    _,auth,r=h.run(lambda _:httpx.Response(200,content=body(rows=rows,cursor=False)))
    assert r.status=="PARTIAL" and len(h.calls)==1
    e=HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)
    assert e.partitions[0].execution=="INCOMPLETE" and "COMPLETENESS_UNPROVEN" in e.partitions[0].diagnostics
    assert len(e.events)==len(rows) and e.partitions[1].execution=="NOT_REQUESTED"


def test_cursor_pages_duplicates_and_conflicts_preserved(harness):
    h=harness
    def handler(r):
        start=int(r.url.params["start"])
        return httpx.Response(200,content=body(rows=[ROW,ROW] if start==0 else [ROW,ROW[:3]+["3"]+ROW[4:]],index=start,total=4,size=2))
    _,auth,result=h.run(handler)
    assert result.status=="COMPLETE" and len(h.calls)==2
    e=HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)
    assert len(e.events)==4 and len(e.duplicate_groups)==1 and len(e.duplicate_groups[0][1])==3
    assert e.conflicting_event_groups and e.economic_qualification=="CONFLICTING"
    assert all(x.amount in (Decimal(2),Decimal(3)) for x in e.events)


@pytest.mark.parametrize("mode",["repeat","missing","total","size","offset","bool"])
def test_cursor_failures_do_not_complete(harness,mode):
    h=harness
    def handler(r):
        start=int(r.url.params["start"])
        if not start:return httpx.Response(200,content=body(rows=[ROW],total=2,size=1))
        data=body(rows=[ROW] if mode=="repeat" else [ROW[:3]+["3"]+ROW[4:]],cursor=mode!="missing",index=0 if mode=="offset" else start,
            total=3 if mode=="total" else 2,size=2 if mode=="size" else True if mode=="bool" else 1)
        return httpx.Response(200,content=data)
    _,auth,result=h.run(handler)
    assert result.status=="PARTIAL" and len(h.calls)==2
    assert HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root).status!="BLOCKED"


def test_transient_retries_and_three_attempt_bound(harness):
    h=harness
    _,auth,r=h.run(lambda _:httpx.Response(503,headers={"Retry-After":"1"}))
    assert len(h.calls)==3 and r.accepted_pages==0 and h.waits==[1,1]
    assert replay(h.plan,auth,h.root)[0].status=="PARTIAL"


def test_transient_success_and_secret_sanitization(harness):
    h=harness
    def handler(r):
        if len(h.calls)==1:raise httpx.ReadTimeout("secret password response-body",request=r)
        return httpx.Response(200,content=body(rows=[ROW]))
    _,auth,result=h.run(handler)
    assert result.status=="COMPLETE" and len(h.calls)==2
    assert b"secret" not in b"".join(p.read_bytes() for p in h.root.iterdir() if p.is_file())
    assert h.waits==[.5]


@pytest.mark.parametrize("status",[400,401,403,404])
def test_nonretryable(harness,status):
    h=harness
    _,_,r=h.run(lambda _:httpx.Response(status,content=b"secret"))
    assert len(h.calls)==1 and r.accepted_pages==0


def test_authorization_and_network_guards_before_client(harness):
    h=harness
    service=h.service(lambda _:pytest.fail("HTTP forbidden"))
    auth=h.auth(service)
    with pytest.raises(IdentityError):service.probe(plan=h.plan,authorization=auth,evidence_root=h.root)
    with pytest.raises(IdentityError):service.authorize(plan=h.plan,reviewed_plan_sha256="0"*64,secids=["SU24020RMFS8"],tables=["coupons"],limits=ProbeLimits(),evidence_root=h.root,confirm=True)
    with pytest.raises(IdentityError):service.authorize(plan=h.plan,reviewed_plan_sha256=h.plan.plan_sha256,secids=["MVZ220004019"],tables=["coupons"],limits=ProbeLimits(),evidence_root=h.root,confirm=True)
    assert not h.calls


def test_pending_unknown_and_tampering_refused(harness):
    h=harness
    service,auth,_=h.run(lambda _:httpx.Response(200,content=body(rows=[ROW])))
    state=read_model(h.root/"b2-state.json",ProbeState)
    pending=signed(ProbeState,"state_sha256",authorization_sha256=state.authorization_sha256,receipt_sha256s=state.receipt_sha256s,
        deadline_at=state.deadline_at,pending_sequence=2)
    atomic(h.root/"b2-state.json",encode(pending))
    r=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert r.status=="BLOCKED" and "UNKNOWN_INTENT_OUTCOME" in r.blockers and len(h.calls)==1
    atomic(h.root/"b2-state.json",encode(state))
    raw=next(h.root.glob("*.body"));raw.write_bytes(b"tampered")
    assert HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root).status=="BLOCKED"


def test_budget_and_resume_deadline(harness):
    h=harness
    service,auth,r=h.run(lambda _:httpx.Response(200,content=body(rows=[ROW],total=3,size=1)),limits=ProbeLimits(max_requests=1))
    assert r.status=="PARTIAL" and len(h.calls)==1
    h.now+=timedelta(seconds=400)
    r=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert r.status=="PARTIAL" and len(h.calls)==1


def test_response_and_space_limits(harness,monkeypatch):
    h=harness
    _,_,r=h.run(lambda _:httpx.Response(200,content=b"x"*1000),limits=ProbeLimits(response_bytes=64))
    assert r.status=="PARTIAL" and len(h.calls)==1
    monkeypatch.setattr(shutil,"disk_usage",lambda _:shutil._ntuple_diskusage(1,0,1))
    with pytest.raises(IdentityError):h.auth(h.service(lambda _:pytest.fail()))


def test_runner_arbitrary_cwd_and_no_unconfirmed_network(tmp_path):
    script=Path(__file__).parents[2]/"scripts/historical_cashflow_feasibility.py"
    run=subprocess.run([sys.executable,str(script),"--mode","PLAN"],cwd=tmp_path,capture_output=True)
    assert run.returncode==2 and json.loads(run.stdout)["status"]=="BLOCKED"
    assert not list(tmp_path.iterdir())


def test_known_interruption_resume_and_deadline(harness):
    h=harness
    def handler(r):
        offset=int(r.url.params["start"])
        return httpx.Response(200,content=body(rows=[ROW[:3]+[str(offset+1)]+ROW[4:]],index=offset,total=2,size=1))
    service=h.service(handler);auth=h.auth(service)
    def interrupt(n):raise KeyboardInterrupt()
    service.sleep=interrupt
    with pytest.raises(KeyboardInterrupt):service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert len(h.calls)==1 and read_model(h.root/"b2-state.json",ProbeState).pending_sequence is None
    service.sleep=h.sleep
    result=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert result.status=="COMPLETE" and [r.url.params["start"] for r in h.calls]==["0","1"]


def test_downtime_expires_without_repeating_accepted(harness):
    h=harness
    service=h.service(lambda r:httpx.Response(200,content=body(rows=[ROW],total=2,size=1)))
    auth=h.auth(service)
    service.sleep=lambda _:(_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    h.now+=timedelta(seconds=301)
    result=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert result.status=="PARTIAL" and result.blockers==("GLOBAL_BUDGET_STOP",) and len(h.calls)==1
    assert replay(h.plan,auth,h.root)[0]==result


@pytest.mark.parametrize("gate",["max_pages","total_response_bytes","evidence_bytes"])
def test_other_global_budgets(harness,gate):
    h=harness
    limits=ProbeLimits(**{gate:1 if gate=="max_pages" else 600 if gate=="total_response_bytes" else 100})
    if gate=="evidence_bytes":
        with pytest.raises(IdentityError):h.auth(h.service(lambda _:pytest.fail()),limits=limits)
        return
    _,auth,result=h.run(lambda r:httpx.Response(200,content=body(rows=[ROW[:3]+[r.url.params["start"]]+ROW[4:]],index=int(r.url.params["start"]),total=10,size=1)),limits=limits)
    assert result.status=="PARTIAL" and len(h.calls)<=3
    assert replay(h.plan,auth,h.root)[0]==result


@pytest.mark.parametrize("payload",[b"not json",b'{"coupons":{},"coupons":{}}',b'{"coupons":{"columns":["VALUE","value"],"data":[]}}',
    b'{"coupons":{"columns":["VALUE"],"data":[[1,2]]}}'])
def test_malformed_not_successful_empty(harness,payload):
    h=harness
    _,auth,result=h.run(lambda _:httpx.Response(200,content=payload))
    assert result.status=="PARTIAL" and result.accepted_pages==0 and len(h.calls)==1
    assert not list(h.root.glob("*.body"))
    receipt=json.loads(next(h.root.glob("receipt-*.json")).read_bytes())
    assert receipt["response_sha256"]==hashlib.sha256(payload).hexdigest()
    evaluation=HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)
    assert evaluation.partitions[0].execution=="SOURCE_ERROR"


def test_receipt_tampering_and_scope_change(harness):
    h=harness
    service,auth,_=h.run(lambda _:httpx.Response(200,content=body(rows=[ROW])))
    receipt=next(h.root.glob("receipt-*.json"))
    receipt.write_bytes(receipt.read_bytes().replace(b'"row_count":1',b'"row_count":2'))
    result=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert result.status=="BLOCKED" and len(h.calls)==1
    other=h.root/"other";other.mkdir()
    with pytest.raises(IdentityError):service.probe(plan=h.plan,authorization=auth,evidence_root=other,confirm_network=True)


def test_unreferenced_journal_file_blocks_evaluation(harness):
    h=harness
    _,auth,_=h.run(lambda _:httpx.Response(200,content=body(rows=[ROW])))
    (h.root/"intent-003.json").write_bytes(b"{}")
    evaluation=HistoricalCashflowFeasibilityService.evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)
    assert evaluation.status=="BLOCKED" and evaluation.blockers==("UNRECONCILED_JOURNAL_FILES",)
