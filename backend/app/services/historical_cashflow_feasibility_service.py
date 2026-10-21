"""Offline planning/evaluation; no HTTP execution or application DB access."""
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation, localcontext, Context, ROUND_HALF_EVEN
import hashlib
from pathlib import Path
import tempfile
import io
import re
from app.schemas.historical_cashflow_feasibility import Candidate, PilotPlan, Event, Partition, Evaluation, TABLES
from app.schemas.historical_identity_recovery_inventory import InventoryRow, AcquisitionRoadmap
from app.services.historical_identity_recovery_artifacts import verify_published, verify_inputs, NAMES, file_sha, disjoint
from app.services.historical_identity_probe_service import read_model, read_bytes, safe_path, encode, atomic, writer_lock, independent_root
from app.services.historical_identity_probe_service import assert_capacity
from app.schemas.historical_identity_feasibility import ProbeLimits
from app.services.historical_identity_feasibility_service import require, IdentityError, isin_valid
from app.services.historical_evidence_normalization import checked, signed, sha, fits, hash_syntax
from app.services.moex_normalization import canonicalize_moex_currency
from app.services.ofz_identity import is_ofz_instrument
from app.services.historical_audit_canonical_json import chunks


def exact_strings(values, *, nonempty=True):
    require(isinstance(values, Sequence) and not isinstance(values, (str, bytes)), "DETERMINISTIC_SEQUENCE_REQUIRED")
    require((bool(values) or not nonempty) and all(type(v) is str and v and v.strip() == v for v in values)
        and len(set(values)) == len(values), "UNIQUE_NONBLANK_STRINGS_REQUIRED")
    return tuple(sorted(values))


def validate_plan(plan):
    require(type(plan) is PilotPlan, "PLAN_REQUIRED")
    checked(plan, "plan_sha256")
    require(plan.status == "PLANNED" and not plan.blockers and len(plan.cohort) == plan.requested_size,
        "PLAN_NOT_EXECUTABLE")
    secids = tuple(c.secid for c in plan.cohort)
    require(secids == exact_strings(secids) and plan.tables == TABLES and
        sorted(c.selection_order for c in plan.cohort) == list(range(1, len(secids)+1)), "PLAN_COHORT_INVALID")
    upstream = dict(plan.upstream_hashes)
    require(len(upstream)==len(plan.upstream_hashes) and plan.upstream_hashes==tuple(sorted(plan.upstream_hashes)) and
        {"task307_audit","source_manifest","source_content","source_scope","source_implementation","task308a_plan",
            "task308a_authorization","task308a_probe","task308a_evaluation","task308b1_report","task308b1_inventory",
            "task308b1_roadmap","task308b1_roadmap_file"} <= set(upstream), "PLAN_UPSTREAM_HASHES_INVALID")
    for h in upstream.values():hash_syntax(h)
    for c in plan.cohort:
        require(re.fullmatch(r"[A-Za-z0-9._-]{1,64}",c.secid) is not None,"SECID_NOT_REQUESTABLE")
        require(isin_valid(c.accepted_current_isin) and c.request_sha256s and c.response_sha256s and c.observations,
            "PLAN_LINEAGE_INVALID")
        for h in (c.lineage_sha256, *c.request_sha256s, *c.response_sha256s):
            hash_syntax(h)
        require(all(t.utcoffset() is not None and t.utcoffset().total_seconds() == 0 for t in c.observations), "OBSERVATION_UTC_REQUIRED")
    return plan


def verified_pool(root, expected_sha, optional_replay=None):
    hash_syntax(expected_sha)
    root = safe_path(root)
    before = tuple(file_sha(root/n) for n in NAMES)
    audit = verify_published(output_dir=root, expected_report_sha256=expected_sha)
    roadmap = read_model(root/NAMES[2],AcquisitionRoadmap,32*1024**2)
    require(hashlib.sha256(encode(roadmap)+b"\n").hexdigest()==before[2],"ROADMAP_CANONICAL_FILE_SHA_MISMATCH")
    hashes = dict(audit.input_hashes)
    required = ("task307_audit", "source_manifest", "source_content", "source_scope", "source_implementation",
        "task308a_plan", "task308a_authorization", "task308a_probe", "task308a_evaluation")
    require(all(k in hashes for k in required), "UPSTREAM_HASH_MISSING")
    for h in hashes.values():
        hash_syntax(h)
    if optional_replay is not None:
        replay = verify_inputs(**optional_replay)
        require(dict(replay.analysis.hashes) == hashes, "OPTIONAL_REPLAY_MISMATCH")
        replay.verify_unchanged()
    pool, exclusions = [], Counter()
    with safe_path(root/NAMES[0]).open("rb") as stream:
        for line in stream:
            row = InventoryRow.model_validate_json(line)
            if row.secid == "MVZ220004019" or not row.current_binding_confirmed or not row.previously_probed:
                exclusions["CURRENT_BINDING_NOT_CONFIRMED"] += 1
                continue
            if row.binding_conflict or row.requires_review:
                exclusions["IDENTITY_REVIEW_OR_CONFLICT"] += 1
                continue
            require(row.confirmed_current_isin is not None and isin_valid(row.confirmed_current_isin), "INVENTORY_LINEAGE_INVALID")
            confirmed = [s for s in row.source_results if s.current_binding_observed]
            require(confirmed and all(s.secid == row.secid and s.returned_secid == row.secid and
                s.returned_isin == row.confirmed_current_isin and s.status == "EXACT_CURRENT_SOURCE_BINDING_FOUND" and s.complete_response and not s.requires_review
                and s.request_sha256s and s.response_sha256s and s.observation_times for s in confirmed), "INVENTORY_LINEAGE_INVALID")
            require(not any(s.returned_isin and s.returned_isin != row.confirmed_current_isin for s in row.source_results), "INVENTORY_SOURCE_CONFLICT")
            for s in confirmed:
                for h in (*s.request_sha256s, *s.response_sha256s):
                    hash_syntax(h)
                require(all(t.utcoffset() is not None and t.utcoffset().total_seconds() == 0 for t in s.observation_times), "OBSERVATION_UTC_REQUIRED")
            pool.append(row)
            require(len(pool) <= 12, "CONFIRMED_POOL_EXCEEDS_UPSTREAM_PROBE_SCOPE")
    require(before == tuple(file_sha(root/n) for n in NAMES), "INPUT_EVIDENCE_CHANGED")
    upstream = tuple(sorted((*audit.input_hashes, ("task308b1_report", audit.report_sha256),
        ("task308b1_inventory", audit.inventory_sha256), ("task308b1_roadmap", audit.roadmap_sha256),
        ("task308b1_roadmap_file", before[2]))))
    return pool, exclusions, upstream


class HistoricalCashflowFeasibilityService:
    @staticmethod
    def plan(*, b1_root, expected_b1_report_sha256, pilot_size, candidate_secids=None, optional_replay=None):
        require(type(pilot_size) is int and 1 <= pilot_size <= 6, "PILOT_SIZE_REQUIRED_1_TO_6")
        try:
            pool, exclusions, upstream = verified_pool(b1_root, expected_b1_report_sha256, optional_replay)
            if candidate_secids is not None:
                restrictions = exact_strings(candidate_secids)
                require(set(restrictions) <= {r.secid for r in pool}, "RESTRICTION_NOT_ELIGIBLE")
                pool = [r for r in pool if r.secid in restrictions]
            require(len(pool) >= pilot_size, "INSUFFICIENT_ELIGIBLE_COHORT")
            selected, covered = [], set()
            while len(selected) < pilot_size:
                remaining = [r for r in pool if r.secid not in {x.secid for x in selected}]
                ofz = [r for r in remaining if r.observed and is_ofz_instrument(secid=r.secid, isin=r.confirmed_current_isin)]
                choices = ofz if not selected and ofz else remaining
                def features(r):
                    return {"BOARD:"+b for b in r.boards} | {"STRATUM:"+s for s in r.strata}
                row = min(choices, key=lambda r: (-int(r.observed), -int(r.actual_trade_evidence),
                    -int(r.usable_price_nkd_evidence), -len(features(r)-covered), r.secid))
                reasons = ("OBSERVED_CANONICAL_OFZ_FIRST",) if not selected and ofz else ("DETERMINISTIC_COMPONENT_COVERAGE",)
                selected.append(Candidate(secid=row.secid, accepted_current_isin=row.confirmed_current_isin,
                    selection_order=len(selected)+1, selection_reasons=reasons, observed=row.observed,
                    canonical_ofz=is_ofz_instrument(secid=row.secid, isin=row.confirmed_current_isin),
                    actual_trade_indicator=row.actual_trade_evidence, usable_price_nkd_indicator=row.usable_price_nkd_evidence,
                    boards=row.boards, strata=row.strata, lineage_sha256=sha(row.source_results),
                    request_sha256s=tuple(sorted({h for s in row.source_results for h in s.request_sha256s})),
                    response_sha256s=tuple(sorted({h for s in row.source_results for h in s.response_sha256s})),
                    observations=tuple(sorted({t for s in row.source_results for t in s.observation_times}))))
                covered |= features(row)
            return validate_plan(signed(PilotPlan, "plan_sha256", status="PLANNED", requested_size=pilot_size,
                cohort=tuple(sorted(selected, key=lambda r:r.secid)), upstream_hashes=upstream,
                exclusions=tuple(sorted(exclusions.items()))))
        except (IdentityError, ValueError, OSError) as exc:
            code = exc.code if isinstance(exc, IdentityError) else "UPSTREAM_VALIDATION_FAILED"
            return signed(PilotPlan, "plan_sha256", status="BLOCKED", requested_size=pilot_size, blockers=(code,))

    @staticmethod
    def evaluate(*, plan, authorization, evidence_root):
        # Import the replay service only at execution; this path never creates an HTTP client.
        from app.services.historical_cashflow_probe_service import replay
        try:
            validate_plan(plan)
            result, receipts = replay(plan, authorization, evidence_root)
            events, partitions = [], []
            for candidate in plan.cohort:
                for table in TABLES:
                    rs = [r for r in receipts if r.request.secid == candidate.secid and r.request.table == table]
                    accepted = [r for r in rs if r.status == "ACCEPTED"]
                    local = []
                    from app.services.historical_cashflow_probe_client import parse_page
                    for r in accepted:
                        page = parse_page(read_bytes(safe_path(evidence_root)/(r.body_sha256+".body"), authorization.limits.response_bytes), r.request)
                        for index, row in enumerate(page.rows):
                            local.append(map_event(row, candidate, table, r.request.offset, index, r.body_sha256))
                    events.extend(local)
                    requested = candidate.secid in authorization.secids and table in authorization.tables
                    complete = bool(accepted and accepted[-1].complete)
                    diagnostics = {r.category for r in rs if r.category not in ("CURSOR_CONTINUES", "CURSOR_COMPLETE")}
                    diagnostics |= {d for e in local for d in e.diagnostics}
                    execution = "NOT_REQUESTED" if not requested else "NOT_STARTED" if not rs else "COMPLETE" if complete else "INCOMPLETE" if accepted else "SOURCE_ERROR"
                    dup_counts = Counter(e.row_sha256 for e in local)
                    by_event = defaultdict(list)
                    for event in local:
                        if event.event_date is not None: by_event[event.event_date].append(event)
                    conflicting = sum(len(group) for group in by_event.values() if len({sha((e.amount,e.currency,e.amount_unit)) for e in group})>1)
                    partitions.append(Partition(secid=candidate.secid, table=table, execution=execution,
                        completeness="PROVEN" if complete else "UNPROVEN", attempts=len(rs), pages=len(accepted), raw_rows=len(local),
                        valid_rows=sum(e.status != "EVENT_INVALID" for e in local), invalid_rows=sum(e.status == "EVENT_INVALID" for e in local),
                        date_rows=sum(e.event_date is not None for e in local), amount_rows=sum(e.amount is not None for e in local),
                        currency_rows=sum(e.currency is not None for e in local), unit_rows=sum(e.amount_unit is not None for e in local),
                        duplicate_occurrences=sum(n for n in dup_counts.values() if n>1), conflicting_occurrences=conflicting,
                        diagnostics=tuple(sorted(diagnostics))))
            duplicates, economic = defaultdict(list), defaultdict(list)
            for e in events:
                occurrence = f"{e.requested_secid}/{e.table}/{e.offset}/{e.row_index}"
                duplicates[sha((e.requested_secid,e.table,e.row_sha256))].append(occurrence)
                if e.event_date is not None:
                    economic[(e.requested_secid,e.table,e.event_date)].append((e,occurrence))
            conflicts = []
            for key, group in economic.items():
                if len({sha((e.amount,e.currency,e.amount_unit)) for e,_ in group}) > 1:
                    conflicts.append((sha(key),tuple(o for _,o in group)))
            require(replay(plan,authorization,evidence_root)==(result,receipts),"EVIDENCE_CHANGED_DURING_EVALUATION")
            return signed(Evaluation, "report_sha256", status="CASHFLOW_SOURCE_FEASIBLE_WITH_LIMITATIONS" if any(p.execution == "COMPLETE" for p in partitions) else "SOURCE_FEASIBILITY_UNPROVEN",
                plan_sha256=plan.plan_sha256, authorization_sha256=authorization.authorization_sha256, probe_sha256=result.probe_sha256,
                upstream_hashes=plan.upstream_hashes,cohort=plan.cohort,
                events=tuple(events), partitions=tuple(partitions), duplicate_groups=tuple(sorted((k,tuple(v)) for k,v in duplicates.items() if len(v)>1)),
                conflicting_event_groups=tuple(sorted(conflicts)), economic_qualification="CONFLICTING" if conflicts or any(e.source_identity_conflict for e in events) else "UNPROVEN")
        except (IdentityError, ValueError, OSError) as exc:
            return signed(Evaluation, "report_sha256", status="BLOCKED", blockers=(exc.code if isinstance(exc,IdentityError) else "EVIDENCE_VALIDATION_FAILED",),
                plan_sha256=plan.plan_sha256, authorization_sha256=authorization.authorization_sha256, probe_sha256="")


DATE_ALIASES = {
    "coupons":("COUPONDATE","COUPON_DATE","DATE"),
    "amortizations":("AMORTDATE","AMORTIZATIONDATE","AMORT_DATE","DATE"),
    "redemptions":("REDEMPTIONDATE","REDEMPTION_DATE","MATDATE","MATURITYDATE","MATURITY_DATE","DATE"),
    "offers":("OFFERDATE","OFFER_DATE","DATE"),
}
AMOUNT_ALIASES = {
    "coupons":("VALUE","AMOUNT","COUPONVALUE","COUPON_VALUE"),
    "amortizations":("VALUE","AMOUNT","AMORTVALUE","AMORTIZATION_VALUE"),
    "redemptions":("VALUE","AMOUNT","REDEMPTIONVALUE","REDEMPTION_VALUE"),
    "offers":("VALUE","AMOUNT"),
}


def map_event(row, candidate, table, offset, index, page_sha):
    diagnostics = set()
    def alias(names, parser, label):
        supplied = [v for k,v in row.items() if k.upper() in names and v is not None and not (type(v) is str and not v.strip())]
        if not supplied:
            diagnostics.add(label+"_MISSING")
            return None
        values = []
        for value in supplied:
            try:
                values.append(parser(value))
            except (ValueError, TypeError, InvalidOperation, OverflowError):
                diagnostics.add(label+"_INVALID")
        if len(values) != len(supplied):
            return None
        if any(v != values[0] for v in values):
            diagnostics.add(label+"_ALIAS_CONFLICT")
            return None
        return values[0]
    def string(v):
        if type(v) is not str: raise ValueError()
        return v
    def number(v):
        if type(v) not in (str,int,Decimal): raise ValueError()
        if type(v) is str and re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?",v.strip()) is None:
            raise ValueError()
        with localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
            n = Decimal(v)
        if not n.is_finite(): raise ValueError()
        return n
    def day(v):
        if type(v) is not str: raise ValueError()
        d = date.fromisoformat(v)
        if d.isoformat() != v: raise ValueError()
        return d
    secid = alias(("SECID","SECURITYID"),string,"SOURCE_SECID")
    isin = alias(("ISIN",),string,"SOURCE_ISIN")
    identity_conflict = (secid is not None and secid != candidate.secid) or (isin is not None and isin != candidate.accepted_current_isin) or any(
        x in diagnostics for x in ("SOURCE_SECID_ALIAS_CONFLICT","SOURCE_ISIN_ALIAS_CONFLICT"))
    if identity_conflict: diagnostics.add("SOURCE_IDENTITY_CONFLICT")
    d = alias(DATE_ALIASES[table],day,"EVENT_DATE")
    amount = alias(AMOUNT_ALIASES[table],number,"AMOUNT")
    if amount is not None:
        if amount < 0: diagnostics.add("AMOUNT_NEGATIVE")
        if not fits(amount,18,6): diagnostics.add("STORAGE_UNREPRESENTABLE")
    def currency(v):
        result = canonicalize_moex_currency(string(v))
        if result is None: raise ValueError()
        return result
    curr = alias(("CURRENCY","CURRENCYID","FACEUNIT"),currency,"CURRENCY")
    unit = alias(("UNIT","AMOUNTUNIT","AMOUNT_UNIT","VALUEUNIT"),string,"AMOUNT_UNIT")
    diagnostics.add("AMOUNT_UNIT_AUTHORITY_UNPROVEN")
    context_names = {"VALUEPRC","PRICE","OFFERPRICE","OFFER_PRICE","COUPONPERCENT","COUPON_PERCENT",
        "FACEVALUE","FACEUNIT","PUBLICATIONDATE","PUBLISHDATE","EFFECTIVEDATE","RECORDDATE","COUPONTYPE"}
    context = tuple(sorted((k,None if v is None else str(v)) for k,v in row.items() if k.upper() in context_names))
    invalid = identity_conflict or any(x.endswith(("_INVALID","_ALIAS_CONFLICT")) for x in diagnostics) or any(x in diagnostics for x in ("AMOUNT_MISSING","EVENT_DATE_MISSING","AMOUNT_NEGATIVE","STORAGE_UNREPRESENTABLE","CURRENCY_MISSING"))
    return Event(requested_secid=candidate.secid, accepted_current_isin_context=candidate.accepted_current_isin, table=table,
        offset=offset,row_index=index,row_sha256=sha(row),page_sha256=page_sha,returned_source_secid=secid,returned_source_isin=isin,
        source_identity_asserted=bool((secid or isin) and not identity_conflict and not any(d.startswith("SOURCE_") and d.endswith("_INVALID") for d in diagnostics)),source_identity_conflict=bool(identity_conflict),
        binding_provenance="RETURNED_IDENTITY" if secid or isin else "REQUEST_CONTEXT_ONLY",event_date=d,amount=amount,currency=curr,
        amount_unit=unit,status="EVENT_INVALID" if invalid else "SCHEDULED_OBSERVATION",diagnostics=tuple(sorted(diagnostics)),source_context=context)


def publish(*, report, output_dir, protected_roots=()):
    """Audit JSON is the completion artifact, installed after its validated summary."""
    require(type(report) in (PilotPlan,Evaluation),"REPORT_REQUIRED")
    field = "plan_sha256" if type(report) is PilotPlan else "report_sha256"
    checked(report,field)
    root = safe_path(output_dir)
    require(root.is_dir(), "OUTPUT_DIRECTORY_REQUIRED")
    independent_root(root)
    require(not any((p/n).exists() for p in (root,*root.parents) for n in
        ("task308b1-identity-audit.json","task308a-scope.json","b2-state.json","manifest.json")),"OUTPUT_INSIDE_SOURCE_ROOT")
    require(all(disjoint(root,safe_path(p)) for p in protected_roots), "OUTPUT_INPUT_OVERLAP")
    prefix = "task308b2-plan" if type(report) is PilotPlan else "task308b2-evaluation"
    output = io.BytesIO()
    for chunk in chunks(report):
        block = chunk.encode("ascii")
        require(output.tell()+len(block) <= 32*1024**2, "OUTPUT_BYTE_LIMIT")
        output.write(block)
    body = output.getvalue()
    details = ""
    if type(report) is PilotPlan:
        details = "\n".join(f"- {c.secid}: current ISIN {c.accepted_current_isin}; selection {c.selection_order}" for c in report.cohort)
    else:
        details = "\n".join(f"- {p.secid}/{p.table}: {p.execution}; {p.raw_rows} rows, {p.invalid_rows} invalid; completeness {p.completeness}" for p in report.partitions)
    summary = (f"# Task308B2\n\nStatus: {report.status}\n\nSHA: {getattr(report,field)}\n\n"+details+"\n\n"
        "Source qualification only. No actual payment, historical, PIT or execution readiness.\n").encode("ascii")
    require(len(body)+len(summary) <= 32*1024**2, "OUTPUT_BYTE_LIMIT")
    assert_capacity(root,ProbeLimits(),extra=2*(len(body)+len(summary)))
    with writer_lock(root):
        for name,data in ((prefix+".json",body),(prefix+"-summary.md",summary)):
            p = safe_path(root/name)
            if p.exists(): require(read_bytes(p,32*1024**2)==data,"PUBLICATION_CONFLICT")
        with tempfile.TemporaryDirectory(prefix=".task308b2-",dir=root) as stage:
            staging = Path(stage)
            for name,data in ((prefix+"-summary.md",summary),(prefix+".json",body)):
                atomic(staging/name,data,immutable=True)
            created=[]
            try:
                for name in (prefix+"-summary.md",prefix+".json"):
                    target=root/name
                    existed=target.exists()
                    atomic(target,read_bytes(staging/name,32*1024**2),immutable=True)
                    if not existed:created.append(target)
            except BaseException:
                for target in reversed(created):target.unlink(missing_ok=True)
                raise
    return getattr(report,field)
