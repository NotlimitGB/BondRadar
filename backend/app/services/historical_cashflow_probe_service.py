"""Separately authorized bounded requests, immutable journal and offline replay."""
from datetime import datetime, timezone, timedelta
from dataclasses import replace
import hashlib
import os
import re
import time
from app.schemas.historical_cashflow_feasibility import (
    Authorization, Request, Intent, Receipt, ProbeState, ProbeResult, TABLES,
)
from app.schemas.historical_identity_feasibility import ProbeLimits
from app.services.historical_cashflow_feasibility_service import validate_plan, exact_strings
from app.services.historical_cashflow_probe_client import HistoricalCashflowProbeClient, parse_page, route
from app.services.historical_identity_probe_client import SourceFailure
from app.services.historical_identity_feasibility_service import require, IdentityError
from app.services.historical_identity_probe_service import read_model, read_bytes, safe_path, encode, atomic, writer_lock, independent_root, assert_capacity
from app.services.historical_evidence_normalization import checked, signed, sha, hash_syntax


def private_root(value):
    root = safe_path(value)
    require(root.is_dir(), "PRIVATE_DESTINATION_REQUIRED")
    independent_root(root)
    markers = ("task308b1-identity-audit.json", "task308a-scope.json", "task308a-plan.json", "task308a-authorization.json", "manifest.json")
    require(not any((p/n).exists() for p in (root,*root.parents) for n in markers), "DESTINATION_INSIDE_SOURCE_ROOT")
    # A uses differently named checkpoints; never append to another task's evidence.
    require(not any((root/n).exists() for n in ("authorization.json","checkpoint.json","probe-manifest.json")), "DESTINATION_NOT_PRIVATE")
    allowed = {".task308a.lock", "b2-authorization.json", "b2-state.json", "b2-probe-result.json"}
    size = 0
    for index,p in enumerate(root.iterdir(),1):
        require(index<=256,"EVIDENCE_FILE_COUNT_LIMIT")
        require(safe_path(p).is_file() and (p.name in allowed or re.fullmatch(r"(?:intent|receipt)-\d{3}\.json|[0-9a-f]{64}\.body",p.name)), "DESTINATION_NOT_PRIVATE")
        size += p.stat().st_size
        require(size<=128*1024**2,"EVIDENCE_BYTE_LIMIT")
    return root


def destination_sha(root):
    return sha(str(root).casefold() if os.name == "nt" else str(root))


def validate_authorization(plan, authorization, root):
    validate_plan(plan)
    require(type(authorization) is Authorization, "AUTHORIZATION_REQUIRED")
    checked(authorization,"authorization_sha256")
    require(authorization.explicit_confirmation is True and authorization.plan_sha256 == plan.plan_sha256,
        "AUTHORIZATION_PLAN_MISMATCH")
    require(authorization.secids == exact_strings(authorization.secids) and
        set(authorization.secids) <= {c.secid for c in plan.cohort} and len(authorization.secids) <= 6,
        "AUTHORIZED_SECIDS_INVALID")
    require(authorization.tables and authorization.tables == tuple(t for t in TABLES if t in authorization.tables), "AUTHORIZED_TABLES_INVALID")
    ProbeLimits.model_validate(authorization.limits)
    require(authorization.destination_sha256 == destination_sha(root), "AUTHORIZED_DESTINATION_MISMATCH")


def utc(value):
    require(type(value) is datetime and value.utcoffset() is not None and value.utcoffset().total_seconds() == 0,
        "CLOCK_UTC_REQUIRED")
    return value


def partition_progress(receipts, secid, table):
    """One interpretation used by execution and replay; no request on a terminal partition."""
    rows = [r for r in receipts if r.request.secid == secid and r.request.table == table]
    accepted = [r for r in rows if r.status == "ACCEPTED"]
    if rows and rows[-1].status == "BUDGET_STOP": return None
    offset = accepted[-1].next_offset if accepted else 0
    if offset is None: return None
    failures = [r for r in rows if r.request.offset == offset and r.status != "ACCEPTED"]
    if failures and (not failures[-1].retryable or len(failures) >= 3): return None
    return offset, len(failures), (failures[-1].retry_after if failures else 0)


def qualified_page(page, prior):
    if any(r.content_sha256 == page.content_sha256 for r in prior):
        return replace(page,complete=False,next_offset=None,diagnostic="REPEATED_PAGE")
    if prior and (page.total,page.size) != (prior[0].cursor_total,prior[0].cursor_page_size):
        return replace(page,complete=False,next_offset=None,diagnostic="CURSOR_DRIFT")
    return page


def replay(plan, authorization, evidence_root, *, verify_result=True):
    root = private_root(evidence_root)
    validate_authorization(plan,authorization,root)
    saved = read_model(root/"b2-authorization.json",Authorization)
    require(saved == authorization, "SAVED_AUTHORIZATION_MISMATCH")
    state = read_model(root/"b2-state.json",ProbeState)
    state_bytes_sha = hashlib.sha256(encode(state)).hexdigest()
    checked(state,"state_sha256")
    require(state.authorization_sha256 == authorization.authorization_sha256, "STATE_AUTHORIZATION_MISMATCH")
    utc(state.deadline_at)
    limits = authorization.limits
    require(len(state.receipt_sha256s) <= limits.max_requests, "ATTEMPT_BUDGET_INVALID")
    receipts, received, pages = [], 0, 0
    previous_start = None
    for seq, receipt_sha in enumerate(state.receipt_sha256s,1):
        intent = read_model(root/f"intent-{seq:03}.json",Intent)
        checked(intent,"intent_sha256")
        receipt = read_model(root/f"receipt-{seq:03}.json",Receipt)
        checked(receipt,"receipt_sha256")
        previous = state.receipt_sha256s[seq-2] if seq > 1 else None
        require(intent.sequence == receipt.sequence == seq and intent.authorization_sha256 == authorization.authorization_sha256
            and intent.previous_receipt_sha256 == receipt.previous_receipt_sha256 == previous
            and receipt.intent_sha256 == intent.intent_sha256 and receipt.request == intent.request and receipt.receipt_sha256 == receipt_sha,
            "RECEIPT_CHAIN_INVALID")
        require(intent.request.secid in authorization.secids and intent.request.table in authorization.tables, "REQUEST_OUTSIDE_AUTHORIZATION")
        route(intent.request)
        progress = partition_progress(receipts,intent.request.secid,intent.request.table)
        require(progress is not None and progress[0] == intent.request.offset, "REQUEST_SEQUENCE_INVALID")
        started, observed = utc(intent.started_at), utc(receipt.observed_at)
        require(started <= observed and started <= state.deadline_at and (seq == 1 or started >= receipts[-1].observed_at), "RECEIPT_TIME_INVALID")
        require(previous_start is None or (started-previous_start).total_seconds() >= .5, "RATE_LIMIT_PROOF_INVALID")
        previous_start = started
        require(receipt.received_bytes <= limits.response_bytes, "RECEIPT_BYTE_LIMIT")
        received += receipt.received_bytes
        if receipt.body_sha256:
            hash_syntax(receipt.body_sha256)
            body = read_bytes(root/(receipt.body_sha256+".body"),limits.response_bytes)
            require(len(body) == receipt.received_bytes and hashlib.sha256(body).hexdigest() == receipt.body_sha256, "RAW_BODY_HASH_MISMATCH")
            require(receipt.response_sha256 == receipt.body_sha256,"RESPONSE_BODY_BINDING_INVALID")
        if receipt.response_sha256 is not None:hash_syntax(receipt.response_sha256)
        if receipt.status == "ACCEPTED":
            require(receipt.body_sha256 is not None and not receipt.retryable, "ACCEPTED_RECEIPT_INVALID")
            page = parse_page(body,receipt.request)
            prior = [r for r in receipts if r.status == "ACCEPTED" and r.request.secid == receipt.request.secid and r.request.table == receipt.request.table]
            page = qualified_page(page,prior)
            require((receipt.complete,receipt.next_offset,receipt.cursor_total,receipt.cursor_page_size,receipt.row_count,receipt.content_sha256,receipt.category)
                == (page.complete,page.next_offset,page.total,page.size,len(page.rows),page.content_sha256,page.diagnostic), "PAGE_RECEIPT_MISMATCH")
            pages += 1
        else:
            require(not receipt.complete and receipt.next_offset is None and receipt.row_count == 0, "FAILED_RECEIPT_INVALID")
        receipts.append(receipt)
    require(received <= limits.total_response_bytes and pages <= limits.max_pages, "PERSISTED_BUDGET_EXCEEDED")
    require(not any(root.glob(f"receipt-{len(receipts)+1:03}.json")), "UNRECONCILED_RECEIPT")
    if state.pending_sequence is not None:
        require(False, "UNKNOWN_INTENT_OUTCOME")
    else:
        require(not (root/f"intent-{len(receipts)+1:03}.json").exists(), "UNRECONCILED_INTENT")
    require({p.name for p in root.glob("intent-*.json")}=={f"intent-{i:03}.json" for i in range(1,len(receipts)+1)} and
        {p.name for p in root.glob("receipt-*.json")}=={f"receipt-{i:03}.json" for i in range(1,len(receipts)+1)},"UNRECONCILED_JOURNAL_FILES")
    require({p.stem for p in root.glob("*.body")}=={r.body_sha256 for r in receipts if r.body_sha256 is not None},"UNRECONCILED_SOURCE_BODIES")
    require(hashlib.sha256(read_bytes(root/"b2-state.json",8*1024**2)).hexdigest()==state_bytes_sha,"EVIDENCE_CHANGED_DURING_REPLAY")
    complete = all(partition_progress(receipts,s,t) is None and any(r.complete and r.request.secid == s and r.request.table == t for r in receipts)
        for s in authorization.secids for t in authorization.tables)
    result = signed(ProbeResult,"probe_sha256",status="COMPLETE" if complete else "PARTIAL",
        blockers=(state.stop_reason,) if state.stop_reason else (),
        authorization_sha256=authorization.authorization_sha256,receipt_sha256s=state.receipt_sha256s,
        attempts=len(receipts),accepted_pages=pages,received_bytes=received)
    published = root/"b2-probe-result.json"
    if verify_result and published.exists():
        saved_result=read_model(published,ProbeResult)
        checked(saved_result,"probe_sha256")
        require(saved_result==result,"PUBLISHED_PROBE_REPLAY_MISMATCH")
    return result, tuple(receipts)


class HistoricalCashflowProbeService:
    def __init__(self, *, client_factory=HistoricalCashflowProbeClient, sleeper=time.sleep, monotonic=time.monotonic,
        clock=lambda:datetime.now(timezone.utc)):
        self.client_factory,self.sleep,self.monotonic,self.clock = client_factory,sleeper,monotonic,clock

    @staticmethod
    def authorize(*, plan, reviewed_plan_sha256, secids, tables, limits, evidence_root, confirm=False):
        validate_plan(plan)
        require(confirm is True and reviewed_plan_sha256 == plan.plan_sha256, "EXPLICIT_REVIEW_CONFIRMATION_REQUIRED")
        root = private_root(evidence_root)
        secids = exact_strings(secids)
        supplied_tables = exact_strings(tables)
        require(set(supplied_tables) <= set(TABLES), "AUTHORIZED_TABLES_INVALID")
        require(type(limits) is ProbeLimits, "EXACT_LIMITS_REQUIRED")
        result = signed(Authorization,"authorization_sha256",explicit_confirmation=True,plan_sha256=plan.plan_sha256,
            secids=secids,tables=tuple(t for t in TABLES if t in supplied_tables),limits=limits,destination_sha256=destination_sha(root))
        validate_authorization(plan,result,root)
        assert_capacity(root,limits,extra=16384)
        return result

    def probe(self, *, plan, authorization, evidence_root, confirm_network=False):
        require(confirm_network is True, "EXPLICIT_NETWORK_CONFIRMATION_REQUIRED")
        root = private_root(evidence_root)
        validate_authorization(plan,authorization,root)
        client = None
        try:
            with writer_lock(root):
                assert_capacity(root,authorization.limits,extra=32768)
                atomic(root/"b2-authorization.json",encode(authorization),immutable=True)
                if not (root/"b2-state.json").exists():
                    require(not any(root.glob("intent-*.json")) and not any(root.glob("receipt-*.json")), "UNRECONCILED_EVIDENCE")
                    state = signed(ProbeState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                        deadline_at=utc(self.clock())+timedelta(seconds=authorization.limits.max_seconds))
                    atomic(root/"b2-state.json",encode(state))
                result, old = replay(plan,authorization,root,verify_result=False)
                receipts = list(old)
                state = read_model(root/"b2-state.json",ProbeState)
                deadline = state.deadline_at
                limits = authorization.limits
                began, start_wall = self.monotonic(),utc(self.clock())
                def remaining():
                    return min((deadline-utc(self.clock())).total_seconds(), (deadline-start_wall).total_seconds()-(self.monotonic()-began))
                def check_time():
                    require(remaining()>0,"TIME_BUDGET_STOP")
                last_start,last_monotonic_start = None,None
                if receipts:
                    last_start = receipts[-1].observed_at
                stop = state.stop_reason is not None or any(r.status == "BUDGET_STOP" for r in receipts)
                for secid in authorization.secids:
                    for table in authorization.tables:
                        while not stop:
                            progress = partition_progress(receipts,secid,table)
                            if progress is None: break
                            offset, failures, retry_after = progress
                            if len(receipts)>=limits.max_requests or sum(r.status=="ACCEPTED" for r in receipts)>=limits.max_pages or sum(r.received_bytes for r in receipts)>=limits.total_response_bytes or remaining()<=0:
                                stop = True
                                break
                            delay = max((.25,.50)[failures-1] if failures else 0, retry_after,
                                max(0,.5-(utc(self.clock())-last_start).total_seconds()) if last_start else 0,
                                max(0,.5-(self.monotonic()-last_monotonic_start)) if last_monotonic_start is not None else 0)
                            if delay>=remaining():
                                stop=True
                                break
                            if delay:self.sleep(delay)
                            check_time()
                            request = Request(secid=secid,table=table,offset=offset)
                            sequence = len(receipts)+1
                            intent = signed(Intent,"intent_sha256",sequence=sequence,authorization_sha256=authorization.authorization_sha256,
                                request=request,previous_receipt_sha256=receipts[-1].receipt_sha256 if receipts else None,started_at=utc(self.clock()))
                            assert_capacity(root,limits,extra=limits.response_bytes+32768)
                            atomic(root/f"intent-{sequence:03}.json",encode(intent),immutable=True)
                            state = signed(ProbeState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                                receipt_sha256s=tuple(r.receipt_sha256 for r in receipts),pending_sequence=sequence,deadline_at=deadline)
                            atomic(root/"b2-state.json",encode(state))
                            last_start = intent.started_at
                            body, body_sha, response_sha, page = None,None,None,None
                            try:
                                if client is None:client=self.client_factory()
                                last_monotonic_start = self.monotonic()
                                body = client.fetch(request,response_limit=limits.response_bytes,
                                    bytes_remaining=limits.total_response_bytes-sum(r.received_bytes for r in receipts),seconds_remaining=remaining(),check_time=check_time)
                                response_sha = hashlib.sha256(body).hexdigest()
                                page = parse_page(body,request)
                                body_sha = response_sha
                                atomic(root/(body_sha+".body"),body,immutable=True)
                                prior = [r for r in receipts if r.status=="ACCEPTED" and r.request.secid==secid and r.request.table==table]
                                page = qualified_page(page,prior)
                                check_time()
                                status,category,retryable,retry_delay,received = "ACCEPTED",page.diagnostic,False,0,len(body)
                            except SourceFailure as exc:
                                category,retryable,retry_delay,received = exc.category,exc.retryable,exc.retry_after,exc.received_bytes
                                response_sha = exc.response_sha256
                                status = "BUDGET_STOP" if "BUDGET" in category else "SOURCE_ERROR"
                                page=None
                            except IdentityError as exc:
                                status,category,retryable,retry_delay,received = "BUDGET_STOP" if "BUDGET" in exc.code else "SOURCE_ERROR",exc.code,False,0,len(body) if body else 0
                                page=None
                            receipt = signed(Receipt,"receipt_sha256",sequence=sequence,intent_sha256=intent.intent_sha256,request=request,
                                previous_receipt_sha256=intent.previous_receipt_sha256,observed_at=utc(self.clock()),status=status,category=category,
                                retryable=retryable,retry_after=retry_delay,received_bytes=received,body_sha256=body_sha,response_sha256=response_sha,
                                complete=page.complete if page else False,next_offset=page.next_offset if page else None,cursor_total=page.total if page else None,
                                cursor_page_size=page.size if page else None,row_count=len(page.rows) if page else 0,content_sha256=page.content_sha256 if page else None)
                            atomic(root/f"receipt-{sequence:03}.json",encode(receipt),immutable=True)
                            receipts.append(receipt)
                            state = signed(ProbeState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                                receipt_sha256s=tuple(r.receipt_sha256 for r in receipts),deadline_at=deadline)
                            atomic(root/"b2-state.json",encode(state))
                            if status == "BUDGET_STOP":stop=True
                result,_ = replay(plan,authorization,root,verify_result=False)
                if stop and result.status != "COMPLETE":
                    state = signed(ProbeState,"state_sha256",authorization_sha256=authorization.authorization_sha256,
                        receipt_sha256s=tuple(r.receipt_sha256 for r in receipts),deadline_at=deadline,stop_reason="GLOBAL_BUDGET_STOP")
                    atomic(root/"b2-state.json",encode(state))
                    result,_ = replay(plan,authorization,root,verify_result=False)
                # Journal is authoritative; finalization never changes accepted evidence.
                atomic(root/"b2-probe-result.json",encode(result))
                return result
        except (IdentityError,ValueError,OSError) as exc:
            return signed(ProbeResult,"probe_sha256",status="BLOCKED",blockers=(exc.code if isinstance(exc,IdentityError) else "PROBE_EVIDENCE_FAILURE",),
                authorization_sha256=authorization.authorization_sha256)
        finally:
            if client is not None:client.close()
