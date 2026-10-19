"""Offline Task308A input qualification, sampling and source-result evaluation."""
from collections import defaultdict
from datetime import timedelta
import re
from app.schemas.historical_discovery_coverage_audit import HistoricalDiscoveryCoverageAudit
from app.schemas.historical_discovery_checkpoint import DiscoveryManifestIndex
from app.schemas.historical_identity_feasibility import (
    Context, SampleUnit, Stratum, SamplePlan, Diagnosis, SourceCapability,
    SourceResult, EvaluatedUnit, StratumEvaluation, FeasibilityReport,
)
from app.services.historical_audit_canonical_json import digest
from app.services.historical_evidence_normalization import sha, checked, signed, hash_syntax
from app.services.historical_discovery_checkpoint import fingerprint
from app.services.historical_discovery_coverage_audit import metric
from app.services.ofz_identity import is_ofz_instrument


SOURCE_ORDER = ("REFERENCE","DESCRIPTION")
STRATA = ("LISTING_ONLY","POSSIBLE_INACTIVE_PROXY","CANONICAL_OFZ","MULTI_OBSERVED_BOARD",
    "LISTING_INTERVAL_SUPPLIED","LISTING_INTERVAL_MISSING","EARLY_FIRST","LATE_FIRST",
    "OLDER_LAST","RECENT_LAST","ACTUAL_TRADES","USABLE_PRICE_NKD","SHARED","SINGLE_OBSERVED_BOARD")


class IdentityError(Exception):
    def __init__(self, code):
        self.code=code
        super().__init__(code)


def require(condition, code):
    if not condition: raise IdentityError(code)


def validate_report(report, *, manifest=None, expected_audit_sha256=None, expected_manifest_sha256=None):
    require(type(report) is HistoricalDiscoveryCoverageAudit,"TASK307_REPORT_REQUIRED")
    HistoricalDiscoveryCoverageAudit.model_validate({k:getattr(report,k) for k in type(report).model_fields})
    require(digest(report)==report.audit_sha256,"TASK307_AUDIT_SHA_MISMATCH")
    require(report.status=="COMPLETE","TASK307_NOT_COMPLETE")
    for name in ("source_manifest_sha256","source_content_sha256","scope_sha256","implementation_sha256","audit_sha256"):
        hash_syntax(getattr(report,name))
    require(report.implementation_sha256==fingerprint(),"SOURCE_IMPLEMENTATION_MISMATCH")
    for expected,actual in ((expected_audit_sha256,report.audit_sha256),(expected_manifest_sha256,report.source_manifest_sha256)):
        if expected is not None:
            hash_syntax(expected);require(expected==actual,"EXPECTED_PROVENANCE_MISMATCH")
    c=report.capabilities
    require(all(getattr(c,n) is False for n in ("historical_universe_complete","pit_ready","acquisition_ready","db_mutation","network_access")),"UPSTREAM_SAFETY_MISMATCH")
    integrity=report.evidence_integrity
    require(integrity.verification_level=="FULL_ACCEPTED_GENERATION_REPLAY" and
        all(getattr(integrity,n) is True for n in ("manifest_verified","indexes_verified","receipt_pages_verified","index_replay_verified","reconciliation_verified")),"UPSTREAM_INTEGRITY_UNPROVEN")
    d,u,ic,dates=report.discovery_coverage,report.universe_coverage,report.identity_coverage,report.date_coverage
    observed=dates.observed_dates
    require(observed==tuple(sorted(set(observed))) and all(d.history_start<=day<=d.cutoff for day in observed),"UPSTREAM_DATES_INVALID")
    pairs=set();population=defaultdict(list);secid_boards=set();isins=set();listing=history=shared=missing=multi=0
    for item in ic.identities:
        require(type(item.secid) is str and bool(item.secid.strip()),"SOURCE_SECID_INVALID")
        require(item.isin is None or (type(item.isin) is str and bool(item.isin.strip())),"SOURCE_ISIN_INVALID")
        key=(item.secid,item.isin);require(key not in pairs,"DUPLICATE_EXACT_IDENTITY")
        pairs.add(key);population[item.secid].append(item)
        if item.isin is not None: isins.add(item.isin)
        require(item.listing or item.observed,"EMPTY_IDENTITY_MEMBERSHIP")
        for boards in (item.listing_boards,item.observed_boards):
            require(boards==tuple(sorted(set(boards))) and all(type(b) is str and b.strip() for b in boards),"UPSTREAM_BOARDS_INVALID")
        require(item.listing or not item.listing_boards,"UPSTREAM_MEMBERSHIP_INVALID")
        require(item.observed==bool(item.observed_date_count),"UPSTREAM_OBSERVATION_INVALID")
        require(0<=item.actual_trade_date_count<=item.observed_date_count<=len(observed) and
            0<=item.usable_price_nkd_date_count<=item.observed_date_count,"UPSTREAM_IDENTITY_COUNTS_INVALID")
        if item.observed:
            require(item.observed_boards and item.first_observed_date in observed and item.last_observed_date in observed and
                    item.first_observed_date<=item.last_observed_date,"UPSTREAM_OBSERVATION_INVALID")
        else:
            require(not item.observed_boards and item.first_observed_date is None and item.last_observed_date is None,"UPSTREAM_OBSERVATION_INVALID")
        if item.first_listing_interval and item.last_listing_interval:
            require(item.first_listing_interval<=item.last_listing_interval,"UPSTREAM_LISTING_INTERVAL_INVALID")
        boards=set(item.listing_boards)|set(item.observed_boards)
        secid_boards.update((item.secid,b) for b in boards)
        listing+=item.listing;history+=item.observed;shared+=item.listing and item.observed
        missing+=item.isin is None;multi+=len(boards)>1
    conflicts=0
    for items in population.values():
        candidates=tuple(sorted({i.isin for i in items if i.isin is not None}))
        expected=candidates if len(candidates)>1 else ()
        conflicts+=len(candidates)>1
        require(all(i.conflicting_isin_candidates==expected for i in items),"UPSTREAM_CONFLICT_MEMBERSHIP_INVALID")
    checks={"distinct_secids":len(population),"distinct_isins":len(isins),"exact_pairs":len(pairs),"secid_board_pairs":len(secid_boards),
        "listing_pairs":listing,"observed_pairs":history,"shared_pairs":shared,"listing_only_pairs":listing-shared,
        "observed_only_pairs":history-shared,"missing_isin_pairs":missing,"affected_missing_isin_secids":len({s for s,i in pairs if i is None}),
        "conflicting_secids":conflicts,"multi_board_pairs":multi,
        "not_observed_on_last_market_date_pairs":sum(i.observed and i.last_observed_date!=observed[-1] for i in ic.identities) if observed else 0}
    require(all(getattr(u,k)==v for k,v in checks.items()),"TASK307_POPULATION_RECONCILIATION_FAILED")
    require(u.identity_records>=len(pairs) and d.completed_partitions==d.requested_partitions and
        d.accepted_pages>=d.completed_partitions,"UPSTREAM_ENVELOPE_INVALID")
    require({b.board for b in report.board_coverage}=={b for _,b in secid_boards} and
        len(report.board_coverage)==len({b.board for b in report.board_coverage}),"UPSTREAM_BOARD_MEMBERSHIP_INVALID")
    if manifest is not None:
        require(type(manifest) is DiscoveryManifestIndex,"A4_MANIFEST_REQUIRED")
        checked(manifest,"source_manifest_sha256")
        require((manifest.source_manifest_sha256,manifest.source_content_sha256,manifest.scope_sha256,manifest.implementation_sha256)==
            (report.source_manifest_sha256,report.source_content_sha256,report.scope_sha256,report.implementation_sha256),"A4_MANIFEST_BINDING_MISMATCH")
        require(manifest.exact_binding_count==u.exact_pairs and manifest.identity_count==u.identity_records and
                manifest.observed_dates==observed and manifest.policy.cutoff==d.cutoff and manifest.policy.history_start==d.history_start,"A4_MANIFEST_COUNTS_MISMATCH")
    context=Context(task307_audit_sha256=report.audit_sha256,source_manifest_sha256=report.source_manifest_sha256,
        source_content_sha256=report.source_content_sha256,scope_sha256=report.scope_sha256,source_implementation_sha256=report.implementation_sha256,
        history_start=d.history_start,cutoff=d.cutoff,last_observed_date=observed[-1] if observed else None)
    return context,{s:tuple(sorted(items,key=lambda i:(i.isin is not None,i.isin or ""))) for s,items in population.items()}


def memberships(context, identities):
    midpoint=context.history_start+timedelta(days=(context.cutoff-context.history_start).days//2)
    first=min((i.first_observed_date for i in identities if i.observed),default=None)
    last=max((i.last_observed_date for i in identities if i.observed),default=None)
    boards={b for i in identities for b in i.observed_boards}
    listing=any(i.listing for i in identities);observed=any(i.observed for i in identities)
    intervals=any(i.first_listing_interval is not None or i.last_listing_interval is not None for i in identities)
    tests={"LISTING_ONLY":listing and not observed,"SHARED":listing and observed,
        "POSSIBLE_INACTIVE_PROXY":last is not None and context.last_observed_date is not None and last<context.last_observed_date-timedelta(days=90),
        "CANONICAL_OFZ":any(is_ofz_instrument(secid=i.secid,isin=i.isin) for i in identities),
        "MULTI_OBSERVED_BOARD":len(boards)>1,"SINGLE_OBSERVED_BOARD":len(boards)==1,
        "LISTING_INTERVAL_SUPPLIED":intervals,"LISTING_INTERVAL_MISSING":not intervals,
        "EARLY_FIRST":first is not None and first<=midpoint,"LATE_FIRST":first is not None and first>midpoint,
        "OLDER_LAST":last is not None and last<=midpoint,"RECENT_LAST":last is not None and last>midpoint,
        "ACTUAL_TRADES":any(i.actual_trade_date_count>0 for i in identities),
        "USABLE_PRICE_NKD":any(i.usable_price_nkd_date_count>0 for i in identities)}
    return tuple(sorted(k for k,v in tests.items() if v))


def source_matrix():
    rows=[("REFERENCE","https://iss.moex.com/iss/reference/205","iss.moex.com","GET /iss/securities.json?q=raw_SECID; no trading/market filters",("secid","isin","type","group"),True,1),
          ("DESCRIPTION","https://iss.moex.com/iss/reference/193","iss.moex.com","GET /iss/securities/{safe_raw_SECID}.json; description name/value table",("SECID","ISIN","TYPE"),True,1),
          ("MOEX_LISTING_ARCHIVES","https://www.moex.com/n32224","www.moex.com","Documented archive directory; not enabled for Task308A probe",("SecurityID","ISIN"),False,2),
          ("NNA_REGISTRATION_RESEARCH","https://anna-web.org/identifiers/",None,"Official numbering/registration research; no automated endpoint assumed",("ISIN",),False,3)]
    return tuple(SourceCapability(source=s,documentation_url=url,host=host,request_contract=request,expected_fields=fields,
        provenance="CURRENT_REFERENCE_OR_DATED_ARCHIVE_CANDIDATE_NOT_HISTORICAL_AUTHORITY",
        limits_and_access="Task308A reviewed ceilings only; historical coverage/licensing/access require separate verification",
        historical_limitations="No guaranteed old/matured/delisted coverage or publication proof",
        unknowns=("ISIN availability for historical SECIDs","Historical publication and completeness","Current access/usage conditions"),
        probe_supported=probe,priority=priority) for s,url,host,request,fields,probe,priority in rows)


def diagnoses():
    return (Diagnosis(explanation="DISCOVERY maps only supplied ISIN/ISINCODE; no field-removal filter",classification="CONFIRMED_BY_CODE",evidence="A4 identity and route code; absent values stay null"),
        Diagnosis(explanation="Historical tables universally omit ISIN",classification="REQUIRES_SOURCE_PROBE",evidence="Provided Task307 confirms null bindings, not complete raw source column schemas"),
        Diagnosis(explanation="q=SECID guarantees an exact result",classification="CONTRADICTED",evidence="MOEX reference/205 documents partial search; exact post-filter required"),
        Diagnosis(explanation="Separate specification route exists",classification="CONFIRMED_BY_OFFICIAL_DOCUMENTATION",evidence="MOEX reference/193; no guaranteed ISIN or extinct coverage"),
        Diagnosis(explanation="All historical identities are recoverable from current data",classification="UNKNOWN",evidence="Only an explicitly authorized diagnostic probe can supply sample observations"))


class HistoricalIdentityFeasibilityService:
    def build(self, *, report, sample_size=36, manifest=None, expected_audit_sha256=None, expected_manifest_sha256=None):
        require(type(sample_size) is int and 1<=sample_size<=48,"SAMPLE_SIZE_INVALID")
        context,population=validate_report(report,manifest=manifest,expected_audit_sha256=expected_audit_sha256,expected_manifest_sha256=expected_manifest_sha256)
        require(len(population)>=sample_size,"INSUFFICIENT_SAMPLE_POPULATION")
        features={s:memberships(context,items) for s,items in population.items()}
        groups={name:tuple(sorted(s for s in population if name in features[s])) for name in STRATA}
        positions=dict.fromkeys(STRATA,0);selected={}
        while len(selected)<sample_size:
            changed=False
            for name in STRATA:
                group=groups[name]
                while positions[name]<len(group) and group[positions[name]] in selected:positions[name]+=1
                if positions[name]<len(group):
                    s=group[positions[name]];positions[name]+=1;selected[s]=name;changed=True
                    if len(selected)==sample_size:break
            if not changed:break
        for s in sorted(population):
            if len(selected)==sample_size:break
            selected.setdefault(s,"POPULATION_FILL")
        samples=tuple(SampleUnit(secid=s,original_identities=population[s],strata=features[s],selection_reason=selected[s]) for s in sorted(selected))
        strata=tuple(Stratum(name=name,available=bool(groups[name]),eligible_count=len(groups[name]),
            selected_count=sum(name in features[s] for s in selected),selected_secids=tuple(s for s in sorted(selected) if name in features[s]),
            overlap_counts=tuple((other,sum(name in features[s] and other in features[s] for s in selected)) for other in sorted(STRATA) if other!=name),
            represented=any(name in features[s] for s in selected)) for name in STRATA)
        return signed(SamplePlan,"plan_sha256",context=context,requested_sample_size=sample_size,population_secids=len(population),
            samples=samples,strata=strata,diagnoses=diagnoses(),source_matrix=source_matrix())


def validate_plan(plan):
    require(type(plan) is SamplePlan,"SAMPLE_PLAN_REQUIRED");checked(plan,"plan_sha256")
    for name in ("task307_audit_sha256","source_manifest_sha256","source_content_sha256","scope_sha256","source_implementation_sha256"):
        hash_syntax(getattr(plan.context,name))
    require(plan.context.history_start<=plan.context.cutoff and (plan.context.last_observed_date is None or
        plan.context.history_start<=plan.context.last_observed_date<=plan.context.cutoff),"SAMPLE_PLAN_CONTEXT_INVALID")
    secids=tuple(s.secid for s in plan.samples)
    require(secids==tuple(sorted(set(secids))) and len(secids)==plan.requested_sample_size<=plan.population_secids,"SAMPLE_PLAN_MEMBERSHIP_INVALID")
    for sample in plan.samples:
        require(sample.original_identities and all(i.secid==sample.secid for i in sample.original_identities),"SAMPLE_PLAN_IDENTITY_INVALID")
        require(len({i.isin for i in sample.original_identities})==len(sample.original_identities),"DUPLICATE_EXACT_IDENTITY")
        require(sample.strata==memberships(plan.context,sample.original_identities),"SAMPLE_PLAN_STRATUM_INVALID")
    require(plan.source_matrix==source_matrix() and plan.diagnoses==diagnoses(),"SOURCE_CONTRACT_VERSION_MISMATCH")
    require(tuple(s.name for s in plan.strata)==STRATA,"SAMPLE_PLAN_STRATUM_INVALID")
    for stratum in plan.strata:
        members=tuple(s.secid for s in plan.samples if stratum.name in s.strata)
        require(stratum.selected_secids==members and stratum.selected_count==len(members)<=stratum.eligible_count and
            stratum.available==(stratum.eligible_count>0) and stratum.represented==bool(members),"SAMPLE_PLAN_STRATUM_INVALID")
        require(stratum.overlap_counts==tuple((other,sum(stratum.name in s.strata and other in s.strata for s in plan.samples)) for other in sorted(STRATA) if other!=stratum.name),"SAMPLE_PLAN_OVERLAP_INVALID")
    return plan


def isin_valid(value):
    if type(value) is not str or not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]",value): return False
    digits="".join(str(ord(c)-55) if "A"<=c<="Z" else c for c in value)
    return sum((int(c)*2//10+int(c)*2%10) if i%2 else int(c) for i,c in enumerate(reversed(digits)))%10==0


def field(row, *aliases):
    values=[v for k,v in row.items() if k.upper() in aliases and v is not None and v!=""]
    require(all(type(v) is str for v in values),"SOURCE_IDENTITY_TYPE_INVALID")
    require(len(set(values))<=1,"SOURCE_IDENTITY_ALIAS_CONFLICT")
    return values[0] if values else None


def resolve_rows(*, secid, source, rows, receipts, complete, failure=None):
    exact=[];candidates=[];types=[];publications=[];effective=[];review=False;diagnostics=[]
    status="NOT_PROBED" if not receipts else "SOURCE_RESPONSE_INCOMPLETE"
    isins=();returned=None
    try:
        candidates=[field(r,"SECID") for r in rows]
        exact=[r for r,s in zip(rows,candidates) if s==secid]
        raw_isins=[field(r,"ISIN","ISINCODE") for r in exact]
        isins=tuple(sorted({v for v in raw_isins if v is not None}))
        invalid=any(not isin_valid(v) for v in isins)
        for r in exact:
            for k,v in r.items():
                if k.upper() in ("TYPE","GROUP","SECURITY_TYPE") and v is not None:
                    if type(v) is not str:raise IdentityError("SOURCE_TYPE_INVALID")
                    types.append(v)
                    if v not in ("stock_bonds","corporate_bond","ofz_bond","municipal_bond","subfederal_bond","exchange_bond"):
                        review=True;diagnostics.append("INSTRUMENT_TYPE_REQUIRES_REVIEW")
                if k.upper() in ("PUBLICATION_DATE","PUBLISHED_AT","PUBLICATION_TIMESTAMP") and v is not None:publications.append(str(v))
                if k.upper() in ("EFFECTIVE_DATE","VALID_FROM") and v is not None:effective.append(str(v))
        if complete:
            if not rows:status="SOURCE_RECORD_UNAVAILABLE"
            elif not exact:status="SOURCE_MATCH_AMBIGUOUS" if len(rows)>1 else "SOURCE_RESULT_NOT_EXACT"
            elif invalid:status="SOURCE_INVALID";diagnostics.append("ISIN_SYNTAX_OR_CHECK_DIGIT_INVALID")
            elif len(isins)>1:status="EXACT_CURRENT_SOURCE_BINDING_CONFLICT"
            elif not isins:status="ISIN_NOT_PRESENT"
            else:status="EXACT_CURRENT_SOURCE_BINDING_FOUND";returned=isins[0]
        elif failure:status=failure
    except IdentityError as exc:
        status="SOURCE_INVALID";diagnostics.append(exc.code);review=True;isins=()
    # Unbounded malformed raw strings stay in the bounded response artifact, not summaries.
    isins=tuple(v for v in isins if len(v)<=64)
    return SourceResult(secid=secid,source=source,status=status,request_sha256s=tuple(r.request_sha256 for r in receipts),
        response_sha256s=tuple(r.response_sha256 for r in receipts if r.response_sha256),observation_times=tuple(r.observed_at for r in receipts),
        retrieval_times=tuple(r.observed_at for r in receipts),
        returned_secid=secid if exact else None,returned_isin=returned,returned_isin_candidates=isins,
        candidate_count=len(rows),exact_candidate_count=len(exact),current_binding_observed=status=="EXACT_CURRENT_SOURCE_BINDING_FOUND",
        complete_response=complete,attempt_count=len(receipts),transient_failure_count=sum(r.outcome=="TRANSIENT_SOURCE_FAILURE" for r in receipts),
        requires_review=review,diagnostics=tuple(sorted(set(diagnostics))),source_type_values=tuple(sorted(set(types))),
        source_reported_publication_values=tuple(sorted(set(publications))),source_reported_effective_values=tuple(sorted(set(effective))))


def evaluate_results(plan, authorization, manifest, results):
    units=[]
    for sample in plan.samples:
        sources=tuple(results[(sample.secid,s)] for s in SOURCE_ORDER)
        probed=any(r.request_sha256s for r in sources)
        found={r.returned_isin for r in sources if r.current_binding_observed}
        baseline={i.isin for i in sample.original_identities if i.isin is not None}
        conflict=any(r.status=="EXACT_CURRENT_SOURCE_BINDING_CONFLICT" for r in sources) or len(found)>1 or len(baseline)>1 or bool(found and baseline and found!=baseline)
        recovered=next(iter(found)) if len(found)==1 and not conflict else None
        status="EXACT_CURRENT_SOURCE_BINDING_CONFLICT" if conflict else "EXACT_CURRENT_SOURCE_BINDING_FOUND" if recovered else next((r.status for r in sources if r.status!="NOT_PROBED"),"NOT_PROBED")
        units.append(EvaluatedUnit(sample=sample,approved=sample.secid in authorization.secids,actually_probed=probed,results=sources,
            status=status,recovered_current_isin=recovered,requires_review=conflict or any(r.requires_review for r in sources)))
    approved=len(authorization.secids);probed=sum(u.actually_probed for u in units)
    good=sum(u.recovered_current_isin is not None and not u.requires_review for u in units)
    all_results=[r for u in units if u.approved for r in u.results if r.source in authorization.sources]
    metrics=[metric("selected_secids",len(units),len(units)),metric("approved_secids",approved,len(units)),metric("probed_secids",probed,len(units)),
        metric("request_attempts",manifest.attempts,authorization.limits.max_requests),metric("accepted_pages",manifest.accepted_pages,authorization.limits.max_pages),
        metric("exact_current_bindings",sum(u.recovered_current_isin is not None for u in units),approved),
        metric("unresolved_after_probe",sum(u.approved and u.recovered_current_isin is None for u in units),approved)]
    metrics.extend((metric("complete_source_responses",sum(r.complete_response for r in all_results),len(all_results)),
        metric("source_valid_isin_bindings",sum(r.current_binding_observed for r in all_results),len(all_results)),
        metric("valid_isin_syntax",sum(isin_valid(v) for r in all_results for v in r.returned_isin_candidates),sum(len(r.returned_isin_candidates) for r in all_results)),
        metric("transient_attempt_failures",sum(r.transient_failure_count for r in all_results),manifest.attempts)))
    for status in ("SOURCE_RECORD_UNAVAILABLE","SOURCE_FAILURE","TRANSIENT_SOURCE_FAILURE","SOURCE_INVALID","SOURCE_RESPONSE_INCOMPLETE","ISIN_NOT_PRESENT","SOURCE_MATCH_AMBIGUOUS","SOURCE_RESULT_NOT_EXACT","EXACT_CURRENT_SOURCE_BINDING_CONFLICT"):
        metrics.append(metric(status,sum(r.status==status for r in all_results),len(all_results)))
    for name,predicate in (("reference_only",lambda u:u.results[0].current_binding_observed and not u.results[1].current_binding_observed),
        ("description_only",lambda u:u.results[1].current_binding_observed and not u.results[0].current_binding_observed),
        ("mutually_consistent",lambda u:all(r.current_binding_observed for r in u.results) and u.results[0].returned_isin==u.results[1].returned_isin)):
        metrics.append(metric(name,sum(u.approved and predicate(u) for u in units),approved))
    feasibility="NOT_EXECUTED" if not manifest.attempts else "FEASIBLE_ON_OBSERVED_SAMPLE" if good==approved and manifest.status=="COMPLETE" else "PARTIALLY_FEASIBLE_ON_OBSERVED_SAMPLE" if good else "INCONCLUSIVE"
    return signed(FeasibilityReport,"report_sha256",context=plan.context,plan_sha256=plan.plan_sha256,authorization_sha256=authorization.authorization_sha256,
        evidence_sha256=manifest.evidence_sha256,feasibility=feasibility,units=tuple(units),metrics=tuple(metrics),
        strata=tuple(StratumEvaluation(name=s.name,selected=s.selected_count,probed=sum(u.actually_probed and s.name in u.sample.strata for u in units),
            recovered=sum(u.recovered_current_isin is not None and s.name in u.sample.strata for u in units)) for s in plan.strata),
        limitations=("DIAGNOSTIC_SAMPLE_NOT_POPULATION_ESTIMATE","CURRENT_BINDING_NOT_HISTORICAL_PUBLICATION","MOEX_ENDPOINTS_NOT_INDEPENDENT_AUTHORITIES","MISSING_CURRENT_REFERENCE_NOT_IRRECOVERABLE"),
        next_steps=("Independent review of per-source exact bindings and conflicts","Specify Task308B only after reviewed source feasibility; separate acquisition authorization required"))
