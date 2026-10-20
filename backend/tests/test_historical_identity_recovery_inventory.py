"""Task308B1 source-bound inventory and exception policy, synthetic evidence only."""
from datetime import datetime, timezone
from decimal import getcontext
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
import pytest
import httpx
from pydantic import ValidationError
from app.schemas.historical_identity_recovery_inventory import InventoryLimits, Expectations, InventoryRow
from app.services.historical_identity_recovery_inventory_service import syntax, row_for, select_exceptions
from app.services.historical_identity_recovery_artifacts import verify_inputs, publish, NAMES, file_sha
from app.services.historical_identity_feasibility_service import HistoricalIdentityFeasibilityService, IdentityError, isin_valid
from app.services.historical_identity_probe_service import HistoricalIdentityProbeService, encode
from app.services.historical_identity_probe_client import HistoricalIdentityProbeClient
from app.services.historical_audit_canonical_json import digest
from test_historical_identity_feasibility import make_report, reseal, DATES


SPECIAL={"SU24018RMFS2":"RU000A0JV7K7","SU24020RMFS8":"RU000A100QS2","US0378331005":"US0378331005","MVZ220004019":None}


def valid_id(i):
    head="ZZ"+f"{i:09}"
    return next(head+str(d) for d in range(10) if isin_valid(head+str(d)))


def reconcile_report(report,items):
    items=tuple(sorted(items,key=lambda r:(r.secid,r.isin or "")))
    secids={r.secid for r in items};all_boards={(r.secid,b) for r in items for b in r.listing_boards+r.observed_boards}
    values=dict(identity_records=len(items)+sum(len(r.observed_boards) for r in items),distinct_secids=len(secids),
        distinct_isins=len({r.isin for r in items if r.isin}),exact_pairs=len(items),secid_board_pairs=len(all_boards),
        listing_pairs=sum(r.listing for r in items),observed_pairs=sum(r.observed for r in items),
        listing_only_pairs=sum(r.listing and not r.observed for r in items),observed_only_pairs=sum(r.observed and not r.listing for r in items),
        shared_pairs=sum(r.observed and r.listing for r in items),missing_isin_pairs=sum(r.isin is None for r in items),
        affected_missing_isin_secids=len({r.secid for r in items if r.isin is None}),conflicting_secids=len({r.secid for r in items if r.conflicting_isin_candidates}),
        multi_board_pairs=sum(len(set(r.listing_boards+r.observed_boards))>1 for r in items),not_observed_on_last_market_date_pairs=sum(r.observed and r.last_observed_date!=DATES[-1] for r in items))
    return reseal(report,universe_coverage=report.universe_coverage.model_copy(update=values),
        identity_coverage=report.identity_coverage.model_copy(update={"identities":items}))


def source_report(n=72):
    r=make_report(n);ids=[]
    for i in range(n-4):
        value=valid_id(i)
        if i<min(99,n//3):value=value[:-1]+str((int(value[-1])+1)%10)
        ids.append(value)
    ids+=list(SPECIAL)
    items=[]
    for i,(old,sid) in enumerate(zip(r.identity_coverage.identities,ids)):
        updates={"secid":sid,"first_listing_interval":DATES[0]}
        if sid=="MVZ220004019":updates["first_listing_interval"]=None
        items.append(old.model_copy(update=updates))
    return reconcile_report(r,items)


def prepare_evidence(tmp_path,monkeypatch,n=72):
    monkeypatch.setattr("app.services.historical_identity_probe_service.shutil.disk_usage",lambda _:SimpleNamespace(free=30*1024**3))
    inputs=tmp_path/"inputs";inputs.mkdir();probe=tmp_path/"probe";probe.mkdir();out=tmp_path/"out";out.mkdir()
    report=source_report(n);plan=HistoricalIdentityFeasibilityService().build(report=report)
    assert set(SPECIAL)<={s.secid for s in plan.samples}
    selected=list(SPECIAL)+[s.secid for s in plan.samples if s.secid not in SPECIAL][:8]
    service=HistoricalIdentityProbeService(sleeper=lambda _:None,clock=lambda:datetime(2026,10,19,tzinfo=timezone.utc))
    authorization=service.authorize(plan=plan,expected_plan_sha256=plan.plan_sha256,secids=selected,sources=["REFERENCE","DESCRIPTION"],destination=probe,explicit_authorization=True)
    def handler(request):
        sid=request.url.params.get("q") or request.url.path.split("/")[-1][:-5]
        isin=SPECIAL.get(sid,"US0378331005")
        if "q" in request.url.params:
            rows=[] if request.url.params["start"]!="0" else [[sid,isin,"stock_bonds"]]
            return httpx.Response(200,json={"securities":{"columns":["secid","isin","type"],"data":rows}})
        return httpx.Response(200,json={"description":{"columns":["name","value"],"data":[["SECID",sid],["ISIN",isin]]}})
    client=HistoricalIdentityProbeClient(transport=httpx.MockTransport(handler))
    try:manifest=service.probe(plan=plan,authorization=authorization,expected_plan_sha256=plan.plan_sha256,destination=probe,confirm_network=True,source_client=client)
    finally:client.close()
    evaluation=service.evaluate(plan=plan,authorization=authorization,evidence_root=probe)
    for name,value in (("task307",report),("plan",plan),("authorization",authorization),("evaluation",evaluation)):(inputs/(name+".json")).write_bytes(encode(value))
    kwargs=dict(task307_report=inputs/"task307.json",task308a_plan=inputs/"plan.json",task308a_authorization=inputs/"authorization.json",task308a_evidence_root=probe,task308a_evaluation=inputs/"evaluation.json")
    # B1 must not instantiate a transport, even when replaying formerly live evidence.
    monkeypatch.setattr(HistoricalIdentityProbeClient,"__init__",lambda *a,**k:pytest.fail("offline operation constructed HTTP client"))
    return SimpleNamespace(report=report,plan=plan,authorization=authorization,manifest=manifest,evaluation=evaluation,kwargs=kwargs,output=out,probe=probe)


@pytest.fixture(scope="module")
def pristine_evidence(tmp_path_factory):
    with pytest.MonkeyPatch.context() as patch:
        return prepare_evidence(tmp_path_factory.mktemp("task308b1-pristine"),patch)


@pytest.fixture
def evidence(tmp_path,monkeypatch,pristine_evidence):
    original=pristine_evidence
    shutil.copytree(original.probe,tmp_path/"probe")
    shutil.copytree(original.kwargs["task307_report"].parent,tmp_path/"inputs")
    output=tmp_path/"out";output.mkdir()
    kwargs={key:tmp_path/"probe" if key=="task308a_evidence_root" else tmp_path/"inputs"/value.name for key,value in original.kwargs.items()}
    monkeypatch.setattr("app.services.historical_identity_probe_service.shutil.disk_usage",lambda _:SimpleNamespace(free=30*1024**3))
    monkeypatch.setattr(HistoricalIdentityProbeClient,"__init__",lambda *a,**k:pytest.fail("offline operation constructed HTTP client"))
    return SimpleNamespace(report=original.report,plan=original.plan,authorization=original.authorization,manifest=original.manifest,
        evaluation=original.evaluation,kwargs=kwargs,output=output,probe=tmp_path/"probe")


def test_inventory_known_bindings_orthogonal_counts_and_immutability(evidence):
    e=evidence;before={p:file_sha(p,128*1024**2) for p in e.probe.iterdir()}
    verified=verify_inputs(**e.kwargs,expectations=Expectations(total_secids=72,probed=12,current_bindings=11))
    rows=list(verified.analysis.iter_inventory());by={r.secid:r for r in rows}
    assert len(by)==len(rows)==72 and [r.secid for r in rows]==sorted(by)
    for secid,isin in SPECIAL.items():
        row=by[secid];assert row.confirmed_current_isin==isin
        assert row.original_identities[0].isin is None and not row.pit_ready and not row.historical_identity_at_decision_proven
        assert row.source_observation_times and all(r.complete_response for r in row.source_results)
    assert by["SU24018RMFS2"].secid_equals_current_isin is False
    assert by["SU24020RMFS8"].secid_equals_current_isin is False
    assert by["US0378331005"].secid_equals_current_isin is True
    assert by["MVZ220004019"].current_binding=="EXACT_RECORD_WITHOUT_ISIN"
    assert by["MVZ220004019"].next_investigation=="OFFICIAL_ALTERNATIVE_IDENTITY_SOURCE_REQUIRED"
    unprobed=next(r for r in rows if not r.previously_probed and r.checksum_valid)
    assert unprobed.confirmed_current_isin is None and unprobed.current_binding=="NOT_PROBED"
    assert all(r.joint_execution_coverage=="NOT_ESTABLISHED" for r in rows)
    assert before=={p:file_sha(p,128*1024**2) for p in e.probe.iterdir()}
    assert InventoryRow.model_validate_json(encode(rows[0]))==rows[0]
    with pytest.raises(ValidationError):rows[0].secid="MUTATED"


def test_proposal_9_plus_3_determinism_exclusion_and_backfill(evidence):
    a=verify_inputs(**evidence.kwargs).analysis;p=a.proposal
    assert len(p.selected)<=12 and not set(p.previously_probed_secids)&{r.secid for r in p.selected}
    assert all(r.new_features for r in p.selected if r.role=="CONTROL")
    assert sum(r.role=="CONTROL" for r in p.selected)<=3
    assert sum(r.role=="CHECKSUM_EXCEPTION" for r in p.selected)==9
    assert not p.network_authorized and not p.consumable_by_task308a_probe
    assert select_exceptions(tuple(reversed(a.population)),a.context,a.units)==p
    assert p.selected==tuple(sorted(p.selected,key=lambda r:r.secid))
    no_exceptions=tuple((s,i) for s,i in a.population if syntax(s)[1])
    small=select_exceptions(no_exceptions,a.context,{})
    assert small.unmet_exception_slots==9 and len(small.selected)<=3
    observations=a.roadmap.source_field_observations
    assert any(o.source_field=="ISIN" and "MVZ220004019" in o.exact_record_secids and "MVZ220004019" not in o.supplied_value_secids for o in observations)


@pytest.mark.parametrize("value,format_valid,checksum_valid",[("US0378331005",True,True),("US0378331006",True,False),("us0378331005",False,False),("H1",False,False),(" US0378331005",False,False)])
def test_secids_syntax_is_not_binding(value,format_valid,checksum_valid):
    fmt,checksum,_=syntax(value);assert (fmt,checksum)==(format_valid,checksum_valid)


def test_semantically_resigned_evaluation_and_plan_rejected(evidence):
    from app.services.historical_evidence_normalization import signed
    e=evidence;p=e.kwargs["task308a_evaluation"];body=p.read_bytes()
    changed=e.evaluation.model_copy(update={"limitations":("FABRICATED",)})
    changed=changed.model_copy(update={"report_sha256":digest(changed,exclude=("report_sha256",))})
    p.write_bytes(encode(changed))
    with pytest.raises(IdentityError,match="REPLAY"):verify_inputs(**e.kwargs)
    p.write_bytes(body)
    altered=e.plan.model_copy(update={"population_secids":e.plan.population_secids+1})
    altered=altered.model_copy(update={"plan_sha256":digest(altered,exclude=("plan_sha256",))})
    e.kwargs["task308a_plan"].write_bytes(encode(altered))
    with pytest.raises(IdentityError,match="REBUILD"):verify_inputs(**e.kwargs)


@pytest.mark.parametrize("damage",["task307_sha","plan_sha","authorization","receipt","raw","incomplete","evaluation","missing_manifest","expected_count","expected_sha"])
def test_provenance_tampering_is_blocked(evidence,damage):
    e=evidence;kw=dict(e.kwargs)
    if damage in ("task307_sha","plan_sha","authorization","evaluation"):
        key={"task307_sha":"task307_report","plan_sha":"task308a_plan","authorization":"task308a_authorization","evaluation":"task308a_evaluation"}[damage]
        obj=json.loads(kw[key].read_bytes());field={"task307_sha":"audit_sha256","plan_sha":"plan_sha256","authorization":"authorization_sha256","evaluation":"report_sha256"}[damage]
        obj[field]="0"*64;kw[key].write_text(json.dumps(obj))
    elif damage=="receipt":
        p=next(e.probe.glob("*.receipt.json"));p.write_bytes(p.read_bytes().replace(b'"ACCEPTED"',b'"SOURCE_FAILURE"'))
    elif damage=="raw":
        p=next(e.probe.glob("*.response.json"));p.write_bytes(p.read_bytes()+b" ")
    elif damage=="incomplete":
        p=e.probe/"task308a-probe-manifest.json";obj=json.loads(p.read_bytes());obj["status"]="INCOMPLETE";p.write_text(json.dumps(obj))
    elif damage=="missing_manifest":(e.probe/"task308a-probe-manifest.json").unlink()
    elif damage=="expected_count":kw["expectations"]=Expectations(current_bindings=72)
    else:kw["expected_task308a_evaluation_sha256"]="0"*64
    with pytest.raises((IdentityError,ValueError,OSError)):verify_inputs(**kw)
    assert not list(e.output.iterdir())


def test_same_secid_different_pairs_preserved_duplicate_pair_rejected(evidence):
    e=evidence;r=e.report;items=list(r.identity_coverage.identities)
    item=next(i for i in items if i.secid not in {u.sample.secid for u in e.evaluation.units})
    values=("RU000A0JS3W6","US0378331005")
    items.remove(item);items.extend(item.model_copy(update={"isin":v,"conflicting_isin_candidates":values}) for v in values)
    report=reconcile_report(r,items)
    from app.services.historical_identity_feasibility_service import validate_report
    context,pop=validate_report(report)
    row=row_for(item.secid,pop[item.secid],context,None)
    assert len(row.original_identities)==2 and row.binding_conflict and row.confirmed_current_isin is None
    with pytest.raises(IdentityError):validate_report(reconcile_report(r,items+[items[-1]]))


def test_population_scale_10671_streaming_and_decimal_isolation(tmp_path,monkeypatch):
    # Real offline replay of a synthetic, completely acquired 12-security MockTransport probe.
    import tracemalloc
    from app.services.historical_identity_recovery_inventory_service import HistoricalIdentityRecoveryInventoryService
    e=prepare_evidence(tmp_path,monkeypatch,10671)
    verify_inputs(**e.kwargs)
    original=getcontext().copy();getcontext().prec=7
    tracemalloc.start()
    try:
        analysis=HistoricalIdentityRecoveryInventoryService().build(report=e.report,plan=e.plan,authorization=e.authorization,evaluation=e.evaluation,probe_manifest=e.manifest)
        h=hashlib.sha256();count=0
        from app.services.historical_audit_canonical_json import chunks
        for row in analysis.iter_inventory():
            for part in chunks(row):h.update(part.encode("ascii"))
            h.update(b"\n");count+=1
        peak=tracemalloc.get_traced_memory()[1]
        assert count==10671 and len(analysis.proposal.selected)<=12 and peak<64*1024**2
        assert getcontext().prec==7
        print(f"TASK308B1_SCALE rows={count}; traced_peak_bytes={peak}; production_RSS_not_measured=true")
    finally:
        tracemalloc.stop();getcontext().prec=original.prec
