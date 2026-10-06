"""Read-only multi-horizon audit; upstream decision semantics remain frozen."""
from bisect import bisect_left
from collections import Counter,defaultdict
from datetime import datetime,time,timedelta,timezone
from decimal import Context,DecimalException,ROUND_HALF_EVEN,localcontext
import hashlib
import json
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.schemas.multi_horizon_historical_backtestability import (
    MultiHorizonHistoricalBacktestabilityAuditV1 as Audit, HistoricalHorizonReadinessV1 as Horizon,
    HistoricalMultiHorizonDateReadinessV1 as DateReadiness, HorizonPortfolioCoverageV1 as Portfolio,
    Historical365DayQualificationReadinessV1 as Primary, HistoricalResearchRangeScenarioV1 as Scenario,
    OfzHistoricalHorizonReadinessV1 as Ofz, EndpointCoverageV1 as Coverage, BackfillRequirementV1 as Requirement,
)
from app.services import historical_replay_blocker_root_cause_audit_service as rca
from app.services.modern_historical_replay_evidence_reader import read_evidence
from app.services.ofz_reference_curve_service import OfzReferenceCurveService
from app.services.historical_endpoint_evidence import EndpointIndex,CashflowIndex,bond_horizon,HORIZONS,finite,terms_ready

ORDER=("OBSERVED_BEFORE_ENTRY","CURRENT_DIAGNOSTIC_TERMS_READY","ENTRY_SNAPSHOT_FRESH","ENTRY_PRICE_READY","ENTRY_NKD_READY",
    "ENTRY_VALUATION_EVIDENCE_READY","TERMINAL_TARGET_INSIDE_PERSISTED_GLOBAL_RANGE","TERMINAL_OR_REDEMPTION_PATH_EXISTS",
    "TERMINAL_SNAPSHOT_FRESH","TERMINAL_PRICE_READY","TERMINAL_NKD_READY","TERMINAL_VALUATION_EVIDENCE_READY",
    "CASHFLOW_EVENTS_VALID","TERMINAL_OUTCOME_READY")
LIMITS=tuple(sorted(("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN","SURVIVORSHIP_BIAS_RISK","CURRENT_SECURITY_MASTER_NOT_HISTORICAL_PROOF",
    "CURRENT_ISSUER_MAPPING_DIAGNOSTIC","CONTRACTUAL_CASHFLOW_COMPLETENESS_UNPROVEN","HISTORICAL_DIAGNOSTIC_ONLY",
    "ACTUAL_TASK272_AND_TASK277_READINESS_NOT_EVALUATED","DAILY_CONTINUITY_NOT_REQUIRED","NO_PROFITABILITY_CALCULATED")))

def signed(result):
    encoded=json.dumps(result.model_dump(mode="json",exclude={"audit_sha256"}),sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False)
    return result.model_copy(update={"audit_sha256":hashlib.sha256(encoded.encode("ascii")).hexdigest()})

def blocked(code,source=None):
    return signed(Audit(status="BLOCKED",source_task306a1_sha256=source.audit_sha256 if source else None,blockers=(code,),known_limitations=LIMITS))

def month_shift(day,offset):
    n=day.year*12+day.month-1+offset
    return day.replace(year=n//12,month=n%12+1,day=1)

def ranges(first,last):
    if first is None or last is None:return (),()
    latest=(last-timedelta(days=365)).replace(day=1)
    def scenario(n,name):
        earliest=month_shift(latest,1-n);start=earliest-timedelta(days=30);end=latest+timedelta(days=365)
        missing=max(0,(first-start).days)+max(0,(end-last).days)
        return Scenario(scenario=name,monthly_window_count=n,desired_earliest_entry_date=earliest,desired_latest_completed_entry_date=latest,
            required_history_start_date=start,required_history_end_date=end,missing_calendar_span_days=missing,range_covered=missing==0)
    return tuple(scenario(n,str(n)+"_MONTHLY_ANNUAL_WINDOWS") for n in (1,12,24,36)),tuple(scenario(n,name) for n,name in ((36,"3Y_ENTRY_SPAN"),(60,"5Y_ENTRY_SPAN")))

def make_horizon(day,h,ids,decision,index,flows,first,last):
    end=day+timedelta(days=h);inside=first is not None and first<=end<=last
    rows=tuple(bond_horizon(index,flows[bid],bid,day,h) for bid in sorted(ids))
    def funnel(recovered):
        predicates={name:set() for name in ORDER}
        predicates[ORDER[0]]=set(ids)
        for row in rows:
            bid=row.bond_id;e=row.entry;t=row.terminal;red=row.terminal_path=="REDEEMED"
            values=(True,e.current_terms_ready,e.fresh,e.recoverable_price_ready if recovered else e.canonical_price_ready,
                e.recoverable_nkd_ready if recovered else e.canonical_nkd_ready,e.after_raw_recovery_ready if recovered else e.canonical_ready,
                inside,red or t.snapshot_id is not None,red or t.fresh,red or (t.recoverable_price_ready if recovered else t.canonical_price_ready),
                red or (t.recoverable_nkd_ready if recovered else t.canonical_nkd_ready),red or (t.after_raw_recovery_ready if recovered else t.canonical_ready),
                row.cashflow_events_valid,row.after_raw_recovery_ready if recovered else row.canonical_ready)
            for name,ok in zip(ORDER,values):
                if ok:predicates[name].add(bid)
        return rca.stages(ids,predicates,ORDER,("TERMINAL_TARGET_INSIDE_PERSISTED_GLOBAL_RANGE",))
    canonical={r.bond_id for r in rows if r.canonical_ready};recovered={r.bond_id for r in rows if r.after_raw_recovery_ready}
    return Horizon(horizon_days=h,terminal_target_date=end,terminal_target_inside_global_range=inside,
        entry_ready_bond_count=sum(r.entry.canonical_ready for r in rows),terminal_market_ready_bond_count=sum(r.terminal is not None and r.terminal.canonical_ready for r in rows),
        redeemed_ready_bond_count=sum(r.terminal_path=="REDEEMED" and r.canonical_ready for r in rows),cashflow_valid_bond_count=sum(r.cashflow_events_valid for r in rows),
        terminal_outcome_ready_bond_count=len(canonical),terminal_outcome_ready_after_raw_recovery_count=len(recovered),coupon_baseline_observable_bond_count=sum(r.coupon_baseline_observable for r in rows),
        decision_ready_bond_ids=tuple(sorted(canonical&decision)),decision_ready_after_raw_recovery_bond_ids=tuple(sorted(recovered&decision)),
        canonical_funnel=funnel(False),recoverable_funnel=funnel(True),bonds=rows,blockers=tuple(sorted({code for r in rows for code in r.blockers})))

def frequency_status(bid,profile,evidence,day):
    values=set();invalid=False
    for row in evidence:
        if row["bond_id"]!=bid or row["field_name"]!="coupon_frequency_per_year":continue
        observed=row["observed_at"]
        if observed is None:continue
        if observed.tzinfo is None:observed=observed.replace(tzinfo=timezone.utc)
        if observed>datetime.combine(day,time.min,timezone.utc):continue
        if row["source"] not in ("moex_universe","moex_description") or row["contract_version"]!="bond-security-master-v2":continue
        payload=row["normalized_value_json"];value=payload.get("value") if isinstance(payload,dict) else None
        if type(value) is int and value>0:values.add(value)
        else:invalid=True
    state=profile["coupon_frequency_state"] if profile else None
    current=profile["coupon_frequency_per_year"] if profile else None
    if state=="conflict" or len(values)>1 or invalid or (state=="verified" and values and values!={current}):return "EVIDENCE_CONFLICT"
    if state=="verified" and type(current) is int and current>0:return "ALREADY_VERIFIED"
    return "EVIDENCE_RECOVERABLE" if len(values)==1 else "EVIDENCE_MISSING"

def portfolio(rows,h):
    selected=[next(x for x in r.horizons if x.horizon_days==h) for r in rows if r.monthly_research_entry_date]
    counts={"dates_with_"+str(n)+suffix:sum(len(getattr(r,field))>=n for r in selected)
        for n in (3,5,10) for suffix,field in (("_ready","decision_ready_bond_ids"),("_after_raw_recovery","decision_ready_after_raw_recovery_bond_ids"))}
    return Portfolio(horizon_days=h,monthly_entry_date_count=len(selected),**counts)

def endpoint_coverage(label,endpoints):
    unique={e.snapshot_id:e for e in endpoints if e.snapshot_id is not None};rows=list(unique.values());status=Counter(f.field+":"+f.status for e in rows for f in e.fields)
    ready=sum(e.canonical_price_ready and e.canonical_nkd_ready for e in rows)
    extra=sum(e.recoverable_price_ready and e.recoverable_nkd_ready and not (e.canonical_price_ready and e.canonical_nkd_ready) for e in rows)
    return Coverage(endpoint=label,snapshot_count=len(rows),usable_price_and_nkd=sum(e.canonical_price_ready and e.canonical_nkd_ready for e in rows),
        usable_price_missing_nkd=sum(e.canonical_price_ready and not e.canonical_nkd_ready for e in rows),missing_price_usable_nkd=sum(not e.canonical_price_ready and e.canonical_nkd_ready for e in rows),
        missing_both=sum(not e.canonical_price_ready and not e.canonical_nkd_ready for e in rows),already_ready=ready,additional_ready_from_raw=extra,
        still_unavailable=len(rows)-ready-extra,raw_field_status_counts=tuple(sorted(status.items())))

class MonthlyEntryDateNotInSourceRcaGrid(ValueError):
    """Fixed schedule cannot be represented by the upstream date grid."""


def frozen_monthly_dates(raw_market_dates,source_dates):
    first_by_month={}
    for day in sorted(set(raw_market_dates)):
        first_by_month.setdefault((day.year,day.month),day)
    selected=tuple(first_by_month.values())
    if not set(selected).issubset(set(source_dates)):
        raise MonthlyEntryDateNotInSourceRcaGrid()
    return selected


def assemble(source,data,curve_service):
    rca.require(rca.signed(source).audit_sha256==source.audit_sha256)
    index=rca.EvidenceIndex(data);endpoints=EndpointIndex(data.tables["bond_market_snapshots"],index.profiles,index.bonds)
    flows={bid:CashflowIndex(index.flows[bid]) for bid in index.bonds}
    dates=tuple(sorted(r["trade_date"] for r in data.market_date_counts));first=dates[0] if dates else None;last=dates[-1] if dates else None
    monthly_dates=set(frozen_monthly_dates(dates,(r.as_of_date for r in source.per_date)))
    records=[];ofz_rows=[]
    for base in source.per_date:
        day=base.as_of_date;observed={bid for bid,ds in index.days.items() if ds[0]<day}
        _,_,keys,cohorts=rca.credit_funnel(day,observed,index)
        _,_,decision,_,_,_,_,_=rca.joint_funnel(day,observed,index,keys,set())
        rca.require(len(decision)==base.decision_only_intersection_count)
        monthly=day in monthly_dates
        horizons=tuple(make_horizon(day,h,observed,decision,endpoints,flows,first,last) for h in HORIZONS)
        records.append(DateReadiness(as_of_date=day,monthly_research_entry_date=monthly,decision_only_bond_ids=tuple(sorted(decision)),decision_only_intersection_count=len(decision),credit_prerequisite_count=len(rca.qualified(cohorts)),horizons=horizons))
        selection=day-timedelta(days=1);curve=curve_service.build_curve(selection,market_source="moex",max_curve_age_days=7)
        rca.curve_valid(curve,selection,index)
        ids=tuple(sorted(bid for node in curve.nodes for bid in node.component_bond_ids))
        frequency=tuple((bid,frequency_status(bid,index.profiles.get(bid),data.tables["bond_security_master_evidence"],day)) for bid in sorted(index.ofz))
        prerequisites=curve.status=="READY" and all(terms_ready(index.profiles.get(bid)) and
            index.profiles[bid]["coupon_structure"]=="fixed" and index.profiles[bid]["perpetual_structure"]=="dated" and status=="ALREADY_VERIFIED"
            and endpoints.endpoint(bid,day,"ENTRY").fresh and finite(endpoints.rows[bid][bisect_left(endpoints.days[bid],day)-1]["yield_to_maturity"])
            for bid,status in frequency if bid in ids)
        ofz_rows.append(Ofz(as_of_date=day,selection_as_of_date=selection,task306a1_curve_status_at_t=base.task268_status,curve_status=curve.status,curve_trade_date=curve.curve_trade_date,
            node_count=curve.node_count,component_bond_ids=ids,curve_ready=curve.status=="READY",modified_duration_prerequisites_ready=prerequisites,frequency_recoverability=frequency,
            horizons=tuple(make_horizon(day,h,set(ids),set(ids),endpoints,flows,first,last) for h in HORIZONS),
            blockers=tuple(sorted({"OFZ_MODIFIED_DURATION_PREREQUISITES_UNAVAILABLE"} if not prerequisites else set()))))
    coverage=tuple(portfolio(records,h) for h in HORIZONS);primary=coverage[-1]
    annual=[r.horizons[-1] for r in records if r.monthly_research_entry_date]
    def classification(field):
        counts=[len(getattr(r,field)) for r in annual]
        return "RESEARCH_WINDOWS_AVAILABLE" if any(n>=3 for n in counts) else "PARTIAL_365D_WINDOWS" if any(counts) else "NO_365D_WINDOWS"
    prim=Primary(monthly_entry_date_count=len(annual),entry_dates_with_terminal_global_range=sum(r.terminal_target_inside_global_range for r in annual),
        dates_with_any_terminal_outcome=sum(bool(r.decision_ready_bond_ids) for r in annual),dates_with_any_after_raw_recovery=sum(bool(r.decision_ready_after_raw_recovery_bond_ids) for r in annual),
        classification=classification("decision_ready_bond_ids"),after_raw_recovery_classification=classification("decision_ready_after_raw_recovery_bond_ids"),coverage=primary)
    required,scenarios=ranges(first,last);requirements=defaultdict(set)
    requirements["HISTORICAL_UNIVERSE_CAPTURE_REQUIRED"].add("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN")
    requirements["SECURITY_MASTER_VERSIONING_REQUIRED"].add("CURRENT_TERMS_NOT_HISTORICAL_PROOF")
    selected=[r for d in records if d.monthly_research_entry_date for r in d.horizons[-1].bonds if r.bond_id in d.decision_only_bond_ids]
    errors={code for r in selected for code in r.blockers}
    if required and not required[0].range_covered and not primary.dates_with_3_after_raw_recovery:requirements["BROAD_HISTORICAL_MOEX_BACKFILL_REQUIRED"].add("FIRST_ANNUAL_MONTHLY_WINDOW_RANGE_MISSING")
    elif any(not r.after_raw_recovery_ready and (not r.entry.after_raw_recovery_ready or r.terminal is not None and not r.terminal.after_raw_recovery_ready) for r in selected):
        requirements["TARGETED_ENDPOINT_MOEX_BACKFILL_REQUIRED"].add("PRIMARY_ENDPOINT_EVIDENCE_MISSING")
    recovered_fields={f.field for r in selected for e in (r.entry,r.terminal) if e for f in e.fields if f.status=="RAW_RECOVERABLE"}
    if recovered_fields:
        price=bool(recovered_fields&{"price","clean_price"});nkd="nkd" in recovered_fields
        category="OFFLINE_RAW_PRICE_AND_NKD_REPAIR_REQUIRED" if price and nkd else "OFFLINE_RAW_PRICE_REPAIR_REQUIRED" if price else "OFFLINE_RAW_NKD_REPAIR_SUFFICIENT"
        requirements[category].add("SAFE_SAME_SNAPSHOT_RAW_EVIDENCE_AVAILABLE")
    if selected and all(r.canonical_ready for r in selected):requirements["NO_MARKET_REPAIR_REQUIRED"].add("PRIMARY_CANONICAL_ENDPOINTS_READY")
    if any(not r.cashflow_events_valid for r in selected):requirements["CASHFLOW_HISTORY_REPAIR_REQUIRED"].update(errors&{"CASHFLOW_AMOUNT_OR_CURRENCY_INVALID","DUPLICATE_AUTOMATIC_EVENT","AUTOMATIC_EVENT_AFTER_REDEMPTION","UNSUPPORTED_AUTOMATIC_EVENT","MATURITY_REDEMPTION_EVIDENCE_MISSING"})
    for row in ofz_rows:
        for bid,status in row.frequency_recoverability:
            if status!="ALREADY_VERIFIED":
                reason=("TARGETED_OFZ_SECURITY_MASTER_REFRESH" if status=="EVIDENCE_MISSING" else status)+":BOND_ID="+str(bid)
                requirements["OFZ_SECURITY_MASTER_REPAIR_REQUIRED"].add(reason)
    groups={"ENTRY":[r.entry for d in records for r in d.horizons[0].bonds]}
    for h in HORIZONS:groups["TERMINAL_"+str(h)]=[r.terminal for d in records for x in d.horizons if x.horizon_days==h for r in x.bonds if r.terminal]
    market_coverage=tuple(endpoint_coverage(k,v) for k,v in groups.items())
    return signed(Audit(status="COMPLETE",source_task306a1_sha256=source.audit_sha256,market_date_range=(first,last) if first else None,raw_market_dates=dates,
        monthly_entry_dates=tuple(r.as_of_date for r in records if r.monthly_research_entry_date),per_date=tuple(records),readiness_by_horizon=coverage,primary_365d_readiness=prim,
        canonical_market_coverage=market_coverage,raw_recoverability=market_coverage,research_range_scenarios=scenarios,required_backfill_ranges=required,ofz_readiness=tuple(ofz_rows),
        remediation_requirements=tuple(Requirement(category=k,factual_reasons=tuple(sorted(v))) for k,v in sorted(requirements.items()) if v),known_limitations=LIMITS))

class MultiHorizonHistoricalBacktestabilityAuditService:
    def __init__(self,db):self.db=db
    def build(self):
        source=None
        try:
            with self.db.no_autoflush,localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
                connection=rca.consistent_connection(self.db)
                if connection is None:return blocked("CONSISTENT_READ_ONLY_TRANSACTION_REQUIRED")
                source=rca.HistoricalReplayBlockerRootCauseAuditService(self.db).build()
                if source.status=="BLOCKED":return blocked("SOURCE_TASK306A1_BLOCKED",source)
                data=read_evidence(self.db)
                rca.require(rca.original._build(data).audit_sha256==source.source_readiness_audit_sha256)
                with Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as curve_db:
                    return assemble(source,data,OfzReferenceCurveService(curve_db))
        except MonthlyEntryDateNotInSourceRcaGrid:return blocked("MONTHLY_ENTRY_DATE_NOT_IN_SOURCE_RCA_GRID",source)
        except SQLAlchemyError:return blocked("PERSISTED_ENDPOINT_EVIDENCE_UNAVAILABLE",source)
        except (ValueError,TypeError,KeyError,OverflowError,DecimalException):return blocked("ENDPOINT_EVIDENCE_OR_SOURCE_BINDING_INVALID",source)
