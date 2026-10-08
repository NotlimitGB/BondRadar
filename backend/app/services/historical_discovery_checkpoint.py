"""Source-only filesystem checkpoints. No application DB or acquisition execution."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import heapq
import json
import os
from pathlib import Path
import stat
import time
import tempfile
from app.schemas.historical_discovery_checkpoint import (
    DiscoveryLimits, DiscoveryReadAuthorization, DiscoveryProgress,
    DiscoveryManifestIndex, DiscoveryProbeResult, DiscoverySourcePage,
)
from app.schemas.historical_evidence_foundation import HistoricalEvidencePolicy, HistoricalSourcePage, HistoricalSecurity
from app.services.historical_evidence_discovery import discovery_queries, read_scope
from app.services.historical_evidence_normalization import checked, signed, sha, normalize, LIMIT, value, text, raw_date
from app.services.historical_evidence_source_client import HistoricalSourceError
from app.services.historical_audit_canonical_json import chunks

VERSION = "historical-discovery-checkpoint-v1"
PLAN_CEILING = 32 * 1024 * 1024


def fingerprint():
    # Compatibility changes require a new source generation, never silent reuse.
    base = Path(__file__).parent
    files = (Path(__file__), base / "historical_evidence_source_client.py",
             base / "historical_evidence_discovery.py", base / "historical_evidence_normalization.py",
             base / "historical_audit_canonical_json.py",base / "moex_iss_client.py",base / "moex_market_data_service.py",
             base.parent / "schemas/historical_evidence_foundation.py",
             base.parent / "schemas/historical_discovery_checkpoint.py")
    return sha({p.name: hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in files})


def safe_path(root):
    path = Path(os.path.abspath(root))
    if os.name=="nt" and not str(path).startswith("\\\\?\\"):
        if str(path).startswith("\\\\"):raise ValueError("CHECKPOINT_LOCAL_ROOT_REQUIRED")
        path=Path("\\\\?\\"+str(path))
    for ancestor in (path, *path.parents):
        if ancestor.exists() or ancestor.is_symlink():
            info = ancestor.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                raise ValueError("CHECKPOINT_UNSAFE_PATH")
    return path


def parse_json(content):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("CHECKPOINT_DUPLICATE_KEY")
            result[key] = value
        return result
    try:
        return json.loads(content, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("NONFINITE_JSON")))
    except (ValueError, TypeError, RecursionError):
        raise ValueError("CHECKPOINT_INVALID_JSON") from None


def stream_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""): h.update(block)
    return h.hexdigest()


def atomic_json(path, value, *, immutable=False):
    safe_path(path)
    descriptor, name = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            count = 0
            for part in chunks(value):
                encoded = part.encode("ascii"); count += len(encoded)
                if count > LIMIT: raise ValueError("CHECKPOINT_RECORD_TOO_LARGE")
                stream.write(encoded)
            stream.flush(); os.fsync(stream.fileno())
        if immutable and path.exists():
            if stream_hash(temp) != stream_hash(path): raise ValueError("CHECKPOINT_IMMUTABLE_CONFLICT")
        else:
            os.replace(temp, path)
            if os.name != "nt":
                directory = os.open(path.parent, os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
    finally:
        if temp.exists(): temp.unlink()


def read_json(path):
    safe_path(path)
    with path.open("rb") as stream: content = stream.read(LIMIT + 1)
    if len(content) > LIMIT: raise ValueError("CHECKPOINT_RECORD_TOO_LARGE")
    return parse_json(content)


def sign_state(value):
    return {**value, "state_sha256": sha(value)}


def verify_state(value):
    if type(value) is not dict or sha({k: v for k, v in value.items() if k != "state_sha256"}) != value.get("state_sha256"):
        raise ValueError("CHECKPOINT_HASH_MISMATCH")
    return value


class RequestBudget:
    def __init__(self, limits, *, monotonic=time.monotonic):
        self.limits = limits; self.clock = monotonic; self.started = monotonic()
        self.attempts = 0; self.pages = 0

    def remaining(self): return self.limits.max_seconds - (self.clock() - self.started)

    def check_time(self):
        if self.remaining() <= 0: raise HistoricalSourceError("DISCOVERY_BUDGET_EXHAUSTED")

    def before_wait(self, delay):
        if self.remaining() <= delay: raise HistoricalSourceError("DISCOVERY_BUDGET_EXHAUSTED")

    def before_attempt(self, wait):
        self.check_time(); self.before_wait(wait)
        if self.attempts >= self.limits.max_requests or self.pages >= self.limits.max_pages:
            raise HistoricalSourceError("DISCOVERY_BUDGET_EXHAUSTED")

    def start_attempt(self):
        self.before_attempt(0)
        self.attempts += 1

    def timeout(self):
        self.check_time()
        return min(30, self.remaining())


def authorize(*, policy, evidence_root, limits=None, probe_dates=()):
    if type(policy) is not HistoricalEvidencePolicy: raise ValueError("DISCOVERY_POLICY_REQUIRED")
    HistoricalEvidencePolicy.model_validate(policy)
    if policy.cutoff != date(2026, 9, 30): raise ValueError("DISCOVERY_FROZEN_CUTOFF_REQUIRED")
    if type(probe_dates) not in (tuple, list) or any(type(d) is not date or not policy.history_start <= d <= policy.cutoff for d in probe_dates):
        raise ValueError("DISCOVERY_PROBE_SCOPE_INVALID")
    if len(set(probe_dates)) != len(probe_dates): raise ValueError("DISCOVERY_DUPLICATE_PROBE_DATE")
    return signed(DiscoveryReadAuthorization, "authorization_sha256", explicit_authorization=True,
                  evidence_root=str(safe_path(evidence_root)), scope_sha256=read_scope(policy),
                  implementation_sha256=fingerprint(), limits=limits or DiscoveryLimits(),
                  probe_dates=tuple(sorted(probe_dates)))


def check_authorization(policy, root, authorization, limits):
    if type(authorization) is not DiscoveryReadAuthorization or type(limits) is not DiscoveryLimits:
        raise ValueError("DISCOVERY_AUTHORIZATION_REQUIRED")
    checked(authorization, "authorization_sha256")
    expected = authorize(policy=policy, evidence_root=root, limits=limits, probe_dates=authorization.probe_dates)
    if authorization != expected: raise ValueError("DISCOVERY_AUTHORIZATION_MISMATCH")


def identity(query, row):
    # Discovery identity is independent of economic mapping/price availability.
    try:
        secid=text(value(row,"SECID"));board=text(value(row,"BOARDID"));isin=text(value(row,"ISIN","ISINCODE"))
        if query.family=="MARKET" and raw_date(value(row,"TRADEDATE"))!=query.trade_date:
            raise ValueError("SOURCE_DATE_MISMATCH")
        start=raw_date(value(row,"HISTORY_FROM","DATE_FROM","LISTED_FROM","FROM")) if query.family=="LISTING" else None
        end=raw_date(value(row,"HISTORY_TILL","DATE_TILL","LISTED_TILL","TILL")) if query.family=="LISTING" else None
        if start and end and end<start:raise ValueError("SOURCE_INTERVAL_INVALID")
    except (ValueError,TypeError):raise HistoricalSourceError("SOURCE_BINDING_INVALID") from None
    if type(secid) is not str or not secid.strip() or len(secid) > 256:
        raise HistoricalSourceError("SOURCE_SECID_INVALID")
    if board is not None and (type(board) is not str or not board.strip() or len(board) > 256):
        raise HistoricalSourceError("SOURCE_BOARD_INVALID")
    if query.family == "MARKET" and board is None: raise HistoricalSourceError("SOURCE_BOARD_MISSING")
    if isin is not None and (type(isin) is not str or not isin.strip() or len(isin) > 256):
        raise HistoricalSourceError("SOURCE_ISIN_INVALID")
    return HistoricalSecurity(secid=secid, isin=isin, board=board,
        interval_start=start,interval_end=end,
        authority="SOURCE_LISTING" if query.family == "LISTING" else "OBSERVED_HISTORY")


def content_hash(page):
    return sha({"query": page.query, "rows": tuple(sorted(sha(row) for row in page.rows)),
                "complete": page.complete, "next_offset": page.next_offset,
                "cursor_total": page.cursor_total, "cursor_page_size": page.cursor_page_size})


class CheckpointStore:
    def __init__(self, root, policy, *, create=False):
        self.root = safe_path(root); self.policy = policy
        if create:
            self.root.mkdir(mode=0o700,exist_ok=True)
        if not self.root.is_dir(): raise ValueError("CHECKPOINT_ROOT_MISSING")
        if os.name!="nt":
            info=self.root.stat()
            if info.st_uid!=os.getuid() or info.st_mode&0o022:raise ValueError("CHECKPOINT_UNSAFE_OWNERSHIP")
        self.meta = {"contract_version": VERSION, "policy": policy,
                     "scope_sha256": read_scope(policy), "implementation_sha256": fingerprint()}
        if create and not (self.root / "scope.json").exists():
            atomic_json(self.root / "scope.json", sign_state(self.meta), immutable=True)
        if verify_state(read_json(self.root / "scope.json")) != parse_json("".join(chunks(sign_state(self.meta)))):
            raise ValueError("CHECKPOINT_SCOPE_OR_VERSION_MISMATCH")

    @contextmanager
    def lock(self):
        path = safe_path(self.root / ".lock")
        with path.open("a+b") as handle:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                if path.stat().st_size == 0: handle.write(b"0"); handle.flush()
                handle.seek(0)
                try: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError: raise ValueError("CHECKPOINT_LOCK_UNAVAILABLE") from None
            else:
                import fcntl
                try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError: raise ValueError("CHECKPOINT_LOCK_UNAVAILABLE") from None
            try: yield self
            finally:
                if os.name == "nt":
                    handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else: fcntl.flock(handle, fcntl.LOCK_UN)

    def directory(self, query): return safe_path(self.root / sha(query))

    def state(self, query):
        directory = self.directory(query)
        pointer = directory / "state.json"
        if not pointer.exists():
            return {"query_sha256": sha(query), "generation": 0, "page_count": 0, "next_offset": 0,
                    "complete": False, "last_receipt": None, "failure_code": None,
                    "http_attempt_count": 0, "retry_count": 0, "row_count": 0}
        value = verify_state(read_json(pointer))
        expected_keys = set(self.state_template(query)) | {"state_sha256"}
        if set(value) != expected_keys or value["query_sha256"] != sha(query): raise ValueError("CHECKPOINT_STATE_INVALID")
        if any(type(value[k]) is not int or value[k] < 0 for k in ("generation", "page_count", "http_attempt_count", "retry_count", "row_count")):
            raise ValueError("CHECKPOINT_COUNTS_INVALID")
        if type(value["complete"]) is not bool or (value["complete"] != (value["next_offset"] is None)):
            raise ValueError("CHECKPOINT_COMPLETION_INVALID")
        if not value["complete"] and (type(value["next_offset"]) is not int or value["next_offset"] < 0): raise ValueError("CHECKPOINT_OFFSET_INVALID")
        return {k: v for k, v in value.items() if k != "state_sha256"}

    @staticmethod
    def state_template(query):
        return {"query_sha256": sha(query), "generation": 0, "page_count": 0, "next_offset": 0,
                "complete": False, "last_receipt": None, "failure_code": None,
                "http_attempt_count": 0, "retry_count": 0, "row_count": 0}

    def save_state(self, query, state):
        directory = self.directory(query); directory.mkdir(exist_ok=True)
        atomic_json(directory / "state.json", sign_state(state))

    def receipts(self, query, state, *, verify_files=True, budget=None):
        previous = None; offset = 0; rows = 0
        directory = self.directory(query) / str(state["generation"])
        for _ in range(state["page_count"]):
            if budget is not None:budget.check_time()
            receipt = verify_state(read_json(directory / f"{offset}.json"))
            if set(receipt)!={"page_sha256","file_sha256","content_sha256","previous_receipt","observed_at","state_sha256",
                              "query_sha256","row_count","next_offset","complete","cursor_total","cursor_page_size","completion_basis"}:
                raise ValueError("CHECKPOINT_RECEIPT_INVALID")
            for key in ("page_sha256","file_sha256","content_sha256","query_sha256"):
                digest=receipt[key]
                if type(digest) is not str or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("CHECKPOINT_RECEIPT_HASH_INVALID")
            if receipt.get("previous_receipt") != previous: raise ValueError("CHECKPOINT_CHAIN_INVALID")
            page_path=directory/(receipt["page_sha256"]+".json")
            if safe_path(page_path).stat().st_size>LIMIT:raise ValueError("CHECKPOINT_PAGE_TOO_LARGE")
            if verify_files and stream_hash(safe_path(page_path))!=receipt["file_sha256"]:raise ValueError("CHECKPOINT_PAGE_BYTES_INVALID")
            if receipt["query_sha256"]!=sha(query.model_copy(update={"offset":offset})):
                raise ValueError("CHECKPOINT_QUERY_INVALID")
            if type(receipt["row_count"]) is not int or receipt["row_count"]<0 or type(receipt["complete"]) is not bool:
                raise ValueError("CHECKPOINT_RECEIPT_COUNTS_INVALID")
            next_offset=receipt["next_offset"]
            if receipt["complete"]!=(next_offset is None) or (next_offset is not None and (type(next_offset) is not int or next_offset<=offset)):
                raise ValueError("CHECKPOINT_RECEIPT_OFFSET_INVALID")
            previous=receipt["state_sha256"];offset=next_offset;rows+=receipt["row_count"]
            yield page_path,receipt
        if previous != state["last_receipt"] or offset != state["next_offset"] or rows != state["row_count"]:
            raise ValueError("CHECKPOINT_TAIL_INVALID")

    def accepted(self, query, state, *, deep=True):
        for page_path,receipt in self.receipts(query,state,verify_files=deep):
            with page_path.open("rb") as stream:content=stream.read(LIMIT+1)
            if len(content)>LIMIT:raise ValueError("CHECKPOINT_PAGE_TOO_LARGE")
            if deep:parse_json(content)
            page = DiscoverySourcePage.model_validate_json(content)
            if deep:checked(page, "page_sha256")
            if receipt["query_sha256"] != sha(page.query) or receipt["page_sha256"] != page.page_sha256 or (deep and receipt["content_sha256"] != content_hash(page)):
                raise ValueError("CHECKPOINT_PAGE_BINDING_INVALID")
            if (len(page.rows),page.next_offset,page.complete,page.cursor_total,page.cursor_page_size,page.completion_basis)!=(
                receipt["row_count"],receipt["next_offset"],receipt["complete"],receipt["cursor_total"],receipt["cursor_page_size"],receipt["completion_basis"]):
                raise ValueError("CHECKPOINT_RECEIPT_PAGE_MISMATCH")
            yield page, receipt

    def check_page(self, query, state, page):
        if type(page) is not DiscoverySourcePage:raise HistoricalSourceError("SOURCE_PAGE_CONTRACT_INVALID")
        checked(page, "page_sha256")
        if page.query != query.model_copy(update={"offset": state["next_offset"]}): raise HistoricalSourceError("SOURCE_QUERY_BINDING_CONFLICT")
        hashes = {sha(row) for row in page.rows}
        if len(hashes) != len(page.rows): raise HistoricalSourceError("OVERLAPPING_SOURCE_ROWS")
        if query.family in ("MARKET", "LISTING"):
            for row in page.rows: identity(query, row)
        for old, _ in self.accepted(query, state,deep=False):
            if old.cursor_total is not None and page.cursor_total is None: raise HistoricalSourceError("SOURCE_CURSOR_DISAPPEARED")
            if old.cursor_total != page.cursor_total: raise HistoricalSourceError("SOURCE_TOTAL_CHANGED")
            if old.cursor_page_size != page.cursor_page_size: raise HistoricalSourceError("SOURCE_PAGE_SIZE_CHANGED")
            if hashes & {sha(row) for row in old.rows}: raise HistoricalSourceError("OVERLAPPING_SOURCE_ROWS")

    def accept(self, query, state, page, attempts):
        self.check_page(query, state, page)
        directory = self.directory(query) / str(state["generation"]); directory.mkdir(parents=True, exist_ok=True)
        atomic_json(directory / (page.page_sha256 + ".json"), page, immutable=True)
        receipt = sign_state({"page_sha256": page.page_sha256, "content_sha256": content_hash(page),
                              "file_sha256":stream_hash(directory/(page.page_sha256+".json")),
                              "previous_receipt": state["last_receipt"], "observed_at": page.observed_at,
                              "query_sha256":sha(page.query),"row_count":len(page.rows),"next_offset":page.next_offset,
                              "complete":page.complete,"cursor_total":page.cursor_total,"cursor_page_size":page.cursor_page_size,
                              "completion_basis":page.completion_basis})
        atomic_json(directory / f"{page.query.offset}.json", receipt)
        new = {**state, "last_receipt": receipt["state_sha256"], "page_count": state["page_count"] + 1,
               "next_offset": page.next_offset, "complete": page.complete, "failure_code": None,
               "row_count": state["row_count"] + len(page.rows),
               "http_attempt_count": state["http_attempt_count"] + attempts,
               "retry_count": state["retry_count"] + max(0, attempts - 1)}
        self.save_state(query, new)

    def validate(self, *, budget=None, deep=True):
        for query in discovery_queries(self.policy):
            if budget is not None:budget.check_time()
            state = self.state(query)
            prior_total=prior_size=None; prior_cursor=False
            entries=((page,receipt) for page,receipt in self.accepted(query,state)) if deep else ((None,receipt) for _,receipt in self.receipts(query,state,budget=budget))
            for page,receipt in entries:
                if budget is not None:budget.check_time()
                if prior_cursor and (receipt["cursor_total"]!=prior_total or receipt["cursor_page_size"]!=prior_size):
                    raise ValueError("CHECKPOINT_CURSOR_DRIFT")
                prior_cursor=receipt["cursor_total"] is not None
                prior_total=receipt["cursor_total"];prior_size=receipt["cursor_page_size"]
                if deep and query.family in ("MARKET","LISTING"):
                    try:
                        for row in page.rows:identity(query,row)
                    except HistoricalSourceError:raise ValueError("CHECKPOINT_SOURCE_BINDING_INVALID") from None
        return self.progress()

    def progress(self, *, status=None, attempts=0, pages=0, blockers=(), network=False):
        count = completed = absent = failed = accepted = requests = retries = 0
        for query in discovery_queries(self.policy):
            state = self.state(query); count += 1; completed += state["complete"]
            absent += query.family == "MARKET" and state["complete"] and state["row_count"] == 0
            failed += state["failure_code"] is not None
            accepted += state["page_count"]; requests += state["http_attempt_count"]; retries += state["retry_count"]
        return DiscoveryProgress(status=status or ("COMPLETE" if completed == count else "SOURCE_FAILED" if failed else "IN_PROGRESS"),
            scope_sha256=read_scope(self.policy), requested_partition_count=count, completed_partition_count=completed,
            absent_partition_count=absent, failed_partition_count=failed, incomplete_partition_count=count-completed,
            accepted_page_count=accepted, http_attempt_count=requests, retry_count=retries,
            execution_http_attempt_count=attempts, execution_page_count=pages, blockers=tuple(sorted(set(blockers))), network_access=network)

    def recheck(self, partition_sha256):
        found = False
        for query in discovery_queries(self.policy):
            if sha(query) == partition_sha256:
                state = self.state(query)
                # Preserve all old immutable generations and their pointer evidence.
                directory = self.directory(query); directory.mkdir(exist_ok=True)
                atomic_json(directory / f"generation-{state['generation']}.json", sign_state(state), immutable=True)
                new = self.state_template(query); new["generation"] = state["generation"] + 1
                self.save_state(query, new); found = True
        if not found: raise ValueError("CHECKPOINT_PARTITION_UNKNOWN")
        manifest = self.root / "manifest.json"
        if manifest.exists():
            value = read_json(manifest)
            atomic_json(self.root / ("manifest-" + value["source_manifest_sha256"] + ".json"), value, immutable=True)
            # Keep the old manifest, but it will fail current-head validation.
        return self.progress()


def run_discovery(*, source_client, policy, evidence_root, authorization, limits=None):
    limits = limits or DiscoveryLimits(); budget = RequestBudget(limits, monotonic=source_client.monotonic)
    try: check_authorization(policy, evidence_root, authorization, limits)
    except (ValueError, TypeError): return DiscoveryProgress(status="AUTHORIZATION_FAILED", blockers=("DISCOVERY_AUTHORIZATION_INVALID",))
    if authorization.probe_dates: return DiscoveryProgress(status="AUTHORIZATION_FAILED", blockers=("PROBE_AUTHORIZATION_NOT_DISCOVERY",))
    try:
        store = CheckpointStore(evidence_root, policy, create=True)
        with store.lock():
            store.validate(budget=budget,deep=False)  # Byte hashes preserve the validated publication contracts.
            for query in discovery_queries(policy):
                state = store.state(query)
                while not state["complete"]:
                    before = budget.attempts
                    try:
                        page = source_client.fetch_page(query.model_copy(update={"offset": state["next_offset"]}), budget=budget)
                        store.accept(query, state, page, budget.attempts-before)
                        budget.pages += 1; state = store.state(query)
                    except HistoricalSourceError as exc:
                        delta = budget.attempts-before
                        if exc.code == "DISCOVERY_BUDGET_EXHAUSTED":
                            state["http_attempt_count"] += delta; state["retry_count"] += max(0, delta-1)
                            store.save_state(query, state)
                            return store.progress(status="BUDGET_STOP", attempts=budget.attempts, pages=budget.pages, network=budget.attempts>0)
                        state.update(failure_code=exc.code, http_attempt_count=state["http_attempt_count"]+delta,
                                     retry_count=state["retry_count"]+max(0,delta-1))
                        store.save_state(query, state)
                        inconsistency = any(word in exc.code for word in ("CURSOR", "CHANGED", "OVERLAPPING", "BINDING", "INVALID", "MALFORMED"))
                        return store.progress(status="EVIDENCE_INCONSISTENT" if inconsistency else "SOURCE_FAILED",
                                              attempts=budget.attempts, pages=budget.pages, blockers=(exc.code,), network=budget.attempts>0)
            return store.progress(attempts=budget.attempts, pages=budget.pages, network=budget.attempts>0)
    except HistoricalSourceError as exc:
        if exc.code=="DISCOVERY_BUDGET_EXHAUSTED":
            return store.progress(status="BUDGET_STOP",attempts=budget.attempts,pages=budget.pages,network=budget.attempts>0)
        raise
    except (ValueError, OSError, TypeError, KeyError):
        return DiscoveryProgress(status="CHECKPOINT_INVALID", blockers=("DISCOVERY_CHECKPOINT_INVALID",),
                                 execution_http_attempt_count=budget.attempts, execution_page_count=budget.pages, network_access=budget.attempts>0)


def merge_files(paths, output):
    with output.open("w", encoding="ascii", newline="\n") as target:
        streams = [p.open("r", encoding="ascii") for p in paths]
        try:
            previous = None
            for line in heapq.merge(*streams):
                if line != previous: target.write(line)
                previous = line
        finally:
            for stream in streams: stream.close()


def sorted_index(records, destination, scratch):
    # Bounded external merge; no number-of-pages sized Python collection.
    runs = []; buffer = []; size = 0; number = 0
    for record in records:
        line = "".join(chunks(record)) + "\n"; buffer.append(line); size += len(line)
        if size >= 1024*1024:
            run = scratch / f"run-{number}"; number += 1
            run.write_text("".join(sorted(set(buffer))), encoding="ascii"); runs.append(run); buffer=[]; size=0
            if len(runs) == 32:
                merged = scratch / f"run-{number}"; number += 1
                merge_files(runs, merged)
                for p in runs: p.unlink()
                runs = [merged]
    if buffer:
        run = scratch / f"run-{number}"; run.write_text("".join(sorted(set(buffer))), encoding="ascii"); runs.append(run)
    merge_files(runs, destination)
    for p in runs: p.unlink()


def iterate_index(path):
    with path.open("rb") as stream:
        while True:
            line=stream.readline(LIMIT+1)
            if not line:break
            if len(line) > LIMIT: raise ValueError("DISCOVERY_INDEX_RECORD_TOO_LARGE")
            yield parse_json(line)


def finalize(store, *, publish=True):
    with store.lock():
        progress = store.validate(deep=False)
        if progress.status != "COMPLETE": raise ValueError("DISCOVERY_INCOMPLETE")
        with tempfile.TemporaryDirectory(prefix=".finalize-", dir=store.root) as temp:
            scratch = Path(temp)
            identity_path=None
            def records(kind):
                if kind in ("bindings","boards","secids"):
                    for item in iterate_index(identity_path):
                        if kind=="bindings":yield {"secid":item["secid"],"isin":item["isin"]}
                        elif kind=="secids":yield [item["secid"],item["isin"]]
                        elif item["board"] is not None:yield item["board"]
                    return
                for query in discovery_queries(store.policy):
                    state = store.state(query)
                    if kind == "partitions":
                        yield {"query": query, "head": state["last_receipt"], "pages": state["page_count"], "rows": state["row_count"]}
                        continue
                    if kind=="contents":
                        offset=0
                        for _,receipt in store.receipts(query,state,verify_files=False):
                            yield {"query":query.model_copy(update={"offset":offset}),"content_sha256":receipt["content_sha256"]}
                            offset=receipt["next_offset"]
                        continue
                    for page, receipt in store.accepted(query, state,deep=False):
                        if query.family in ("MARKET", "LISTING"):
                            for row in page.rows:
                                item = identity(query, row)
                                yield item
            hashes = {}; counts = {}; observed_dates = set(); listing = observed = unresolved = conflicts = 0
            for kind in ("identities", "bindings", "boards", "contents", "partitions", "secids"):
                output = scratch / (kind + ".ndjson")
                sorted_index(records(kind), output, scratch)
                digest = stream_hash(output); hashes[kind] = digest
                counts[kind] = sum(1 for _ in iterate_index(output))
                destination = safe_path(store.root / (digest + ".ndjson")) if publish else output
                if not publish:pass
                elif destination.exists():
                    if stream_hash(destination) != digest: raise ValueError("DISCOVERY_INDEX_CONFLICT")
                else:
                    with output.open("r+b") as stream: os.fsync(stream.fileno())
                    os.replace(output, destination)
                if kind == "identities":
                    identity_path=destination
                    for item in iterate_index(destination):
                        listing += item["authority"] == "SOURCE_LISTING"; observed += item["authority"] == "OBSERVED_HISTORY"
                if kind == "bindings": unresolved = sum(item["isin"] is None for item in iterate_index(destination))
                if kind == "secids":
                    previous_secid=None;first_isin=None;conflict=False
                    for secid,isin in iterate_index(destination):
                        if secid!=previous_secid:
                            conflicts+=conflict;first_isin=None;conflict=False;previous_secid=secid
                        if isin is not None:
                            if first_isin is not None and first_isin!=isin:conflict=True
                            first_isin=isin
                    conflicts+=conflict
            market_rows=actual=zero=unknown=priced=0
            for query in discovery_queries(store.policy):
                state = store.state(query)
                if query.family == "MARKET" and state["row_count"]: observed_dates.add(query.trade_date)
                if query.family=="MARKET":
                    for page,_ in store.accepted(query,state,deep=False):
                        for row in page.rows:
                            market_rows+=1
                            native={k.upper():v for k,v in row.items()}
                            try:
                                trades=Decimal(str(native.get("NUMTRADES")))
                                if not trades.is_finite() or trades<0 or trades!=trades.to_integral_value():raise ValueError()
                                actual+=trades>0;zero+=trades==0
                            except (ValueError,InvalidOperation):unknown+=1
                            values=normalize(query,row)["values"]
                            price=values.get("clean_price") if values.get("clean_price") is not None else values.get("price")
                            nkd=values.get("nkd")
                            try:
                                p=Decimal(str(price));n=Decimal(str(nkd))
                                priced+=p.is_finite() and p>0 and n.is_finite() and n>=0
                            except InvalidOperation:pass
            manifest = signed(DiscoveryManifestIndex, "source_manifest_sha256", policy=store.policy,
                scope_sha256=read_scope(store.policy), implementation_sha256=fingerprint(),
                source_content_sha256=sha({"policy":store.policy,"contents":hashes["contents"],"identities":hashes["identities"]}),
                completed_partition_count=progress.completed_partition_count, absent_partition_count=progress.absent_partition_count,
                source_page_count=progress.accepted_page_count, identity_count=counts["identities"], exact_binding_count=counts["bindings"],
                listing_identity_count=listing, observed_identity_count=observed, board_count=counts["boards"],
                unresolved_binding_count=unresolved,identity_conflict_secid_count=conflicts,
                market_row_count=market_rows,actual_trade_row_count=actual,zero_trade_row_count=zero,
                unknown_trade_row_count=unknown,usable_price_row_count=priced,
                observed_dates=tuple(sorted(observed_dates)), index_sha256s=hashes)
            if publish:atomic_json(store.root / "manifest.json", manifest)
            return manifest


class VerifiedDiscoveryIndex:
    """Validated view for read-only acquisition planning; never an APPLY grant."""
    def __init__(self, root, policy):
        self.store = CheckpointStore(root, policy)
        self.manifest = DiscoveryManifestIndex.model_validate_json(json.dumps(read_json(self.store.root / "manifest.json")))
        checked(self.manifest, "source_manifest_sha256")
        if self.manifest.policy != policy or self.manifest.implementation_sha256 != fingerprint(): raise ValueError("DISCOVERY_MANIFEST_SCOPE_INVALID")
        with self.store.lock():
            if self.store.validate(deep=False).status != "COMPLETE": raise ValueError("DISCOVERY_INCOMPLETE")
            for kind, digest in self.manifest.index_sha256s.items():
                if kind not in ("identities", "bindings", "boards", "contents", "partitions", "secids") or len(digest)!=64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("DISCOVERY_INDEX_HASH_INVALID")
                if stream_hash(safe_path(self.store.root / (digest + ".ndjson"))) != digest: raise ValueError("DISCOVERY_INDEX_TAMPERED")
            if set(self.manifest.index_sha256s) != {"identities", "bindings", "boards", "contents", "partitions", "secids"}: raise ValueError("DISCOVERY_INDEX_MISSING")
            recorded = iterate_index(self.path("partitions"))
            # Compare exact membership, not merely counts; sorted external hashes avoid retaining heads.
            expected = sorted(("".join(chunks({"query":q,"head":self.store.state(q)["last_receipt"],
                            "pages":self.store.state(q)["page_count"],"rows":self.store.state(q)["row_count"]}))
                            for q in discovery_queries(policy)))
            if expected != ["".join(chunks(r)) for r in recorded]: raise ValueError("DISCOVERY_CURRENT_HEAD_DRIFT")
        self.policy = policy; self.source_manifest_sha256 = self.manifest.source_manifest_sha256
        if finalize(self.store,publish=False)!=self.manifest:
            raise ValueError("DISCOVERY_MANIFEST_REPLAY_MISMATCH")
        self.status = "COMPLETE"

    def path(self, kind): return safe_path(self.store.root / (self.manifest.index_sha256s[kind] + ".ndjson"))

    @property
    def securities(self):
        return (HistoricalSecurity.model_validate_json(json.dumps(item)) for item in iterate_index(self.path("identities")))


def probe(*, source_client, policy, evidence_root, authorization, limits=None):
    limits = limits or DiscoveryLimits(); budget = RequestBudget(limits, monotonic=source_client.monotonic)
    completed=[]; equal=[]; different=[]
    try:
        check_authorization(policy,evidence_root,authorization,limits)
        if not authorization.probe_dates: raise ValueError("PROBE_DATES_REQUIRED")
    except (ValueError,TypeError): return DiscoveryProbeResult(status="AUTHORIZATION_FAILED",blockers=("DISCOVERY_PROBE_AUTHORIZATION_INVALID",))
    try:
        store=CheckpointStore(evidence_root,policy,create=True)
        with store.lock(), tempfile.TemporaryDirectory(prefix=".probe-",dir=store.root) as temp:
            scratch=Path(temp)
            for day in authorization.probe_dates:
                query=next(q for q in discovery_queries(policy) if q.trade_date==day)
                hashes=[]
                for supplied in (True,False):
                    offset=0; previous_total=None; previous_size=None
                    target=scratch/f"{day}-{supplied}.ndjson"
                    comparison_store=CheckpointStore(scratch/f"evidence-{day}-{supplied}",policy,create=True)
                    def rows():
                        nonlocal offset,previous_total,previous_size
                        while True:
                            page=source_client.fetch_page(query.model_copy(update={"offset":offset}),budget=budget,include_numtrades=supplied)
                            state=comparison_store.state(query)
                            comparison_store.accept(query,state,page,page.attempts)
                            budget.pages+=1
                            if page.query!=query.model_copy(update={"offset":offset}):raise HistoricalSourceError("SOURCE_QUERY_BINDING_CONFLICT")
                            if previous_total is not None and (page.cursor_total!=previous_total or page.cursor_page_size!=previous_size):raise HistoricalSourceError("SOURCE_CURSOR_CHANGED")
                            previous_total=page.cursor_total;previous_size=page.cursor_page_size
                            for row in page.rows:identity(query,row);yield row
                            if page.complete:return
                            offset=page.next_offset
                    sorted_index(rows(),target,scratch)
                    hashes.append(stream_hash(target))
                completed.append(day)
                (equal if hashes[0]==hashes[1] else different).append(day)
            result=DiscoveryProbeResult(status="COMPLETE",scope_sha256=read_scope(policy),compared_dates=tuple(completed),
                equal_population_dates=tuple(equal),different_population_dates=tuple(different),http_attempt_count=budget.attempts,
                page_count=budget.pages,network_access=budget.attempts>0)
            atomic_json(store.root/("probe-"+sha(result)+".json"),result,immutable=True)
            return result
    except HistoricalSourceError as exc:
        return DiscoveryProbeResult(status="BUDGET_STOP" if exc.code=="DISCOVERY_BUDGET_EXHAUSTED" else "SOURCE_FAILED",
            compared_dates=tuple(completed),equal_population_dates=tuple(equal),different_population_dates=tuple(different),
            http_attempt_count=budget.attempts,page_count=budget.pages,blockers=(exc.code,),network_access=budget.attempts>0)
    except (ValueError,OSError,TypeError):return DiscoveryProbeResult(status="CHECKPOINT_INVALID",blockers=("DISCOVERY_PROBE_CHECKPOINT_INVALID",),network_access=budget.attempts>0)
