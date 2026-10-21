"""Separately authorized synthetic diagnostics; no live source execution."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import pytest
import httpx
from app.schemas.historical_cashflow_source_forensics import DiagnosticLimits, DiagnosticState, DiagnosticResult
from app.services.historical_cashflow_source_forensics_service import HistoricalCashflowSourceForensicsService as Offline, publish
from app.services.historical_cashflow_diagnostic_probe_service import HistoricalCashflowDiagnosticProbeService as Probe, replay
from app.services.historical_cashflow_diagnostic_client import HistoricalCashflowDiagnosticClient, route
from app.services.historical_identity_probe_service import read_model, encode, atomic
from app.services.historical_evidence_normalization import signed
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_cashflow_source_forensics import original, pristine_original, response_body, row


@pytest.fixture
def harness(original,tmp_path):
    package=Offline.forensic_plan(**original.kwargs)
    assert package.status=="COMPLETE",package.report.blockers
    class Harness:
        plan=package.plan
        now=datetime(2026,2,1,tzinfo=timezone.utc)
        elapsed=0
        calls=[]
        waits=[]
        root=tmp_path/"new-private-diagnostic"
        def sleep(self,n):self.elapsed+=n;self.now+=timedelta(seconds=n);self.waits.append(n)
        def service(self,handler):
            def handle(request):self.calls.append(request);return handler(request)
            return Probe(client_factory=lambda:HistoricalCashflowDiagnosticClient(transport=httpx.MockTransport(handle)),sleeper=self.sleep,
                monotonic=lambda:self.elapsed,clock=lambda:self.now)
        def authorize(self,service,**changes):
            values=dict(plan=self.plan,reviewed_plan_sha256=self.plan.plan_sha256,secids=self.plan.secids,tables=self.plan.tables,
                profiles=("BASELINE","TABLE_ONLY"),limits=DiagnosticLimits(),evidence_root=self.root,confirm=True)
            values.update(changes)
            return service.authorize(**values)
        def run(self,handler,**changes):
            service=self.service(handler);auth=self.authorize(service,**changes)
            return service,auth,service.probe(plan=self.plan,authorization=auth,evidence_root=self.root,confirm_network=True)
    h=Harness();h.root.mkdir(mode=0o700);h.original=original
    return h


def handler(request):
    block=request.url.params["iss.only"].split(",")[0]
    sid=request.url.path.rsplit("/",1)[-1][:-5]
    if "," in request.url.params["iss.only"]:
        return httpx.Response(200,content=(f'{{"{block}":{{}},"{block}":{{"metadata":{{}}}}}}').encode())
    return httpx.Response(200,content=response_body(block,rows=[row(sid,None if block=="offers" else "3")],cursor=block!="offers"))


def test_profiles_quarantine_and_offline_replay_are_separate(harness,tmp_path,monkeypatch):
    h=harness;before={p.name:p.read_bytes() for p in h.original.root.iterdir()}
    service,auth,result=h.run(handler)
    assert result.status=="COMPLETE",result.blockers
    assert result.attempts==10 and result.accepted_pages==6 and result.quarantine_pages==4
    assert len(result.observations)==12 and len(list((h.root/"quarantine").glob("*.body")))==2
    assert not any("," in req.url.params["iss.only"] and req.url.params["iss.only"].startswith("offers") for req in h.calls)
    for req in h.calls:
        assert req.url.host=="iss.moex.com" and req.url.scheme=="https"
        assert req.url.params["iss.meta"]=="on" and req.url.params["start"]=="0" and req.url.params["limit"]=="100"
        table=req.url.params["iss.only"].split(",")[0]
        assert req.url.params[table+".start"]=="0"
        assert "authorization" not in req.headers and "cookie" not in req.headers
    assert all(o.inspection.tables==() for o in result.observations if o.inspection.disposition=="QUARANTINE")
    su=next(o for o in result.observations if o.request.secid=="SU24020RMFS8")
    assert su.accepted_current_isin_context=="RU000A100QS2"
    monkeypatch.setattr(HistoricalCashflowDiagnosticClient,"__init__",lambda *a,**k:pytest.fail("offline HTTP constructor"))
    assert Offline.diagnostic_evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)==result
    assert service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)==result
    assert len(h.calls)==10 and before=={p.name:p.read_bytes() for p in h.original.root.iterdir()}
    assert DiagnosticResult.model_validate_json(result.model_dump_json())==result
    output=tmp_path/"diagnostic-output";output.mkdir()
    publish(package=result,output_dir=output,protected_roots=(h.root,h.original.root))
    with pytest.raises(ValueError):result.status="BLOCKED"


@pytest.mark.parametrize("change",[
    {"confirm":False},{"reviewed_plan_sha256":"0"*64},{"secids":["SU24020RMFS8","RU000A0JQAM6","EXTRA"]},
    {"tables":["amortizations"]},{"profiles":["ARBITRARY"]},{"secids":["SYNTH001"]},
])
def test_authorization_refuses_before_http(harness,change):
    h=harness
    with pytest.raises((IdentityError,ValueError)):h.authorize(h.service(lambda _:pytest.fail("network forbidden")),**change)
    assert not h.calls and not list(h.root.iterdir())


def test_network_confirmation_and_destination_are_exact(harness,tmp_path):
    h=harness;service=h.service(lambda _:pytest.fail("network forbidden"));auth=h.authorize(service)
    with pytest.raises(IdentityError):service.probe(plan=h.plan,authorization=auth,evidence_root=h.root)
    other=tmp_path/"other-private";other.mkdir(mode=0o700)
    with pytest.raises(IdentityError):service.probe(plan=h.plan,authorization=auth,evidence_root=other,confirm_network=True)
    with pytest.raises(IdentityError):h.authorize(service,evidence_root=h.original.root)
    assert not h.calls


@pytest.mark.parametrize("budget",["max_requests","max_pages","response_bytes","total_response_bytes"])
def test_strict_resource_limits(harness,budget):
    h=harness
    limits=DiagnosticLimits(**{budget:1 if budget in ("max_requests","max_pages") else 64})
    _,auth,result=h.run(handler,limits=limits)
    assert result.status=="PARTIAL" and len(h.calls)<=limits.max_requests
    if budget in ("max_requests","max_pages"):assert len(h.calls)==1
    assert all(p.stat().st_size<=limits.response_bytes for folder in ("accepted","quarantine") for p in (h.root/folder).glob("*.body"))
    assert result.received_bytes<=limits.total_response_bytes
    assert replay(plan=h.plan,authorization=auth,evidence_root=h.root)[0]==result


def test_quarantine_raw_sha_tampering_blocks(harness):
    h=harness;_,auth,result=h.run(handler)
    path=next((h.root/"quarantine").glob("*.body"));assert hashlib.sha256(path.read_bytes()).hexdigest()==path.stem
    path.write_bytes(b"altered")
    blocked=Offline.diagnostic_evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)
    assert blocked.status=="BLOCKED" and not blocked.pit_ready


def test_unknown_attempt_never_retries_automatically(harness):
    h=harness
    service=h.service(lambda _:(_ for _ in ()).throw(KeyboardInterrupt()))
    auth=h.authorize(service)
    with pytest.raises(KeyboardInterrupt):service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    result=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert result.status=="BLOCKED" and "UNKNOWN_DIAGNOSTIC_INTENT_OUTCOME" in result.blockers and len(h.calls)==1


def test_known_interruption_resume_does_not_refetch_and_deadline_persists(harness):
    h=harness;service=h.service(handler);auth=h.authorize(service)
    service.sleep=lambda _:(_ for _ in ()).throw(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert len(h.calls)==1
    h.now+=timedelta(seconds=91);service.sleep=h.sleep
    result=service.probe(plan=h.plan,authorization=auth,evidence_root=h.root,confirm_network=True)
    assert result.status=="PARTIAL" and len(h.calls)==1 and result.blockers==("GLOBAL_DIAGNOSTIC_BUDGET_STOP",)


@pytest.mark.parametrize("status,retry_count",[(503,3),(429,3),(401,1),(404,1)])
def test_transient_retry_and_error_sanitization(harness,status,retry_count):
    h=harness
    _,auth,result=h.run(lambda _:httpx.Response(status,headers={"Retry-After":"1"},content=b"secret source text"),
        secids=["SU24020RMFS8"],tables=["coupons"],profiles=["BASELINE"])
    assert result.status=="PARTIAL" and len(h.calls)==retry_count
    assert b"secret source text" not in b"".join(p.read_bytes() for p in h.root.iterdir() if p.is_file())
    assert Offline.diagnostic_evaluate(plan=h.plan,authorization=auth,evidence_root=h.root)==result


def test_source_scope_and_offset_cannot_expand(harness):
    from app.schemas.historical_cashflow_source_forensics import DiagnosticRequest
    for values in ({"secid":"EXTRA","table":"coupons","profile":"BASELINE"},
        {"secid":"SU24020RMFS8","table":"amortizations","profile":"BASELINE"}):
        with pytest.raises(ValueError):DiagnosticRequest(**values)
    req=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE")
    with pytest.raises((ValueError,IdentityError)):route(req.model_copy(update={"offset":True}))
    with pytest.raises(ValueError):DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE",offset=False)


def test_static_no_database_legacy_or_existing_mutation_paths():
    import ast
    root=Path(__file__).parents[1]/"app/services"
    for file in ("historical_cashflow_diagnostic_client.py","historical_cashflow_diagnostic_probe_service.py"):
        tree=ast.parse((root/file).read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.ImportFrom):assert not (node.module or "").startswith(("sqlalchemy","app.models","app.database"))
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):assert node.func.attr not in ("sync","commit","flush","getenv","fetch_page")
