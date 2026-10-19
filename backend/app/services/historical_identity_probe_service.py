"""Task308A private probe evidence, explicit authorization, bounded resume/replay."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import time
from app.schemas.historical_identity_feasibility import ProbeAuthorization, ProbeLimits, AttemptReceipt, ProbeManifest
from app.services.historical_identity_feasibility_service import (
    IdentityError, require, validate_plan, SOURCE_ORDER, resolve_rows, evaluate_results,
)
from app.services.historical_identity_probe_client import HistoricalIdentityProbeClient, SourceFailure, request_for, parse_page
from app.services.historical_discovery_checkpoint import safe_path, parse_json
from app.services.historical_evidence_normalization import sha, checked, signed
from app.services.historical_audit_canonical_json import chunks


INPUT_LIMIT=32*1024**2
OUTPUT_LIMIT=8*1024**2


def read_bytes(path, limit):
    path=safe_path(path)
    with path.open("rb") as stream:body=stream.read(limit+1)
    require(len(body)<=limit,"INPUT_SIZE_LIMIT")
    return body


def read_model(path, model, limit=OUTPUT_LIMIT):
    body=read_bytes(path,limit);parse_json(body)
    return model.model_validate_json(body)


def encode(value):return b"".join(c.encode("ascii") for c in chunks(value))


def independent_root(root):
    require(not any((p/"scope.json").exists() for p in (root,*root.parents)),"OUTPUT_INSIDE_DISCOVERY_ROOT")


def atomic(path, body, *, immutable=False):
    path=safe_path(path)
    if immutable and path.exists():
        require(read_bytes(path,max(len(body),OUTPUT_LIMIT))==body,"IMMUTABLE_ARTIFACT_CONFLICT");return
    descriptor,name=tempfile.mkstemp(prefix=".task308a-",dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,"wb") as stream:
            stream.write(body);stream.flush();os.fsync(stream.fileno())
        if immutable and os.name=="nt":os.rename(temporary,path)
        elif immutable:os.link(temporary,path)
        else:os.replace(temporary,path)
        if os.name!="nt":
            directory=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
    finally:temporary.unlink(missing_ok=True)


@contextmanager
def writer_lock(root):
    path=safe_path(root/".task308a.lock")
    with path.open("a+b") as stream:
        stream.seek(0)
        if os.name=="nt":
            import msvcrt
            if path.stat().st_size==0:stream.write(b"0");stream.flush()
            stream.seek(0)
            try:msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise IdentityError("PROBE_WRITER_LOCKED") from None
        else:
            import fcntl
            try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError:raise IdentityError("PROBE_WRITER_LOCKED") from None
        try:yield
        finally:
            if os.name=="nt":stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def assert_capacity(root, limits, extra=0):
    require(shutil.disk_usage(root).free-extra>=limits.reserve_bytes,"FREE_SPACE_RESERVE_NOT_MET")
    size=sum(p.stat().st_size for p in root.iterdir() if p.is_file())
    require(size+extra<=limits.evidence_bytes,"PROBE_EVIDENCE_SIZE_LIMIT")


def verify_authorization(plan, authorization, expected_plan_sha256, destination=None):
    validate_plan(plan)
    require(expected_plan_sha256==plan.plan_sha256,"REVIEWED_PLAN_SHA_MISMATCH")
    require(type(authorization) is ProbeAuthorization,"PROBE_AUTHORIZATION_REQUIRED")
    checked(authorization,"authorization_sha256")
    a=authorization
    require(a.explicit_authorization is True and a.plan_sha256==plan.plan_sha256 and a.context==plan.context,"AUTHORIZATION_SCOPE_MISMATCH")
    require(a.secids and a.secids==tuple(sorted(set(a.secids))) and len(a.secids)<=a.max_secids<=12 and
        set(a.secids)<={s.secid for s in plan.samples},"AUTHORIZATION_SECIDS_INVALID")
    require(a.sources and a.sources==tuple(s for s in SOURCE_ORDER if s in a.sources),"AUTHORIZATION_SOURCES_INVALID")
    for s in a.secids:
        for source in a.sources:request_for(s,source)
    if destination is not None:
        require(sha(str(safe_path(destination)))==a.destination_sha256,"AUTHORIZATION_DESTINATION_MISMATCH")
    return a


def state_hash(value):return {**value,"state_sha256":sha(value)}


def load_state(root, authorization):
    path=root/"task308a-state.json"
    if not path.exists():return {"receipt_sha256s":[],"pending":None,"elapsed_seconds":0.0,"deadline_at":None}
    raw=parse_json(read_bytes(path,OUTPUT_LIMIT))
    require(type(raw) is dict and set(raw)=={"receipt_sha256s","pending","elapsed_seconds","deadline_at","state_sha256"} and
        sha({k:v for k,v in raw.items() if k!="state_sha256"})==raw["state_sha256"],"PROBE_STATE_INVALID")
    state={k:v for k,v in raw.items() if k!="state_sha256"}
    require(type(state["receipt_sha256s"]) is list and len(state["receipt_sha256s"])<=authorization.limits.max_requests and
        type(state["elapsed_seconds"]) in (int,float) and 0<=state["elapsed_seconds"]<=authorization.limits.max_seconds+60,"PROBE_STATE_INVALID")
    if state["deadline_at"] is not None:
        require(type(state["deadline_at"]) is str,"PROBE_STATE_INVALID")
        deadline=datetime.fromisoformat(state["deadline_at"])
        require(deadline.tzinfo is not None and deadline.utcoffset()==timedelta(0),"PROBE_STATE_INVALID")
    require(not state["receipt_sha256s"] or state["deadline_at"] is not None,"PROBE_STATE_INVALID")
    return state


def save_state(root,state):atomic(root/"task308a-state.json",encode(state_hash(state)))


def replay(plan, authorization, root):
    verify_authorization(plan,authorization,plan.plan_sha256)
    root=safe_path(root)
    scope=parse_json(read_bytes(root/"task308a-scope.json",OUTPUT_LIMIT))
    require(scope=={"authorization_sha256":authorization.authorization_sha256,"plan_sha256":plan.plan_sha256},"PROBE_EVIDENCE_SCOPE_MISMATCH")
    state=load_state(root,authorization)
    groups={(s,source):{"rows":[],"receipts":[],"complete":False,"offset":0,"failed":None,"failures":0,"seen":set(),"cursor":None} for s in authorization.secids for source in authorization.sources}
    receipts=[];previous=None;received=accepted=0
    for sequence,digest in enumerate(state["receipt_sha256s"],1):
        require(type(digest) is str and len(digest)==64 and all(c in "0123456789abcdef" for c in digest),"RECEIPT_SHA_INVALID")
        r=read_model(root/(digest+".receipt.json"),AttemptReceipt)
        checked(r,"receipt_sha256")
        if r.response_sha256 is not None:
            require(len(r.response_sha256)==64 and all(c in "0123456789abcdef" for c in r.response_sha256),"PROBE_RAW_HASH_INVALID")
        require(r.receipt_sha256==digest and r.sequence==sequence and r.previous_receipt_sha256==previous and
            r.authorization_sha256==authorization.authorization_sha256 and r.request_sha256==sha(r.request),"PROBE_RECEIPT_CHAIN_INVALID")
        require(r.observed_at.tzinfo is not None and r.observed_at.utcoffset()==timezone.utc.utcoffset(r.observed_at),"OBSERVATION_UTC_REQUIRED")
        request=r.request
        require(request==request_for(request.secid,request.source,request.offset) and (request.secid,request.source) in groups,"PROBE_REQUEST_SCOPE_INVALID")
        intent=parse_json(read_bytes(root/f"intent-{sequence}.json",OUTPUT_LIMIT))
        require(intent=={"authorization_sha256":authorization.authorization_sha256,"sequence":sequence,"request_sha256":r.request_sha256,"previous_receipt_sha256":previous},"PROBE_INTENT_MISMATCH")
        group=groups[(request.secid,request.source)]
        require(not group["complete"] and group["failed"] is None and request.offset==group["offset"],"PROBE_REQUEST_SEQUENCE_INVALID")
        group["receipts"].append(r)
        received+=r.received_bytes
        require(r.received_bytes<=authorization.limits.response_bytes and received<=authorization.limits.total_response_bytes,"PROBE_BYTE_ACCOUNTING_INVALID")
        if r.outcome=="ACCEPTED":
            require(r.raw_stored and r.response_sha256 is not None,"PROBE_RAW_EVIDENCE_MISSING")
            require(len(r.response_sha256)==64 and all(c in "0123456789abcdef" for c in r.response_sha256),"PROBE_RAW_HASH_INVALID")
            body=read_bytes(root/(r.response_sha256+".response.json"),authorization.limits.response_bytes)
            require(hashlib.sha256(body).hexdigest()==r.response_sha256 and len(body)==r.received_bytes,"PROBE_RAW_HASH_MISMATCH")
            page=parse_page(body,request)
            require((page.complete,page.next_offset,page.cursor_total,page.cursor_page_size,page.completion_basis)==
                (r.complete,r.next_offset,r.cursor_total,r.cursor_page_size,r.completion_basis),"PROBE_PAGE_RECEIPT_MISMATCH")
            cursor=(page.completion_basis,page.cursor_total,page.cursor_page_size)
            require(group["cursor"] is None or group["cursor"]==cursor,"PROBE_CURSOR_CHANGED")
            group["cursor"]=cursor
            hashes={sha(row) for row in page.rows}
            require(len(hashes)==len(page.rows) and not hashes&group["seen"],"PROBE_PAGE_OVERLAP")
            group["seen"].update(hashes);group["rows"].extend(page.rows)
            group["complete"]=page.complete;group["offset"]=page.next_offset;group["failures"]=0
            accepted+=1
        else:
            require(not r.raw_stored and r.complete is None and r.next_offset is None and r.cursor_total is None and r.cursor_page_size is None and r.completion_basis is None,"PROBE_FAILURE_NOT_EMPTY_SUCCESS")
            group["failures"]+=1
            if r.outcome!="TRANSIENT_SOURCE_FAILURE" or group["failures"]>=3:
                group["failed"]=r.outcome if r.outcome in ("SOURCE_INVALID","TRANSIENT_SOURCE_FAILURE","SOURCE_FAILURE") else "SOURCE_RESPONSE_INCOMPLETE"
        previous=digest;receipts.append(r)
    require(accepted<=authorization.limits.max_pages,"PROBE_PAGE_ACCOUNTING_INVALID")
    blockers=("UNRECONCILED_ATTEMPT_INTENT",) if state["pending"] is not None else ()
    complete=all(g["complete"] for g in groups.values())
    manifest=signed(ProbeManifest,"evidence_sha256",status="BLOCKED" if blockers else "COMPLETE" if complete else "INCOMPLETE",
        plan_sha256=plan.plan_sha256,authorization_sha256=authorization.authorization_sha256,accepted_pages=accepted,attempts=len(receipts),
        received_bytes=received,receipt_sha256s=tuple(state["receipt_sha256s"]),blockers=blockers,network_access=bool(receipts) or state["pending"] is not None)
    return state,groups,manifest


class HistoricalIdentityProbeService:
    def __init__(self, *, sleeper=time.sleep, monotonic=time.monotonic, clock=lambda:datetime.now(timezone.utc)):
        self.sleep,self.monotonic,self.clock=sleeper,monotonic,clock

    def authorize(self, *, plan, expected_plan_sha256, secids, sources, destination, explicit_authorization=False, limits=None):
        validate_plan(plan)
        require(explicit_authorization is True and expected_plan_sha256==plan.plan_sha256,"EXPLICIT_REVIEWED_AUTHORIZATION_REQUIRED")
        require(type(secids) in (tuple,list) and secids and all(type(s) is str for s in secids) and len(set(secids))==len(secids)<=12,"AUTHORIZATION_SECIDS_INVALID")
        require(type(sources) in (tuple,list) and sources and len(set(sources))==len(sources) and set(sources)<=set(SOURCE_ORDER),"AUTHORIZATION_SOURCES_INVALID")
        root=safe_path(destination);require(root.is_dir(),"EXPLICIT_EVIDENCE_DIRECTORY_REQUIRED")
        independent_root(root)
        require(not list(root.iterdir()) or (root/"task308a-scope.json").is_file(),"PROBE_DIRECTORY_NOT_PRIVATE")
        limits=ProbeLimits() if limits is None else limits
        require(type(limits) is ProbeLimits,"PROBE_LIMITS_REQUIRED")
        a=signed(ProbeAuthorization,"authorization_sha256",explicit_authorization=True,plan_sha256=plan.plan_sha256,context=plan.context,
            secids=tuple(sorted(secids)),max_secids=len(secids),sources=tuple(s for s in SOURCE_ORDER if s in sources),limits=limits,destination_sha256=sha(str(root)))
        verify_authorization(plan,a,expected_plan_sha256,root);assert_capacity(root,limits)
        return a

    def probe(self, *, plan, authorization, expected_plan_sha256, destination, confirm_network=False, source_client=None):
        require(confirm_network is True,"CONFIRM_NETWORK_REQUIRED")
        root=safe_path(destination)
        independent_root(root)
        a=verify_authorization(plan,authorization,expected_plan_sha256,root)
        require(root.is_dir(),"EXPLICIT_EVIDENCE_DIRECTORY_REQUIRED")
        require(not list(root.iterdir()) or (root/"task308a-scope.json").is_file(),"PROBE_DIRECTORY_NOT_PRIVATE")
        assert_capacity(root,a.limits)
        with writer_lock(root):
            atomic(root/"task308a-scope.json",encode({"authorization_sha256":a.authorization_sha256,"plan_sha256":plan.plan_sha256}),immutable=True)
            state,groups,manifest=replay(plan,a,root)
            require(state["pending"] is None,"UNRECONCILED_ATTEMPT_INTENT")
            # A byte/time budget stop is permanent for this authorization, including resume.
            budget_stopped=any(r.outcome=="BUDGET_STOP" for g in groups.values() for r in g["receipts"])
            start=self.monotonic();spent=state["elapsed_seconds"]
            now=self.clock()
            require(now.tzinfo is not None and now.utcoffset()==timedelta(0),"OBSERVATION_UTC_REQUIRED")
            if state["deadline_at"] is None:
                state["deadline_at"]=(now+timedelta(seconds=a.limits.max_seconds-spent)).isoformat()
                save_state(root,state)
            deadline=datetime.fromisoformat(state["deadline_at"])
            owned=source_client is None
            client=HistoricalIdentityProbeClient() if owned else source_client
            def elapsed():
                wall=a.limits.max_seconds-max(0,(deadline-self.clock()).total_seconds())
                return min(a.limits.max_seconds,max(wall,spent+max(0,self.monotonic()-start)))
            def check_time():require(elapsed()<a.limits.max_seconds,"PROBE_TIME_BUDGET")
            try:
                stop=budget_stopped
                for secid in a.secids:
                    if stop:break
                    for source in a.sources:
                        key=(secid,source)
                        while not groups[key]["complete"] and groups[key]["failed"] is None:
                            if elapsed()+0.5>=a.limits.max_seconds or manifest.attempts>=a.limits.max_requests or manifest.accepted_pages>=a.limits.max_pages or manifest.received_bytes>=a.limits.total_response_bytes:
                                stop=True;break
                            assert_capacity(root,a.limits,extra=a.limits.response_bytes+16384)
                            self.sleep(0.5);check_time()
                            request=request_for(secid,source,groups[key]["offset"])
                            n=manifest.attempts+1;previous=state["receipt_sha256s"][-1] if state["receipt_sha256s"] else None
                            atomic(root/f"intent-{n}.json",encode({"authorization_sha256":a.authorization_sha256,"sequence":n,"request_sha256":sha(request),"previous_receipt_sha256":previous}),immutable=True)
                            state["pending"]=n;state["elapsed_seconds"]=elapsed();save_state(root,state)
                            body=None;page=None;category="ACCEPTED";outcome="ACCEPTED";count=0;response_sha=None;retry_after=0
                            try:
                                body=client.fetch(request,response_limit=a.limits.response_bytes,
                                    bytes_remaining=a.limits.total_response_bytes-manifest.received_bytes,
                                    seconds_remaining=a.limits.max_seconds-elapsed(),check_time=check_time)
                                count=len(body);response_sha=hashlib.sha256(body).hexdigest()
                                require(count<=min(a.limits.response_bytes,a.limits.total_response_bytes-manifest.received_bytes),"RESPONSE_BYTE_BUDGET")
                                page=parse_page(body,request)
                                cursor=(page.completion_basis,page.cursor_total,page.cursor_page_size)
                                require(groups[key]["cursor"] is None or groups[key]["cursor"]==cursor,"PROBE_CURSOR_CHANGED")
                                hashes={sha(row) for row in page.rows}
                                require(len(hashes)==len(page.rows) and not hashes&groups[key]["seen"],"PROBE_PAGE_OVERLAP")
                            except SourceFailure as exc:
                                outcome="TRANSIENT_SOURCE_FAILURE" if exc.retryable else "BUDGET_STOP" if "BUDGET" in exc.category else "SOURCE_FAILURE"
                                category=exc.category;count=exc.received_bytes;response_sha=exc.response_sha256;retry_after=exc.retry_after
                            except IdentityError as exc:
                                outcome="BUDGET_STOP" if "BUDGET" in exc.code else "SOURCE_INVALID";category=exc.code
                            except Exception:
                                outcome="SOURCE_FAILURE";category="UNEXPECTED_SOURCE_FAILURE"
                            accepted=outcome=="ACCEPTED"
                            if accepted:atomic(root/(response_sha+".response.json"),body,immutable=True)
                            receipt=signed(AttemptReceipt,"receipt_sha256",sequence=n,authorization_sha256=a.authorization_sha256,
                                request=request,request_sha256=sha(request),previous_receipt_sha256=previous,observed_at=self.clock(),outcome=outcome,
                                category=category,received_bytes=count,response_sha256=response_sha,raw_stored=accepted,
                                complete=page.complete if accepted else None,next_offset=page.next_offset if accepted else None,
                                cursor_total=page.cursor_total if accepted else None,cursor_page_size=page.cursor_page_size if accepted else None,
                                completion_basis=page.completion_basis if accepted else None,elapsed_seconds=float(elapsed()))
                            atomic(root/(receipt.receipt_sha256+".receipt.json"),encode(receipt),immutable=True)
                            state["receipt_sha256s"].append(receipt.receipt_sha256);state["pending"]=None;state["elapsed_seconds"]=elapsed();save_state(root,state)
                            state,groups,manifest=replay(plan,a,root)
                            if outcome=="TRANSIENT_SOURCE_FAILURE" and groups[key]["failed"] is None:
                                delay=max((0.25,0.50)[min(groups[key]["failures"]-1,1)],retry_after)
                                if elapsed()+delay>=a.limits.max_seconds:stop=True;break
                                self.sleep(delay)
                            if outcome=="BUDGET_STOP":stop=True;break
                        if stop:break
                    if stop:break
            finally:
                state["elapsed_seconds"]=elapsed();save_state(root,state)
                if owned:client.close()
            state,groups,manifest=replay(plan,a,root)
            atomic(root/"task308a-probe-manifest.json",encode(manifest))
            return manifest

    def evaluate(self, *, plan, authorization, evidence_root):
        root=safe_path(evidence_root)
        before=hashlib.sha256(read_bytes(root/"task308a-state.json",OUTPUT_LIMIT)).hexdigest()
        _,groups,manifest=replay(plan,authorization,root)
        require(manifest.status!="BLOCKED","UNRECONCILED_ATTEMPT_INTENT")
        published=root/"task308a-probe-manifest.json"
        if published.exists():
            saved=read_model(published,ProbeManifest);checked(saved,"evidence_sha256")
            require(saved==manifest,"PROBE_MANIFEST_REPLAY_MISMATCH")
        results={}
        for sample in plan.samples:
            for source in SOURCE_ORDER:
                group=groups.get((sample.secid,source))
                results[(sample.secid,source)]=resolve_rows(secid=sample.secid,source=source,
                    rows=group["rows"] if group else (),receipts=group["receipts"] if group else (),
                    complete=group["complete"] if group else False,failure=group["failed"] if group else None)
        result=evaluate_results(plan,authorization,manifest,results)
        require(hashlib.sha256(read_bytes(root/"task308a-state.json",OUTPUT_LIMIT)).hexdigest()==before,"PROBE_CHANGED_DURING_REPLAY")
        return result
