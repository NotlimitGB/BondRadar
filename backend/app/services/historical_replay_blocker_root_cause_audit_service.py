"""Task306A1 SELECT-only RCA; counts and prerequisites, never performance."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext
import json
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.schemas.historical_replay_blocker_root_cause import (
    HistoricalReplayBlockerRootCauseAuditV1 as Audit, HistoricalReplayBlockerDateRcaV1 as DateRca,
    FunnelStageV1 as Stage, BlockerCountV1 as Reason, RepresentativeBondV1 as Example,
    CountDistributionV1 as Distribution, OutcomeGapSummaryV1 as Gaps,
    OutcomeRootCauseSummaryV1 as OutcomeSummary, OfzRootCauseSummaryV1 as OfzSummary,
    CreditPeerRootCauseSummaryV1 as CreditSummary, JointChainRootCauseSummaryV1 as JointSummary,
    CreditCohortCountV1 as Cohort, CreditCohortKeyV1 as Key,
    HistoricalCohortMaximumV1 as CohortMaximum, RemediationEvidenceV1 as Remediation,
)
from app.schemas.modern_historical_replay_readiness import CountEntry
from app.services import modern_historical_replay_readiness_audit_service as original
from app.services.modern_historical_replay_evidence_reader import read_evidence
from app.services.moex_duration_semantics import normalize_moex_duration
from app.services.ofz_identity import is_ofz_instrument
from app.services.ofz_reference_curve_service import OfzReferenceCurveService
from app.services.historical_audit_canonical_json import digest, ModelSpool, RcaLinkage, SourceDateLink


OUTCOME_ORDER = (
    "OBSERVED_BEFORE_ENTRY", "CURRENT_PROFILE_PRESENT", "DIRTY_VALUE_TERMS_READY",
    "ENTRY_OR_PRE_ENTRY_QUOTE_AVAILABLE", "VALID_QUOTE_INTERVAL_EXISTS_AT_ENTRY",
    "FUTURE_HORIZON_IN_DATABASE_RANGE", "SOME_FUTURE_MARKET_COVERAGE",
    "FULL_QUOTE_COVERAGE_TO_REQUIRED_END", "CASHFLOW_EVENTS_SYNTACTICALLY_VALID",
    "MATURITY_REDEMPTION_EVIDENCE_READY", "FULL_OBSERVED_OUTCOME",
)
OFZ_ORDER = (
    "CANONICAL_OFZ_IDENTITY", "SECURITY_MASTER_PROFILE_PRESENT", "RUB_VERIFIED", "FIXED_COUPON",
    "DATED_NON_PERPETUAL", "COUPON_FREQUENCY_VERIFIED", "BULLET_AMORTIZATION", "MATURITY_VERIFIED",
    "NOT_MATURED_AT_DATE", "NOT_EXCLUDED_OFZ_PK_IN_AD", "FRESH_HISTORICAL_MOEX_OBSERVATION",
    "DURATION_READY", "YTM_READY", "TASK306A_CURRENT_STRUCTURE_ELIGIBLE", "TASK268_CURVE_ELIGIBLE",
    "DISTINCT_DURATION_NODE",
)
CREDIT_ORDER = (
    "OBSERVED_BOND", "HAS_ANY_RATING_TARGET", "EVENT_DATE_BEFORE_DECISION", "RESOLUTION_STATE_RESOLVED",
    "TARGET_IDENTITY_MATCHES", "PUBLICATION_NOT_FUTURE", "PUBLICATION_PROVEN",
    "UNIQUE_LATEST_EVENT_PER_SELECTOR", "ARTIFACT_CONTRACT_VALID", "SOURCE_PROVIDER_PRESENT",
    "RATING_VALUE_PRESENT", "SOURCE_NATIVE_KEY_CREATED", "COHORT_SIZE_GE_2_TOTAL", "COHORT_SIZE_GE_3_TOTAL",
    "TASK299_MIN_2_PEERS_SIZE_PREREQUISITE",
)
JOINT_ORDER = ("OBSERVED", "DECISION_MARKET", "LIQUIDITY_OBSERVATIONS", "LIQUIDITY_COMPONENTS",
    "LIQUIDITY_CAPACITY", "CURRENT_TERMS", "CREDIT_EVENT", "CURRENT_ISSUER", "CREDIT_PEER", "OUTCOME")
LIMITATIONS = tuple(sorted((
    "CURRENT_ISSUER_MAPPING_DIAGNOSTIC", "CURRENT_SECURITY_MASTER_NOT_HISTORICAL_PROOF",
    "HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN", "ZERO_PERSISTED_EVENTS_DOES_NOT_PROVE_ZERO_CONTRACTUAL_CASHFLOWS",
    "TASK277_READY_MEMBERS_AND_SPREADS_NOT_EVALUATED", "DECISION_INTERSECTION_IS_DATA_PREREQUISITE_NOT_MODEL_READY",
    "TASK268_INCLUDES_ENTRY_DAY_QUOTES_AND_CURRENT_STRUCTURAL_STATE", "NO_PROFITABILITY_OR_REPAIR",
)))
finite, present, require, pct = original.finite, original.present, original.require, original.pct


def signed(report):
    return report.model_copy(update={"audit_sha256":digest(report)})


def blocked(code, source=None):
    return signed(Audit(status="BLOCKED", blockers=(code,),known_limitations=LIMITATIONS,
        source_readiness_audit_sha256=source.audit_sha256 if source else None,
        source_readiness_classification=source.audit_classification if source else None,
        candidate_date_count=source.all_candidate_date_count if source else 0,
        source_pit_safe_date_count=source.pit_safe_date_count if source else 0,
        source_diagnostic_only_date_count=source.diagnostic_only_date_count if source else 0,
        source_unusable_date_count=source.unusable_date_count if source else 0))


def consistent_connection(db):
    bind = db.get_bind()
    if not db.in_transaction() and not (isinstance(bind,Connection) and bind.in_transaction()):
        return None
    connection = db.connection()
    if connection.dialect.name == "sqlite":
        if connection.exec_driver_sql("PRAGMA query_only").scalar_one() != 1:
            return None
        return connection if connection.connection.driver_connection.in_transaction else None
    if connection.dialect.name == "postgresql":
        isolation, readonly = connection.execute(text(
            "SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')"
        )).one()
        return connection if isolation in ("repeatable read","serializable") and readonly == "on" else None
    return None


def distribution(values):
    values = sorted(values)
    if not values:
        return Distribution()
    middle = len(values)//2
    median = Decimal(values[middle]) if len(values)%2 else (Decimal(values[middle-1])+Decimal(values[middle]))/2
    return Distribution(count=len(values),minimum=values[0],maximum=values[-1],median=median)


def stages(universe, predicates, order, independent=()):
    current = set(universe)
    result = []
    for name in order:
        separate = name in independent
        incoming = set(universe) if separate else current
        passed = incoming & predicates[name]
        result.append(Stage(stage=name,kind="INDEPENDENT_DIAGNOSTIC" if separate else "CUMULATIVE_GATE",
            input_count=len(incoming),pass_count=len(passed),fail_count=len(incoming)-len(passed),pass_pct=pct(len(passed),len(incoming)),
            independent_pass_count=len(set(universe)&predicates[name])))
        if not separate:
            current = passed
    return tuple(result)


def primary(funnel):
    gates = [s for s in funnel if s.kind == "CUMULATIVE_GATE"]
    if not gates or gates[-1].pass_count:
        return ()
    zero = next((s for s in gates if s.input_count and not s.pass_count),None)
    return (("COMBINATION_AT_"+zero.stage if zero.independent_pass_count else zero.stage),) if zero else ("NO_OBSERVED_INPUT_UNIVERSE",)


def reasons(mapping, bonds, *, independent=(), bank=None, universe_count=None):
    if bank is not None:
        for code,ids in mapping.items():bank[code].update(ids)
    denominator = len(bonds) if universe_count is None else universe_count
    return tuple(Reason(reason=code,affected_bond_date_count=len(ids),affected_date_count=1 if ids else 0,
        distinct_bond_count=len(ids),blocking=code not in independent,
        universe_count=denominator,affected_pct=pct(len(ids),denominator),
        representative_bonds=tuple(Example(bond_id=bid,isin=bonds[bid]["isin"],secid=bonds[bid]["secid"])
            for bid in sorted(ids)[:10])) for code,ids in sorted(mapping.items(),key=lambda item:(-len(item[1]),item[0])) if ids)


def fail_reasons(universe, predicates, codes):
    return {code:set(universe)-predicates[stage] for stage,code in codes.items()}


def gap_metrics(day, end, intervals):
    covered = [(max(a,day),min(b,end)) for a,b in intervals if a <= end and b >= day]
    cursor = day
    missing = []
    for a,b in covered:
        if a > cursor:
            missing.append((cursor,a-timedelta(days=1)))
        cursor = max(cursor,b+timedelta(days=1))
    if cursor <= end:
        missing.append((cursor,end))
    return (max(((b-a).days+1 for a,b in covered),default=0),
        (missing[0][0]-day).days if missing else None,
        max(((b-a).days+1 for a,b in missing),default=0),len(missing))


def gap_summary(metrics):
    if isinstance(metrics,GapAccumulator):return metrics.result()
    return Gaps(maximum_continuous_covered_days=distribution([m[0] for m in metrics]),
        first_gap_offset_from_entry=distribution([m[1] for m in metrics if m[1] is not None]),
        largest_uncovered_interval_days=distribution([m[2] for m in metrics]),gap_count=distribution([m[3] for m in metrics]))


class GapAccumulator:
    def __init__(self):self.counts=[Counter() for _ in range(4)]
    def extend(self,metrics):
        for values in metrics:
            for counter,value in zip(self.counts,values):
                if value is not None:counter[value]+=1
    def result(self):
        def dist(counter):
            count=sum(counter.values())
            if not count:return Distribution()
            ordered=sorted(counter);middle=(count-1)//2,count//2;found=[];offset=0
            for value in ordered:
                for index in middle:
                    if offset<=index<offset+counter[value]:found.append(value)
                offset+=counter[value]
            return Distribution(count=count,minimum=ordered[0],maximum=ordered[-1],median=Decimal(sum(found))/2)
        return Gaps(**dict(zip(("maximum_continuous_covered_days","first_gap_offset_from_entry","largest_uncovered_interval_days","gap_count"),map(dist,self.counts))))


def outcome(day, observed, index, inside):
    predicates = {name:set() for name in OUTCOME_ORDER}
    predicates[OUTCOME_ORDER[0]] = set(observed)
    extra = defaultdict(set)
    metrics = []
    full = set()
    empty_events = 0
    for bid in sorted(observed):
        p = index.profiles.get(bid)
        rows, days = index.history[bid],index.days[bid]
        entry_index = bisect_right(days,day)-1
        if p:
            predicates["CURRENT_PROFILE_PRESENT"].add(bid)
        terms = bool(p and p["currency_state"] == "verified" and p["currency_code"] == "RUB" and
            p["nominal_state"] == "verified" and finite(p["nominal_value"]) and p["nominal_value"] > 0)
        if terms:predicates["DIRTY_VALUE_TERMS_READY"].add(bid)
        if entry_index >= 0:predicates["ENTRY_OR_PRE_ENTRY_QUOTE_AVAILABLE"].add(bid)
        state,_,issues,flow_diagnostics = original.outcome_coverage(day,index.intervals[bid],index.flows[bid],index.bonds[bid]["maturity_date"])
        events = [r for r in index.flows[bid] if day < r["event_date"] <= day+timedelta(days=90)]
        if not events:empty_events += 1
        valid_redemptions = [r["event_date"] for r in events if r["event_type"] == "redemption" and
            r["currency"] == "RUB" and finite(r["amount"]) and r["amount"] >= 0]
        end = min(valid_redemptions)-timedelta(days=1) if valid_redemptions else day+timedelta(days=90)
        m = gap_metrics(day,end,index.intervals[bid])
        metrics.append(m)
        if any(a <= day <= b for a,b in index.intervals[bid]):predicates["VALID_QUOTE_INTERVAL_EXISTS_AT_ENTRY"].add(bid)
        if inside:predicates["FUTURE_HORIZON_IN_DATABASE_RANGE"].add(bid)
        if any(a <= end and b > day for a,b in index.intervals[bid]):predicates["SOME_FUTURE_MARKET_COVERAGE"].add(bid)
        if not m[3]:predicates["FULL_QUOTE_COVERAGE_TO_REQUIRED_END"].add(bid)
        cash_issues = set(issues) & {"CASHFLOW_AMOUNT_OR_CURRENCY_INVALID","DUPLICATE_CASHFLOW_EVENT",
            "CASHFLOW_AFTER_REDEMPTION","UNSUPPORTED_CASHFLOW_TYPE","REDEMPTION_ON_OR_BEFORE_ENTRY"}
        if not cash_issues:predicates["CASHFLOW_EVENTS_SYNTACTICALLY_VALID"].add(bid)
        if "MATURITY_REDEMPTION_EVIDENCE_MISSING" not in issues:predicates["MATURITY_REDEMPTION_EVIDENCE_READY"].add(bid)
        if state == "FULL":full.add(bid)
        for code in (*issues,*flow_diagnostics):extra[code].add(bid)
        if entry_index >= 0 and not original._dirty_inputs(rows[entry_index],p):extra["QUOTE_INPUT_INVALID"].add(bid)
        if inside and m[3]:extra["FIRST_MARKET_GAP_BEFORE_HORIZON_END"].add(bid)
        if inside and m[3]>1:extra["MULTIPLE_MARKET_GAPS"].add(bid)
    predicates["FULL_OBSERVED_OUTCOME"] = full
    codes = {"CURRENT_PROFILE_PRESENT":"CURRENT_PROFILE_MISSING", "DIRTY_VALUE_TERMS_READY":"CURRENT_TERMS_BLOCK_DIRTY_VALUE",
        "ENTRY_OR_PRE_ENTRY_QUOTE_AVAILABLE":"ENTRY_QUOTE_MISSING", "VALID_QUOTE_INTERVAL_EXISTS_AT_ENTRY":"NO_USABLE_ENTRY_INTERVAL",
        "FUTURE_HORIZON_IN_DATABASE_RANGE":"OUTCOME_HORIZON_EXTENDS_BEYOND_MARKET_HISTORY"}
    extra.update(fail_reasons(observed,predicates,codes))
    funnel = stages(observed,predicates,OUTCOME_ORDER,("FUTURE_HORIZON_IN_DATABASE_RANGE","SOME_FUTURE_MARKET_COVERAGE"))
    return funnel,reasons(extra,index.bonds,independent=("OUTCOME_HORIZON_EXTENDS_BEYOND_MARKET_HISTORY","OFFER_NOT_AUTOMATIC_CASH"),bank=index.reason_ids,universe_count=len(observed)),full,metrics,empty_events


class EvidenceIndex:
    def __init__(self, data):
        self.data = data
        self.reason_ids = defaultdict(set)
        tables = data.tables
        self.bonds = {r["id"]:r for r in tables["bonds"]}
        self.profiles = {r["bond_id"]:r for r in tables["bond_security_master_profiles"]}
        self.history,self.flows = defaultdict(list),defaultdict(list)
        for r in tables["bond_market_snapshots"]:self.history[r["bond_id"]].append(r)
        order = {"coupon":0,"amortization":1,"redemption":2,"offer_redemption":3,"other":4}
        for r in tables["bond_cashflow_events"]:
            if r["source"] == "moex":self.flows[r["bond_id"]].append(r)
        for rows in self.history.values():rows.sort(key=lambda r:(r["trade_date"],r["id"]))
        for rows in self.flows.values():rows.sort(key=lambda r:(r["event_date"],order.get(r["event_type"],5),r["id"]))
        self.days = {bid:tuple(r["trade_date"] for r in rows) for bid,rows in self.history.items()}
        self.intervals = {bid:original._intervals(rows,self.profiles.get(bid)) for bid,rows in self.history.items()}
        self.issuer = {}
        issuers = {(r["identity_source"],r["source_issuer_id"]):r for r in tables["legal_issuers"] if r["resolution_state"] == "verified"}
        for p in tables["bond_legal_issuer_profiles"]:
            if p["mapping_state"] == "verified" and (p["mapping_source"],p["source_issuer_id"]) in issuers:
                self.issuer[p["bond_id"]] = issuers[p["mapping_source"],p["source_issuer_id"]]
        self.artifacts = {r["id"]:r for r in tables["credit_risk_source_artifacts"]}
        self.rating_bonds,self.rating_issuers = defaultdict(list),defaultdict(list)
        isins = defaultdict(set)
        for bid,b in self.bonds.items():
            if present(b["isin"]):isins[b["isin"]].add(bid)
        for r in tables["credit_rating_events"]:
            if r["target_kind"] == "BOND":
                bids = {r["bond_id"]} if r["bond_id"] in self.bonds else set()
                bids |= isins[r["source_bond_isin"]]
                for bid in bids:self.rating_bonds[bid].append(r)
            else:self.rating_issuers[r["legal_issuer_id"]].append(r)
        self.ofz = {bid for bid,b in self.bonds.items() if is_ofz_instrument(isin=b["isin"],secid=b["secid"])}
        self.windows = defaultdict(dict)
        for r in data.decision_windows:self.windows[r["entry_date"]][r["bond_id"]] = r
        self.liquidity = {bid:original._liquidity_prefix(rows) for bid,rows in self.history.items()}
        self.volume_prefix = {}
        for bid,rows in self.history.items():
            prefix = [0]
            for r in rows:prefix.append(prefix[-1]+(original._liquidity(r["raw_payload"],set())[0] is not None))
            self.volume_prefix[bid] = tuple(prefix)

    def snapshot(self,bid,day):
        days = self.days.get(bid,())
        pos = bisect_left(days,day)-1
        return self.history[bid][pos] if pos >= 0 and (day-days[pos]).days <= 7 else None


def ofz_funnel(day,index,curve):
    predicates = {name:set() for name in OFZ_ORDER[:-1]}
    for bid in index.ofz:
        predicates["CANONICAL_OFZ_IDENTITY"].add(bid)
        p = index.profiles.get(bid)
        if p:
            predicates["SECURITY_MASTER_PROFILE_PRESENT"].add(bid)
            gates = {
                "RUB_VERIFIED":p["currency_state"] == "verified" and p["currency_code"] == "RUB",
                "FIXED_COUPON":p["coupon_structure"] == "fixed",
                "DATED_NON_PERPETUAL":p["perpetual_structure"] == "dated",
                "COUPON_FREQUENCY_VERIFIED":p["coupon_frequency_state"] == "verified" and type(p["coupon_frequency_per_year"]) is int and p["coupon_frequency_per_year"] > 0,
                "BULLET_AMORTIZATION":p["amortization_structure"] == "bullet",
                "MATURITY_VERIFIED":p["maturity_state"] == "verified" and p["maturity_date"] is not None,
                "NOT_MATURED_AT_DATE":p["maturity_date"] is not None and p["maturity_date"] >= day,
            }
            for name,passed in gates.items():
                if passed:predicates[name].add(bid)
        b = index.bonds[bid]
        markers = " ".join((b["name"],b["isin"] or "",b["secid"] or "")).upper()
        if not any(m in markers for m in ("ОФЗ-ПК","ОФЗ-ИН","ОФЗ-АД","OFZ-PK","OFZ-IN","OFZ-AD")):
            predicates["NOT_EXCLUDED_OFZ_PK_IN_AD"].add(bid)
        snap = index.snapshot(bid,day)
        if snap:
            predicates["FRESH_HISTORICAL_MOEX_OBSERVATION"].add(bid)
            duration = normalize_moex_duration(snap["raw_payload"],stored_duration_years=snap["duration_years"])
            if duration.status == "READY" and finite(duration.duration_years) and duration.duration_years > 0:
                predicates["DURATION_READY"].add(bid)
            if finite(snap["yield_to_maturity"]):predicates["YTM_READY"].add(bid)
            if original._ofz_market_inputs(snap) and original._ofz_structure(b,p,day):
                predicates["TASK306A_CURRENT_STRUCTURE_ELIGIBLE"].add(bid)
    members = {bid for node in curve.nodes for bid in node.component_bond_ids}
    predicates["TASK268_CURVE_ELIGIBLE"] = members
    funnel = stages(index.ofz,predicates,OFZ_ORDER[:-1],("TASK268_CURVE_ELIGIBLE",))
    funnel += (Stage(stage="DISTINCT_DURATION_NODE",kind="INDEPENDENT_DIAGNOSTIC",unit="COMPONENTS_TO_NODES",
        input_count=len(members),pass_count=curve.node_count,fail_count=len(members)-curve.node_count,
        pass_pct=pct(curve.node_count,len(members)),independent_pass_count=curve.node_count),)
    mapping = fail_reasons(index.ofz,predicates,{
        "SECURITY_MASTER_PROFILE_PRESENT":"OFZ_PROFILE_MISSING", "RUB_VERIFIED":"OFZ_RUB_NOT_VERIFIED",
        "FIXED_COUPON":"COUPON_STRUCTURE_NOT_FIXED", "DATED_NON_PERPETUAL":"PERPETUAL_STRUCTURE_NOT_DATED",
        "COUPON_FREQUENCY_VERIFIED":"COUPON_FREQUENCY_NOT_VERIFIED", "BULLET_AMORTIZATION":"AMORTIZATION_STRUCTURE_NOT_BULLET",
        "MATURITY_VERIFIED":"MATURITY_NOT_VERIFIED", "NOT_MATURED_AT_DATE":"OFZ_MATURED_OR_MATURITY_MISSING",
        "NOT_EXCLUDED_OFZ_PK_IN_AD":"EXCLUDED_OFZ_PK_IN_AD", "FRESH_HISTORICAL_MOEX_OBSERVATION":"NO_FRESH_PRE_ENTRY_OFZ_SNAPSHOT",
        "DURATION_READY":"OFZ_DURATION_NOT_READY", "YTM_READY":"OFZ_YTM_NOT_READY", "TASK268_CURVE_ELIGIBLE":"TASK268_COMPONENT_NOT_ELIGIBLE"})
    if curve.node_count<2:
        mapping["TASK268_TWO_DISTINCT_DURATION_NODES_UNAVAILABLE"] = set(index.ofz)
    return funnel,reasons(mapping,index.bonds,bank=index.reason_ids,universe_count=len(index.ofz)),predicates["TASK306A_CURRENT_STRUCTURE_ELIGIBLE"]


def key_sort(key):
    return json.dumps(key,ensure_ascii=True,separators=(",",":"))


def cohort_rows(keys, pool=None):
    groups = defaultdict(set)
    for bid,values in keys.items():
        if pool is None or bid in pool:
            for key in values:groups[key].add(bid)
    return tuple(Cohort(key=Key(target_kind=k[0],rating_agency=k[1],source_provider=k[2],rating_scale_raw=k[3],rating_value_raw=k[4]),
        bond_ids=tuple(sorted(ids)),total_member_count=len(ids),size_implied_peer_count=max(0,len(ids)-1))
        for k,ids in sorted(groups.items(),key=lambda item:key_sort(item[0])))


def qualified(cohorts, minimum=3):
    return {bid for row in cohorts if row.total_member_count >= minimum for bid in row.bond_ids}


def credit_funnel(day,observed,index,*,proven_only=False):
    predicates = {name:set() for name in CREDIT_ORDER}
    predicates["OBSERVED_BOND"] = set(observed)
    issues = defaultdict(set)
    keys = defaultdict(set)
    cutoff = datetime.combine(day,time.min,timezone.utc)
    has_issuer_events = bool(index.rating_issuers)
    for bid in sorted(observed):
        issuer = index.issuer.get(bid)
        records = list(index.rating_bonds[bid]) + (list(index.rating_issuers[issuer["id"]]) if issuer else [])
        if has_issuer_events and not issuer:issues["CURRENT_ISSUER_MAPPING_MISSING"].add(bid)
        if not records:issues["RATING_TARGET_MISSING"].add(bid)
        if records:predicates["HAS_ANY_RATING_TARGET"].add(bid)
        records = [r for r in records if r["event_date"] < day]
        if records:predicates["EVENT_DATE_BEFORE_DECISION"].add(bid)
        if any(r["resolution_state"] != "RESOLVED" for r in records):issues["RATING_TARGET_UNRESOLVED"].add(bid)
        records = [r for r in records if r["resolution_state"] == "RESOLVED"]
        if records:predicates["RESOLUTION_STATE_RESOLVED"].add(bid)
        matched = []
        for r in records:
            if r["target_kind"] == "BOND":
                match = r["bond_id"] == bid and r["source_bond_isin"] == index.bonds[bid]["isin"]
                code = "SOURCE_BOND_ISIN_MISMATCH"
            else:
                match = issuer is not None and r["legal_issuer_id"] == issuer["id"] and r["source_issuer_inn"] == issuer["issuer_inn"]
                code = "SOURCE_ISSUER_INN_MISMATCH"
            if match:matched.append(r)
            else:issues[code].add(bid)
        records = matched
        if records:predicates["TARGET_IDENTITY_MATCHES"].add(bid)
        eligible = []
        for r in records:
            gate = original.publication_gate(r,cutoff)
            if gate == "FUTURE":issues["PUBLICATION_FUTURE"].add(bid)
            elif gate == "UNPROVEN":issues["PUBLICATION_UNKNOWN"].add(bid)
            else:predicates["PUBLICATION_PROVEN"].add(bid)
            if gate != "FUTURE":eligible.append(r)
        if eligible:predicates["PUBLICATION_NOT_FUTURE"].add(bid)
        records = [r for r in eligible if not proven_only or original.publication_gate(r,cutoff)=="PROVEN"]
        selectors = defaultdict(list)
        for r in records:selectors[r["target_kind"],r["rating_agency"]].append(r)
        winners = []
        for rows in selectors.values():
            latest = max(r["event_date"] for r in rows)
            latest_rows = [r for r in rows if r["event_date"] == latest]
            if len(latest_rows) == 1:winners.extend(latest_rows)
            else:issues["MULTIPLE_LATEST_RATING_EVENTS"].add(bid)
        if winners:predicates["UNIQUE_LATEST_EVENT_PER_SELECTOR"].add(bid)
        valid = []
        for r in winners:
            artifact = index.artifacts.get(r["artifact_id"])
            if (r["contract_version"] == "credit-rating-event-v1" and artifact and
                artifact["contract_version"] == "credit-risk-source-artifact-v1" and present(artifact["content_sha256"])):
                valid.append(r)
            else:issues["ARTIFACT_CONTRACT_INVALID"].add(bid)
        if valid:predicates["ARTIFACT_CONTRACT_VALID"].add(bid)
        providers = [r for r in valid if present(index.artifacts[r["artifact_id"]]["source_provider"])]
        if len(providers)<len(valid):issues["SOURCE_PROVIDER_MISSING"].add(bid)
        if providers:predicates["SOURCE_PROVIDER_PRESENT"].add(bid)
        values = [r for r in providers if present(r["rating_value_raw"])]
        if len(values)<len(providers):issues["RATING_VALUE_MISSING"].add(bid)
        if values:predicates["RATING_VALUE_PRESENT"].add(bid)
        for r in values:
            if r["rating_scale_raw"] is not None and type(r["rating_scale_raw"]) is not str:
                issues["SOURCE_NATIVE_KEY_INVALID"].add(bid)
                continue
            keys[bid].add((r["target_kind"],r["rating_agency"],index.artifacts[r["artifact_id"]]["source_provider"],r["rating_scale_raw"],r["rating_value_raw"]))
        if keys[bid]:predicates["SOURCE_NATIVE_KEY_CREATED"].add(bid)
    cohorts = cohort_rows(keys)
    predicates["COHORT_SIZE_GE_2_TOTAL"] = qualified(cohorts,2)
    predicates["COHORT_SIZE_GE_3_TOTAL"] = qualified(cohorts)
    predicates["TASK299_MIN_2_PEERS_SIZE_PREREQUISITE"] = qualified(cohorts)
    issues["COHORT_SIZE_BELOW_THREE"] = predicates["SOURCE_NATIVE_KEY_CREATED"] - qualified(cohorts)
    # UNKNOWN remains diagnostic; this side branch is not a gate in the diagnostic funnel.
    independent = () if proven_only else ("PUBLICATION_PROVEN",)
    funnel = stages(observed,predicates,CREDIT_ORDER,independent)
    return funnel,reasons(issues,index.bonds,independent=("PUBLICATION_UNKNOWN",),bank=index.reason_ids if not proven_only else None,universe_count=len(observed)),keys,cohorts


def joint_funnel(day,observed,index,keys,full):
    market,liquid,components,capacity,terms,volume = set(),set(),set(),set(),set(),set()
    for bid,w in index.windows[day].items():
        p = index.profiles.get(bid)
        snap = index.snapshot(bid,day)
        if snap and ((snap["_decision_inputs"] and snap["_decision_ready"]) if hasattr(snap,"decision_inputs") else
            (original._market_inputs(snap) and original._market_ready(snap,p))):market.add(bid)
        if w["observation_days"] >= 5:liquid.add(bid)
        ds = index.days[bid]
        left,right = bisect_left(ds,day-timedelta(days=30)),bisect_left(ds,day)
        a,b = index.liquidity[bid][left],index.liquidity[bid][right]
        val,num,positive = tuple(y-x for x,y in zip(a,b))
        if val and num:
            components.add(bid)
            if index.volume_prefix[bid][right] > index.volume_prefix[bid][left]:volume.add(bid)
            if positive >= (val+1)//2:capacity.add(bid)
        if original._profile_ready(p):terms.add(bid)
    credit = {bid for bid,values in keys.items() if values}
    issuer = set(index.issuer)
    decision_base = set(observed)&market&liquid&components&capacity&terms&credit&issuer
    decision_peers = qualified(cohort_rows(keys,decision_base))
    decision_only = decision_base & decision_peers
    legacy_joint = decision_base & full
    legacy_peers = qualified(cohort_rows(keys,legacy_joint))
    predicates = dict(zip(JOINT_ORDER,(set(observed),market,liquid,components,capacity,terms,credit,issuer,decision_peers,full)))
    funnel = stages(observed,predicates,JOINT_ORDER)
    issues = fail_reasons(observed,predicates,{name:"JOINT_"+name+"_UNAVAILABLE" for name in JOINT_ORDER[1:]})
    return funnel,reasons(issues,index.bonds,bank=index.reason_ids,universe_count=len(observed)),decision_only,decision_only&full,legacy_joint,legacy_peers,len(liquid&components),len(liquid&volume)


def aggregate_stages(rows,field):
    results = []
    if not rows:return ()
    bases=getattr(rows[0],field);counts=[[0,0,0] for _ in bases]
    for row in rows:
        for totals,stage in zip(counts,getattr(row,field)):
            totals[0]+=stage.input_count;totals[1]+=stage.pass_count;totals[2]+=stage.independent_pass_count
    for base,(incoming,passed,independent) in zip(bases,counts):
        results.append(base.model_copy(update={"input_count":incoming,"pass_count":passed,"fail_count":incoming-passed,
            "pass_pct":pct(passed,incoming),"independent_pass_count":independent,
            "unit":"COMPONENTS_TO_NODES" if base.unit=="COMPONENTS_TO_NODES" else "BOND_DATES"}))
    return tuple(results)


def aggregate_reasons(rows,field,index):
    grouped = {};universe=0
    for row in rows:
        universe+=getattr(row,field.replace("_reasons","_funnel"))[0].input_count
        for issue in getattr(row,field):
            totals=grouped.setdefault(issue.reason,[0,0,True])
            totals[0]+=issue.affected_bond_date_count;totals[1]+=1;totals[2]=totals[2] and issue.blocking
    result = []
    for code,(affected,dates,blocking) in grouped.items():
        ids = index.reason_ids[code]
        result.append(Reason(reason=code,affected_bond_date_count=affected,
            affected_date_count=dates,distinct_bond_count=len(ids),blocking=blocking,
            universe_count=universe,affected_pct=pct(affected,universe),
            representative_bonds=tuple(Example(bond_id=bid,isin=index.bonds[bid]["isin"],secid=index.bonds[bid]["secid"])
                for bid in sorted(ids)[:10])))
    return tuple(sorted(result,key=lambda r:(-r.affected_bond_date_count,r.reason)))


def summary(rows,path,index):
    funnel = aggregate_stages(rows,path+"_funnel")
    issues = aggregate_reasons(rows,path+"_reasons",index)
    roots = set(primary(funnel))
    for row in rows:roots.update(primary(getattr(row,path+"_funnel")))
    if path=="ofz" and any(r.task268_node_count<2 for r in rows):roots.add("TASK268_TWO_DISTINCT_DURATION_NODES_UNAVAILABLE")
    return dict(funnel=funnel,reasons=issues,primary_root_causes=tuple(sorted(roots)),
        secondary_root_causes=tuple(r.reason for r in issues),
        affected_date_count=sum(r.task268_node_count<2 for r in rows) if path=="ofz" else sum(not getattr(r,path+"_funnel")[-1].pass_count for r in rows),
        affected_bond_date_count=sum(getattr(r,path+"_funnel")[0].input_count-getattr(r,path+"_funnel")[-1].pass_count for r in rows)
            if path != "ofz" else sum(r.ofz_funnel[0].input_count-r.ofz_funnel[-2].pass_count for r in rows))


def curve_valid(curve,day,index):
    require(curve.contract_version == "ofz-reference-curve-v1" and curve.pit_ready is False and
        curve.as_of_date == day and curve.market_source == "moex" and type(curve.node_count) is int and curve.node_count == len(curve.nodes))
    require(curve.status in ("READY","NO_ELIGIBLE_OFZ","NO_FRESH_MARKET_DATA","INSUFFICIENT_DISTINCT_DURATIONS"))
    require((curve.status=="READY") == (curve.node_count>=2))
    seen = set()
    previous = None
    snapshots = index.snapshot_by_id if hasattr(index,"snapshot_by_id") else {r["id"]:r for r in index.data.tables["bond_market_snapshots"]}
    for node in curve.nodes:
        require(finite(node.duration_years) and node.duration_years>0 and (previous is None or node.duration_years>previous))
        previous = node.duration_years
        require(len(node.component_bond_ids)>0 and len(node.component_bond_ids)==len(node.component_snapshot_ids))
        for bid,sid in zip(node.component_bond_ids,node.component_snapshot_ids):
            require(bid in index.ofz and bid not in seen and sid in snapshots)
            snap = snapshots[sid]
            require(snap["bond_id"]==bid and snap["trade_date"]==curve.curve_trade_date and snap["trade_date"]<=day and (day-snap["trade_date"]).days<=7)
            seen.add(bid)


def build_rca(source,data,curves,*,row_store=None):
    # Reconstruct the frozen source semantics without a second service call.
    if row_store is None:require(original._build(data).audit_sha256 == source.audit_sha256)
    index = EvidenceIndex(data)
    latest = max((r["trade_date"] for r in data.market_date_counts),default=None)
    rows = row_store if row_store is not None else []
    gaps,cohort_maxima = GapAccumulator(),{}
    for base in source.per_date_readiness:
        day = base.as_of_date
        observed = {bid for bid,days in index.days.items() if days[0]<day}
        inside = latest is not None and day+timedelta(days=90)<=latest
        out,oreasons,full,metrics,zero = outcome(day,observed,index,inside)
        curve = curves.build_curve(day,market_source="moex",max_curve_age_days=7)
        curve_valid(curve,day,index)
        ofz,ofz_reasons,diag_ofz = ofz_funnel(day,index,curve)
        diagnostic_nodes = {normalize_moex_duration(index.snapshot(bid,day)["raw_payload"],
            stored_duration_years=index.snapshot(bid,day)["duration_years"]).duration_years for bid in diag_ofz}
        require(len(diagnostic_nodes)==base.ofz_duration_node_count and len(full)==base.outcome_full_count)
        credit,creasons,keys,cohorts = credit_funnel(day,observed,index)
        proven,_,_,proven_cohorts = credit_funnel(day,observed,index,proven_only=True)
        joint,jreasons,decision,decision_outcome,legacy_joint,legacy_peers,liquidity,authoritative = joint_funnel(day,observed,index,keys,full)
        require(tuple(sorted(legacy_joint))==base.joint_qualifying_bond_ids and tuple(sorted(legacy_peers))==base.credit_peer_qualified_bond_ids)
        diagnostics = {"CURRENT_ISSUER_MAPPING_DIAGNOSTIC", "TASK277_READY_NOT_EVALUATED",
            "PUBLICATION_UNKNOWN_RETAINED_IN_DIAGNOSTIC_BRANCH", "TASK306A_PEER_POOL_IS_OUTCOME_GATED"}
        if curve.curve_trade_date==day:diagnostics.add("TASK268_USES_ENTRY_DAY_QUOTES")
        if len(diagnostic_nodes)!=curve.node_count:
            diagnostics.add("TASK306A_OFZ_DIAGNOSTIC_GATE_MISMATCH")
            ofz_reasons += reasons({"TASK306A_OFZ_DIAGNOSTIC_GATE_MISMATCH":index.ofz},index.bonds,bank=index.reason_ids,universe_count=len(index.ofz))
            ofz_reasons = tuple(sorted(ofz_reasons,key=lambda r:(-r.affected_bond_date_count,r.reason)))
        if liquidity != authoritative:diagnostics.add("TASK306A_LIQUIDITY_COMPONENT_GATE_APPROXIMATION")
        if authoritative<10:diagnostics.add("TASK270_BENCHMARK_UNIVERSE_INSUFFICIENT")
        if qualified(cohorts) and not legacy_peers:diagnostics.add("TASK306A_CREDIT_PEER_ZERO_IS_INTERSECTION_DEPENDENT")
        sizes = [r.total_member_count for r in cohorts]
        for row in cohorts:
            key = (row.key.target_kind,row.key.rating_agency,row.key.source_provider,row.key.rating_scale_raw,row.key.rating_value_raw)
            prior = cohort_maxima.get(key)
            if prior is None or row.total_member_count>prior.maximum_total_member_count:
                cohort_maxima[key] = CohortMaximum(key=row.key,maximum_total_member_count=row.total_member_count,first_date_at_maximum=day)
        if inside:gaps.extend(metrics)
        rows.append(DateRca(as_of_date=day,source_task306a_classification=base.classification,
            outcome_funnel=out,outcome_reasons=oreasons,outcome_primary_blockers=primary(out),
            outcome_horizon_inside_available_market_history=inside,maximum_continuous_outcome_days=max((m[0] for m in metrics),default=0),
            outcome_gaps=gap_summary(metrics if inside else []),horizons_with_zero_persisted_cashflow_events=zero,
            ofz_funnel=ofz,ofz_reasons=ofz_reasons,task306a_ofz_node_count=base.ofz_duration_node_count,
            task268_status=curve.status,task268_curve_trade_date=curve.curve_trade_date,task268_node_count=curve.node_count,
            task268_diagnostics=tuple(CountEntry(key=k,count=n) for k,n in sorted(curve.diagnostics.model_dump().items())),
            task268_uses_entry_day_quotes=curve.curve_trade_date==day,credit_funnel=credit,credit_proven_funnel=proven,
            credit_reasons=creasons,credit_cohorts=cohorts,credit_proven_cohorts=proven_cohorts,
            unique_credit_key_count=len(cohorts),maximum_credit_cohort_size=max(sizes,default=0),median_credit_cohort_size=distribution(sizes).median or Decimal("0"),
            credit_cohorts_size_one_count=sizes.count(1),credit_cohorts_size_two_count=sizes.count(2),credit_cohorts_size_ge_three_count=sum(n>=3 for n in sizes),
            joint_funnel=joint,joint_reasons=jreasons,decision_only_intersection_count=len(decision),decision_plus_outcome_intersection_count=len(decision_outcome),
            source_task306a_joint_count=len(base.joint_qualifying_bond_ids),source_task306a_peer_count=len(base.credit_peer_qualified_bond_ids),
            reconstructed_task306a_joint_count=len(legacy_joint),reconstructed_task306a_peer_count=len(legacy_peers),
            task306a_liquidity_benchmark_count=liquidity,authoritative_liquidity_component_count=authoritative,
            task270_benchmark_size_prerequisite_met=authoritative>=10,
            blockers=tuple(sorted({r.reason for r in (*oreasons,*ofz_reasons,*creasons,*jreasons) if r.blocking})),diagnostics=tuple(sorted(diagnostics))))
    horizon_dates = [r.as_of_date for r in rows if r.outcome_horizon_inside_available_market_history]
    market_ofz = [r for r in data.tables["bond_market_snapshots"] if r["bond_id"] in index.ofz]
    duration_ready = [r for r in market_ofz if (d:=normalize_moex_duration(r["raw_payload"],stored_duration_years=r["duration_years"])).status=="READY" and finite(d.duration_years) and d.duration_years>0]
    yield_ready = [r for r in market_ofz if finite(r["yield_to_maturity"])]
    yield_ids = {r["id"] for r in yield_ready}
    by_date = defaultdict(set)
    for r in duration_ready:
        by_date[r["trade_date"]].add(normalize_moex_duration(r["raw_payload"],stored_duration_years=r["duration_years"]).duration_years)
    states = Counter()
    for bid in index.ofz:
        p = index.profiles.get(bid)
        if p is None:states["PROFILE_MISSING"] += 1
        else:
            for name in ("currency_state","currency_code","coupon_structure","amortization_structure","perpetual_structure",
                "coupon_frequency_state","coupon_frequency_per_year","maturity_state","maturity_date","listing_status"):
                states[name+"="+str(p[name])] += 1
    maxima = sorted(cohort_maxima.values(),key=lambda r:(-r.maximum_total_member_count,key_sort(tuple(r.key.model_dump(exclude={"pit_ready"}).values()))))
    by_target = defaultdict(int)
    for r in maxima:by_target[r.key.target_kind]=max(by_target[r.key.target_kind],r.maximum_total_member_count)
    outcome_summary = OutcomeSummary(**summary(rows,"outcome",index),
        candidate_date_count_with_complete_calendar_horizon=len(horizon_dates),candidate_date_count_beyond_available_history=len(rows)-len(horizon_dates),
        earliest_theoretically_complete_horizon_date=min(horizon_dates) if horizon_dates else None,
        latest_theoretically_complete_horizon_date=max(horizon_dates) if horizon_dates else None,
        horizons_with_zero_persisted_cashflow_events=sum(r.horizons_with_zero_persisted_cashflow_events for r in rows),
        cashflow_type_counts=tuple(CountEntry(key=k,count=n) for k,n in sorted(Counter(r["event_type"] for r in data.tables["bond_cashflow_events"] if r["source"]=="moex").items())),gaps=gap_summary(gaps))
    ofz_summary = OfzSummary(**summary(rows,"ofz",index),canonical_ofz_count=len(index.ofz),
        canonical_ofz_with_market_history_count=len({r["bond_id"] for r in market_ofz}),market_observation_count=len(market_ofz),
        first_market_date=min((r["trade_date"] for r in market_ofz),default=None),last_market_date=max((r["trade_date"] for r in market_ofz),default=None),
        usable_yield_observation_count=len(yield_ready),usable_duration_observation_count=len(duration_ready),
        usable_yield_and_duration_observation_count=sum(r["id"] in yield_ids for r in duration_ready),
        structural_distributions=tuple(CountEntry(key=k,count=n,pct=pct(n,len(index.ofz))) for k,n in sorted(states.items())),
        market_duration_nodes_by_date=tuple((day,len(nodes)) for day,nodes in sorted(by_date.items())),
        task268_ready_date_count=sum(r.task268_status=="READY" for r in rows),
        diagnostic_gate_mismatch_date_count=sum("TASK306A_OFZ_DIAGNOSTIC_GATE_MISMATCH" in r.diagnostics for r in rows))
    credit_summary = CreditSummary(**summary(rows,"credit",index),proven_funnel=aggregate_stages(rows,"credit_proven_funnel"),
        top_cohort_maxima=tuple(maxima[:20]),maximum_cohort_sizes_by_target_kind=tuple(CountEntry(key=k,count=v) for k,v in sorted(by_target.items())))
    joint_summary = JointSummary(**summary(rows,"joint",index),maximum_decision_only_intersection_count=max((r.decision_only_intersection_count for r in rows),default=0),
        maximum_decision_plus_outcome_intersection_count=max((r.decision_plus_outcome_intersection_count for r in rows),default=0),
        maximum_independent_credit_peer_prerequisite_count=max((len(qualified(r.credit_cohorts)) for r in rows),default=0))
    remediation = defaultdict(set)
    remediation["HISTORICAL_UNIVERSE_CAPTURE"].add("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN")
    remediation["SECURITY_MASTER_EVIDENCE_VERSIONING"].add("CURRENT_SECURITY_MASTER_NOT_HISTORICAL_PROOF")
    remediation["ISSUER_IDENTITY_VERSIONING"].add("CURRENT_ISSUER_MAPPING_DIAGNOSTIC")
    for item in (*outcome_summary.reasons,*ofz_summary.reasons,*credit_summary.reasons):
        code = item.reason
        if code=="TASK306A_OFZ_DIAGNOSTIC_GATE_MISMATCH":category="AUDIT_LOGIC_FIX"
        elif code.startswith(("CASHFLOW_","DUPLICATE_CASHFLOW","UNSUPPORTED_CASHFLOW","MATURITY_REDEMPTION","REDEMPTION_ON")):category="CASHFLOW_HISTORY_REPAIR"
        elif code in ("OFZ_DURATION_NOT_READY","OFZ_YTM_NOT_READY","NO_FRESH_PRE_ENTRY_OFZ_SNAPSHOT"):category="MARKET_HISTORY_BACKFILL"
        elif code in ("EXCLUDED_OFZ_PK_IN_AD","PUBLICATION_FUTURE"):category="NO_REMEDIATION_REQUIRED"
        elif code.startswith(("OFZ_","COUPON_","AMORTIZATION_","PERPETUAL_","MATURITY_NOT")):category="OFZ_SECURITY_MASTER_REPAIR"
        elif code.startswith(("SOURCE_ISSUER","CURRENT_ISSUER")):category="ISSUER_IDENTITY_VERSIONING"
        elif code.startswith(("RATING_","SOURCE_PROVIDER","SOURCE_NATIVE","SOURCE_BOND","MULTIPLE_LATEST","COHORT_","PUBLICATION_","ARTIFACT_")):category="CREDIT_COHORT_EVIDENCE_REPAIR"
        elif code.startswith(("CURRENT_PROFILE","CURRENT_TERMS")):category="SECURITY_MASTER_EVIDENCE_VERSIONING"
        elif code in ("FIRST_MARKET_GAP_BEFORE_HORIZON_END","MULTIPLE_MARKET_GAPS","QUOTE_INPUT_INVALID","ENTRY_QUOTE_MISSING",
            "NO_USABLE_ENTRY_INTERVAL","OUTCOME_DAILY_MARKET_COVERAGE_INCOMPLETE","OUTCOME_HORIZON_EXTENDS_BEYOND_MARKET_HISTORY"):
            category="MARKET_HISTORY_BACKFILL"
        else:continue
        if item.blocking or code=="OUTCOME_HORIZON_EXTENDS_BEYOND_MARKET_HISTORY":remediation[category].add(code)
    if any("TASK306A_CREDIT_PEER_ZERO_IS_INTERSECTION_DEPENDENT" in r.diagnostics for r in rows):
        remediation["AUDIT_LOGIC_FIX"].add("TASK306A_CREDIT_PEER_ZERO_IS_INTERSECTION_DEPENDENT")
    report = Audit(status="COMPLETE",source_readiness_audit_sha256=source.audit_sha256,
        source_readiness_classification=source.audit_classification,candidate_date_count=source.all_candidate_date_count,
        source_pit_safe_date_count=source.pit_safe_date_count,source_diagnostic_only_date_count=source.diagnostic_only_date_count,
        source_unusable_date_count=source.unusable_date_count,outcome_summary=outcome_summary,ofz_summary=ofz_summary,
        credit_summary=credit_summary,joint_summary=joint_summary,per_date=tuple(rows) if row_store is None else (),known_limitations=LIMITATIONS,
        remediation_categories=tuple(Remediation(category=k,factual_reasons=tuple(sorted(v))) for k,v in sorted(remediation.items())))
    if row_store is not None:
        return RcaLinkage("COMPLETE",digest(report,overrides={"per_date":rows}),
            tuple(SourceDateLink(r.as_of_date,r.decision_only_intersection_count,r.task268_status) for r in rows))
    return signed(report)


class HistoricalReplayBlockerRootCauseAuditService:
    def __init__(self,db):
        self.db = db

    def build_linkage(self):
        """Same RCA and semantic SHA, without retaining the per-date RCA graph."""
        source = None
        try:
            with self.db.no_autoflush,localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
                connection = consistent_connection(self.db)
                if connection is None:
                    result = blocked("CONSISTENT_READ_ONLY_TRANSACTION_REQUIRED")
                    return RcaLinkage(result.status,result.audit_sha256)
                source = original.ModernHistoricalReplayReadinessAuditService(self.db).build()
                if source.status=="BLOCKED":
                    result = blocked("SOURCE_TASK306A_BLOCKED",source)
                    return RcaLinkage(result.status,result.audit_sha256)
                data = read_evidence(self.db)
                with ModelSpool(DateRca) as rows,Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as curve_session:
                    return build_rca(source,data,OfzReferenceCurveService(curve_session),row_store=rows)
        except (SQLAlchemyError,OSError):
            result = blocked("PERSISTED_RCA_EVIDENCE_UNAVAILABLE",source)
        except (ValueError,TypeError,KeyError,OverflowError,DecimalException):
            result = blocked("RCA_EVIDENCE_OR_SOURCE_BINDING_INVALID",source)
        return RcaLinkage(result.status,result.audit_sha256)

    def build(self):
        source = None
        try:
            with self.db.no_autoflush,localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
                connection = consistent_connection(self.db)
                if connection is None:return blocked("CONSISTENT_READ_ONLY_TRANSACTION_REQUIRED")
                source = original.ModernHistoricalReplayReadinessAuditService(self.db).build()
                if source.status=="BLOCKED":return blocked("SOURCE_TASK306A_BLOCKED",source)
                data = read_evidence(self.db)
                with Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as curve_session:
                    return build_rca(source,data,OfzReferenceCurveService(curve_session))
        except SQLAlchemyError:
            return blocked("PERSISTED_RCA_EVIDENCE_UNAVAILABLE",source)
        except (ValueError,TypeError,KeyError,OverflowError,DecimalException):
            return blocked("RCA_EVIDENCE_OR_SOURCE_BINDING_INVALID",source)
