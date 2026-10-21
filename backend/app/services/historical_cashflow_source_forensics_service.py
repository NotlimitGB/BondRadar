"""Offline original-evidence reconciliation and source-only report publication."""
from collections import Counter
import hashlib
from pathlib import Path
import tempfile
from app.schemas.historical_cashflow_feasibility import PilotPlan, Authorization, Evaluation
from app.schemas.historical_cashflow_source_forensics import (
    SECIDS, TABLES, OriginalHashes, OriginalOutcome, OriginalObservation, Observation,
    ForensicsReport, DiagnosticPlan, ForensicPackage, DiagnosticLimits, DiagnosticRequest, DiagnosticResult,
)
from app.services.historical_cashflow_probe_service import replay as original_replay, destination_sha
from app.services.historical_cashflow_feasibility_service import HistoricalCashflowFeasibilityService, exact_strings
from app.services.historical_cashflow_diagnostic_client import inspect_original_body
from app.services.historical_identity_probe_service import read_model, read_bytes, safe_path, encode, atomic, writer_lock, assert_capacity, independent_root
from app.services.historical_identity_recovery_artifacts import file_sha, disjoint
from app.services.historical_identity_feasibility_service import require, IdentityError
from app.services.historical_evidence_normalization import checked, signed, sha, hash_syntax
from app.services.historical_audit_canonical_json import chunks


def code(exc):
    return exc.code if isinstance(exc,IdentityError) else "EVIDENCE_VALIDATION_FAILED"


def work_for(secids,tables,reused):
    reused_keys={(o.request.secid,o.request.table) for o in reused}
    return tuple(DiagnosticRequest(secid=s,table=t,profile=p) for p in ("BASELINE","TABLE_ONLY")
        for s in secids for t in tables if p!="BASELINE" or (s,t) not in reused_keys)


def validate_plan(plan):
    require(type(plan) is DiagnosticPlan,"DIAGNOSTIC_PLAN_REQUIRED")
    checked(plan,"plan_sha256")
    require(plan.status=="PLANNED" and not plan.blockers,"DIAGNOSTIC_PLAN_BLOCKED")
    require(plan.protected_source_root_sha256s and plan.protected_source_root_sha256s==tuple(sorted(set(plan.protected_source_root_sha256s))),"SOURCE_ROOT_GUARDS_REQUIRED")
    for h in plan.protected_source_root_sha256s:hash_syntax(h)
    require(plan.secids==exact_strings(plan.secids) and set(plan.secids)<=set(SECIDS),"SECID_SCOPE_INVALID")
    require(plan.tables and plan.tables==tuple(t for t in TABLES if t in plan.tables),"TABLE_SCOPE_INVALID")
    require(tuple(s for s,_ in plan.bindings)==plan.secids and all(v for _,v in plan.bindings),"BINDING_INVALID")
    for h in (plan.forensics_sha256,*(getattr(plan.original_hashes,k) for k in type(plan.original_hashes).model_fields if k.endswith("sha256"))):hash_syntax(h)
    require(len({(o.request.secid,o.request.table) for o in plan.reused})==len(plan.reused),"REUSED_BASELINE_DUPLICATE")
    for o in plan.reused:
        require(o.request.secid in plan.secids and o.request.table in plan.tables and o.request.profile=="BASELINE"
            and o.inspection.disposition=="ACCEPTED" and o.accepted_current_isin_context==dict(plan.bindings)[o.request.secid]
            and o.original_receipt_sha256 is not None,"REUSED_BASELINE_INVALID")
        hash_syntax(o.original_receipt_sha256);hash_syntax(o.inspection.body_sha256)
    require(all(any(o.request.secid==s and o.request.table=="offers" for o in plan.reused) for s in plan.secids) if "offers" in plan.tables else True,
        "OFFERS_BASELINE_REQUIRED")
    require(plan.work==work_for(plan.secids,plan.tables,plan.reused) and len(plan.work)<=12,"WORK_SCOPE_INVALID")
    return plan


def ensure_separate_source_roots(*, plan, output_root):
    root=safe_path(output_root)
    require(not set(plan.protected_source_root_sha256s)&{destination_sha(p) for p in (root,*root.parents)},"DESTINATION_INSIDE_VERIFIED_SOURCE_ROOT")


def _snapshot(paths,root):
    result=[];total=0
    for index,p in enumerate(sorted(root.iterdir(),key=lambda p:p.name),1):
        p=safe_path(p)
        require(index<=256 and p.is_file(),"ORIGINAL_EVIDENCE_ENVELOPE_INVALID")
        total+=p.stat().st_size;require(total<=128*1024**2,"ORIGINAL_EVIDENCE_LIMIT")
        result.append(("evidence/"+p.name,file_sha(p,8*1024**2)))
    for name,p in paths:
        result.append(("input/"+name,file_sha(p,32*1024**2)))
    return tuple(sorted(result))


class HistoricalCashflowSourceForensicsService:
    @staticmethod
    def forensic_plan(*, b2_plan, b2_authorization, b2_evidence_root, b2_evaluation, expected,
        secids, tables=TABLES, limits=None):
        require(type(expected) is OriginalHashes,"FOUR_EXPECTED_HASHES_REQUIRED")
        OriginalHashes.model_validate(expected)
        try:
            for key in type(expected).model_fields:
                if key.endswith("sha256"):hash_syntax(getattr(expected,key))
            secids=exact_strings(secids);tables_supplied=exact_strings(tables)
            require(set(secids)<=set(SECIDS) and set(tables_supplied)<=set(TABLES),"DIAGNOSTIC_SCOPE_NOT_ALLOWED")
            tables=tuple(t for t in TABLES if t in tables_supplied)
            limits=DiagnosticLimits() if limits is None else DiagnosticLimits.model_validate(limits)
            paths=tuple((name,safe_path(p)) for name,p in (("plan",b2_plan),("authorization",b2_authorization),("evaluation",b2_evaluation)))
            root=safe_path(b2_evidence_root);before=_snapshot(paths,root)
            original_plan=read_model(b2_plan,PilotPlan)
            original_auth=read_model(b2_authorization,Authorization)
            original_eval=read_model(b2_evaluation,Evaluation,32*1024**2)
            checked(original_eval,"report_sha256")
            require(original_plan.plan_sha256==expected.plan_sha256 and original_auth.authorization_sha256==expected.authorization_sha256
                and original_eval.report_sha256==expected.evaluation_sha256,"EXPECTED_ORIGINAL_SHA_MISMATCH")
            result,receipts=original_replay(original_plan,original_auth,root)
            require(result.probe_sha256==expected.probe_sha256,"EXPECTED_PROBE_SHA_MISMATCH")
            reevaluated=HistoricalCashflowFeasibilityService.evaluate(plan=original_plan,authorization=original_auth,evidence_root=root)
            require(reevaluated.status!="BLOCKED" and reevaluated==original_eval,"ORIGINAL_EVALUATION_RECONCILIATION_FAILED")
            actual_secids={r.request.secid for r in receipts}
            require(set(secids)<=actual_secids and set(secids)<=set(original_auth.secids) and set(tables)<=set(original_auth.tables),"ORIGINAL_PROBE_SCOPE_MISMATCH")
            bindings={c.secid:c.accepted_current_isin for c in original_plan.cohort}
            observations=[];reused=[];outcomes=[]
            for r in receipts:
                if r.status!="ACCEPTED":continue
                body=read_bytes(root/(r.body_sha256+".body"),original_auth.limits.response_bytes)
                inspection=inspect_original_body(body=body,request=r.request,expected_isin=bindings[r.request.secid])
                require(inspection.disposition=="ACCEPTED","ORIGINAL_ACCEPTED_SCHEMA_DRIFT")
                observations.append(OriginalObservation(request=r.request,accepted_current_isin_context=bindings[r.request.secid],
                    observed_at=r.observed_at,original_receipt_sha256=r.receipt_sha256,inspection=inspection))
                if r.request.secid in secids and r.request.table in tables and r.request.offset==0:
                    reused.append(Observation(request=DiagnosticRequest(secid=r.request.secid,table=r.request.table,profile="BASELINE"),
                        accepted_current_isin_context=bindings[r.request.secid],observed_at=r.observed_at,
                        original_receipt_sha256=r.receipt_sha256,inspection=inspection))
            for table in ("coupons","amortizations","redemptions","offers"):
                rs=[r for r in receipts if r.request.table==table]
                ps=[p for p in original_eval.partitions if p.table==table]
                outcomes.append(OriginalOutcome(table=table,attempts=len(rs),accepted_pages=sum(r.status=="ACCEPTED" for r in rs),
                    rejected_attempts=sum(r.status!="ACCEPTED" for r in rs),raw_rows=sum(p.raw_rows for p in ps),invalid_rows=sum(p.invalid_rows for p in ps),
                    receipt_categories=tuple(sorted(Counter(r.category for r in rs).items())),
                    missing_rejected_bodies=sum(r.status!="ACCEPTED" and r.body_sha256 is None for r in rs)))
            require(sum(o.attempts for o in outcomes)==result.attempts and before==_snapshot(paths,root),"ORIGINAL_EVIDENCE_CHANGED")
            report=signed(ForensicsReport,"report_sha256",status="COMPLETE",expected=expected,source_fingerprints=before,
                original_evaluation_status=original_eval.status,outcomes=tuple(outcomes),observations=tuple(observations),
                limitations=("REJECTED_DUPLICATE_LOCATION_NOT_RECOVERABLE_WITHOUT_BODY","UNIT_AUTHORITY_UNPROVEN",
                    "CRITICAL_CASHFLOW_FAMILIES_REQUIRE_INDEPENDENT_REVIEW","OPERATOR_NARRATIVE_NOT_A_RUNTIME_AUTHORITY"))
            reused=tuple(sorted(reused,key=lambda o:(o.request.secid,TABLES.index(o.request.table))))
            plan=signed(DiagnosticPlan,"plan_sha256",status="PLANNED",original_hashes=expected,forensics_sha256=report.report_sha256,
                protected_source_root_sha256s=tuple(sorted({destination_sha(p) for p in (root,*(p.parent for _,p in paths))})),
                secids=secids,tables=tables,bindings=tuple((s,bindings[s]) for s in secids),reused=reused,
                work=work_for(secids,tables,reused),limits=limits)
            validate_plan(plan)
            return ForensicPackage(status="COMPLETE",report=report,plan=plan)
        except (IdentityError,ValueError,OSError,TypeError):
            # Do not publish paths, raw validation inputs or SQL/HTTP exception messages.
            report=signed(ForensicsReport,"report_sha256",status="BLOCKED",expected=expected,blockers=("ORIGINAL_EVIDENCE_VALIDATION_FAILED",))
            plan=signed(DiagnosticPlan,"plan_sha256",status="BLOCKED",original_hashes=expected,forensics_sha256=report.report_sha256,
                blockers=("ORIGINAL_EVIDENCE_VALIDATION_FAILED",))
            return ForensicPackage(status="BLOCKED",report=report,plan=plan)

    @staticmethod
    def diagnostic_evaluate(*, plan, authorization, evidence_root):
        from app.services.historical_cashflow_diagnostic_probe_service import replay
        try:
            return replay(plan=plan,authorization=authorization,evidence_root=evidence_root)[0]
        except (IdentityError,ValueError,OSError,TypeError) as exc:
            return signed(DiagnosticResult,"report_sha256",status="BLOCKED",blockers=(code(exc),),plan_sha256=plan.plan_sha256,
                authorization_sha256=authorization.authorization_sha256,original_hashes=plan.original_hashes,
                protected_source_root_sha256s=plan.protected_source_root_sha256s)


def publish(*, package, output_dir, protected_roots=()):
    """New artifacts only. Completion JSON is installed last; identical reruns are safe."""
    root=safe_path(output_dir);require(root.is_dir(),"OUTPUT_DIRECTORY_REQUIRED");independent_root(root)
    require(all(disjoint(root,safe_path(p)) for p in protected_roots),"OUTPUT_SOURCE_OVERLAP")
    require(not any((p/n).exists() for p in (root,*root.parents) for n in
        ("task308b1-identity-audit.json","task308a-scope.json","b2-state.json","r1-state.json")),"OUTPUT_INSIDE_SOURCE_ROOT")
    if type(package) is ForensicPackage:
        ForensicPackage.model_validate(package);checked(package.report,"report_sha256");checked(package.plan,"plan_sha256")
        require(package.plan.forensics_sha256==package.report.report_sha256,"FORENSIC_PLAN_LINK_INVALID")
        require(package.status==package.report.status and package.plan.original_hashes==package.report.expected and
            package.plan.status==("PLANNED" if package.status=="COMPLETE" else "BLOCKED"),"FORENSIC_PACKAGE_ENVELOPE_INVALID")
        values=(("task308b2r1-diagnostic-plan.json",package.plan),)
        report=package.report;prefix="task308b2r1-source-forensics"
    else:
        require(type(package) is DiagnosticResult,"DIAGNOSTIC_RESULT_REQUIRED")
        checked(package,"report_sha256");report=package;values=();prefix="task308b2r1-diagnostic"
    ensure_separate_source_roots(plan=package.plan if type(package) is ForensicPackage else package,output_root=root)
    lines=["# Task308B2R1","",f"Status: {report.status}","",f"Report SHA: {report.report_sha256}",""]
    if type(package) is ForensicPackage:
        lines.extend(f"- {o.table}: {o.attempts} attempts, {o.accepted_pages} accepted, {o.rejected_attempts} rejected; {o.missing_rejected_bodies} rejected bodies unavailable."
            for o in report.outcomes)
        lines.extend(("","Rejected duplicate locations cannot be recovered without their original bytes."))
    else:
        lines.extend(f"- {o.request.secid}/{o.request.table}/{o.request.profile}: {o.inspection.disposition}, {o.inspection.completeness}, {o.inspection.unit_authority}."
            for o in report.observations)
        lines.extend(("","Cross-time profile differences are not causal proof."))
    lines.extend(("","Source-contract diagnostics only. Actual payments, PIT, historical backtest and total-return execution remain unproven.",""))
    summary="\n".join(lines).encode("ascii")
    report_name=prefix+".json" if type(package) is ForensicPackage else prefix+"-report.json"
    entries=tuple((n,v) for n,v in values)+((prefix+"-summary.md",summary),(report_name,report))
    with writer_lock(root),tempfile.TemporaryDirectory(prefix=".task308b2r1-",dir=root) as directory:
        staging=safe_path(directory);total=0
        for name,value in entries:
            with (staging/name).open("xb") as stream:
                parts=(value,) if type(value) is bytes else (p.encode("ascii") for p in chunks(value))
                for part in parts:
                    total+=len(part);require(total<=32*1024**2,"REPORT_BYTE_LIMIT")
                    assert_capacity(root,DiagnosticLimits(),extra=2*total)
                    stream.write(part)
                stream.flush()
                import os
                os.fsync(stream.fileno())
        for name,_ in entries:
            if (root/name).exists():require(file_sha(root/name)==file_sha(staging/name),"CONFLICTING_OUTPUT")
        created=[]
        try:
            for name,_ in entries:
                target=root/name;existed=target.exists()
                atomic(target,read_bytes(staging/name,32*1024**2),immutable=True)
                if not existed:created.append(target)
        except BaseException:
            for target in reversed(created):target.unlink(missing_ok=True)
            raise
    return report.report_sha256
