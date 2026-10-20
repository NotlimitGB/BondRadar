"""Task308B1 inventory reduction; source identity resolution remains Task308A's."""
from collections import Counter
from dataclasses import dataclass
from decimal import Context as DecimalContext, Decimal, ROUND_HALF_EVEN, localcontext
from types import MappingProxyType
import re
from app.schemas.historical_identity_recovery_inventory import (
    InventoryLimits, Expectations, InventoryRow, ProposalRow, ProposalStratum, ExceptionProposal,
    FieldRequirement, AcquisitionStage, ResourceScenario, AcquisitionRoadmap, PopulationView, InventoryAudit,
)
from app.services.historical_identity_feasibility_service import (
    validate_report, validate_plan, memberships, isin_valid, require,
)
from app.services.historical_identity_probe_service import verify_authorization
from app.services.historical_evidence_normalization import checked, signed
from app.services.historical_discovery_coverage_audit import metric
from app.services.ofz_identity import is_ofz_instrument


LIMITATIONS=("CURRENT_REFERENCE_NOT_HISTORICAL_DECISION_AUTHORITY","HISTORICAL_UNIVERSE_COMPLETENESS_UNPROVEN",
    "SAME_ROW_IDENTITY_EXECUTION_INTERSECTION_NOT_AVAILABLE","NO_CURRENT_SURVIVORSHIP_FILTER",
    "NO_ECONOMIC_STRUCTURE_INFERENCE_FROM_CODES","SAMPLE_COST_NOT_POPULATION_ESTIMATE",
    "SOURCE_UNAVAILABLE_NOT_IRRECOVERABLE","SOURCE_AGREEMENT_NOT_INDEPENDENT_PUBLICATION_PROOF")


def syntax(secid):
    valid=re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}[0-9]",secid) is not None
    checksum=isin_valid(secid)
    return valid,checksum,"NOT_ISIN_FORMAT" if not valid else "ISIN_FORMAT_VALID_CHECKSUM_VALID" if checksum else "ISIN_FORMAT_VALID_CHECKSUM_INVALID"


def row_for(secid,identities,context,unit):
    fmt,check,label=syntax(secid)
    probed=unit is not None and unit.actually_probed
    current=unit.recovered_current_isin if probed else None
    conflict=bool(unit and unit.status=="EXACT_CURRENT_SOURCE_BINDING_CONFLICT") or any(i.conflicting_isin_candidates for i in identities)
    status=unit.status if probed else "NOT_PROBED"
    binding={"ISIN_NOT_PRESENT":"EXACT_RECORD_WITHOUT_ISIN","EXACT_CURRENT_SOURCE_BINDING_CONFLICT":"SOURCE_CONFLICT",
        "SOURCE_MATCH_AMBIGUOUS":"SOURCE_RESULT_AMBIGUOUS","SOURCE_RECORD_UNAVAILABLE":"SOURCE_UNAVAILABLE",
        "SOURCE_FAILURE":"SOURCE_UNAVAILABLE","TRANSIENT_SOURCE_FAILURE":"SOURCE_UNAVAILABLE"}.get(status,status)
    if conflict:binding="SOURCE_CONFLICT";current=None
    elif current:binding="EXACT_CURRENT_BINDING_FOUND" if secid==current else "EXACT_CURRENT_BINDING_NOT_EQUAL_SECID"
    next_step="REUSE_ACCEPTED_CURRENT_REFERENCE" if current else "OFFICIAL_ALTERNATIVE_IDENTITY_SOURCE_REQUIRED" if binding=="EXACT_RECORD_WITHOUT_ISIN" else "MANUAL_IDENTITY_CONFLICT_REVIEW" if conflict else "SOURCE_CONTRACT_REVIEW_REQUIRED" if probed else "UNPROBED_SOURCE_QUALIFICATION_REQUIRED"
    return InventoryRow(secid=secid,original_identities=identities,boards=tuple(sorted({b for i in identities for b in i.listing_boards+i.observed_boards})),
        listing=any(i.listing for i in identities),observed=any(i.observed for i in identities),
        first_observed_date=min((i.first_observed_date for i in identities if i.first_observed_date is not None),default=None),
        last_observed_date=max((i.last_observed_date for i in identities if i.last_observed_date is not None),default=None),
        supplied_listing_intervals=tuple(sorted({(i.first_listing_interval,i.last_listing_interval) for i in identities},key=lambda v:(str(v[0]),str(v[1])))),
        economic_relevance="OBSERVED_COMPONENT_EVIDENCE" if any(i.actual_trade_date_count or i.usable_price_nkd_date_count for i in identities) else "OBSERVED_WITHOUT_TRADE_OR_USABLE_SIGNAL" if any(i.observed for i in identities) else "LISTING_ONLY_UNASSESSED",
        actual_trade_evidence=any(i.actual_trade_date_count>0 for i in identities),usable_price_nkd_evidence=any(i.usable_price_nkd_date_count>0 for i in identities),
        syntax=label,format_valid=fmt,checksum_valid=check,raw_prefix=secid[:2],
        canonical_ofz=any(is_ofz_instrument(isin=i.isin,secid=secid) for i in identities) or is_ofz_instrument(isin=current,secid=secid),
        strata=memberships(context,identities),task308a_sample_selected=unit is not None,task308a_probe_approved=bool(unit and unit.approved),previously_probed=probed,source_results=unit.results if unit else (),
        current_binding=binding,confirmed_current_isin=current,current_binding_confirmed=current is not None,secid_equals_current_isin=secid==current if current else None,
        binding_conflict=conflict,requires_review=conflict or bool(unit and unit.requires_review),
        source_observation_times=tuple(sorted({t for r in unit.results for t in r.observation_times})) if unit else (),
        next_investigation=next_step,requirements=tuple(sorted({next_step,"DATED_PUBLICATION_AUTHORITY_REQUIRED","CONTRACTUAL_CASHFLOW_COMPLETENESS_UNPROVEN"})))


def select_exceptions(population,context,units):
    features={};invalid=set();probed=set();all_ids=set()
    for secid,items in population:
        all_ids.add(secid);unit=units.get(secid)
        fmt,check,label=syntax(secid)
        features[secid]=set(memberships(context,items))|{"PREFIX:"+secid[:2],"SYNTAX:"+label}
        if unit and unit.actually_probed:probed.add(secid)
        elif fmt and not check:invalid.add(secid)
    covered=set().union(*(features[s] for s in sorted(probed))) if probed else set()
    selected=[];used=set()
    def pick(pool,role,ceiling,novel_only=False):
        for _ in range(ceiling):
            available=pool-used
            if not available:break
            secid=min(available,key=lambda s:(-len(features[s]-covered),s))
            new=features[secid]-covered
            if novel_only and not new:break
            selected.append((secid,role,tuple(sorted(new))))
            used.add(secid);covered.update(features[secid])
    pick(invalid,"CHECKSUM_EXCEPTION",9)
    pick(all_ids-probed-invalid,"CONTROL",3,True)
    primary=sum(r=="CHECKSUM_EXCEPTION" for _,r,_ in selected);controls=sum(r=="CONTROL" for _,r,_ in selected)
    pick(invalid,"EXCEPTION_BACKFILL",12-len(selected))
    identities=dict(population)
    rows=tuple(ProposalRow(secid=s,role=role,original_identities=identities[s],features=tuple(sorted(features[s])),new_features=new,
        selection_reason="GREEDY_MAX_NEW_FEATURES_THEN_RAW_SECID_ASC"+("_POSITIVE_GAIN_REQUIRED" if role=="CONTROL" else "")) for s,role,new in sorted(selected))
    eligible_counts=Counter(f for s,fs in features.items() if s not in probed for f in fs)
    all_features=sorted({f for fs in features.values() for f in fs})
    overlap_counts={f:Counter() for f in all_features}
    for row in rows:
        for feature in row.features:
            overlap_counts[feature].update(other for other in row.features if other!=feature)
    strata=[]
    for feature in all_features:
        member=tuple(r.secid for r in rows if feature in r.features)
        overlaps=tuple(sorted(overlap_counts[feature].items()))
        strata.append(ProposalStratum(feature=feature,eligible=eligible_counts[feature],selected=len(member),selected_secids=member,overlaps=overlaps))
    require(not used&probed and len(used)<=12,"EXCEPTION_SCOPE_INVALID")
    return signed(ExceptionProposal,"proposal_sha256",previously_probed_secids=tuple(sorted(probed)),selected=rows,strata=tuple(strata),
        unmet_exception_slots=max(0,9-primary),unmet_control_slots=3-controls,unfilled_total_slots=12-len(rows))


def field_requirements():
    reference=(
        ("SECID/exact ISIN","Bond.secid/isin; exact source identity","SOURCE_DOCUMENTED"),
        ("instrument type","Task308A source type review; no non-OFZ corporate inference","REQUIRES_PROBE"),
        ("issuer title/INN","Company exact INN; Task269 issuer proof remains separate","REQUIRES_PROBE"),
        ("issue name","Bond.name; never positive identity","REQUIRES_PROBE"),
        ("nominal amount/currency","Task273 verified RUB/positive nominal","REQUIRES_PROBE"),
        ("maturity date","Bond.maturity_date; canonical maturity evidence","REQUIRES_PROBE"),
        ("effective listing dates/issue status","historical listing authority, not current active status","REQUIRES_PROBE"),
        ("coupon type/fixed/floating/variable flags","Security Master coupon_structure; Task272","REQUIRES_PROBE"),
        ("coupon frequency","Task272 positive verified frequency; no spacing inference","REQUIRES_PROBE"),
        ("subordination/perpetual/amortizing/offer flags","Security Master structural assertions; explicit conflicts","REQUIRES_PROBE"),
        ("lot size/trading board","Task302 verified execution terms; no lot fallback","REQUIRES_PROBE"),
        ("observed/retrieved timestamps","existing Task308A receipt UTC timestamp; current observation","SOURCE_DOCUMENTED"),
        ("historical publication/version authority","decision cutoff 00:00 UTC T; effective date insufficient","UNKNOWN"))
    result=[FieldRequirement(field=f,family="REFERENCE",priority="P0",source_status=status,
        source_contract="MOEX reference/205 and description/193; implementation aliases are not field-availability proof",
        application_field_or_gate=gate,required_proof="Exact returned SECID/ISIN, raw field/page hash and source provenance; dated publication authority separately") for f,gate,status in reference]
    for table,event in (("coupons","coupon"),("amortizations","amortization"),("redemptions","redemption"),("offers","offer_redemption")):
        result.append(FieldRequirement(field=table,family="CASHFLOW",priority="P0",source_status="REQUIRES_PROBE",
            source_contract="Existing bondization/{SECID}.json table="+table+"; source coverage/completeness unproven",
            application_field_or_gate="BondCashflowEvent.event_type="+event+"; amount/currency/event_date; Task303/304 semantics",
            required_proof="Exact identity/date, finite nonnegative supplied amount, RUB, schedule version, duplicates/revisions and completeness; not proof of actual payment"))
    result.extend((FieldRequirement(field="nominal changes",family="CASHFLOW",priority="P0",source_status="UNKNOWN",source_contract="No separate nominal-change acquisition contract established",
        application_field_or_gate="Security Master outstanding nominal and amortization; current values not historical versions",required_proof="Dated exact amount/version and independent publication authority"),
        FieldRequirement(field="exceptional events / actual-payment confirmation",family="CASHFLOW",priority="P1",source_status="NOT_AVAILABLE",source_contract="NOT_AVAILABLE_IN_CURRENT_FOUR_TABLE_ACQUISITION_CONTRACT; not a claim about all MOEX sources",
        application_field_or_gate="BondCashflowEvent.other blocks automatic application",required_proof="Separately reviewed official event/payment source; do not infer from a scheduled row")))
    return tuple(result)


def roadmap(proposal,rows,manifest,source_field_observations):
    confirmed=tuple(r.secid for r in rows if r.confirmed_current_isin and not r.requires_review)
    # Stable diagnostic pool, not an economically-ready or survivorship-safe universe.
    observed=tuple(r.secid for r in rows if r.confirmed_current_isin and r.observed and not r.requires_review)
    probed=len(proposal.previously_probed_secids)
    with localcontext(DecimalContext(prec=28,rounding=ROUND_HALF_EVEN)):
        averages=tuple((name,str(Decimal(value)/Decimal(probed)) if probed else "UNKNOWN") for name,value in
            (("attempts_per_probed_secid",manifest.attempts),("pages_per_probed_secid",manifest.accepted_pages),("bytes_per_probed_secid",manifest.received_bytes)))
    return signed(AcquisitionRoadmap,"roadmap_sha256",requirements=field_requirements(),source_field_observations=source_field_observations,stages=(
        AcquisitionStage(stage=1,name="SMALL_REFERENCE_QUALIFICATION",cohort_secids=tuple(r.secid for r in proposal.selected),scope="Proposed new cohort only; REFERENCE/DESCRIPTION; not a Task308A authorization",prerequisite="Independent review and separate bounded acquisition authorization"),
        AcquisitionStage(stage=2,name="CASHFLOW_SOURCE_FEASIBILITY",cohort_secids=confirmed,scope="Planning pool only; actual bounded cohort <=6 needs review; collect four cashflow tables; reuse reference",prerequisite="Source-confirmed identity, unresolved economic structure proof and separate authorization"),
        AcquisitionStage(stage=3,name="BOUNDED_HISTORICAL_RESEARCH_COHORT",cohort_secids=observed,scope="Diagnostic candidate pool, not an import/download queue; independent trade/usable coverage only",prerequisite="Stage 1/2 review, dated terms/cashflow evidence and documented selection bias"),
        AcquisitionStage(stage=4,name="CONTROLLED_WIDER_ACQUISITION",cohort_secids=(),scope="No population-wide scope is granted",prerequisite="Explicit later review of coverage/failures/volume/resources/durability/PIT limits")),
        existing_evidence_reuse_secids=confirmed,cashflow_distinctions=("SCHEDULED_CONTRACT","ACTUAL_PAYMENT_UNPROVEN","REVISED_SCHEDULE","CURRENT_REFERENCE","HISTORICAL_PUBLICATION_UNPROVEN"),
        measured_attempts=manifest.attempts,measured_pages=manifest.accepted_pages,measured_response_bytes=manifest.received_bytes,measured_probed_secids=probed,
        descriptive_averages=averages,scenarios=(
            ResourceScenario(name="REFERENCE_FIRST_COHORT_HARD_CEILING",secids=len(proposal.selected),query_count=2*len(proposal.selected),attempt_ceiling=72,body_byte_ceiling=64*1024**2,interpretation="Recommendation only; pagination and heavy tails can exhaust ceilings"),
            ResourceScenario(name="CASHFLOW_SIX_INSTRUMENT_SCENARIO",secids=min(6,len(confirmed)),query_count=4*min(6,len(confirmed)),attempt_ceiling=72,body_byte_ceiling=64*1024**2,interpretation="Four tables, at least one request each; retries/pagination add cost; separate review required")),limitations=LIMITATIONS)


@dataclass(frozen=True)
class InventoryAnalysis:
    report: object
    context: object
    population: tuple
    units: object
    counts: tuple
    views: tuple
    proposal: ExceptionProposal
    roadmap: AcquisitionRoadmap
    hashes: tuple
    limits: InventoryLimits
    expectations: Expectations

    def iter_inventory(self):
        for secid,items in self.population:yield row_for(secid,items,self.context,self.units.get(secid))

    def audit(self,inventory_sha256):
        counts=dict(self.counts);total=counts["total_secids"]
        return signed(InventoryAudit,"report_sha256",status="COMPLETE",context=self.context,input_hashes=self.hashes,limits=self.limits,expectations=self.expectations,
            counts=self.counts,metrics=tuple(metric(k,v,total) for k,v in self.counts if k!="original_identity_pairs"),population_views=self.views,
            upstream_joint_metrics=tuple(m for m in self.report.trading_activity+self.report.price_nkd_coverage if "joint" in m.name.lower() or "actual_and_usable" in m.name.lower()),
            checksum_invalid_secids=next(v.secids for v in self.views if v.name=="CHECKSUM_INVALID"),
            invariants=("EXACT_DISTINCT_SECID_POPULATION","ORIGINAL_PAIRS_RETAINED","SOURCE_REPLAY_MATCHES_EVALUATION","PREVIOUS_PROBE_EXCLUDED","NO_SECID_TO_ISIN_INFERENCE","HISTORICAL_AUTHORITY_FALSE"),
            inventory_sha256=inventory_sha256,proposal_sha256=self.proposal.proposal_sha256,roadmap_sha256=self.roadmap.roadmap_sha256,limitations=LIMITATIONS)


class HistoricalIdentityRecoveryInventoryService:
    def build(self,*,report,plan,authorization,evaluation,probe_manifest,manifest=None,limits=None,expectations=None,source_field_observations=()):
        limits=InventoryLimits() if limits is None else limits
        expectations=Expectations() if expectations is None else expectations
        InventoryLimits.model_validate(limits);Expectations.model_validate(expectations)
        require(len(report.identity_coverage.identities)<=limits.max_identity_rows,"MEMORY_IDENTITY_ROW_LIMIT")
        context,population=validate_report(report,manifest=manifest)
        validate_plan(plan);verify_authorization(plan,authorization,plan.plan_sha256)
        checked(evaluation,"report_sha256");checked(probe_manifest,"evidence_sha256")
        require(plan.context==context and evaluation.context==context and evaluation.plan_sha256==plan.plan_sha256 and
            evaluation.authorization_sha256==authorization.authorization_sha256 and evaluation.evidence_sha256==probe_manifest.evidence_sha256 and
            probe_manifest.status=="COMPLETE" and probe_manifest.authorization_sha256==authorization.authorization_sha256 and probe_manifest.plan_sha256==plan.plan_sha256,"SOURCE_BINDING_MISMATCH")
        units={u.sample.secid:u for u in evaluation.units}
        require(tuple(units)==tuple(s.secid for s in plan.samples) and all(units[s.secid].sample==s for s in plan.samples),"EVALUATION_MEMBERSHIP_MISMATCH")
        counts=Counter();views={n:[] for n in ("ALL","ACTUAL_TRADES","USABLE_PRICE_NKD","CANONICAL_OFZ","LISTING_ONLY","POSSIBLE_INACTIVE_PROXY","CURRENT_CONFIRMED","PROBED_UNRESOLVED","NEVER_PROBED","CHECKSUM_INVALID")}
        small_rows=[]
        for secid,items in sorted(population.items()):
            row=row_for(secid,items,context,units.get(secid));counts["total_secids"]+=1
            for k,value in (("listing",row.listing),("observed",row.observed),("format_valid",row.format_valid),("checksum_valid",row.checksum_valid),
                ("checksum_invalid",row.format_valid and not row.checksum_valid),("not_isin_format",not row.format_valid),("probed",row.previously_probed),
                ("current_bindings",row.confirmed_current_isin is not None),("mismatched_bindings",row.secid_equals_current_isin is False),
                ("source_unresolved",row.previously_probed and row.confirmed_current_isin is None),("never_probed",not row.previously_probed),("conflicts",row.binding_conflict)):
                counts[k]+=int(value)
            for name,yes in (("ALL",True),("ACTUAL_TRADES",row.actual_trade_evidence),("USABLE_PRICE_NKD",row.usable_price_nkd_evidence),
                ("CANONICAL_OFZ",row.canonical_ofz),("LISTING_ONLY",row.listing and not row.observed),("POSSIBLE_INACTIVE_PROXY","POSSIBLE_INACTIVE_PROXY" in row.strata),
                ("CURRENT_CONFIRMED",row.confirmed_current_isin is not None),("PROBED_UNRESOLVED",row.previously_probed and not row.confirmed_current_isin),
                ("NEVER_PROBED",not row.previously_probed),("CHECKSUM_INVALID",row.format_valid and not row.checksum_valid)):
                if yes:views[name].append(secid)
            if row.confirmed_current_isin:small_rows.append(row)  # Task308A caps this at 12.
        counts["original_identity_pairs"]=len(report.identity_coverage.identities)
        require(counts["total_secids"]==report.universe_coverage.distinct_secids and counts["format_valid"]==counts["checksum_valid"]+counts["checksum_invalid"] and
            counts["format_valid"]+counts["not_isin_format"]==counts["total_secids"] and counts["probed"]+counts["never_probed"]==counts["total_secids"] and
            counts["current_bindings"]+counts["source_unresolved"]==counts["probed"],"POPULATION_RECONCILIATION_FAILED")
        for key in type(expectations).model_fields:
            if key in counts and getattr(expectations,key) is not None:require(counts[key]==getattr(expectations,key),"EXPECTED_COUNTER_MISMATCH")
        pop=tuple(sorted(population.items()));proposal=select_exceptions(pop,context,units)
        road=roadmap(proposal,small_rows,probe_manifest,source_field_observations)
        hashes=tuple(sorted({"task307_audit":report.audit_sha256,"source_manifest":context.source_manifest_sha256,"source_content":context.source_content_sha256,
            "source_scope":context.scope_sha256,"source_implementation":context.source_implementation_sha256,"task308a_plan":plan.plan_sha256,
            "task308a_authorization":authorization.authorization_sha256,"task308a_probe":probe_manifest.evidence_sha256,"task308a_evaluation":evaluation.report_sha256}.items()))
        return InventoryAnalysis(report,context,pop,MappingProxyType(units),tuple(sorted(counts.items())),
            tuple(PopulationView(name=n,secids=tuple(ids),definition="Independent identity membership; not same-row execution proof or economic eligibility") for n,ids in sorted(views.items())),
            proposal,road,hashes,limits,expectations)
