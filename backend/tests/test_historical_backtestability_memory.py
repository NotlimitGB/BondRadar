"""Task306A2H small semantic oracle, bounded aggregation and streamed output."""
import gc
import hashlib
import io
import json
from datetime import timedelta
from pathlib import Path
from time import perf_counter
import tracemalloc
from types import SimpleNamespace
from collections import defaultdict
import pytest
from app.services import multi_horizon_historical_backtestability_audit_service as audit
from app.services import historical_replay_blocker_root_cause_audit_service as rca
from app.services import historical_backtestability_evidence_reader as reader
from app.services.historical_audit_canonical_json import chunks,digest,write_json,ModelSpool
from app.schemas.multi_horizon_historical_backtestability import (
    MultiHorizonHistoricalBacktestabilityAuditV2 as Audit, HistoricalBondHorizonReadinessV1 as BondHorizon,
    SnapshotEndpointReadinessV1 as Endpoint, RawFieldRecoverabilityV1 as Field,
)
from app.schemas.historical_replay_blocker_root_cause import HistoricalReplayBlockerDateRcaV1
from test_modern_historical_replay_readiness_audit import seeded,T,D
from test_historical_replay_blocker_root_cause_audit import readonly


def legacy_projection(value):
    excluded={"bonds","canonical_economic_ready","recoverable_economic_ready","blocker_counts","security_master_profile_id","frequency_evidence_ids",
        "endpoint_observation_count","missing_snapshot_observation_count","blocker_count_unit","decision_membership_id",
        "decision_ready_membership_id","decision_ready_after_raw_recovery_membership_id","decision_ready_bond_count","decision_ready_after_raw_recovery_bond_count"}
    if isinstance(value,dict):return {k:legacy_projection(v) for k,v in value.items() if k not in excluded}
    if isinstance(value,list):return [legacy_projection(v) for v in value]
    return value


def decoded_projection(report):
    value=report.model_dump(mode="json");members={r["membership_id"]:r["bond_ids"] for r in value["bond_memberships"]}
    for row in value["per_date"]:row["decision_only_bond_ids"]=members[row["decision_membership_id"]]
    for row in (*value["per_date"],*value["ofz_readiness"]):
        for h in row["horizons"]:
            h["decision_ready_bond_ids"]=members[h["decision_ready_membership_id"]]
            h["decision_ready_after_raw_recovery_bond_ids"]=members[h["decision_ready_after_raw_recovery_membership_id"]]
    return legacy_projection(value)


def test_accepted_a2g_research_conclusions_exact_oracle(seeded):
    expected=json.loads((Path(__file__).parent/"fixtures"/"task306a2g_semantic_oracle.json").read_text())
    with readonly(seeded.engine) as db:result=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
    assert result.status=="COMPLETE",result.blockers
    projection=decoded_projection(result)
    for key,sha in expected.items():
        encoded=json.dumps(projection[key],sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False).encode("ascii")
        assert hashlib.sha256(encoded).hexdigest()==sha,key
    assert result.contract_version.endswith("v2")
    assert all(not h.bonds for d in (*result.per_date,*result.ofz_readiness) for h in d.horizons)


def test_bounded_rca_linkage_same_full_semantic_sha_and_dates(seeded):
    with readonly(seeded.engine) as db:full=rca.HistoricalReplayBlockerRootCauseAuditService(db).build()
    expected=hashlib.sha256(json.dumps(full.model_dump(mode="json",exclude={"audit_sha256"}),sort_keys=True,
        ensure_ascii=True,separators=(",",":"),allow_nan=False).encode("ascii")).hexdigest()
    assert full.audit_sha256==expected
    with readonly(seeded.engine) as db:link=rca.HistoricalReplayBlockerRootCauseAuditService(db).build_linkage()
    assert link.status=="COMPLETE" and link.audit_sha256==full.audit_sha256
    assert [(r.as_of_date,r.decision_only_intersection_count,r.task268_status) for r in link.per_date]==[
        (r.as_of_date,r.decision_only_intersection_count,r.task268_status) for r in full.per_date]


def test_narrow_reader_no_raw_retention_and_exact_decision_parity(seeded):
    from app.services.modern_historical_replay_evidence_reader import read_evidence
    from sqlalchemy import event
    sql=[]
    event.listen(seeded.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
    with readonly(seeded.engine) as db:compact=reader.read_evidence(db)
    assert all(not hasattr(row,"raw_payload") for row in compact.tables["bond_market_snapshots"])
    assert not any(name in statement for statement in sql for name in ("cbr_bank_","controlled_financial_statement_values","bond_return_labels","content_bytes"))
    assert all(s.lstrip().upper().startswith(("SELECT","WITH","PRAGMA QUERY_ONLY","BEGIN")) for s in sql)
    with readonly(seeded.engine) as db:original=read_evidence(db)
    old=rca.EvidenceIndex(original);new=reader.DecisionIndex(compact)
    for item in original.market_date_counts:
        day=item["trade_date"];observed={b for b,days in old.days.items() if days[0]<day}
        _,_,keys,_=rca.credit_funnel(day,observed,old)
        assert rca.joint_funnel(day,observed,new,keys,set())[2]==rca.joint_funnel(day,observed,old,keys,set())[2]


def test_streaming_json_no_whole_dump_matches_reference(monkeypatch):
    report=Audit(status="BLOCKED",blockers=("SAFE_РЕASON",),known_limitations=("x",))
    reference=json.dumps(report.model_dump(mode="json"),sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False)
    monkeypatch.setattr(Audit,"model_dump",lambda *a,**k:pytest.fail("whole model dump"))
    monkeypatch.setattr(Audit,"model_dump_json",lambda *a,**k:pytest.fail("whole JSON string"))
    output=io.StringIO();write_json(report,output)
    assert output.getvalue()==reference+"\n"
    expected=json.loads(reference);expected.pop("audit_sha256")
    assert digest(report)==hashlib.sha256(json.dumps(expected,sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False).encode("ascii")).hexdigest()
    for value in (D("NaN"),D("Infinity"),float("nan")):
        with pytest.raises(ValueError):tuple(chunks({"value":value}))


def test_private_spool_reiteration_cleanup_and_sha(seeded):
    with readonly(seeded.engine) as db:full=rca.HistoricalReplayBlockerRootCauseAuditService(db).build()
    with ModelSpool(HistoricalReplayBlockerDateRcaV1) as spool:
        for row in full.per_date:spool.append(row)
        assert tuple(spool)==full.per_date and tuple(spool)==full.per_date
        assert digest(full,overrides={"per_date":spool})==full.audit_sha256
        file=spool.file
    assert file.closed
    with pytest.raises(RuntimeError):
        with ModelSpool(HistoricalReplayBlockerDateRcaV1) as spool:
            file=spool.file;raise RuntimeError("injected")
    assert file.closed


@pytest.mark.parametrize("raw",[
    {"ACCINT":"0"},{"ACCRUEDINT":"2.00"},{"ACCINT":"2","ACCRUEDINT":2},
    {"ACCINT":"2","ACCRUEDINT":"3"},{"ACCINT":True},{"ACCINT":"NaN"},
    {"ACCINT":"-1"},{"SECID":"OTHER","ACCINT":"2"},{"TRADEDATE":"2026-01-01","ACCINT":"2"},
    {"ACCINT":"2","canonical_conflict":True},{}
])
def test_compacted_endpoint_parser_parity_and_exact_raw_targets(raw):
    from app.services import historical_endpoint_evidence as ep
    from test_multi_horizon_historical_backtestability_audit import snapshot,profile
    day=T-timedelta(days=1);metadata={"SECID":"TEST","TRADEDATE":day.isoformat(),"CLOSE":"100",**raw}
    row=snapshot(day,nkd=None,yield_to_maturity=D("10"),duration_years=D("1"),raw_payload={"moex":metadata})
    if raw.get("canonical_conflict"):row["raw_payload"]["canonical"]={"accrued_interest":"3"}
    bonds={1:{"secid":"TEST","isin":"RU_TEST","maturity_date":None}};profiles={1:profile()}
    compact=reader.compact_market_row(row,bonds[1],profiles[1])
    old=ep.EndpointIndex([row],profiles,bonds).endpoint(1,T,"ENTRY")
    new=ep.EndpointIndex([compact],profiles,bonds).endpoint(1,T,"ENTRY")
    assert old.model_dump(exclude={"fields"})==new.model_dump(exclude={"fields"})
    assert [(p.field,p.status,p.source_fields,p.blockers) for p in old.fields]==[(p.field,p.status,p.source_fields,p.blockers) for p in new.fields]
    assert [p.value for p in old.fields if p.status=="RAW_RECOVERABLE"]==[p.value for p in new.fields if p.status=="RAW_RECOVERABLE"]
    targets=audit.RepairTargets(bonds,{1:ep.CashflowIndex([])})
    result=ep.bond_horizon(ep.EndpointIndex([compact],profiles,bonds),ep.CashflowIndex([]),1,T,90)
    targets.observe(result,"CORPORATE_PRIMARY",90,0,T);offline,gaps=targets.result()
    assert all(t.value.is_finite() and t.snapshot_id==row["id"] and t.secid=="TEST" and t.trade_date==day for t in offline)
    if new.recoverable_nkd_ready:
        assert next(t for t in offline if t.field=="nkd").value==next(p.value for p in old.fields if p.field=="nkd")
    else:assert not any(t.field=="nkd" for t in offline)


def test_observation_blockers_not_lost_by_snapshot_deduplication():
    accumulator=audit.CoverageAccumulator("ENTRY")
    fresh=scale_row(1).entry
    accumulator.add(fresh);accumulator.add(fresh.model_copy(update={"fresh":False,"blockers":("SNAPSHOT_STALE",)}))
    accumulator.add(Endpoint(bond_id=2,target_date=T,kind="ENTRY",blockers=("SNAPSHOT_MISSING",)))
    result=accumulator.result()
    assert result.snapshot_count==1 and result.endpoint_observation_count==3 and result.missing_snapshot_observation_count==1
    assert dict(result.blocker_counts)=={"SNAPSHOT_STALE":1,"SNAPSHOT_MISSING":1}


def test_contract_v2_old_version_rejected_and_member_sets_exact():
    with pytest.raises(ValueError):Audit(status="COMPLETE",contract_version="multi-horizon-historical-backtestability-audit-v1")
    pool=audit.MembershipPool()
    assert pool.add((1,3))==pool.add((1,3))==0
    assert pool.add(())==1 and pool.result()[0].bond_ids==(1,3)


def scale_row(bid):
    recovered=bid%3!=0;canonical=bid%3==1
    proof=Field(field="nkd",status="RAW_RECOVERABLE" if recovered and not canonical else "ALREADY_CANONICAL" if canonical else "RAW_MISSING",
        value=D("0") if recovered else None,source_fields=("ACCINT",) if recovered and not canonical else ())
    endpoint=Endpoint(bond_id=bid,target_date=T,kind="ENTRY",snapshot_id=bid,trade_date=T-timedelta(days=1),fresh=True,
        current_terms_ready=True,canonical_price_ready=True,canonical_nkd_ready=canonical,recoverable_price_ready=True,
        recoverable_nkd_ready=recovered,canonical_ready=canonical,after_raw_recovery_ready=recovered,fields=(proof,),
        blockers=() if canonical else ("NKD_UNAVAILABLE",))
    return BondHorizon(bond_id=bid,entry=endpoint,terminal=endpoint.model_copy(update={"kind":"TERMINAL"}),terminal_path="MARKET",
        cashflow_events_valid=True,persisted_event_count=0,persisted_coupon_event_count=0,coupon_events_valid=True,
        coupon_baseline_observable=False,zero_persisted_events=True,canonical_ready=canonical,after_raw_recovery_ready=recovered,
        blockers=endpoint.blockers,diagnostics=("ZERO_PERSISTED_EVENTS",))


def test_600_bonds_360_dates_all_horizons_bounded_retention_and_output(monkeypatch):
    # Outcomes are precomputed synthetic source evidence. This stress measures
    # the real accumulator/funnel/target/hash/output pipeline, not DB or parsers.
    pool={b:scale_row(b) for b in range(1,601)};calls=0
    def outcome(*args):
        nonlocal calls
        calls+=1;return pool[args[2]]
    monkeypatch.setattr(audit,"bond_horizon",outcome)
    bonds={b:{"isin":f"RU{b}","secid":f"TEST{b}","maturity_date":None} for b in pool}
    targets=audit.RepairTargets(bonds,{b:SimpleNamespace(rows=[]) for b in pool})
    groups={k:audit.CoverageAccumulator(k) for k in ("ENTRY","TERMINAL_90","TERMINAL_180","TERMINAL_365")}
    gc.collect();tracemalloc.start();start=perf_counter();records=[];members=audit.MembershipPool()
    for n in range(360):
        day=T+timedelta(days=n)
        for h in (90,180,365):
            def observe(row):
                if h==90:groups["ENTRY"].add(row.entry)
                groups["TERMINAL_"+str(h)].add(row.terminal)
                targets.observe(row,"CORPORATE_PRIMARY",h,n,day)
            result=audit.make_horizon(day,h,set(pool),set(pool),None,{b:None for b in pool},T,T+timedelta(days=800),
                retain_detail=False,on_row=observe)
            assert not result.bonds
            assert len(result.decision_ready_bond_ids)==200 and len(result.decision_ready_after_raw_recovery_bond_ids)==400
            records.append(members.horizon(result))
    offline,gaps=targets.result();aggregation_peak=tracemalloc.get_traced_memory()[1]
    assert calls==600*360*3 and len(offline)==200
    assert len(gaps)==200*2*3
    assert all(r.entry_date_index_ranges==((0,359),) for r in gaps)
    coverage=tuple(g.result() for g in groups.values());assert all(r.snapshot_count==600 for r in coverage)
    class Sink:
        def __init__(self):self.sha=hashlib.sha256();self.count=0;self.maximum=0
        def write(self,part):
            if part!="\n":self.sha.update(part.encode("ascii"))
            self.count+=len(part);self.maximum=max(self.maximum,len(part))
    assert len(members.result())==2
    sink=Sink();content={"horizons":records,"memberships":members.result(),"offline":offline,"gaps":gaps,"coverage":coverage}
    sha=digest(content,exclude=());write_json(content,sink)
    assert sha==sink.sha.hexdigest() and sink.maximum<4096
    output_peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    print(f"A2H_SCALE evaluations={calls} aggregation_peak={aggregation_peak} output_peak={output_peak} bytes={sink.count} seconds={perf_counter()-start:.2f}")
    assert aggregation_peak<64*1024*1024 and output_peak<64*1024*1024
