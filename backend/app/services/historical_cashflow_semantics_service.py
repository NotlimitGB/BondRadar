"""Offline R3 semantics; old qualification and artifacts remain unchanged."""
from collections import defaultdict, Counter
from app.schemas.historical_cashflow_semantics import (ExpectedHashes, SourceRowIntegrityReport, CashflowSemanticsReport,
    SemanticsPackage, FieldEvidence, SourceSemanticEvent, CanonicalObservation, CouponCoverage)
from app.schemas.historical_cashflow_feasibility import ProbeState, Receipt
from app.schemas.historical_cashflow_source_forensics import DiagnosticState, DiagnosticReceipt
from app.services.historical_cashflow_qualification_service import HistoricalCashflowQualificationService, input_fingerprints
from app.services.historical_cashflow_qualification_artifacts import verify_published
from app.services.historical_cashflow_candidate_decoder import SECIDS, TABLES, typed_tree
from app.services.historical_cashflow_feasibility_service import AMOUNT_ALIASES
from app.services.historical_cashflow_row_integrity import verify_retained_rows
from app.services.historical_identity_probe_service import read_model, read_bytes, safe_path, encode
from app.services.historical_identity_feasibility_service import IdentityError, require
from app.services.historical_evidence_normalization import signed, hash_syntax, sha

FIELDS = {
    "faceunit": "Currency of nominal; not monetary unit of VALUE",
    "facevalue": "Candidate nominal value context",
    "initialfacevalue": "Candidate original nominal value context",
    "value": "Supplied scheduled source amount; per-bond/per-lot/per-issue unit unproven",
    "valueprc": "Supplied source percentage context; no conversion formula applied",
    "value_rub": "Candidate RUB equivalent context; denominator and conversion authority unproven",
    "issuevalue": "Candidate issue-level context; not a per-bond amount",
    "data_source": "Exact source event marker; maturity literal classified only by reviewed R3 policy",
}
FACEUNIT_AUTHORITY = "https://www.moex.com/n18266"
AMORTIZATION_DEFINITION = "https://bondguide.moex.com/terms/9"
POLICY = "TASK308B2R3_REVIEWED_SOURCE_ONLY_POLICY_V1"
LIMITATIONS = ("NO_ACTUAL_PAYMENT_PROOF", "MONETARY_UNIT_AUTHORITY_UNPROVEN", "HISTORICAL_PUBLICATION_UNPROVEN",
               "NO_HISTORICAL_DECISION_AVAILABILITY_PROOF", "NO_TOTAL_RETURN_READINESS", "CROSS_TIME_AGREEMENT_NOT_PUBLICATION_PROOF")


def raw_field(row, name):
    return tuple((key, value) for key, value in row.items() if key.upper() == name.upper())


def field_evidence(row, name):
    supplied = raw_field(row, name)
    require(len(supplied) <= 1, "SOURCE_FIELD_AMBIGUOUS")
    value = supplied[0][1] if supplied else None
    proof = "UNPROVEN"; sources = (); scope = "No reviewed field-specific Bondization authority"
    if name == "faceunit":
        proof = "PARTIAL" if supplied else "UNPROVEN"
        sources = (FACEUNIT_AUTHORITY,)
        scope = "FACEUNIT nominal-currency documentation for history responses; Bondization amount units not established"
    elif name == "data_source":
        sources = (POLICY, AMORTIZATION_DEFINITION)
        scope = "Reviewed exact maturity literal is a source-only event policy; glossary is conceptual, not an endpoint field contract"
        if type(value) is str and value == "maturity": proof = "PARTIAL"
    return FieldEvidence(field_name=name, source_field_names=tuple(k for k, _ in supplied),
        raw_source_type=type(value).__name__ if supplied else "MISSING",
        sample_value=encode(typed_tree(value) if supplied else ("MISSING", None)).decode(),
        proposed_semantic=FIELDS[name], authority_source=sources, authority_scope=scope, proof_status=proof,
        limitations=("NOT_MONETARY_UNIT_AUTHORITY", "NOT_PAYMENT_CONFIRMATION", "NOT_HISTORICAL_PUBLICATION_PROOF"))


def classify_source_event(*, table, row):
    if table == "coupons": return "SCHEDULED_COUPON"
    if table == "offers": return "SCHEDULED_OFFER_OBSERVATION"
    if table == "amortizations":
        values = raw_field(row, "data_source")
        return "SCHEDULED_FINAL_MATURITY" if len(values) == 1 and type(values[0][1]) is str and values[0][1] == "maturity" else "SOURCE_AMORTIZATION_UNCLASSIFIED"
    require(table == "redemptions", "SOURCE_TABLE_NOT_SUPPORTED")
    return "SOURCE_REDEMPTION_UNCLASSIFIED"


def field_conflict_keys(observations, rows_by_request):
    """Compare raw field multisets across single-page profiles, not payment totals."""
    groups = defaultdict(lambda: defaultdict(list))
    for o in observations:
        if o.view.structural_status == "STRUCTURALLY_RECOVERABLE":
            groups[(o.requested_secid, o.table)][o.profile].append(o)
    conflicts = set()
    for (secid, table), profiles in groups.items():
        comparable = [members[0] for members in profiles.values() if len(members) == 1]
        if len(comparable) < 2: continue
        for name in FIELDS:
            values = [Counter(sha(tuple((k, typed_tree(v)) for k, v in raw_field(row, name)))
                              for row in rows_by_request[o.request_id]) for o in comparable]
            if any(value != values[0] for value in values[1:]): conflicts.add((secid, table, name))
    return conflicts


def build_semantics(*, source, integrity, rows_by_request):
    """Pure projection of verified row occurrences, never a combined payment schedule."""
    require(integrity.status == "PASS", "SOURCE_ROW_INTEGRITY_BLOCKED")
    observations = source.observations
    require(set(rows_by_request) == {o.request_id for o in observations}, "VERIFIED_ROW_PARTITION_MISMATCH")
    conflicts = field_conflict_keys(observations, rows_by_request)
    events = []
    for observation in observations:
        rows = rows_by_request[observation.request_id]
        require(len(rows) == len(observation.view.events), "VERIFIED_ROW_COUNT_MISMATCH")
        for index, (row, upstream) in enumerate(zip(rows, observation.view.events)):
            require(sha(typed_tree(row)) == observation.view.source_row_sha256s[index], "VERIFIED_ROW_VALUE_MISMATCH")
            events.append(SourceSemanticEvent(request_id=observation.request_id, secid=observation.requested_secid,
                accepted_current_isin_context=observation.view.accepted_current_isin_context,
                returned_isin=upstream.returned_source_isin, returned_secid=upstream.returned_source_secid,
                source_identity_diagnostics=tuple(d for d in upstream.diagnostics if d.startswith("SOURCE_")),
                table=observation.table, profile=observation.profile, row_index=index,
                source_row_sha256=observation.view.source_row_sha256s[index], observed_at=observation.observed_at,
                source_completeness=observation.view.completeness, event_date=upstream.event_date,
                semantic_classification=classify_source_event(table=observation.table, row=row),
                fields=tuple(field_evidence(row, name).model_copy(update={"conflicts":
                    ("CROSS_PROFILE_FIELD_OBSERVATIONS_DIFFER",) if (observation.requested_secid, observation.table, name) in conflicts else ()})
                    for name in FIELDS), diagnostics=upstream.diagnostics))
    canonical = []; coupons = []
    for secid in SECIDS:
        for table in TABLES:
            group = [o for o in observations if o.requested_secid == secid and o.table == table]
            profiles = defaultdict(list)
            reasons = set()
            for o in group:
                reasons.update(o.view.diagnostics)
                if o.view.structural_status == "STRUCTURALLY_RECOVERABLE": profiles[o.profile].append(o)
            eligible = []
            for members in profiles.values():
                if len(members) != 1 or (members[0].view.cursor_index not in (None, 0)) or any(e.offset != 0 for e in members[0].view.events):
                    reasons.add("MULTIPAGE_OBSERVATIONS_NOT_COMBINED")
                else: eligible.extend(members)
            eligible.sort(key=lambda o: (o.view.completeness != "COMPLETE", ("BASELINE", "TABLE_ONLY", "B2_ACCEPTED").index(o.profile), o.request_id))
            chosen = eligible[0] if eligible else None
            comparison = next((c.verdict for c in source.comparisons if c.requested_secid == secid and c.table == table), "SINGLE_PROFILE_ONLY")
            if len({o.view.metadata_json for o in group if o.view.structural_status == "STRUCTURALLY_RECOVERABLE"}) > 1:
                reasons.add("CROSS_PROFILE_METADATA_DIFFERENCE"); comparison = "CROSS_PROFILE_DIFFERENCE"
            if comparison == "CROSS_PROFILE_DIFFERENCE": reasons.add("ECONOMIC_PROMOTION_BLOCKED_BY_PROFILE_DISAGREEMENT")
            if not chosen: reasons.add("CANONICAL_OBSERVATION_UNAVAILABLE")
            canonical.append(CanonicalObservation(secid=secid, table=table,
                canonical_request_id=chosen.request_id if chosen else None,
                observation_request_ids=tuple(o.request_id for o in group), comparison_status=comparison,
                diagnostics=tuple(sorted(reasons))))
            if table != "coupons": continue
            present = missing = invalid = 0; dates = []
            if chosen:
                for row, event in zip(rows_by_request[chosen.request_id], chosen.view.events):
                    values = [v for key, v in row.items() if key.upper() in AMOUNT_ALIASES["coupons"] and
                              v is not None and not (type(v) is str and not v.strip())]
                    if not values: missing += 1; dates.append(event.event_date)
                    else:
                        present += 1
                        if event.amount is None or any(d in event.diagnostics for d in ("AMOUNT_INVALID", "AMOUNT_ALIAS_CONFLICT", "AMOUNT_NEGATIVE", "STORAGE_UNREPRESENTABLE")):
                            invalid += 1
            coupons.append(CouponCoverage(secid=secid, canonical_request_id=chosen.request_id if chosen else None,
                source_schedule_completeness=chosen.view.completeness if chosen else "UNAVAILABLE",
                coupon_row_count=present + missing, coupon_amount_present_count=present, coupon_amount_missing_count=missing,
                coupon_amount_invalid_count=invalid, coupon_amount_missing_dates=tuple(dates), diagnostics=tuple(sorted(reasons))))
    return signed(CashflowSemanticsReport, "report_sha256", status=source.status, source_row_integrity_sha256=integrity.report_sha256,
        structural_integrity_status="PASS", source_cashflow_semantics_status="QUALIFIED_WITH_LIMITATIONS",
        canonical_observations=tuple(canonical), events=tuple(events), coupon_coverage=tuple(coupons), limitations=LIMITATIONS)


def receipt_bindings(*, b2_root, r1_root):
    bindings = {}
    for root, state_name, state_type, receipt_type, pattern in (
        (b2_root, "b2-state.json", ProbeState, Receipt, "receipt-{n:03}.json"),
        (r1_root, "r1-state.json", DiagnosticState, DiagnosticReceipt, "r1-receipt-{n:03}.json")):
        root = safe_path(root)
        state = read_model(root / state_name, state_type)
        for n, digest in enumerate(state.receipt_sha256s, 1):
            receipt = read_model(root / pattern.format(n=n), receipt_type)
            if receipt.body_sha256 is None: continue
            require(receipt.receipt_sha256 == digest and digest not in bindings, "RECEIPT_BINDING_INVALID")
            if receipt_type is DiagnosticReceipt:
                folder = root / ("accepted" if receipt.disposition == "ACCEPTED" else "quarantine")
                profile = receipt.request.profile
            else:
                folder = root; profile = "B2_ACCEPTED" if receipt.request.table == "amortizations" else "BASELINE"
            bindings[digest] = (receipt, profile, folder / (receipt.body_sha256 + ".body"))
    return bindings


class HistoricalCashflowSemanticsService:
    @staticmethod
    def qualify_cashflow_semantics(*, b2_plan, b2_authorization, b2_evidence_root, b2_evaluation, r1_forensics,
        r1_plan, r1_authorization, r1_evidence_root, r1_report, r2_output_dir, expected):
        require(type(expected) is ExpectedHashes, "TEN_EXPECTED_HASHES_REQUIRED")
        ExpectedHashes.model_validate(expected)
        files = dict(b2_plan=b2_plan, b2_authorization=b2_authorization, b2_evaluation=b2_evaluation,
                     r1_forensics=r1_forensics, r1_plan=r1_plan, r1_authorization=r1_authorization, r1_report=r1_report)
        roots = dict(b2=b2_evidence_root, r1=r1_evidence_root, r2=r2_output_dir)
        try:
            hash_syntax(expected.r2_source_contract_sha256); hash_syntax(expected.r2_qualification_sha256)
            before = input_fingerprints(files=files, roots=roots)
            published = verify_published(output_dir=r2_output_dir)
            require(published.source_contract.report_sha256 == expected.r2_source_contract_sha256 and
                    published.qualification.report_sha256 == expected.r2_qualification_sha256, "EXPECTED_R2_SHA_MISMATCH")
            replayed = HistoricalCashflowQualificationService.qualify_existing_evidence(**files,
                b2_evidence_root=b2_evidence_root, r1_evidence_root=r1_evidence_root, expected=expected.upstream)
            require(replayed.status != "BLOCKED" and replayed == published, "R2_FULL_REPLAY_MISMATCH")
            bindings = receipt_bindings(b2_root=b2_evidence_root, r1_root=r1_evidence_root)
            findings = []; rows = {}
            for observation in published.source_contract.observations:
                require(observation.request_id in bindings, "SOURCE_REQUEST_BINDING_MISSING")
                receipt, profile, path = bindings[observation.request_id]
                require(receipt.request.secid == observation.requested_secid and receipt.request.table == observation.table and
                    profile == observation.profile and receipt.observed_at == observation.observed_at and
                    receipt.body_sha256 == observation.view.raw_body_sha256 and
                    all(e.offset == receipt.request.offset for e in observation.view.events), "SOURCE_REQUEST_BINDING_MISMATCH")
                finding, source_rows = verify_retained_rows(body=read_bytes(path, 8 * 1024**2), observation=observation)
                findings.append(finding); rows[observation.request_id] = source_rows
            require(before == input_fingerprints(files=files, roots=roots), "INPUT_EVIDENCE_CHANGED")
            integrity = signed(SourceRowIntegrityReport, "report_sha256", status="PASS", expected=expected,
                input_fingerprints=before, observations=tuple(findings), retained_row_count=sum(f.retained_rows_checked for f in findings))
            semantics = build_semantics(source=published.source_contract, integrity=integrity, rows_by_request=rows)
            require(before == input_fingerprints(files=files, roots=roots), "INPUT_EVIDENCE_CHANGED")
        except (IdentityError, ValueError, TypeError, OSError, KeyError, RecursionError, ArithmeticError) as exc:
            reason = exc.code if isinstance(exc, IdentityError) else "SEMANTICS_EVIDENCE_VALIDATION_FAILED"
            integrity = signed(SourceRowIntegrityReport, "report_sha256", status="BLOCKED", expected=expected, blockers=(reason,))
            semantics = signed(CashflowSemanticsReport, "report_sha256", status="BLOCKED", blockers=(reason,),
                source_row_integrity_sha256=integrity.report_sha256, structural_integrity_status="BLOCKED", source_cashflow_semantics_status="BLOCKED")
        return SemanticsPackage(status=semantics.status, integrity=integrity, semantics=semantics)
