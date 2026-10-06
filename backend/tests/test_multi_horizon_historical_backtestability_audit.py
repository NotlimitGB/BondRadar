"""Task306A2 isolated evidence-only endpoint and integration acceptance."""
import ast
from copy import deepcopy
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,localcontext,ROUND_DOWN
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session
from app.models.company import Company
from app.models.bond import Bond
from app.schemas.multi_horizon_historical_backtestability import MultiHorizonHistoricalBacktestabilityAuditV1 as Audit
from app.services import historical_endpoint_evidence as ep
from app.services import multi_horizon_historical_backtestability_audit_service as audit
from app.services import historical_replay_blocker_root_cause_audit_service as rca
from test_modern_historical_replay_readiness_audit import seeded,T,D
from test_historical_replay_blocker_root_cause_audit import readonly


def profile():
    return {"currency_state":"verified","currency_code":"RUB","nominal_state":"verified","nominal_value":D("1000"),"coupon_frequency_state":"verified","coupon_frequency_per_year":2}

def snapshot(day,identity=1,**values):
    return {"id":identity,"bond_id":1,"trade_date":day,"source":"moex","clean_price":None,"price":D("100"),"nkd":D("0"),
        "raw_payload":{"moex":{"SECID":"TEST","TRADEDATE":day.isoformat(),"CLOSE":"100","ACCINT":"0"}},**values}

def index(rows,p=None):
    return ep.EndpointIndex(rows,{1:p or profile()},{1:{"secid":"TEST","maturity_date":None}})

def cash(kind,day=None,amount=D("1"),currency="RUB",identity=1):
    return {"id":identity,"event_date":day or T+timedelta(days=5),"event_type":kind,"source":"moex","amount":amount,"currency":currency}


def test_independent_horizons_no_daily_chain_and_terminal_future_exclusion():
    rows=[snapshot(T-timedelta(days=1)),snapshot(T+timedelta(days=89),2),snapshot(T+timedelta(days=179),3)]
    idx=index(rows);flows=ep.CashflowIndex([])
    result=[ep.bond_horizon(idx,flows,1,T,h) for h in ep.HORIZONS]
    assert [r.canonical_ready for r in result]==[True,True,False]
    rows+=[snapshot(T+timedelta(days=363),4),snapshot(T+timedelta(days=366),5)]
    idx=index(rows);result=[ep.bond_horizon(idx,flows,1,T,h) for h in ep.HORIZONS]
    assert all(r.canonical_ready for r in result)
    assert result[-1].terminal.snapshot_id==4 and result[-1].terminal.age_days==2
    assert all(r.zero_persisted_events and not r.coupon_baseline_observable for r in result)
    assert all(r.contractual_completeness=="UNPROVEN" for r in result)
    assert result[-1].entry.trade_date<T


@pytest.mark.parametrize("raw,expected",[
    ({"ACCINT":"0"},"RAW_RECOVERABLE"),({"ACCRUEDINT":2},"RAW_RECOVERABLE"),
    ({"ACCINT":"2.00","ACCRUEDINT":2},"RAW_RECOVERABLE"),({"ACCINT":1,"ACCRUEDINT":2},"RAW_CONFLICT"),
    ({"ACCINT":True},"RAW_INVALID"),({"ACCINT":-1},"RAW_INVALID"),({"ACCINT":"NaN"},"RAW_INVALID"),
    ({"ACCINT":"Infinity"},"RAW_INVALID"),({"ACCINT":"bad","ACCRUEDINT":1},"RAW_INVALID"),({"ACCINT":" "},"RAW_MISSING"),
])
def test_nkd_exact_task294_matrix(raw,expected):
    row=snapshot(T,nkd=None);row["raw_payload"]["moex"].pop("ACCINT")
    row["raw_payload"]["moex"].update(raw);before=deepcopy(row)
    assert ep.recover_field(row,{"secid":"TEST"},"nkd").status==expected
    assert row==before


@pytest.mark.parametrize("field,value",[("SECID","OTHER"),("TRADEDATE","2020-01-01"),("TRADEDATE",None)])
def test_raw_binding_rejects_other_instrument_or_date(field,value):
    row=snapshot(T,nkd=None,price=None);row["raw_payload"]["moex"][field]=value
    assert ep.recover_field(row,{"secid":"TEST"},"nkd").status=="RAW_INVALID"
    assert ep.recover_field(row,{"secid":"TEST"},"price").status=="RAW_INVALID"


def test_price_mapping_history_conflict_priority_and_invalid_canonical():
    row=snapshot(T,price=None);row["raw_payload"]["moex"].update({"CLOSE":"101","WAPRICE":"102","LEGALCLOSEPRICE":"103"})
    assert ep.recover_field(row,{"secid":"TEST"},"price").value==D("101")
    assert ep.recover_field(row,{"secid":"TEST"},"clean_price").value==D("103")
    row["raw_payload"]["canonical"]={"close_price":"100"}
    assert ep.recover_field(row,{"secid":"TEST"},"price").status=="RAW_CONFLICT"
    row["price"]=D("NaN")
    assert ep.recover_field(row,{"secid":"TEST"},"price").status=="RAW_INVALID"
    row["price"]=None;row["raw_payload"]["moex"].pop("CLOSE");row["raw_payload"]["moex"].pop("WAPRICE")
    assert ep.recover_field(row,{"secid":"TEST"},"price").value==D("100")


@pytest.mark.parametrize("h",[90,180,365])
@pytest.mark.parametrize("case",["coupon","amortization","redemption","offer","invalid","duplicate","post_redemption","other"])
def test_cashflow_semantics_all_horizons(h,case):
    events=[cash("coupon")]
    if case=="amortization":events=[cash("amortization")]
    elif case=="redemption":events=[cash("redemption",amount=D("1000"))]
    elif case=="offer":events=[cash("offer_redemption",amount=None,currency=None)]
    elif case=="invalid":events=[cash("coupon",amount=None)]
    elif case=="duplicate":events=[cash("coupon"),cash("coupon",identity=2)]
    elif case=="post_redemption":events=[cash("redemption"),cash("coupon",T+timedelta(days=6),identity=2)]
    elif case=="other":events=[cash("other")]
    idx=index([snapshot(T-timedelta(days=1)),snapshot(T+timedelta(days=h-1),2)])
    result=ep.bond_horizon(idx,ep.CashflowIndex(events),1,T,h)
    assert result.canonical_ready==(case in {"coupon","amortization","redemption","offer"})
    assert result.coupon_baseline_observable==(case=="coupon")
    assert (result.terminal_path=="REDEEMED")== (case in {"redemption","post_redemption"})
    if case=="offer":assert "OFFER_NOT_AUTOMATIC_CASH" in result.diagnostics


def test_redemption_closes_tail_and_range_is_independent():
    idx=index([snapshot(T-timedelta(days=1))]);flows={1:ep.CashflowIndex([cash("redemption",amount=D("1000"))])}
    result=audit.make_horizon(T,365,{1},{1},idx,flows,T-timedelta(days=1),T)
    assert result.decision_ready_bond_ids==(1,) and not result.terminal_target_inside_global_range
    stage=next(s for s in result.canonical_funnel if s.stage=="TERMINAL_TARGET_INSIDE_PERSISTED_GLOBAL_RANGE")
    assert stage.kind=="INDEPENDENT_DIAGNOSTIC" and stage.pass_count==0


def test_recovery_endpoint_no_cross_snapshot_or_next_quote_fallback():
    entry=snapshot(T-timedelta(days=1),price=None,nkd=None)
    terminal=snapshot(T+timedelta(days=363),2,price=None,nkd=None)
    idx=index([entry,terminal]);before=deepcopy([entry,terminal]);result=ep.bond_horizon(idx,ep.CashflowIndex([]),1,T,365)
    assert not result.canonical_ready and result.after_raw_recovery_ready
    assert [entry,terminal]==before
    terminal["raw_payload"]={};idx=index([entry,terminal,snapshot(T+timedelta(days=366),3)])
    assert not ep.bond_horizon(idx,ep.CashflowIndex([]),1,T,365).after_raw_recovery_ready


def test_frequency_evidence_recovery_conflict_cutoff():
    p=profile();p["coupon_frequency_state"]="unknown";p["coupon_frequency_per_year"]=None
    def evidence(value,observed):return {"bond_id":1,"field_name":"coupon_frequency_per_year","source":"moex_description","contract_version":"bond-security-master-v2","normalized_value_json":{"value":value},"observed_at":observed}
    past=datetime.combine(T-timedelta(days=1),datetime.min.time(),timezone.utc)
    assert audit.frequency_status(1,p,[],T)=="EVIDENCE_MISSING"
    assert audit.frequency_status(1,p,[evidence(2,past)],T)=="EVIDENCE_RECOVERABLE"
    assert audit.frequency_status(1,p,[evidence(2,past),evidence(4,past)],T)=="EVIDENCE_CONFLICT"
    assert audit.frequency_status(1,p,[evidence(2,past+timedelta(days=2))],T)=="EVIDENCE_MISSING"


def test_range_planning_exact_month_slots_leap_year_no_clock():
    required,scenarios=audit.ranges(date(2021,1,1),date(2026,1,1))
    assert [r.monthly_window_count for r in required]==[1,12,24,36]
    assert [r.monthly_window_count for r in scenarios]==[36,60]
    assert required[0].desired_latest_completed_entry_date==date(2025,1,1)
    assert required[0].required_history_start_date==date(2024,12,2)
    assert required[1].desired_earliest_entry_date==date(2024,2,1)
    assert scenarios[1].desired_earliest_entry_date==date(2020,2,1)
    assert scenarios[1].missing_calendar_span_days==(date(2021,1,1)-date(2020,1,2)).days
    r,_=audit.ranges(date(2020,1,1),date(2024,2,29))
    assert r[0].desired_latest_completed_entry_date==date(2023,3,1)
    assert audit.ranges(None,None)==((),())


def test_service_binding_one_source_call_pending_readonly_and_determinism(seeded,monkeypatch):
    calls=[];curve_calls=[];real=rca.HistoricalReplayBlockerRootCauseAuditService.build
    real_curve=audit.OfzReferenceCurveService.build_curve
    def curve(self,day,**kw):curve_calls.append((day,kw));return real_curve(self,day,**kw)
    monkeypatch.setattr(audit.OfzReferenceCurveService,"build_curve",curve)
    def wrapped(self):calls.append(1);return real(self)
    monkeypatch.setattr(rca.HistoricalReplayBlockerRootCauseAuditService,"build",wrapped)
    with readonly(seeded.engine) as db:
        pending=Company(name="Pending",ticker="A2_PENDING");db.add(pending)
        with db.no_autoflush:
            bond=db.get(Bond,seeded.ids[4]);bond.name="OFZ-PK pending"
        state=set(db.new),set(db.dirty),set(db.deleted);sql=[]
        event.listen(seeded.engine,"before_cursor_execute",lambda c,u,s,p,x,m:sql.append(s))
        with localcontext() as ctx:
            ctx.prec=6;ctx.rounding=ROUND_DOWN
            result=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
            assert ctx.prec==6 and ctx.rounding==ROUND_DOWN
        assert result.status=="COMPLETE",result.blockers
        assert len(curve_calls)==2*len(result.per_date)
        assert all(kw=={"market_source":"moex","max_curve_age_days":7} for _,kw in curve_calls)
        assert calls==[1] and state==(set(db.new),set(db.dirty),set(db.deleted))
        assert all(s.lstrip().upper().startswith(("SELECT","WITH","PRAGMA QUERY_ONLY")) for s in sql)
        assert result.policy.horizons_days==(90,180,365) and result.source_task306a1_sha256
        assert all(x.selection_as_of_date==x.as_of_date-timedelta(days=1) for x in result.ofz_readiness)
        assert all(x==next(r.as_of_date for r in result.per_date if r.as_of_date.year==x.year and r.as_of_date.month==x.month) for x in result.monthly_entry_dates)
        assert Audit.model_validate_json(result.model_dump_json())==result
        assert audit.signed(result).audit_sha256==result.audit_sha256
        with pytest.raises(Exception):result.status="BLOCKED"
        with pytest.raises(Exception):Audit(status="COMPLETE",extra_field=1)
    with Session(seeded.engine) as db:assert audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build().status=="BLOCKED"


def test_static_no_execution_financial_or_source_calls():
    for module in (ep,audit):
        tree=ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        assert not calls&{"commit","flush","delete","apply","sync","evaluate_bond","build_for_bond","get_bond_history","fetch_bond_universe"}
        imports=[n.module or "" for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        assert not any(n.startswith(("httpx","requests","app.services.investment_model","app.services.shadow_experiment")) for n in imports)
    assert not {"profit","returns","alpha","sharpe"}&set(Audit.model_fields)



def test_fixed_policy_rejects_overrides():
    from app.schemas.multi_horizon_historical_backtestability import HistoricalResearchHorizonPolicyV1 as Policy
    for value in ((90,),(365,180,90),(90,180,True),[90,180,365]):
        with pytest.raises(ValueError):Policy(horizons_days=value)
    assert Policy.model_validate_json(Policy().model_dump_json())==Policy()
    assert Policy().monthly_entry_grid=="FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"
    with pytest.raises(ValueError):Policy(monthly_entry_grid="FIRST_TRADE_DATE_WITH_GE3_DECISION_ONLY_BONDS")


def test_future_outcome_changes_do_not_change_decision_grid_or_ofz_selection(seeded):
    from app.models.bond_market_snapshot import BondMarketSnapshot
    from sqlalchemy import select
    with readonly(seeded.engine) as db:before=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
    assert before.status=="COMPLETE"
    target=next(r.as_of_date for r in before.per_date if r.decision_only_intersection_count>=3)
    with Session(seeded.engine) as db:
        for row in db.scalars(select(BondMarketSnapshot).where(BondMarketSnapshot.trade_date>target)):
            row.nkd=None
        db.commit()
    with readonly(seeded.engine) as db:after=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
    assert after.status=="COMPLETE",after.blockers
    assert before.monthly_entry_dates==after.monthly_entry_dates
    a=next(r for r in before.per_date if r.as_of_date==target);b=next(r for r in after.per_date if r.as_of_date==target)
    assert a.decision_only_bond_ids==b.decision_only_bond_ids
    assert a.credit_prerequisite_count==b.credit_prerequisite_count
    assert a.monthly_research_entry_date==b.monthly_research_entry_date
    oa=next(r for r in before.ofz_readiness if r.as_of_date==target);ob=next(r for r in after.ofz_readiness if r.as_of_date==target)
    assert (oa.curve_status,oa.curve_trade_date,oa.component_bond_ids)==(ob.curve_status,ob.curve_trade_date,ob.component_bond_ids)
    assert a.horizons[0].terminal_outcome_ready_bond_count>b.horizons[0].terminal_outcome_ready_bond_count


def test_ofz_curve_ready_does_not_require_frequency(seeded):
    from app.models.bond_security_master_profile import BondSecurityMasterProfile
    from sqlalchemy import select
    with Session(seeded.engine) as db:
        for p in db.scalars(select(BondSecurityMasterProfile).where(BondSecurityMasterProfile.bond_id.in_(seeded.ids[3:]))):
            p.coupon_frequency_state="unknown";p.coupon_frequency_per_year=None
        db.commit()
    with readonly(seeded.engine) as db:result=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
    assert result.status=="COMPLETE",result.blockers
    ready=[r for r in result.ofz_readiness if r.curve_ready]
    assert ready and all(not r.modified_duration_prerequisites_ready for r in ready)
    assert any(status=="EVIDENCE_MISSING" for r in ready for _,status in r.frequency_recoverability)



def test_history_identity_conflict_and_portfolio_thresholds():
    row=snapshot(T,price=None)
    row["raw_payload"]["canonical"]={"secid":"OTHER","close_price":"100"}
    assert ep.recover_field(row,{"secid":"TEST"},"price").status=="RAW_INVALID"
    def day(n,monthly=True):
        return SimpleNamespace(monthly_research_entry_date=monthly,horizons=tuple(SimpleNamespace(horizon_days=h,decision_ready_bond_ids=tuple(range(n)),decision_ready_after_raw_recovery_bond_ids=tuple(range(n+2))) for h in ep.HORIZONS))
    for h in ep.HORIZONS:
        result=audit.portfolio([day(3),day(5),day(10),day(20,False)],h)
        assert result.monthly_entry_date_count==3
        assert (result.dates_with_3_ready,result.dates_with_5_ready,result.dates_with_10_ready)==(3,2,1)
        assert result.dates_with_5_after_raw_recovery==3


def test_source_blocked_stops_supporting_reads_and_sanitizes(seeded,monkeypatch):
    monkeypatch.setattr(rca.HistoricalReplayBlockerRootCauseAuditService,"build",lambda self:rca.blocked("SOURCE_UNAVAILABLE"))
    monkeypatch.setattr(audit,"read_evidence",lambda db:pytest.fail("supporting read after blocked source"))
    with readonly(seeded.engine) as db:result=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
    assert result.blockers==("SOURCE_TASK306A1_BLOCKED",)
    assert result.source_task306a1_sha256 and result.per_date==()
    assert Audit.model_validate_json(result.model_dump_json())==result



def synthetic_monthly_audit(monkeypatch,dates,decision_counts,recovered_counts=None):
    from collections import defaultdict
    from app.schemas.multi_horizon_historical_backtestability import HistoricalHorizonReadinessV1 as Horizon
    recovered_counts=recovered_counts or decision_counts
    bonds={bid:{"secid":str(bid),"maturity_date":None} for bid in range(1,11)}
    index=SimpleNamespace(bonds=bonds,profiles={},flows=defaultdict(list),ofz=set(),days={bid:(date(2024,2,1),) for bid in bonds})
    monkeypatch.setattr(rca,"EvidenceIndex",lambda data:index)
    monkeypatch.setattr(rca,"signed",lambda source:source)
    monkeypatch.setattr(rca,"credit_funnel",lambda *a:(None,None,{},()))
    def joint(day,*args):
        decision=set(range(1,decision_counts[day]+1))
        return None,None,decision,None,None,None,None,None
    monkeypatch.setattr(rca,"joint_funnel",joint)
    monkeypatch.setattr(rca,"curve_valid",lambda *a:None)
    def horizon(day,h,ids,decision,*args):
        ready=tuple(sorted(decision));recovered=tuple(range(1,recovered_counts[day]+1)) if decision else ()
        return Horizon(horizon_days=h,terminal_target_date=day+timedelta(days=h),terminal_target_inside_global_range=False,
            entry_ready_bond_count=len(ready),terminal_market_ready_bond_count=len(ready),redeemed_ready_bond_count=0,cashflow_valid_bond_count=len(ready),
            terminal_outcome_ready_bond_count=len(ready),terminal_outcome_ready_after_raw_recovery_count=len(recovered),coupon_baseline_observable_bond_count=0,
            decision_ready_bond_ids=ready,decision_ready_after_raw_recovery_bond_ids=recovered,canonical_funnel=(),recoverable_funnel=(),bonds=(),blockers=())
    monkeypatch.setattr(audit,"make_horizon",horizon)
    curves=SimpleNamespace(build_curve=lambda *a,**kw:SimpleNamespace(status="NO_ELIGIBLE_OFZ",nodes=(),node_count=0,curve_trade_date=None))
    source=SimpleNamespace(audit_sha256="a"*64,per_date=tuple(SimpleNamespace(as_of_date=d,decision_only_intersection_count=decision_counts[d],task268_status="NO_ELIGIBLE_OFZ") for d in dates))
    data=SimpleNamespace(tables={"bond_market_snapshots":(),"bond_security_master_evidence":()},market_date_counts=tuple({"trade_date":d} for d in dates))
    return audit.assemble(source,data,curves)


@pytest.mark.parametrize("first_count,classification",[(0,"NO_365D_WINDOWS"),(1,"PARTIAL_365D_WINDOWS"),(2,"PARTIAL_365D_WINDOWS")])
def test_monthly_first_trade_date_low_candidates_retained(monkeypatch,first_count,classification):
    dates=(date(2024,3,1),date(2024,3,10),date(2024,3,20))
    result=synthetic_monthly_audit(monkeypatch,dates,dict(zip(dates,(first_count,2,10))))
    assert result.monthly_entry_dates==(dates[0],)
    assert [r.monthly_research_entry_date for r in result.per_date]==[True,False,False]
    for coverage in result.readiness_by_horizon:
        assert coverage.monthly_entry_date_count==1
        assert (coverage.dates_with_3_ready,coverage.dates_with_5_ready,coverage.dates_with_10_ready)==(0,0,0)
    assert result.primary_365d_readiness.monthly_entry_date_count==1
    assert result.primary_365d_readiness.classification==classification
    assert result.primary_365d_readiness.after_raw_recovery_classification==classification


def test_two_months_fixed_schedule_not_shifted_by_late_decisions_or_recovery(monkeypatch):
    dates=(date(2024,3,1),date(2024,3,10),date(2024,3,20),date(2024,4,2),date(2024,4,15))
    counts=dict(zip(dates,(1,2,10,5,10)))
    before=synthetic_monthly_audit(monkeypatch,dates,counts)
    later=dict(zip(dates,(1,10,0,5,0)));recovery=dict(zip(dates,(2,10,10,10,10)))
    after=synthetic_monthly_audit(monkeypatch,dates,later,recovery)
    assert before.monthly_entry_dates==after.monthly_entry_dates==(dates[0],dates[3])
    assert before.primary_365d_readiness.classification=="RESEARCH_WINDOWS_AVAILABLE"
    assert after.primary_365d_readiness.after_raw_recovery_classification=="RESEARCH_WINDOWS_AVAILABLE"
    assert after.primary_365d_readiness.coverage.monthly_entry_date_count==2
    assert after.primary_365d_readiness.coverage.dates_with_10_after_raw_recovery==1
    assert audit.frozen_monthly_dates(reversed(dates),reversed(dates))==(dates[0],dates[3])


def test_monthly_date_missing_from_source_is_explicit_blocked(seeded,monkeypatch):
    with readonly(seeded.engine) as db:
        source=rca.HistoricalReplayBlockerRootCauseAuditService(db).build()
    missing=source.per_date[0].as_of_date
    source=rca.signed(source.model_copy(update={"per_date":source.per_date[1:]}))
    assert missing not in {r.as_of_date for r in source.per_date}
    monkeypatch.setattr(rca.HistoricalReplayBlockerRootCauseAuditService,"build",lambda self:source)
    monkeypatch.setattr(audit.OfzReferenceCurveService,"build_curve",lambda *a,**kw:pytest.fail("A2 curve call after invalid source grid"))
    with readonly(seeded.engine) as db:result=audit.MultiHorizonHistoricalBacktestabilityAuditService(db).build()
    assert result.blockers==("MONTHLY_ENTRY_DATE_NOT_IN_SOURCE_RCA_GRID",)
    assert result.status=="BLOCKED" and result.source_task306a1_sha256==source.audit_sha256
    assert result.per_date==() and result.monthly_entry_dates==()
    assert Audit.model_validate_json(result.model_dump_json())==result
