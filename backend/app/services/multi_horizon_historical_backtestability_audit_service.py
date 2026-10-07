"""Read-only multi-horizon audit; upstream decision semantics remain frozen."""
from collections import Counter,defaultdict
from datetime import datetime,time,timedelta,timezone
from decimal import Context,DecimalException,ROUND_HALF_EVEN,localcontext
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.schemas.multi_horizon_historical_backtestability import (
    MultiHorizonHistoricalBacktestabilityAuditV2 as Audit, HistoricalHorizonReadinessV1 as Horizon,
    HistoricalMultiHorizonDateReadinessV1 as DateReadiness, HorizonPortfolioCoverageV1 as Portfolio,
    Historical365DayQualificationReadinessV1 as Primary, HistoricalResearchRangeScenarioV1 as Scenario,
    OfzHistoricalHorizonReadinessV1 as Ofz, EndpointCoverageV1 as Coverage, BackfillRequirementV1 as Requirement,
    OfzHistoricalRepresentativeV1 as Representative,
    OfflineSnapshotRepairTargetV2 as OfflineTarget, EndpointRepairTargetV2 as RepairTarget,
    OfzFrequencyEvidenceV2 as FrequencyEvidence,
    BondMembershipV2 as Membership,
)
from app.services import historical_replay_blocker_root_cause_audit_service as rca
from app.services.historical_backtestability_evidence_reader import read_evidence, DecisionIndex
from app.services.historical_audit_canonical_json import digest
from app.services.ofz_reference_curve_service import OfzReferenceCurveService
from app.services.ofz_total_return_benchmark_service import curve_representatives
from app.services.historical_endpoint_evidence import EndpointIndex,CashflowIndex,bond_horizon,HORIZONS,terms_ready

ORDER=("OBSERVED_BEFORE_ENTRY","CURRENT_DIAGNOSTIC_TERMS_READY","ENTRY_SNAPSHOT_FRESH","ENTRY_PRICE_READY","ENTRY_NKD_READY",
    "ENTRY_VALUATION_EVIDENCE_READY","TERMINAL_TARGET_INSIDE_PERSISTED_GLOBAL_RANGE","TERMINAL_OR_REDEMPTION_PATH_EXISTS",
    "TERMINAL_SNAPSHOT_FRESH","TERMINAL_PRICE_READY","TERMINAL_NKD_READY","TERMINAL_VALUATION_EVIDENCE_READY",
    "CASHFLOW_EVENTS_VALID","TERMINAL_OUTCOME_READY")
LIMITS=tuple(sorted(("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN","SURVIVORSHIP_BIAS_RISK","CURRENT_SECURITY_MASTER_NOT_HISTORICAL_PROOF",
    "CURRENT_ISSUER_MAPPING_DIAGNOSTIC","CONTRACTUAL_CASHFLOW_COMPLETENESS_UNPROVEN","HISTORICAL_DIAGNOSTIC_ONLY",
    "ACTUAL_TASK272_AND_TASK277_READINESS_NOT_EVALUATED","DAILY_CONTINUITY_NOT_REQUIRED","NO_PROFITABILITY_CALCULATED")))

def signed(result):
    return result.model_copy(update={"audit_sha256":digest(result)})

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

def make_horizon(day,h,ids,decision,index,flows,first,last,*,retain_detail=True,on_row=None):
    end=day+timedelta(days=h);inside=first is not None and first<=end<=last
    rows=[];counts=Counter();canonical=set();recovered_ids=set();errors=set()
    predicates_by_branch=[{name:set() for name in ORDER} for _ in range(2)]
    for bid in sorted(ids):
        row=bond_horizon(index,flows[bid],bid,day,h)
        if retain_detail:rows.append(row)
        if on_row is not None:on_row(row)
        counts["entry"]+=row.entry.canonical_ready
        counts["terminal"]+=row.terminal is not None and row.terminal.canonical_ready
        counts["redeemed"]+=row.terminal_path=="REDEEMED" and row.canonical_ready
        counts["cashflow"]+=row.cashflow_events_valid;counts["coupon"]+=row.coupon_baseline_observable
        if row.canonical_ready:canonical.add(bid)
        if row.after_raw_recovery_ready:recovered_ids.add(bid)
        errors.update(row.blockers)
        for recovered,predicates in enumerate(predicates_by_branch):
            bid=row.bond_id;e=row.entry;t=row.terminal;red=row.terminal_path=="REDEEMED"
            values=(True,e.current_terms_ready,e.fresh,e.recoverable_price_ready if recovered else e.canonical_price_ready,
                e.recoverable_nkd_ready if recovered else e.canonical_nkd_ready,e.after_raw_recovery_ready if recovered else e.canonical_ready,
                inside,red or t.snapshot_id is not None,red or t.fresh,red or (t.recoverable_price_ready if recovered else t.canonical_price_ready),
                red or (t.recoverable_nkd_ready if recovered else t.canonical_nkd_ready),red or (t.after_raw_recovery_ready if recovered else t.canonical_ready),
                row.cashflow_events_valid,row.after_raw_recovery_ready if recovered else row.canonical_ready)
            for name,ok in zip(ORDER,values):
                if ok:predicates[name].add(bid)
    funnels=tuple(rca.stages(ids,p,ORDER,("TERMINAL_TARGET_INSIDE_PERSISTED_GLOBAL_RANGE",)) for p in predicates_by_branch)
    return Horizon(horizon_days=h,terminal_target_date=end,terminal_target_inside_global_range=inside,
        entry_ready_bond_count=counts["entry"],terminal_market_ready_bond_count=counts["terminal"],
        redeemed_ready_bond_count=counts["redeemed"],cashflow_valid_bond_count=counts["cashflow"],
        terminal_outcome_ready_bond_count=len(canonical),terminal_outcome_ready_after_raw_recovery_count=len(recovered_ids),coupon_baseline_observable_bond_count=counts["coupon"],
        decision_ready_bond_ids=tuple(sorted(canonical&decision)),decision_ready_after_raw_recovery_bond_ids=tuple(sorted(recovered_ids&decision)),
        canonical_funnel=funnels[0],recoverable_funnel=funnels[1],bonds=tuple(rows),blockers=tuple(sorted(errors)))

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
    counts={"dates_with_"+str(n)+suffix:sum(ready_count(r,field)>=n for r in selected)
        for n in (3,5,10) for suffix,field in (("_ready","decision_ready_bond_ids"),("_after_raw_recovery","decision_ready_after_raw_recovery_bond_ids"))}
    return Portfolio(horizon_days=h,monthly_entry_date_count=len(selected),**counts)


def ready_count(row,field):
    name="decision_ready_bond_count" if field=="decision_ready_bond_ids" else "decision_ready_after_raw_recovery_bond_count"
    value=getattr(row,name,None)
    return len(getattr(row,field)) if value is None else value


class MembershipPool:
    def __init__(self):self.ids={}
    def add(self,bonds):
        key=tuple(bonds)
        if key not in self.ids:self.ids[key]=len(self.ids)
        return self.ids[key]
    def horizon(self,row):
        return row.model_copy(update={"decision_ready_membership_id":self.add(row.decision_ready_bond_ids),
            "decision_ready_after_raw_recovery_membership_id":self.add(row.decision_ready_after_raw_recovery_bond_ids),
            "decision_ready_bond_count":len(row.decision_ready_bond_ids),
            "decision_ready_after_raw_recovery_bond_count":len(row.decision_ready_after_raw_recovery_bond_ids),
            "decision_ready_bond_ids":(),"decision_ready_after_raw_recovery_bond_ids":()})
    def result(self):return tuple(Membership(membership_id=n,bond_ids=bonds) for bonds,n in self.ids.items())

def endpoint_coverage(label,endpoints):
    unique={e.snapshot_id:e for e in endpoints if e.snapshot_id is not None};rows=list(unique.values());status=Counter(f.field+":"+f.status for e in rows for f in e.fields)
    ready=sum(e.canonical_price_ready and e.canonical_nkd_ready for e in rows)
    extra=sum(e.recoverable_price_ready and e.recoverable_nkd_ready and not (e.canonical_price_ready and e.canonical_nkd_ready) for e in rows)
    return Coverage(endpoint=label,snapshot_count=len(rows),usable_price_and_nkd=sum(e.canonical_price_ready and e.canonical_nkd_ready for e in rows),
        usable_price_missing_nkd=sum(e.canonical_price_ready and not e.canonical_nkd_ready for e in rows),missing_price_usable_nkd=sum(not e.canonical_price_ready and e.canonical_nkd_ready for e in rows),
        missing_both=sum(not e.canonical_price_ready and not e.canonical_nkd_ready for e in rows),already_ready=ready,additional_ready_from_raw=extra,
        still_unavailable=len(rows)-ready-extra,raw_field_status_counts=tuple(sorted(status.items())))


class CoverageAccumulator:
    """Unique source snapshots, without retaining any endpoint models."""
    def __init__(self,label):
        self.label=label;self.seen={};self.counts=Counter();self.status=Counter();self.errors=Counter()

    def add(self,e):
        self.counts["observations"]+=1;self.errors.update(e.blockers)
        self.counts["missing"]+=e.snapshot_id is None
        if e.snapshot_id is None:return
        economic=(e.current_terms_ready and e.canonical_price_ready and e.canonical_nkd_ready,
            e.current_terms_ready and e.recoverable_price_ready and e.recoverable_nkd_ready)
        if e.snapshot_id in self.seen:
            self.seen[e.snapshot_id]=economic
            return
        self.seen[e.snapshot_id]=economic
        price,nkd=e.canonical_price_ready,e.canonical_nkd_ready
        self.counts["both"]+=price and nkd;self.counts["price"]+=price and not nkd
        self.counts["nkd"]+=not price and nkd;self.counts["neither"]+=not price and not nkd
        self.counts["extra"]+=e.recoverable_price_ready and e.recoverable_nkd_ready and not (price and nkd)
        self.status.update(f.field+":"+f.status for f in e.fields)

    def result(self):
        c=self.counts
        return Coverage(endpoint=self.label,snapshot_count=len(self.seen),usable_price_and_nkd=c["both"],usable_price_missing_nkd=c["price"],
            missing_price_usable_nkd=c["nkd"],missing_both=c["neither"],already_ready=c["both"],additional_ready_from_raw=c["extra"],
            still_unavailable=len(self.seen)-c["both"]-c["extra"],raw_field_status_counts=tuple(sorted(self.status.items())),
            canonical_economic_ready=sum(v[0] for v in self.seen.values()),recoverable_economic_ready=sum(v[1] for v in self.seen.values()),
            blocker_counts=tuple(sorted(self.errors.items())),endpoint_observation_count=c["observations"],missing_snapshot_observation_count=c["missing"])


class RepairTargets:
    def __init__(self,bonds,flows):
        self.bonds=bonds;self.flows=flows;self.offline={};self.groups={};self.events=defaultdict(set)

    def interval(self,bid,scope,role,h,reason,date_index):
        key=(bid,scope,role,h,reason);ranges=self.groups.setdefault(key,[])
        if ranges and date_index<=ranges[-1][1]+1:
            ranges[-1]=(ranges[-1][0],max(ranges[-1][1],date_index))
        else:ranges.append((date_index,date_index))
        return key

    def observe(self,row,scope,h,date_index,day):
        bid=row.bond_id;identity=self.bonds[bid]
        for e in (row.entry,row.terminal):
            if e is None:continue
            for f in e.fields:
                if f.status=="RAW_RECOVERABLE":
                    key=(e.snapshot_id,f.field)
                    if key in self.offline:
                        target=self.offline[key]
                        rca.require((target.bond_id,target.secid,target.isin,target.trade_date,target.value,target.source_fields)==
                            (bid,identity["secid"],identity["isin"],e.trade_date,f.value,f.source_fields))
                    else:
                        self.offline[key]=OfflineTarget(snapshot_id=e.snapshot_id,bond_id=bid,secid=identity["secid"],isin=identity["isin"],
                            trade_date=e.trade_date,field=f.field,value=f.value,source_fields=f.source_fields)
            if not e.after_raw_recovery_ready:
                for reason in e.blockers:self.interval(bid,scope,e.kind,h,reason,date_index)
        if not row.cashflow_events_valid:
            _,errors,_,_,_,_=self.flows[bid].inspect(day,day+timedelta(days=h),identity["maturity_date"])
            for reason in errors:
                key=self.interval(bid,scope,"CASHFLOW",h,reason,date_index)
                # Exact source event IDs, including prior redemption when it is
                # the blocker. No absent payment is replaced with nominal.
                self.events[key].update(r["id"] for r in self.flows[bid].rows if r["event_date"]<=day+timedelta(days=h))

    def result(self):
        rows=[]
        for (bid,scope,role,h,reason),ranges in sorted(self.groups.items()):
            identity=self.bonds[bid];key=(bid,scope,role,h,reason)
            rows.append(RepairTarget(bond_id=bid,secid=identity["secid"],isin=identity["isin"],scope=scope,role=role,horizon_days=h,
                reason=reason,entry_date_index_ranges=tuple(ranges),event_ids=tuple(sorted(self.events[key])),maturity_date=identity["maturity_date"]))
        return tuple(self.offline[k] for k in sorted(self.offline)),tuple(rows)

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


def ofz_readiness(day,base,curve,index,data,endpoints,flows,first,last,*,retain_detail=True,on_row=None):
    """Task304 selects identities; this audit checks only diagnostic prerequisites."""
    ids=tuple(sorted(bid for node in curve.nodes for bid in node.component_bond_ids))
    frequency=tuple((bid,frequency_status(bid,index.profiles.get(bid),data.tables["bond_security_master_evidence"],day)) for bid in sorted(index.ofz))
    statuses=dict(frequency);representatives=[]
    for node,component in curve_representatives(curve,day-timedelta(days=1)) if curve.status=="READY" else ():
        bid,snapshot,secid,isin,ytm=component;profile=index.profiles.get(bid);errors=set()
        if not terms_ready(profile) or profile.get("coupon_structure")!="fixed" or profile.get("perpetual_structure")!="dated":
            errors.add("OFZ_CURRENT_STRUCTURAL_PREREQUISITES_UNAVAILABLE")
        status=statuses[bid]
        if status!="ALREADY_VERIFIED":errors.add("OFZ_FREQUENCY_"+status)
        if not 1<=(day-curve.curve_trade_date).days<=7:errors.add("OFZ_REPRESENTATIVE_ENTRY_STALE")
        representatives.append(Representative(bond_id=bid,snapshot_id=snapshot,secid=secid,isin=isin,trade_date=curve.curve_trade_date,
            source_node_macaulay_duration_years=node.duration_years,source_node_yield_pct=node.yield_to_maturity_pct,component_yield_pct=ytm,
            frequency_recoverability=status,modified_duration_prerequisites_ready=not errors,blockers=tuple(sorted(errors)),
            security_master_profile_id=profile.get("id") if profile else None,
            frequency_evidence_ids=tuple(sorted(r["id"] for r in data.tables["bond_security_master_evidence"] if r["bond_id"]==bid))))
    selected=tuple(r.bond_id for r in representatives)
    prerequisites=bool(representatives) and all(r.modified_duration_prerequisites_ready for r in representatives)
    blockers={code for r in representatives for code in r.blockers}
    if curve.status!="READY":blockers.add("OFZ_CURVE_NOT_READY")
    elif not prerequisites:blockers.add("OFZ_MODIFIED_DURATION_PREREQUISITES_UNAVAILABLE")
    return Ofz(as_of_date=day,selection_as_of_date=day-timedelta(days=1),task306a1_curve_status_at_t=base.task268_status,
        curve_status=curve.status,curve_trade_date=curve.curve_trade_date,node_count=curve.node_count,component_bond_ids=ids,
        curve_ready=curve.status=="READY",representatives=tuple(representatives),representative_bond_ids=selected,
        modified_duration_prerequisites_ready=prerequisites,frequency_recoverability=frequency,
        horizons=tuple(make_horizon(day,h,set(selected),set(selected),endpoints,flows,first,last,retain_detail=retain_detail,
            on_row=(lambda bond,h=h:on_row(bond,h)) if on_row else None) for h in HORIZONS),blockers=tuple(sorted(blockers)))


def ofz_bond_requirements(bond,day,h,requirements):
    binding=":BOND_ID="+str(bond.bond_id)+":DATE="+day.isoformat()+":HORIZON="+str(h)
    if not bond.cashflow_events_valid:
        requirements["CASHFLOW_HISTORY_REPAIR_REQUIRED"].add("OFZ_REPRESENTATIVE_CASHFLOW_INVALID"+binding)
    for endpoint in (bond.entry,bond.terminal):
        if endpoint is None:continue
        if not endpoint.fresh or not endpoint.recoverable_price_ready or not endpoint.recoverable_nkd_ready:
            requirements["TARGETED_ENDPOINT_MOEX_BACKFILL_REQUIRED"].add("OFZ_REPRESENTATIVE_"+endpoint.kind+"_EVIDENCE_UNAVAILABLE"+binding)
        fields={f.field for f in endpoint.fields if f.status=="RAW_RECOVERABLE"}
        if fields:
            price=bool(fields&{"price","clean_price"});nkd="nkd" in fields
            category="OFFLINE_RAW_PRICE_AND_NKD_REPAIR_REQUIRED" if price and nkd else "OFFLINE_RAW_PRICE_REPAIR_REQUIRED" if price else "OFFLINE_RAW_NKD_REPAIR_SUFFICIENT"
            requirements[category].add("OFZ_REPRESENTATIVE_SAFE_RAW_"+endpoint.kind+binding)


def ofz_remediation(rows,requirements):
    """Non-selected members never generate repair requirements."""
    for row in rows:
        if row.curve_status=="NO_FRESH_MARKET_DATA":
            requirements["TARGETED_ENDPOINT_MOEX_BACKFILL_REQUIRED"].add("OFZ_CURVE_EVIDENCE_UNAVAILABLE:DATE="+row.selection_as_of_date.isoformat())
        for rep in row.representatives:
            binding=":BOND_ID="+str(rep.bond_id)+":DATE="+row.as_of_date.isoformat()
            if rep.frequency_recoverability!="ALREADY_VERIFIED":
                requirements["OFZ_SECURITY_MASTER_REPAIR_REQUIRED"].add(rep.frequency_recoverability+binding)
            if "OFZ_CURRENT_STRUCTURAL_PREREQUISITES_UNAVAILABLE" in rep.blockers:
                requirements["OFZ_SECURITY_MASTER_REPAIR_REQUIRED"].add("CURRENT_STRUCTURAL_PREREQUISITES_UNAVAILABLE"+binding)
        for horizon in row.horizons:
            for bond in horizon.bonds:
                ofz_bond_requirements(bond,row.as_of_date,horizon.horizon_days,requirements)


def assemble(source,data,curve_service):
    index=DecisionIndex(data) if data.tables["bond_market_snapshots"] and hasattr(data.tables["bond_market_snapshots"][0],"proofs") else rca.EvidenceIndex(data)
    endpoints=EndpointIndex(data.tables["bond_market_snapshots"],index.profiles,index.bonds)
    flows={bid:CashflowIndex(index.flows[bid]) for bid in index.bonds}
    dates=tuple(sorted(r["trade_date"] for r in data.market_date_counts));first=dates[0] if dates else None;last=dates[-1] if dates else None
    monthly_dates=set(frozen_monthly_dates(dates,(r.as_of_date for r in source.per_date)))
    records=[];ofz_rows=[];requirements=defaultdict(set);memberships=MembershipPool()
    groups={k:CoverageAccumulator(k) for k in ("ENTRY","TERMINAL_90","TERMINAL_180","TERMINAL_365")}
    targets=RepairTargets(index.bonds,flows)
    primary_selected_count=0;primary_all_ready=True;primary_missing=False;primary_cash_bad=False;primary_errors=set();recovered_fields=set()
    date_positions={day:n for n,day in enumerate(dates)}
    for base in source.per_date:
        day=base.as_of_date;observed={bid for bid,ds in index.days.items() if ds[0]<day}
        _,_,keys,cohorts=rca.credit_funnel(day,observed,index)
        _,_,decision,_,_,_,_,_=rca.joint_funnel(day,observed,index,keys,set())
        rca.require(len(decision)==base.decision_only_intersection_count)
        monthly=day in monthly_dates
        def observe(bond,h):
            nonlocal primary_selected_count,primary_all_ready,primary_missing,primary_cash_bad
            if h==90:groups["ENTRY"].add(bond.entry)
            if bond.terminal:groups["TERMINAL_"+str(h)].add(bond.terminal)
            if monthly and h==365 and bond.bond_id in decision:
                primary_selected_count+=1;primary_all_ready=primary_all_ready and bond.canonical_ready
                primary_missing=primary_missing or (not bond.after_raw_recovery_ready and (not bond.entry.after_raw_recovery_ready or bond.terminal is not None and not bond.terminal.after_raw_recovery_ready))
                primary_cash_bad=primary_cash_bad or not bond.cashflow_events_valid;primary_errors.update(bond.blockers)
                recovered_fields.update(f.field for e in (bond.entry,bond.terminal) if e for f in e.fields if f.status=="RAW_RECOVERABLE")
                targets.observe(bond,"CORPORATE_PRIMARY",h,date_positions[day],day)
        horizons=tuple(make_horizon(day,h,observed,decision,endpoints,flows,first,last,retain_detail=False,on_row=lambda bond,h=h:observe(bond,h)) for h in HORIZONS)
        records.append(DateReadiness(as_of_date=day,monthly_research_entry_date=monthly,decision_only_bond_ids=(),decision_membership_id=memberships.add(tuple(sorted(decision))),
            decision_only_intersection_count=len(decision),credit_prerequisite_count=len(rca.qualified(cohorts)),horizons=tuple(memberships.horizon(h) for h in horizons)))
        selection=day-timedelta(days=1);curve=curve_service.build_curve(selection,market_source="moex",max_curve_age_days=7)
        rca.curve_valid(curve,selection,index)
        def observe_ofz(bond,h):
            targets.observe(bond,"OFZ_REPRESENTATIVE",h,date_positions[day],day)
            ofz_bond_requirements(bond,day,h,requirements)
        ofz_row=ofz_readiness(day,base,curve,index,data,endpoints,flows,first,last,retain_detail=False,on_row=observe_ofz)
        ofz_rows.append(ofz_row.model_copy(update={"horizons":tuple(memberships.horizon(h) for h in ofz_row.horizons)}))
    coverage=tuple(portfolio(records,h) for h in HORIZONS);primary=coverage[-1]
    annual=[r.horizons[-1] for r in records if r.monthly_research_entry_date]
    def classification(field):
        counts=[ready_count(r,field) for r in annual]
        return "RESEARCH_WINDOWS_AVAILABLE" if any(n>=3 for n in counts) else "PARTIAL_365D_WINDOWS" if any(counts) else "NO_365D_WINDOWS"
    prim=Primary(monthly_entry_date_count=len(annual),entry_dates_with_terminal_global_range=sum(r.terminal_target_inside_global_range for r in annual),
        dates_with_any_terminal_outcome=sum(ready_count(r,"decision_ready_bond_ids")>0 for r in annual),dates_with_any_after_raw_recovery=sum(ready_count(r,"decision_ready_after_raw_recovery_bond_ids")>0 for r in annual),
        classification=classification("decision_ready_bond_ids"),after_raw_recovery_classification=classification("decision_ready_after_raw_recovery_bond_ids"),coverage=primary)
    required,scenarios=ranges(first,last)
    requirements["HISTORICAL_UNIVERSE_CAPTURE_REQUIRED"].add("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN")
    requirements["SECURITY_MASTER_VERSIONING_REQUIRED"].add("CURRENT_TERMS_NOT_HISTORICAL_PROOF")
    if required and not required[0].range_covered and not primary.dates_with_3_after_raw_recovery:requirements["BROAD_HISTORICAL_MOEX_BACKFILL_REQUIRED"].add("FIRST_ANNUAL_MONTHLY_WINDOW_RANGE_MISSING")
    elif primary_missing:
        requirements["TARGETED_ENDPOINT_MOEX_BACKFILL_REQUIRED"].add("PRIMARY_ENDPOINT_EVIDENCE_MISSING")
    if recovered_fields:
        price=bool(recovered_fields&{"price","clean_price"});nkd="nkd" in recovered_fields
        category="OFFLINE_RAW_PRICE_AND_NKD_REPAIR_REQUIRED" if price and nkd else "OFFLINE_RAW_PRICE_REPAIR_REQUIRED" if price else "OFFLINE_RAW_NKD_REPAIR_SUFFICIENT"
        requirements[category].add("SAFE_SAME_SNAPSHOT_RAW_EVIDENCE_AVAILABLE")
    if primary_selected_count and primary_all_ready:requirements["NO_MARKET_REPAIR_REQUIRED"].add("PRIMARY_CANONICAL_ENDPOINTS_READY")
    if primary_cash_bad:requirements["CASHFLOW_HISTORY_REPAIR_REQUIRED"].update(primary_errors&{"CASHFLOW_AMOUNT_OR_CURRENCY_INVALID","DUPLICATE_AUTOMATIC_EVENT","AUTOMATIC_EVENT_AFTER_REDEMPTION","UNSUPPORTED_AUTOMATIC_EVENT","MATURITY_REDEMPTION_EVIDENCE_MISSING"})
    ofz_remediation(ofz_rows,requirements)
    market_coverage=tuple(v.result() for v in groups.values())
    offline_targets,endpoint_targets=targets.result()
    representative_ids={r.bond_id for row in ofz_rows for r in row.representatives}
    frequency_evidence=[]
    for row in data.tables["bond_security_master_evidence"]:
        if row["bond_id"] not in representative_ids or row["field_name"]!="coupon_frequency_per_year":continue
        payload=row["normalized_value_json"];value=payload.get("value") if isinstance(payload,dict) else None
        frequency_evidence.append(FrequencyEvidence(evidence_id=row["id"],bond_id=row["bond_id"],source=row["source"],
            contract_version=row["contract_version"],observed_at=row["observed_at"],positive_exact_int_value=value if type(value) is int and value>0 else None))
    return signed(Audit(status="COMPLETE",source_task306a1_sha256=source.audit_sha256,market_date_range=(first,last) if first else None,raw_market_dates=dates,
        monthly_entry_dates=tuple(r.as_of_date for r in records if r.monthly_research_entry_date),per_date=tuple(records),readiness_by_horizon=coverage,primary_365d_readiness=prim,
        canonical_market_coverage=market_coverage,raw_recoverability=market_coverage,research_range_scenarios=scenarios,required_backfill_ranges=required,ofz_readiness=tuple(ofz_rows),
        remediation_requirements=tuple(Requirement(category=k,factual_reasons=tuple(sorted(v))) for k,v in sorted(requirements.items()) if v),
        offline_repair_targets=offline_targets,endpoint_repair_targets=endpoint_targets,
        ofz_frequency_evidence=tuple(sorted(frequency_evidence,key=lambda r:r.evidence_id)),bond_memberships=memberships.result(),known_limitations=LIMITS))

class MultiHorizonHistoricalBacktestabilityAuditService:
    def __init__(self,db):self.db=db
    def build(self):
        source=None
        try:
            with self.db.no_autoflush,localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
                connection=rca.consistent_connection(self.db)
                if connection is None:return blocked("CONSISTENT_READ_ONLY_TRANSACTION_REQUIRED")
                source=rca.HistoricalReplayBlockerRootCauseAuditService(self.db).build_linkage()
                if source.status=="BLOCKED":return blocked("SOURCE_TASK306A1_BLOCKED",source)
                data=read_evidence(self.db)
                with Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as curve_db:
                    return assemble(source,data,OfzReferenceCurveService(curve_db))
        except MonthlyEntryDateNotInSourceRcaGrid:return blocked("MONTHLY_ENTRY_DATE_NOT_IN_SOURCE_RCA_GRID",source)
        except SQLAlchemyError:return blocked("PERSISTED_ENDPOINT_EVIDENCE_UNAVAILABLE",source)
        except (ValueError,TypeError,KeyError,OverflowError,DecimalException):return blocked("ENDPOINT_EVIDENCE_OR_SOURCE_BINDING_INVALID",source)
