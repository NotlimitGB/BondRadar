"""Offline provenance, selection and exact source-event contracts."""
from datetime import datetime, timezone
from decimal import Decimal, getcontext
import hashlib
import json
import pytest
from app.schemas.historical_cashflow_feasibility import Candidate, PilotPlan
from app.services.historical_cashflow_feasibility_service import HistoricalCashflowFeasibilityService as Service, map_event, publish, validate_plan
from app.services.historical_evidence_normalization import signed
from app.services.historical_identity_recovery_artifacts import verify_inputs, publish as publish_b1
from app.services.historical_identity_feasibility_service import IdentityError
from test_historical_identity_recovery_inventory import evidence, pristine_evidence


def candidate(secid="SU24020RMFS8", isin="RU000A100QS2"):
    return Candidate(secid=secid,accepted_current_isin=isin,selection_order=1,selection_reasons=("TEST",),observed=True,
        canonical_ofz=True,actual_trade_indicator=True,usable_price_nkd_indicator=True,boards=("TQOB",),strata=("OBSERVED",),
        lineage_sha256="1"*64,request_sha256s=("2"*64,),response_sha256s=("3"*64,),observations=(datetime(2026,1,1,tzinfo=timezone.utc),))


def plan_fixture():
    names=("task307_audit","source_manifest","source_content","source_scope","source_implementation","task308a_plan",
        "task308a_authorization","task308a_probe","task308a_evaluation","task308b1_report","task308b1_inventory","task308b1_roadmap","task308b1_roadmap_file")
    return signed(PilotPlan,"plan_sha256",status="PLANNED",requested_size=1,cohort=(candidate(),),upstream_hashes=tuple(sorted((k,"5"*64) for k in names)))


def test_b1_pinned_plan_and_restrictions(evidence):
    e=evidence
    b1=publish_b1(verify_inputs(**e.kwargs),output_dir=e.output)
    p=Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=2)
    assert p.status=="PLANNED",p.blockers
    validate_plan(p)
    assert next(c for c in p.cohort if c.selection_order==1).canonical_ofz
    assert p==Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=2)
    su=Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=1,candidate_secids=["SU24020RMFS8"])
    assert su.cohort[0].accepted_current_isin=="RU000A100QS2"
    equal=Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=1,candidate_secids=["US0378331005"])
    assert equal.cohort[0].secid==equal.cohort[0].accepted_current_isin
    for restriction in (["MVZ220004019"],["UNPROBED"]):
        assert Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=1,candidate_secids=restriction).status=="BLOCKED"
    assert Service.plan(b1_root=e.output,expected_b1_report_sha256="0"*64,pilot_size=1).status=="BLOCKED"
    road=e.output/"task308b1-acquisition-plan.json"
    road.write_bytes(road.read_bytes()+b" ")
    # Semantic equality alone does not bypass the B1 publisher's canonical file-byte contract.
    changed=Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=2)
    assert changed.status=="BLOCKED" and changed.blockers==("ROADMAP_CANONICAL_FILE_SHA_MISMATCH",)


@pytest.mark.parametrize("size",[True,0,7,None,1.0])
def test_size_before_io(size):
    with pytest.raises(IdentityError):Service.plan(b1_root="absent",expected_b1_report_sha256="0"*64,pilot_size=size)


@pytest.mark.parametrize("value,diagnostic",[("0",None),("1.0000000",None),("1.0000001","STORAGE_UNREPRESENTABLE"),
    ("1000000000000","STORAGE_UNREPRESENTABLE"),("-1","AMOUNT_NEGATIVE"),(True,"AMOUNT_INVALID"),
    ("NaN","AMOUNT_INVALID"),("Infinity","AMOUNT_INVALID"),("bad","AMOUNT_INVALID"),(None,"AMOUNT_MISSING")])
def test_money_exact(value,diagnostic):
    old=getcontext().copy()
    row={"SECID":"SU24020RMFS8","COUPONDATE":"2025-01-01","VALUE":value,"CURRENCY":"RUB"}
    e=map_event(row,candidate(),"coupons",0,0,"4"*64)
    if diagnostic:assert diagnostic in e.diagnostics and e.status=="EVENT_INVALID"
    else:assert e.amount==Decimal(value) and e.status=="SCHEDULED_OBSERVATION"
    if diagnostic=="STORAGE_UNREPRESENTABLE":assert str(e.amount)==value
    assert e.returned_source_isin is None and e.unit_authority=="UNPROVEN"
    assert repr(getcontext())==repr(old)


def test_aliases_identifiers_units_and_context():
    row={"SECID":"OTHER","ISIN":"RU000A100QS2","DATE":"2025-01-01","VALUE":"3","AMOUNT":"4",
        "PRICE":"100","VALUEPRC":"10","FACEUNIT":"SUR","CURRENCY":"RUB","UNIT":"per bond"}
    e=map_event(row,candidate(),"offers",0,0,"4"*64)
    assert e.source_identity_conflict and not e.source_identity_asserted
    assert e.amount is None and "AMOUNT_ALIAS_CONFLICT" in e.diagnostics
    assert e.currency=="RUB" and dict(e.source_context)["PRICE"]=="100" and e.amount_unit=="per bond"
    e=map_event({"DATE":"2025-01-01","PRICE":"100","VALUEPRC":"10"},candidate(),"offers",0,0,"4"*64)
    assert e.amount is e.currency is e.returned_source_isin is None and e.binding_provenance=="REQUEST_CONTEXT_ONLY"


def test_publication_idempotency_and_frozen(tmp_path):
    p=plan_fixture()
    assert PilotPlan.model_validate_json(p.model_dump_json())==p
    with pytest.raises(ValueError):p.status="BLOCKED"
    publish(report=p,output_dir=tmp_path)
    before={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in tmp_path.iterdir() if f.is_file()}
    publish(report=p,output_dir=tmp_path)
    assert before=={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in tmp_path.iterdir() if f.is_file()}
    with pytest.raises(ValueError):publish(report=p.model_copy(update={"requested_size":2}),output_dir=tmp_path)


def test_optional_raw_replay_and_pinned_lineage_tampering(evidence):
    from app.schemas.historical_identity_recovery_inventory import InventoryAudit
    from app.services.historical_identity_probe_service import encode, read_model
    from app.services.historical_evidence_normalization import sha
    e=evidence
    b1=publish_b1(verify_inputs(**e.kwargs),output_dir=e.output)
    p=Service.plan(b1_root=e.output,expected_b1_report_sha256=b1.report_sha256,pilot_size=1,optional_replay=e.kwargs)
    assert p.status=="PLANNED",p.blockers
    inventory=e.output/"task308b1-identity-inventory.jsonl"
    lines=inventory.read_bytes().splitlines()
    for i,line in enumerate(lines):
        row=json.loads(line)
        if row["secid"]=="SU24020RMFS8":
            row["source_results"][0]["request_sha256s"]=[]
            lines[i]=json.dumps(row,separators=(",",":"),sort_keys=True).encode()
    inventory.write_bytes(b"\n".join(lines)+b"\n")
    audit_path=e.output/"task308b1-identity-audit.json"
    old=read_model(audit_path,InventoryAudit)
    bad=signed(InventoryAudit,"report_sha256",**{k:getattr(old,k) for k in type(old).model_fields if k not in ("report_sha256","inventory_sha256")},
        inventory_sha256=hashlib.sha256(inventory.read_bytes()).hexdigest())
    audit_path.write_bytes(encode(bad))
    # Even a newly pinned B1 envelope cannot bypass the independent source-lineage gate.
    assert Service.plan(b1_root=e.output,expected_b1_report_sha256=bad.report_sha256,pilot_size=1).status=="BLOCKED"


def test_publication_interruption_and_source_overlap(tmp_path,monkeypatch):
    import app.services.historical_cashflow_feasibility_service as module
    original=module.atomic
    def fail_json(path,body,**kwargs):
        if path.parent==module.safe_path(tmp_path) and path.suffix==".json":raise OSError("sensitive filesystem details")
        return original(path,body,**kwargs)
    monkeypatch.setattr(module,"atomic",fail_json)
    with pytest.raises(OSError):publish(report=plan_fixture(),output_dir=tmp_path)
    assert not list(tmp_path.glob("task308b2*")) and not any(p.is_dir() for p in tmp_path.iterdir())
    with pytest.raises(IdentityError):publish(report=plan_fixture(),output_dir=tmp_path,protected_roots=(tmp_path,))


@pytest.mark.parametrize("table,date_field,amount_field",[("coupons","COUPON_DATE","COUPON_VALUE"),
    ("amortizations","AMORTIZATIONDATE","AMORTIZATION_VALUE"),("redemptions","MATDATE","REDEMPTION_VALUE"),("offers","OFFER_DATE","AMOUNT")])
def test_explicit_alias_matrix(table,date_field,amount_field):
    row={date_field:"2025-01-01",amount_field:"1.00","VALUE":"1","CURRENCYID":"RUB","ISIN":"RU000A100QS2"}
    e=map_event(row,candidate(),table,100,2,"4"*64)
    assert e.amount==Decimal(1) and e.event_date.isoformat()=="2025-01-01" and e.status=="SCHEDULED_OBSERVATION"
    assert e.returned_source_secid is None and e.source_identity_asserted


def test_streaming_planning_full_10671_population(evidence):
    from app.schemas.historical_identity_recovery_inventory import InventoryAudit
    from app.services.historical_identity_probe_service import encode, read_model
    from test_historical_identity_recovery_inventory import valid_id
    e=evidence
    b1=publish_b1(verify_inputs(**e.kwargs),output_dir=e.output)
    inv=e.output/"task308b1-identity-inventory.jsonl"
    # Preserve all previously probed identities and add unprobed synthetic rows one at a time.
    existing=[json.loads(line) for line in inv.read_bytes().splitlines()]
    template=next(row for row in existing if not row["previously_probed"])
    existing_ids={row["secid"] for row in existing}
    generated=[]
    # This test fixture's compact index is strings only; application does not retain the population.
    i=100000
    while len(generated)<10671-len(existing):
        sid=valid_id(i);i+=1
        if sid not in existing_ids:generated.append(sid)
    with inv.open("wb") as stream:
        rows=iter(sorted(existing,key=lambda r:r["secid"]))
        pending=next(rows,None)
        for sid in sorted(generated):
            while pending is not None and pending["secid"]<sid:
                stream.write(json.dumps(pending,separators=(",",":"),sort_keys=True).encode()+b"\n");pending=next(rows,None)
            copy=dict(template,secid=sid,original_identities=template["original_identities"][:1],strata=[],requirements=[])
            stream.write(json.dumps(copy,separators=(",",":"),sort_keys=True).encode()+b"\n")
        while pending is not None:
            stream.write(json.dumps(pending,separators=(",",":"),sort_keys=True).encode()+b"\n");pending=next(rows,None)
    audit_path=e.output/"task308b1-identity-audit.json"
    old=read_model(audit_path,InventoryAudit)
    counts=dict(old.counts);counts["total_secids"]=10671
    updated=signed(InventoryAudit,"report_sha256",**{k:getattr(old,k) for k in type(old).model_fields if k not in ("report_sha256","inventory_sha256","counts")},
        counts=tuple(sorted(counts.items())),inventory_sha256=hashlib.sha256(inv.read_bytes()).hexdigest())
    audit_path.write_bytes(encode(updated))
    import tracemalloc
    tracemalloc.start()
    p=Service.plan(b1_root=e.output,expected_b1_report_sha256=updated.report_sha256,pilot_size=6)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert p.status=="PLANNED" and len(p.cohort)==6,p.blockers
    # The reused upstream verifier permits a 32 MiB bounded read buffer.
    # This guard detects retaining the full expanded Pydantic population as well.
    assert peak<64*1024**2
    print(f"Task308B2 synthetic 10671-SECID planning tracemalloc peak: {peak} bytes")


def test_static_offline_boundaries():
    import ast
    from pathlib import Path
    root=Path(__file__).parents[1]/"app/services"
    for name in ("historical_cashflow_feasibility_service.py","historical_cashflow_probe_service.py","historical_cashflow_probe_client.py"):
        tree=ast.parse((root/name).read_text())
        imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any("sqlalchemy" in n or n.startswith("app.models") for n in imports)
        calls=[n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)]
        assert not set(calls)&{"sync","commit","flush","fetch_page","getenv"}
