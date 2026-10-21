"""Synthetic original journal reconciliation and lossless diagnostic inspection."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import httpx
import pytest
from app.schemas.historical_cashflow_source_forensics import OriginalHashes, DiagnosticRequest, ForensicPackage
from app.schemas.historical_cashflow_feasibility import PilotPlan
from app.schemas.historical_identity_feasibility import ProbeLimits
from app.services.historical_cashflow_probe_client import HistoricalCashflowProbeClient
from app.services.historical_cashflow_probe_service import HistoricalCashflowProbeService
from app.services.historical_cashflow_feasibility_service import HistoricalCashflowFeasibilityService
from app.services.historical_cashflow_diagnostic_client import inspect_body
from app.services.historical_cashflow_source_forensics_service import HistoricalCashflowSourceForensicsService as Service, publish
from app.services.historical_identity_probe_service import encode
from app.services.historical_evidence_normalization import signed
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_cashflow_feasibility import candidate, plan_fixture


def response_body(table, *, rows=None, cursor=True, metadata=None):
    rows=[] if rows is None else rows
    result={table:{"columns":["SECID","ISIN","DATE","VALUE","CURRENCY","VALUEPRC","PRICE"],"data":rows,
        "metadata":metadata or {"VALUE":{"type":"double"}}}}
    if cursor:result[table+".cursor"]={"columns":["INDEX","TOTAL","PAGESIZE"],"data":[[0,len(rows),100]]}
    return json.dumps(result,separators=(",",":"),ensure_ascii=True).encode()


def row(secid="SU24020RMFS8", amount="2"):
    return [secid,None,"2026-01-01",amount,"RUB","1",None]


def make_original(tmp_path,monkeypatch):
    monkeypatch.setattr(shutil,"disk_usage",lambda _:SimpleNamespace(free=100*1024**3))
    inputs=tmp_path/"original-inputs";inputs.mkdir()
    root=tmp_path/"original-evidence";root.mkdir()
    ids=("RU000A0JQAM6","SU24020RMFS8","SYNTH001","SYNTH002","SYNTH003","SYNTH004")
    items=tuple(candidate(s,"RU000A100QS2" if s=="SU24020RMFS8" else "US0378331005").model_copy(update={"selection_order":i+1}) for i,s in enumerate(sorted(ids)))
    base=plan_fixture()
    plan=signed(PilotPlan,"plan_sha256",status="PLANNED",requested_size=6,cohort=items,upstream_hashes=base.upstream_hashes)
    now=[datetime(2026,1,2,tzinfo=timezone.utc)]
    def sleep(n):now[0]+=timedelta(seconds=n)
    service=HistoricalCashflowProbeService(clock=lambda:now[0],sleeper=sleep)
    auth=service.authorize(plan=plan,reviewed_plan_sha256=plan.plan_sha256,secids=list(ids),tables=["coupons","amortizations","redemptions","offers"],
        limits=ProbeLimits(),evidence_root=root,confirm=True)
    def handler(req):
        table=req.url.params["iss.only"].split(",")[0];sid=req.url.path.rsplit("/",1)[-1][:-5]
        if table in ("coupons","redemptions"):
            return httpx.Response(200,content=(f'{{"{table}":{{}},"{table}":{{}}}}').encode())
        rows=[row(sid)] if table=="amortizations" else [row(sid,None)] if sid=="RU000A0JQAM6" else [row(sid,None)]*4 if sid=="SYNTH001" else []
        return httpx.Response(200,content=response_body(table,rows=rows,cursor=table=="amortizations"))
    service.client_factory=lambda:HistoricalCashflowProbeClient(transport=httpx.MockTransport(handler))
    result=service.probe(plan=plan,authorization=auth,evidence_root=root,confirm_network=True)
    assert result.status=="PARTIAL",result.blockers
    evaluation=HistoricalCashflowFeasibilityService.evaluate(plan=plan,authorization=auth,evidence_root=root)
    assert evaluation.status=="CASHFLOW_SOURCE_FEASIBLE_WITH_LIMITATIONS"
    expected=OriginalHashes(plan_sha256=plan.plan_sha256,authorization_sha256=auth.authorization_sha256,
        probe_sha256=result.probe_sha256,evaluation_sha256=evaluation.report_sha256)
    for name,value in (("plan",plan),("authorization",auth),("evaluation",evaluation)):(inputs/(name+".json")).write_bytes(encode(value))
    kwargs=dict(b2_plan=inputs/"plan.json",b2_authorization=inputs/"authorization.json",b2_evidence_root=root,
        b2_evaluation=inputs/"evaluation.json",expected=expected,secids=["SU24020RMFS8","RU000A0JQAM6"])
    return SimpleNamespace(root=root,inputs=inputs,kwargs=kwargs,expected=expected,plan=plan,auth=auth,result=result,evaluation=evaluation)


@pytest.fixture(scope="module")
def pristine_original(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        return make_original(tmp_path_factory.mktemp("r1-original"),patch)


@pytest.fixture
def original(pristine_original,monkeypatch):
    monkeypatch.setattr(shutil,"disk_usage",lambda _:SimpleNamespace(free=100*1024**3))
    before={p:p.read_bytes() for folder in (pristine_original.root,pristine_original.inputs) for p in folder.iterdir()}
    yield pristine_original
    # Deliberate tampering tests restore only their isolated synthetic fixture.
    for p,body in before.items():
        if not p.exists() or p.read_bytes()!=body:p.write_bytes(body)


def test_original_partial_reconciles_and_missing_bodies_remain_missing(original,tmp_path,monkeypatch):
    before={p.name:p.read_bytes() for p in original.root.iterdir()}
    monkeypatch.setattr(HistoricalCashflowProbeClient,"__init__",lambda *a,**k:pytest.fail("offline constructed HTTP client"))
    package=Service.forensic_plan(**original.kwargs)
    assert package.status=="COMPLETE",package.report.blockers
    assert package==Service.forensic_plan(**original.kwargs)
    assert sum(o.attempts for o in package.report.outcomes)==24
    assert sum(o.accepted_pages for o in package.report.outcomes)==12
    assert sum(o.missing_rejected_bodies for o in package.report.outcomes)==12
    assert sum(o.raw_rows for o in package.report.outcomes)==11
    assert len(package.plan.work)==10 and len(package.plan.reused)==2
    assert all(not(r.profile=="BASELINE" and r.table=="offers") for r in package.plan.work)
    assert [r.profile for r in package.plan.work[:4]]==["BASELINE"]*4
    assert package.report.original_evaluation_status==original.evaluation.status
    assert not any("amortizations"==r.table for r in package.plan.work)
    assert any(o.request.table=="amortizations" and o.inspection.tables for o in package.report.observations)
    assert before=={p.name:p.read_bytes() for p in original.root.iterdir()}
    assert ForensicPackage.model_validate_json(package.model_dump_json())==package
    output=tmp_path/"forensic-output";output.mkdir()
    publish(package=package,output_dir=output,protected_roots=(original.root,original.inputs))
    assert (output/"task308b2r1-source-forensics.json").exists()
    assert (output/"task308b2r1-diagnostic-plan.json").exists()
    publish(package=package,output_dir=output,protected_roots=(original.root,))
    with pytest.raises(IdentityError):publish(package=package,output_dir=original.root)
    with pytest.raises(IdentityError):publish(package=package,output_dir=original.inputs)
    summary=output/"task308b2r1-source-forensics-summary.md";summary.write_bytes(b"conflicting output")
    with pytest.raises(IdentityError):publish(package=package,output_dir=output)
    assert summary.read_bytes()==b"conflicting output"


@pytest.mark.parametrize("damage",["expected","evaluation","reevaluation","raw","receipt","scope"])
def test_original_tampering_and_scope_block(original,damage):
    kwargs=dict(original.kwargs)
    if damage=="expected":kwargs["expected"]=original.expected.model_copy(update={"probe_sha256":"0"*64})
    elif damage=="evaluation":kwargs["b2_evaluation"].write_bytes(b"{}")
    elif damage=="reevaluation":
        old=original.evaluation
        changed=signed(type(old),"report_sha256",**{k:getattr(old,k) for k in type(old).model_fields if k not in ("report_sha256","status")},status="SOURCE_FEASIBILITY_UNPROVEN")
        kwargs["b2_evaluation"].write_bytes(encode(changed))
        kwargs["expected"]=original.expected.model_copy(update={"evaluation_sha256":changed.report_sha256})
    elif damage=="raw":next(original.root.glob("*.body")).write_bytes(b"corrupt")
    elif damage=="receipt":next(original.root.glob("receipt-*.json")).write_bytes(b"{}")
    else:kwargs["secids"]=["SYNTH001"]
    package=Service.forensic_plan(**kwargs)
    assert package.status=="BLOCKED" and package.report.outcomes==()
    assert not package.report.pit_ready and not package.plan.acquisition_ready


@pytest.mark.parametrize("body,area,comparison",[
    (b'{"coupons":{},"coupons":{}}',"ROOT","STRUCTURALLY_IDENTICAL"),
    (b'{"coupons":{"metadata":{"VALUE":{"type":"double","type":"string"}}}}',"METADATA","DIFFERENT"),
    (b'{"coupons":{"data":[{"VALUE":1,"VALUE":2}]}}',"DATA","DIFFERENT"),
])
def test_lossless_duplicates(body,area,comparison):
    request=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE")
    result=inspect_body(body=body,request=request)
    assert result.disposition=="QUARANTINE" and result.body_sha256==hashlib.sha256(body).hexdigest()
    assert len(result.duplicates)==1 and result.duplicates[0].area==area
    assert result.duplicates[0].comparison==comparison and result.duplicates[0].occurrences==2
    assert result.tables==() and not result.financial_events_created


@pytest.mark.parametrize("cursor,rows,complete",[(False,[],False),(False,[row()],False),(True,[],True),(True,[row()],True)])
def test_offer_cursor_units_and_missing_amount(cursor,rows,complete):
    old=repr(getcontext())
    rows=[r[:3]+[None]+r[4:] for r in rows]
    result=inspect_body(body=response_body("offers",rows=rows,cursor=cursor),
        request=DiagnosticRequest(secid="SU24020RMFS8",table="offers",profile="TABLE_ONLY"))
    assert result.disposition=="ACCEPTED"
    assert (result.completeness=="COMPLETE")==complete
    assert result.pagination=="NOT_EVALUATED" and result.unit_authority=="UNIT_AUTHORITY_UNPROVEN"
    if rows:assert json.loads(result.tables[0].rows_json[0])[3] is None
    assert not result.actual_payment_confirmed and not result.total_return_execution_ready
    assert repr(getcontext())==old


def test_explicit_unit_claim_is_not_documented_and_key_display_is_bounded():
    result=inspect_body(body=response_body("coupons",rows=[row()],metadata={"VALUE":{"unit":"currency per bond"}}),
        request=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE"))
    assert result.unit_authority=="UNIT_AUTHORITY_PARTIAL"
    key="x"*200+"\n"
    body=("{"+json.dumps(key)+":1,"+json.dumps(key)+":2}").encode()
    d=inspect_body(body=body,request=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE")).duplicates[0]
    assert len(d.key_display)==128 and d.key_sha256==hashlib.sha256(key.encode()).hexdigest()


def test_identifiers_not_filled_or_normalized_and_offer_amount_remains_absent():
    request=DiagnosticRequest(secid="SU24020RMFS8",table="offers",profile="TABLE_ONLY")
    result=inspect_body(body=response_body("offers",rows=[row("OTHER",None)],cursor=False),request=request,expected_isin="RU000A100QS2")
    table=result.tables[0]
    assert table.returned_secids==("OTHER",) and table.returned_isins==(None,)
    assert "SOURCE_IDENTITY_CONFLICT" in result.diagnostics and "MONETARY_AMOUNT_NOT_SUPPLIED" in result.diagnostics
    assert result.unit_authority=="UNIT_AUTHORITY_UNPROVEN"


def test_bounded_inspection_preserves_uncertainty_without_financial_output():
    body=b"["*100+b"0"+b"]"*100
    result=inspect_body(body=body,request=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE"))
    assert result.disposition=="QUARANTINE" and "DUPLICATE_LOCATION_UNKNOWN" in result.diagnostics
    assert result.body_sha256==hashlib.sha256(body).hexdigest() and result.tables==()


def test_malformed_numeric_json_does_not_change_decimal_context():
    before=repr(getcontext())
    result=inspect_body(body=b'{"value":1e99999999999999999999999999999}',
        request=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE"))
    assert result.disposition=="QUARANTINE" and repr(getcontext())==before


def test_schema_rejected_quarantine_exports_structure_not_economic_rows():
    body=response_body("coupons",rows=[row(amount={"nested":"invalid scalar"})])
    result=inspect_body(body=body,request=DiagnosticRequest(secid="SU24020RMFS8",table="coupons",profile="BASELINE"))
    assert result.disposition=="QUARANTINE" and result.tables[0].row_count==1
    assert result.tables[0].rows_json==() and result.unit_authority=="UNIT_AUTHORITY_UNPROVEN"
    assert not result.financial_events_created


def test_runner_errors_are_sanitized_and_import_path_independent(tmp_path):
    script=Path(__file__).parents[2]/"scripts/historical_cashflow_source_forensics.py"
    run=subprocess.run([sys.executable,str(script),"--mode","FORENSIC_PLAN"],cwd=tmp_path,capture_output=True)
    assert run.returncode==2 and json.loads(run.stdout)["status"]=="BLOCKED"
    assert json.loads(run.stdout)["result"] is None
    assert not list(tmp_path.iterdir())


def test_offline_runner_success_has_same_wrapper_shape_and_no_transport(original,tmp_path):
    script=Path(__file__).parents[2]/"scripts/historical_cashflow_source_forensics.py"
    output=tmp_path/"runner-output";output.mkdir()
    expected=original.expected
    args=[str(script),"--mode","FORENSIC_PLAN","--b2-plan",str(original.kwargs["b2_plan"]),
        "--b2-authorization",str(original.kwargs["b2_authorization"]),"--b2-evidence-root",str(original.root),
        "--b2-evaluation",str(original.kwargs["b2_evaluation"]),"--expected-b2-plan-sha256",expected.plan_sha256,
        "--expected-b2-authorization-sha256",expected.authorization_sha256,"--expected-b2-probe-sha256",expected.probe_sha256,
        "--expected-b2-evaluation-sha256",expected.evaluation_sha256,"--secids","SU24020RMFS8","RU000A0JQAM6","--output",str(output)]
    # The subprocess cannot accidentally create an HTTP client or application DB connection.
    code="import sys,runpy,httpx,sqlalchemy; deny=lambda *a,**k:(_ for _ in ()).throw(AssertionError('offline access forbidden')); httpx.Client.__init__=deny; sqlalchemy.engine.Engine.connect=deny; sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    result=subprocess.run([sys.executable,"-c",code,*args],cwd=tmp_path,capture_output=True)
    assert result.returncode==0,result.stdout
    wrapper=json.loads(result.stdout)
    assert wrapper["status"]=="COMPLETE" and wrapper["operation"]=="FORENSIC_PLAN" and wrapper["result"]["report"]["expected"]["probe_sha256"]==expected.probe_sha256
    assert wrapper["blockers"]==[] and wrapper["pit_ready"] is False


def test_no_db_network_execution_or_mutations_in_offline_service():
    import ast
    path=Path(__file__).parents[1]/"app/services/historical_cashflow_source_forensics_service.py"
    tree=ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom):assert not (node.module or "").startswith(("sqlalchemy","app.models","app.database"))
        if isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute):
            assert node.func.attr not in ("sync","commit","getenv","fetch","probe")
            if node.func.attr=="flush":assert isinstance(node.func.value,ast.Name) and node.func.value.id=="stream"


def test_diagnostic_transport_does_not_replay_server_cookies():
    from app.services.historical_cashflow_diagnostic_client import HistoricalCashflowDiagnosticClient
    calls=[]
    def handle(request):
        calls.append(request)
        return httpx.Response(200,content=response_body("offers"),headers={"Set-Cookie":"opaque_session=secret; Path=/"})
    client=HistoricalCashflowDiagnosticClient(transport=httpx.MockTransport(handle))
    try:
        request=DiagnosticRequest(secid="SU24020RMFS8",table="offers",profile="TABLE_ONLY")
        for _ in range(2):client.fetch(request,response_limit=2*1024**2,bytes_remaining=2*1024**2,seconds_remaining=10,check_time=lambda:None)
    finally:client.close()
    assert len(calls)==2 and all("cookie" not in request.headers and "authorization" not in request.headers for request in calls)
