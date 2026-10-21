"""New authorization and immutable diagnostic journal; never modifies B2 evidence."""
from datetime import datetime, timezone, timedelta
import hashlib
import os
import re
import shutil
import time
from app.schemas.historical_cashflow_source_forensics import (
    TABLES, DiagnosticLimits, DiagnosticAuthorization, DiagnosticIntent, DiagnosticReceipt,
    DiagnosticState, DiagnosticResult, Observation,
)
from app.services.historical_cashflow_source_forensics_service import validate_plan, code, ensure_separate_source_roots
from app.services.historical_cashflow_feasibility_service import exact_strings
from app.services.historical_cashflow_diagnostic_client import HistoricalCashflowDiagnosticClient, inspect_body, route
from app.services.historical_cashflow_probe_service import destination_sha, utc
from app.services.historical_identity_probe_client import SourceFailure
from app.services.historical_identity_probe_service import safe_path, read_model, read_bytes, encode, atomic, writer_lock, independent_root
from app.services.historical_identity_feasibility_service import require, IdentityError
from app.services.historical_evidence_normalization import checked, signed, sha, hash_syntax


def private_root(path,limits):
    root=safe_path(path);require(root.is_dir(),"PRIVATE_DIAGNOSTIC_DESTINATION_REQUIRED");independent_root(root)
    require(os.name=="nt" or root.stat().st_mode & 0o077==0,"PRIVATE_DIRECTORY_PERMISSIONS_REQUIRED")
    require(not any((p/n).exists() for p in (root,*root.parents) for n in
        ("b2-state.json","task308a-scope.json","task308b1-identity-audit.json","manifest.json")),"DESTINATION_INSIDE_ORIGINAL_SOURCE")
    total=0;count=0
    for p in root.iterdir():
        p=safe_path(p);count+=1
        require(count<=64,"DIAGNOSTIC_FILE_COUNT_LIMIT")
        if p.name in ("accepted","quarantine"):
            require(p.is_dir(),"DIAGNOSTIC_DIRECTORY_INVALID")
            for child in p.iterdir():
                child=safe_path(child);count+=1
                require(count<=64 and child.is_file() and re.fullmatch(r"[a-f0-9]{64}\.body",child.name),"DIAGNOSTIC_BODY_PATH_INVALID")
                require(child.stat().st_size<=limits.response_bytes,"QUARANTINE_BODY_LIMIT")
                total+=child.stat().st_size
        else:
            require(p.is_file() and (p.name in (".task308a.lock","r1-authorization.json","r1-state.json","r1-probe-result.json")
                or re.fullmatch(r"r1-(?:intent|receipt)-\d{3}\.json",p.name)),"DIAGNOSTIC_DESTINATION_NOT_PRIVATE")
            require(p.stat().st_size<=(32*1024**2 if p.name=="r1-probe-result.json" else 2*1024**2),"JOURNAL_FILE_LIMIT")
            total+=p.stat().st_size
    require(total<=limits.evidence_bytes,"DIAGNOSTIC_EVIDENCE_LIMIT")
    return root,total


def capacity(root,limits,extra=0):
    _,size=private_root(root,limits)
    require(size+extra<=limits.evidence_bytes,"DIAGNOSTIC_EVIDENCE_LIMIT")
    require(shutil.disk_usage(root).free-extra>=limits.reserve_bytes,"FREE_SPACE_RESERVE_NOT_MET")


def validate_authorization(plan,authorization,root):
    validate_plan(plan)
    ensure_separate_source_roots(plan=plan,output_root=root)
    require(type(authorization) is DiagnosticAuthorization,"DIAGNOSTIC_AUTHORIZATION_REQUIRED")
    checked(authorization,"authorization_sha256")
    require(authorization.explicit_confirmation is True and authorization.plan_sha256==plan.plan_sha256,"AUTHORIZATION_PLAN_MISMATCH")
    require(authorization.secids==exact_strings(authorization.secids) and set(authorization.secids)<=set(plan.secids),"AUTHORIZED_SECIDS_INVALID")
    require(authorization.tables and authorization.tables==tuple(t for t in TABLES if t in authorization.tables)
        and set(authorization.tables)<=set(plan.tables),"AUTHORIZED_TABLES_INVALID")
    require(authorization.profiles and authorization.profiles==tuple(p for p in ("BASELINE","TABLE_ONLY") if p in authorization.profiles),"AUTHORIZED_PROFILES_INVALID")
    work=tuple(r for r in plan.work if r.secid in authorization.secids and r.table in authorization.tables and r.profile in authorization.profiles)
    require(work and work==authorization.work,"AUTHORIZED_WORK_INVALID")
    for key in ("max_requests","max_pages","response_bytes","total_response_bytes","max_seconds","evidence_bytes"):
        require(getattr(authorization.limits,key)<=getattr(plan.limits,key),"AUTHORIZATION_LIMIT_INCREASE")
    require(authorization.destination_sha256==destination_sha(root),"DIAGNOSTIC_DESTINATION_MISMATCH")


def progress(receipts,request):
    rs=[r for r in receipts if r.request==request]
    if any(r.disposition in ("ACCEPTED","QUARANTINE","BUDGET_STOP") for r in rs):return None
    if rs and (not rs[-1].retryable or len(rs)>=3):return None
    return len(rs),rs[-1].retry_after if rs else 0


def replay(*, plan, authorization, evidence_root, verify_result=True):
    root,_=private_root(evidence_root,authorization.limits)
    validate_authorization(plan,authorization,root)
    require(read_model(root/"r1-authorization.json",DiagnosticAuthorization)==authorization,"SAVED_AUTHORIZATION_MISMATCH")
    state=read_model(root/"r1-state.json",DiagnosticState);checked(state,"state_sha256");utc(state.deadline_at)
    require(state.authorization_sha256==authorization.authorization_sha256,"STATE_AUTHORIZATION_MISMATCH")
    state_bytes=encode(state)
    require(state.pending_sequence is None,"UNKNOWN_DIAGNOSTIC_INTENT_OUTCOME")
    require(len(state.receipt_sha256s)<=authorization.limits.max_requests,"ATTEMPT_LIMIT")
    receipts=[];observations=[o for o in plan.reused if o.request.secid in authorization.secids and o.request.table in authorization.tables]
    bodies=set();received=0;retained=0;previous_start=None
    for n,digest in enumerate(state.receipt_sha256s,1):
        intent=read_model(root/f"r1-intent-{n:03}.json",DiagnosticIntent);checked(intent,"intent_sha256")
        receipt=read_model(root/f"r1-receipt-{n:03}.json",DiagnosticReceipt);checked(receipt,"receipt_sha256")
        previous=state.receipt_sha256s[n-2] if n>1 else None
        require(intent.sequence==receipt.sequence==n and intent.authorization_sha256==authorization.authorization_sha256
            and intent.intent_sha256==receipt.intent_sha256 and receipt.receipt_sha256==digest
            and intent.previous_receipt_sha256==receipt.previous_receipt_sha256==previous and intent.request==receipt.request,"DIAGNOSTIC_CHAIN_INVALID")
        route(intent.request)
        unfinished=next((r for r in authorization.work if progress(receipts,r) is not None),None)
        require(intent.request==unfinished,"DIAGNOSTIC_REQUEST_ORDER_INVALID")
        started,observed=utc(intent.started_at),utc(receipt.observed_at)
        require(started<=observed and started<=state.deadline_at and (not receipts or started>=receipts[-1].observed_at),"DIAGNOSTIC_TIME_INVALID")
        require(previous_start is None or (started-previous_start).total_seconds()>=.5,"DIAGNOSTIC_RATE_PROOF_INVALID")
        previous_start=started
        require(0<=receipt.received_bytes<=authorization.limits.response_bytes,"RESPONSE_BYTE_LIMIT")
        received+=receipt.received_bytes
        if receipt.disposition in ("ACCEPTED","QUARANTINE"):
            require(receipt.body_sha256 is not None and not receipt.retryable,"RETAINED_RECEIPT_INVALID")
            hash_syntax(receipt.body_sha256)
            name=("accepted" if receipt.disposition=="ACCEPTED" else "quarantine")+"/"+receipt.body_sha256+".body"
            body=read_bytes(root/name,authorization.limits.response_bytes)
            require(len(body)==receipt.received_bytes and hashlib.sha256(body).hexdigest()==receipt.body_sha256,"DIAGNOSTIC_BODY_SHA_MISMATCH")
            inspection=inspect_body(body=body,request=receipt.request,expected_isin=dict(plan.bindings)[receipt.request.secid])
            require(inspection.disposition==receipt.disposition and sha(inspection)==receipt.inspection_sha256,"DIAGNOSTIC_INSPECTION_MISMATCH")
            bodies.add(name);retained+=1
            observations.append(Observation(request=receipt.request,accepted_current_isin_context=dict(plan.bindings)[receipt.request.secid],
                observed_at=observed,original_receipt_sha256=None,inspection=inspection))
        else:require(receipt.body_sha256 is None and receipt.inspection_sha256 is None,"FAILED_RECEIPT_BODY_INVALID")
        receipts.append(receipt)
    require(received<=authorization.limits.total_response_bytes and retained<=authorization.limits.max_pages,"DIAGNOSTIC_BUDGET_EXCEEDED")
    require({p.name for p in root.glob("r1-intent-*.json")}=={f"r1-intent-{i:03}.json" for i in range(1,len(receipts)+1)}
        and {p.name for p in root.glob("r1-receipt-*.json")}=={f"r1-receipt-{i:03}.json" for i in range(1,len(receipts)+1)},"UNRECONCILED_DIAGNOSTIC_JOURNAL")
    actual={d+"/"+p.name for d in ("accepted","quarantine") if (root/d).exists() for p in (root/d).iterdir()}
    require(actual==bodies,"UNRECONCILED_DIAGNOSTIC_BODIES")
    require(read_bytes(root/"r1-state.json",2*1024**2)==state_bytes,"DIAGNOSTIC_STATE_CHANGED")
    complete=all(any(r.request==request and r.disposition in ("ACCEPTED","QUARANTINE") for r in receipts) for request in authorization.work)
    findings={"ECONOMIC_UNIT_AUTHORITY_UNPROVEN","CROSS_TIME_COMPARISON_NOT_CAUSAL_PROOF"}
    if not complete:findings.add("PARTIAL_SOURCE_COVERAGE")
    if (not complete and any(t in ("coupons","redemptions") for t in authorization.tables)) or any(o.inspection.completeness!="COMPLETE" or o.inspection.disposition=="QUARANTINE" for o in observations if o.request.table in ("coupons","redemptions")):
        findings.add("CRITICAL_CASHFLOW_FAMILIES_UNRESOLVED")
    result=signed(DiagnosticResult,"report_sha256",status="COMPLETE" if complete else "PARTIAL",plan_sha256=plan.plan_sha256,
        authorization_sha256=authorization.authorization_sha256,original_hashes=plan.original_hashes,
        protected_source_root_sha256s=plan.protected_source_root_sha256s,
        blockers=("GLOBAL_DIAGNOSTIC_BUDGET_STOP",) if state.budget_stopped else (),receipt_sha256s=state.receipt_sha256s,
        observations=tuple(observations),attempts=len(receipts),received_bytes=received,
        accepted_pages=sum(r.disposition=="ACCEPTED" for r in receipts),quarantine_pages=sum(r.disposition=="QUARANTINE" for r in receipts),critical_findings=tuple(sorted(findings)))
    published=root/"r1-probe-result.json"
    if verify_result and published.exists():
        old=read_model(published,DiagnosticResult,32*1024**2);checked(old,"report_sha256");require(old==result,"DIAGNOSTIC_RESULT_REPLAY_MISMATCH")
    return result,tuple(receipts)


class HistoricalCashflowDiagnosticProbeService:
    def __init__(self, *, client_factory=HistoricalCashflowDiagnosticClient, sleeper=time.sleep, monotonic=time.monotonic,
        clock=lambda:datetime.now(timezone.utc)):
        self.client_factory,self.sleep,self.monotonic,self.clock=client_factory,sleeper,monotonic,clock

    @staticmethod
    def authorize(*, plan, reviewed_plan_sha256, secids, tables, profiles, limits, evidence_root, confirm=False):
        validate_plan(plan)
        require(confirm is True and reviewed_plan_sha256==plan.plan_sha256,"EXPLICIT_DIAGNOSTIC_REVIEW_REQUIRED")
        require(type(limits) is DiagnosticLimits,"EXPLICIT_DIAGNOSTIC_LIMITS_REQUIRED")
        root,_=private_root(evidence_root,limits)
        secids=exact_strings(secids);tables=exact_strings(tables);profiles=exact_strings(profiles)
        require(set(tables)<=set(TABLES) and set(profiles)<={"BASELINE","TABLE_ONLY"},"DIAGNOSTIC_SCOPE_INVALID")
        work=tuple(r for r in plan.work if r.secid in secids and r.table in tables and r.profile in profiles)
        result=signed(DiagnosticAuthorization,"authorization_sha256",explicit_confirmation=True,plan_sha256=plan.plan_sha256,
            secids=secids,tables=tuple(t for t in TABLES if t in tables),profiles=tuple(p for p in ("BASELINE","TABLE_ONLY") if p in profiles),
            work=work,destination_sha256=destination_sha(root),limits=limits)
        validate_authorization(plan,result,root);capacity(root,limits,32768)
        return result

    def probe(self, *, plan, authorization, evidence_root, confirm_network=False):
        require(confirm_network is True,"EXPLICIT_DIAGNOSTIC_NETWORK_CONFIRMATION_REQUIRED")
        root,_=private_root(evidence_root,authorization.limits);validate_authorization(plan,authorization,root)
        client=None
        try:
            with writer_lock(root):
                capacity(root,authorization.limits,32768)
                atomic(root/"r1-authorization.json",encode(authorization),immutable=True)
                for name in ("accepted","quarantine"):(root/name).mkdir(mode=0o700,exist_ok=True)
                if not (root/"r1-state.json").exists():
                    require(not any(root.glob("r1-intent-*.json")) and not any(root.glob("r1-receipt-*.json")),"UNRECONCILED_DIAGNOSTIC_JOURNAL")
                    state=signed(DiagnosticState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                        deadline_at=utc(self.clock())+timedelta(seconds=authorization.limits.max_seconds))
                    atomic(root/"r1-state.json",encode(state))
                _,rs=replay(plan=plan,authorization=authorization,evidence_root=root,verify_result=False)
                receipts=list(rs);state=read_model(root/"r1-state.json",DiagnosticState);limits=authorization.limits
                began=self.monotonic();wall=utc(self.clock());deadline=state.deadline_at
                def remaining():return min((deadline-utc(self.clock())).total_seconds(),(deadline-wall).total_seconds()-(self.monotonic()-began))
                def check_time():require(remaining()>0,"DIAGNOSTIC_TIME_BUDGET")
                stopped=state.budget_stopped
                last_start=receipts[-1].observed_at if receipts else None;last_mono=None
                for request in authorization.work:
                    while not stopped:
                        pending=progress(receipts,request)
                        if pending is None:break
                        failures,retry_after=pending
                        if len(receipts)>=limits.max_requests or sum(r.body_sha256 is not None for r in receipts)>=limits.max_pages or sum(r.received_bytes for r in receipts)>=limits.total_response_bytes or remaining()<=0:
                            stopped=True;break
                        delay=max((.25,.5)[failures-1] if failures else 0,retry_after,
                            max(0,.5-(utc(self.clock())-last_start).total_seconds()) if last_start else 0,
                            max(0,.5-(self.monotonic()-last_mono)) if last_mono is not None else 0)
                        if delay>=remaining():stopped=True;break
                        if delay:self.sleep(delay)
                        check_time()
                        try:capacity(root,limits,limits.response_bytes+65536)
                        except IdentityError:
                            stopped=True;break
                        n=len(receipts)+1
                        intent=signed(DiagnosticIntent,"intent_sha256",sequence=n,authorization_sha256=authorization.authorization_sha256,request=request,
                            previous_receipt_sha256=receipts[-1].receipt_sha256 if receipts else None,started_at=utc(self.clock()))
                        atomic(root/f"r1-intent-{n:03}.json",encode(intent),immutable=True)
                        atomic(root/"r1-state.json",encode(signed(DiagnosticState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                            receipt_sha256s=tuple(r.receipt_sha256 for r in receipts),pending_sequence=n,deadline_at=deadline)))
                        body_sha=None;inspection_sha=None;retryable=False;retry_delay=0;received=0
                        last_start=intent.started_at
                        try:
                            if client is None:client=self.client_factory()
                            last_mono=self.monotonic()
                            body=client.fetch(request,response_limit=limits.response_bytes,bytes_remaining=limits.total_response_bytes-sum(r.received_bytes for r in receipts),seconds_remaining=remaining(),check_time=check_time)
                            received=len(body);inspection=inspect_body(body=body,request=request,expected_isin=dict(plan.bindings)[request.secid]);check_time()
                            body_sha=inspection.body_sha256;inspection_sha=sha(inspection);disposition=inspection.disposition
                            category="DUPLICATE_SOURCE_KEY" if inspection.duplicates else "DIAGNOSTIC_RESPONSE"
                            target=root/("accepted" if disposition=="ACCEPTED" else "quarantine")/(body_sha+".body")
                            atomic(target,body,immutable=True)
                        except SourceFailure as exc:
                            category,retryable,retry_delay,received=exc.category,exc.retryable,exc.retry_after,exc.received_bytes
                            disposition="BUDGET_STOP" if "BUDGET" in category else "SOURCE_ERROR"
                        except IdentityError as exc:
                            category=exc.code;disposition="BUDGET_STOP" if "BUDGET" in category else "SOURCE_ERROR"
                        receipt=signed(DiagnosticReceipt,"receipt_sha256",sequence=n,intent_sha256=intent.intent_sha256,
                            previous_receipt_sha256=intent.previous_receipt_sha256,request=request,observed_at=utc(self.clock()),disposition=disposition,category=category,
                            retryable=retryable,retry_after=retry_delay,received_bytes=received,body_sha256=body_sha,inspection_sha256=inspection_sha)
                        atomic(root/f"r1-receipt-{n:03}.json",encode(receipt),immutable=True);receipts.append(receipt)
                        if disposition=="BUDGET_STOP":stopped=True
                        atomic(root/"r1-state.json",encode(signed(DiagnosticState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                            receipt_sha256s=tuple(r.receipt_sha256 for r in receipts),deadline_at=deadline,budget_stopped=stopped)))
                if stopped:
                    atomic(root/"r1-state.json",encode(signed(DiagnosticState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                        receipt_sha256s=tuple(r.receipt_sha256 for r in receipts),deadline_at=deadline,budget_stopped=True)))
                result,_=replay(plan=plan,authorization=authorization,evidence_root=root,verify_result=False)
                encoded=encode(result)
                target=root/"r1-probe-result.json"
                if not target.exists() or read_bytes(target,32*1024**2)!=encoded:
                    capacity(root,limits,len(encoded))
                    atomic(target,encoded)
                return result
        except (IdentityError,ValueError,OSError,TypeError) as exc:
            return signed(DiagnosticResult,"report_sha256",status="BLOCKED",blockers=(code(exc),),plan_sha256=plan.plan_sha256,
                authorization_sha256=authorization.authorization_sha256,original_hashes=plan.original_hashes,
                protected_source_root_sha256s=plan.protected_source_root_sha256s)
        finally:
            if client is not None:client.close()
