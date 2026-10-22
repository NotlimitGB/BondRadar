"""Verify old journals unchanged, then qualify retained bytes offline."""
from collections import Counter
from app.schemas.historical_cashflow_feasibility import PilotPlan, Request
from app.schemas.historical_cashflow_source_forensics import ForensicsReport, DiagnosticPlan, DiagnosticAuthorization, DiagnosticResult
from app.schemas.historical_cashflow_qualification import (
    ExpectedHashes, QualifiedObservation, ProfileComparison, SourceContractReport,
    QualificationReport, QualificationPackage, CoverageFinding,
)
from app.services.historical_cashflow_source_forensics_service import HistoricalCashflowSourceForensicsService
from app.services.historical_cashflow_diagnostic_probe_service import replay
from app.services.historical_cashflow_candidate_decoder import decode_candidate, SECIDS, TABLES
from app.services.historical_identity_probe_service import safe_path, read_model, read_bytes
from app.services.historical_identity_recovery_artifacts import file_sha
from app.services.historical_identity_feasibility_service import require, IdentityError
from app.services.historical_evidence_normalization import hash_syntax, checked, signed


def input_fingerprints(*, files, roots):
    """Bounded byte-level snapshot; relative logical labels never expose paths."""
    result = []; total = 0
    for label, path in sorted(files.items()):
        path = safe_path(path)
        require(path.is_file(), "INPUT_FILE_MISSING")
        total += path.stat().st_size
        result.append((label, file_sha(path, 32 * 1024**2)))
    count = 0
    for label, root in sorted(roots.items()):
        root = safe_path(root)
        require(root.is_dir(), "INPUT_ROOT_MISSING")
        def walk(folder):
            nonlocal count, total
            for path in sorted(folder.iterdir(), key=lambda p: p.name):
                path = safe_path(path); count += 1
                require(count <= 512, "INPUT_FILE_COUNT_LIMIT")
                if path.is_dir():
                    require(folder == root and path.name in ("accepted", "quarantine"), "INPUT_DIRECTORY_INVALID")
                    walk(path)
                else:
                    require(path.is_file(), "INPUT_FILE_INVALID")
                    total += path.stat().st_size
                    require(total <= 256 * 1024**2, "INPUT_BYTE_LIMIT")
                    result.append((label + "/" + path.relative_to(root).as_posix(), file_sha(path, 32 * 1024**2)))
        walk(root)
    require(total <= 256 * 1024**2, "INPUT_BYTE_LIMIT")
    return tuple(sorted(result))


def compare_profiles(observations):
    comparisons = []
    for secid in SECIDS:
        for table in ("coupons", "redemptions", "offers"):
            sides = [[o for o in observations if o.requested_secid == secid and o.table == table and o.profile == p]
                     for p in ("BASELINE", "TABLE_ONLY")]
            a, b = (s[0] if len(s) == 1 else None for s in sides)
            verdict = "COMPARISON_UNPROVEN"
            if a and b and all(o.view.structural_status == "STRUCTURALLY_RECOVERABLE" for o in (a, b)):
                same = a.view.columns == b.view.columns and Counter(a.view.source_row_sha256s) == Counter(b.view.source_row_sha256s)
                verdict = "CROSS_PROFILE_MATCH" if same else "CROSS_PROFILE_DIFFERENCE"
            comparisons.append(ProfileComparison(requested_secid=secid, table=table,
                baseline_request_id=a.request_id if a else None, table_only_request_id=b.request_id if b else None, verdict=verdict))
    return tuple(comparisons)


def qualify_report(source):
    coverage = []
    for secid in SECIDS:
        for table in TABLES:
            obs = [o for o in source.observations if o.requested_secid == secid and o.table == table]
            good = [o for o in obs if o.view.structural_status == "STRUCTURALLY_RECOVERABLE"]
            reasons = {"MONETARY_UNIT_AUTHORITY_UNPROVEN", "ACTUAL_PAYMENTS_UNPROVEN", "HISTORICAL_PUBLICATION_UNPROVEN"}
            different = any(c.requested_secid == secid and c.table == table and c.verdict == "CROSS_PROFILE_DIFFERENCE" for c in source.comparisons)
            complete = bool(good) and len(good) == len(obs) and any(o.view.completeness == "COMPLETE" for o in good) and not different
            if not complete: reasons.add(table.upper() + "_COMPLETENESS_UNPROVEN")
            if not good: reasons.add("SOURCE_STRUCTURAL_EVIDENCE_UNAVAILABLE")
            if different: reasons.add("CROSS_PROFILE_DIFFERENCE")
            if any(s == secid and t == table for s, t, _ in source.missing_observations): reasons.add("SOURCE_OBSERVATION_MISSING")
            for o in obs:
                reasons.update(o.view.diagnostics)
                for e in o.view.events: reasons.update(e.diagnostics)
                if o.view.completeness != "COMPLETE": reasons.add(o.view.completeness)
            claims = {claim for o in good for claim in o.view.unit_claims}
            coverage.append(CoverageFinding(requested_secid=secid, table=table, observation_count=len(obs),
                structural_observation_count=len(good), source_row_counts=tuple(len(o.view.source_rows_json) for o in obs),
                structural_status="AVAILABLE" if good else "UNAVAILABLE",
                cashflow_coverage_status="STRUCTURALLY_COMPLETE" if complete else "UNPROVEN",
                monetary_unit_status="CONFLICTING" if len(claims) > 1 else "PARTIAL" if claims else "UNPROVEN",
                blockers=tuple(sorted(reasons))))
    return signed(QualificationReport, "report_sha256", status=source.status, blockers=source.blockers,
                  source_contract_report_sha256=source.report_sha256, coverage=tuple(coverage) if source.status != "BLOCKED" else ())


class HistoricalCashflowQualificationService:
    @staticmethod
    def qualify_existing_evidence(*, b2_plan, b2_authorization, b2_evidence_root, b2_evaluation,
        r1_forensics, r1_plan, r1_authorization, r1_evidence_root, r1_report, expected):
        require(type(expected) is ExpectedHashes, "EIGHT_EXPECTED_HASHES_REQUIRED")
        ExpectedHashes.model_validate(expected)
        files = dict(b2_plan=b2_plan, b2_authorization=b2_authorization, b2_evaluation=b2_evaluation,
                     r1_forensics=r1_forensics, r1_plan=r1_plan, r1_authorization=r1_authorization, r1_report=r1_report)
        roots = dict(b2=b2_evidence_root, r1=r1_evidence_root)
        try:
            for key in type(expected).model_fields:
                if key.endswith("sha256"): hash_syntax(getattr(expected, key))
            for key in type(expected.b2).model_fields:
                if key.endswith("sha256"): hash_syntax(getattr(expected.b2, key))
            before = input_fingerprints(files=files, roots=roots)
            plan = read_model(r1_plan, DiagnosticPlan)
            forensic = read_model(r1_forensics, ForensicsReport, 32 * 1024**2)
            auth = read_model(r1_authorization, DiagnosticAuthorization)
            saved = read_model(r1_report, DiagnosticResult, 32 * 1024**2)
            for obj, field, digest in ((plan, "plan_sha256", expected.diagnostic_plan_sha256),
                (forensic, "report_sha256", expected.forensics_sha256),
                (auth, "authorization_sha256", expected.diagnostic_authorization_sha256),
                (saved, "report_sha256", expected.diagnostic_report_sha256)):
                checked(obj, field); require(getattr(obj, field) == digest, "EXPECTED_R1_SHA_MISMATCH")
            require(set(plan.secids) <= set(SECIDS) and plan.original_hashes == expected.b2, "R1_LINEAGE_MISMATCH")
            reconstructed = HistoricalCashflowSourceForensicsService.forensic_plan(
                b2_plan=b2_plan, b2_authorization=b2_authorization, b2_evidence_root=b2_evidence_root,
                b2_evaluation=b2_evaluation, expected=expected.b2, secids=plan.secids, tables=plan.tables, limits=plan.limits)
            require(reconstructed.status == "COMPLETE" and reconstructed.report == forensic and reconstructed.plan == plan,
                    "FORENSIC_LINEAGE_REPLAY_MISMATCH")
            result, receipts = replay(plan=plan, authorization=auth, evidence_root=r1_evidence_root)
            require(result == saved and result.status != "BLOCKED", "R1_REPORT_REPLAY_MISMATCH")
            original = read_model(b2_plan, PilotPlan)
            candidates = {c.secid: c for c in original.cohort}
            observations = []
            def add(request, profile, receipt_sha, time, disposition, root, body_sha):
                body = read_bytes(safe_path(root) / (body_sha + ".body"), 8 * 1024**2)
                view = decode_candidate(body=body, request=request, candidate=candidates[request.secid])
                require(view.raw_body_sha256 == body_sha, "SOURCE_BODY_SHA_MISMATCH")
                observations.append(QualifiedObservation(requested_secid=request.secid, table=request.table,
                    profile=profile, request_id=receipt_sha, observed_at=time, original_disposition=disposition, view=view))
            for r in receipts:
                if r.disposition in ("ACCEPTED", "QUARANTINE"):
                    add(Request(secid=r.request.secid, table=r.request.table, offset=0), r.request.profile,
                        r.receipt_sha256, r.observed_at, r.disposition,
                        safe_path(r1_evidence_root) / ("accepted" if r.disposition == "ACCEPTED" else "quarantine"), r.body_sha256)
            for o in plan.reused:
                if o.request.secid in auth.secids and o.request.table in auth.tables:
                    add(Request(secid=o.request.secid, table=o.request.table, offset=0), "BASELINE",
                        o.original_receipt_sha256, o.observed_at, "ACCEPTED", b2_evidence_root, o.inspection.body_sha256)
            for o in forensic.observations:
                if o.request.secid in SECIDS and o.request.table == "amortizations":
                    add(o.request, "B2_ACCEPTED", o.original_receipt_sha256, o.observed_at, "ACCEPTED", b2_evidence_root, o.inspection.body_sha256)
            observations.sort(key=lambda o: (o.requested_secid, o.table, o.profile, o.observed_at, o.request_id))
            missing = tuple((s, t, p) for s in SECIDS for t in TABLES
                for p in (("B2_ACCEPTED",) if t == "amortizations" else ("BASELINE", "TABLE_ONLY"))
                if not any(o.requested_secid == s and o.table == t and o.profile == p for o in observations))
            require(before == input_fingerprints(files=files, roots=roots), "INPUT_EVIDENCE_CHANGED")
            source = signed(SourceContractReport, "report_sha256", status="PARTIAL" if missing or result.status == "PARTIAL" else "COMPLETE",
                expected=expected, input_fingerprints=before, observations=tuple(observations),
                comparisons=compare_profiles(observations), missing_observations=missing)
        except (IdentityError, ValueError, TypeError, OSError, KeyError) as exc:
            source = signed(SourceContractReport, "report_sha256", status="BLOCKED", expected=expected,
                            blockers=(exc.code if isinstance(exc, IdentityError) else "EVIDENCE_VALIDATION_FAILED",))
        return QualificationPackage(status=source.status, source_contract=source, qualification=qualify_report(source))
