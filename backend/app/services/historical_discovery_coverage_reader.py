"""Read-only A4 verification and private, bounded disk aggregation."""
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from app.schemas.historical_discovery_checkpoint import DiscoveryManifestIndex, DiscoverySourcePage
from app.services.historical_discovery_checkpoint import (
    CheckpointStore, VERSION, safe_path, read_json, parse_json, verify_state,
    fingerprint, stream_hash, content_hash, identity,
)
from app.services.historical_evidence_discovery import discovery_queries, read_scope
from app.services.historical_evidence_normalization import sha, checked, LIMIT, hash_syntax
from app.services.historical_audit_canonical_json import chunks


KINDS = ("identities", "bindings", "boards", "contents", "partitions", "secids")


class AuditFailure(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def disjoint(left, right):
    if left == right or left in right.parents or right in left.parents:
        raise AuditFailure("AUDIT_PATHS_OVERLAP")


def canonical(value):
    return "".join(chunks(value))


class Budget:
    def __init__(self, root, limits):
        self.root, self.limits = root, limits

    def check(self):
        size = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        if size > self.limits.scratch_bytes:
            raise AuditFailure("SCRATCH_BUDGET_EXCEEDED")
        if shutil.disk_usage(self.root).free < self.limits.reserve_bytes:
            raise AuditFailure("FREE_SPACE_RESERVE_NOT_MET")


@contextmanager
def workspace(evidence_root, scratch_dir, limits):
    source, scratch = safe_path(evidence_root), safe_path(scratch_dir)
    disjoint(source, scratch)
    if not source.is_dir() or not scratch.is_dir():
        raise AuditFailure("AUDIT_DIRECTORY_MISSING")
    if shutil.disk_usage(scratch).free < limits.reserve_bytes:
        raise AuditFailure("FREE_SPACE_RESERVE_NOT_MET")
    with tempfile.TemporaryDirectory(prefix="task307-", dir=scratch) as name:
        root = Path(name)
        budget = Budget(root, limits)
        db = sqlite3.connect(root / "aggregation.sqlite")
        try:
            # Only disposable scratch is writable; no durable transaction or DB URL.
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-2048")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute(f"PRAGMA max_page_count={max(1, limits.scratch_bytes // 4096)}")
            db.executescript("""
                CREATE TABLE canonical(kind TEXT,line TEXT,PRIMARY KEY(kind,line)) WITHOUT ROWID;
                CREATE TABLE seen(hash TEXT PRIMARY KEY) WITHOUT ROWID;
                CREATE TABLE binding(key TEXT PRIMARY KEY,secid TEXT,isin TEXT,listing INTEGER DEFAULT 0,
                    observed INTEGER DEFAULT 0,first TEXT,last TEXT,start TEXT,end TEXT) WITHOUT ROWID;
                CREATE INDEX binding_secid ON binding(secid,isin);
                CREATE TABLE identity(line TEXT PRIMARY KEY,secid TEXT,isin TEXT,board TEXT,authority TEXT) WITHOUT ROWID;
                CREATE TABLE identity_board(key TEXT,board TEXT,authority TEXT,PRIMARY KEY(key,board,authority)) WITHOUT ROWID;
                CREATE TABLE daily(day TEXT,key TEXT,actual INTEGER,usable INTEGER,joint INTEGER,
                    PRIMARY KEY(day,key)) WITHOUT ROWID;
                CREATE TABLE observations(key TEXT,day TEXT,board TEXT,PRIMARY KEY(key,day,board)) WITHOUT ROWID;
                CREATE TABLE board_identity(board TEXT,key TEXT,PRIMARY KEY(board,key)) WITHOUT ROWID;
                CREATE TABLE board_date(board TEXT,day TEXT,PRIMARY KEY(board,day)) WITHOUT ROWID;
                CREATE TABLE ofz_identity(key TEXT PRIMARY KEY) WITHOUT ROWID;
                CREATE TABLE stats(day TEXT,board TEXT,total INTEGER,actual INTEGER,zero INTEGER,
                    missing INTEGER,malformed INTEGER,negative INTEGER,fractional INTEGER,nonfinite INTEGER,
                    clean INTEGER,nkd INTEGER,usable INTEGER,joint INTEGER,usablezero INTEGER,
                    usableunknown INTEGER,ofz INTEGER,ofzactual INTEGER,ofzusable INTEGER,
                    cleanmissing INTEGER,cleaninvalid INTEGER,nkdmissing INTEGER,nkdinvalid INTEGER,
                    PRIMARY KEY(day,board)) WITHOUT ROWID;
            """)
            yield source, db, budget
        finally:
            db.close()


class ReadOnlyPartitions:
    """Reuse only A4 reading methods, without constructing or locking its store."""
    state = CheckpointStore.state
    state_template = staticmethod(CheckpointStore.state_template)
    receipts = CheckpointStore.receipts

    def __init__(self, root, policy):
        self.root, self.policy = root, policy

    def directory(self, query):
        return safe_path(self.root / sha(query))


class CoverageReader:
    def __init__(self, root, db, budget, *, expected_manifest_sha256=None):
        self.root, self.db, self.budget = root, db, budget
        raw = read_json(root / "manifest.json")
        self.manifest = DiscoveryManifestIndex.model_validate_json(json.dumps(raw))
        checked(self.manifest, "source_manifest_sha256")
        if expected_manifest_sha256 is not None:
            hash_syntax(expected_manifest_sha256)
            if self.manifest.source_manifest_sha256 != expected_manifest_sha256:
                raise AuditFailure("EXPECTED_MANIFEST_SHA_MISMATCH")
        m = self.manifest
        if m.policy.cutoff!=date(2026,9,30) or m.policy.horizons!=(90,180,365) or m.policy.entry_span!=(date(2020,10,1),date(2025,9,1)):
            raise AuditFailure("SOURCE_RESEARCH_POLICY_INCOMPATIBLE")
        if m.scope_sha256 != read_scope(m.policy) or m.implementation_sha256 != fingerprint():
            raise AuditFailure("SOURCE_SCOPE_OR_FINGERPRINT_MISMATCH")
        if any(getattr(m, k) is not False for k in ("pit_ready", "db_mutation", "historical_universe_complete", "acquisition_ready")):
            raise AuditFailure("SOURCE_READINESS_DECLARATION_INVALID")
        scope = verify_state(read_json(root / "scope.json"))
        expected = {"contract_version": VERSION, "policy": m.policy,
                    "scope_sha256": m.scope_sha256, "implementation_sha256": m.implementation_sha256}
        if scope != parse_json(canonical({**expected, "state_sha256": sha(expected)})):
            raise AuditFailure("SOURCE_SCOPE_OR_FINGERPRINT_MISMATCH")
        if set(m.index_sha256s) != set(KINDS):
            raise AuditFailure("SOURCE_INDEX_MEMBERSHIP_INVALID")
        for kind in KINDS:
            digest = m.index_sha256s[kind]
            hash_syntax(digest)
            path = safe_path(root / (digest + ".ndjson"))
            h, previous = hashlib.sha256(), None
            with path.open("rb") as stream:
                while True:
                    line = stream.readline(LIMIT + 1)
                    if not line:
                        break
                    if len(line) > LIMIT or not line.endswith(b"\n") or (previous is not None and line <= previous):
                        raise AuditFailure("SOURCE_INDEX_STRUCTURE_INVALID")
                    parsed = parse_json(line)
                    if line != (canonical(parsed) + "\n").encode("ascii"):
                        raise AuditFailure("SOURCE_INDEX_STRUCTURE_INVALID")
                    h.update(line)
                    previous = line
            if h.hexdigest() != digest:
                raise AuditFailure("SOURCE_INDEX_HASH_MISMATCH")
        self.store = ReadOnlyPartitions(root, m.policy)
        self.manifest_bytes_sha = stream_hash(root / "manifest.json")
        self.scope_bytes_sha = stream_hash(root / "scope.json")
        self.states = []
        self.pages = self.http_attempts = self.retries = 0
        self.binding_rows = self.identity_rows = 0
        self.empty_dates = []
        self.observation_first = self.observation_last = None

    def index(self, kind, value):
        self.db.execute("INSERT OR IGNORE INTO canonical VALUES (?,?)", (kind, canonical(value) + "\n"))

    def binding(self, item, day):
        key = canonical([item.secid, item.isin])
        if not self.db.execute("SELECT 1 FROM binding WHERE key=?",(key,)).fetchone():
            self.binding_rows+=1
        d = str(day) if day else None
        self.db.execute("""INSERT INTO binding(key,secid,isin,listing,observed,first,last,start,end)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET
            listing=max(listing,excluded.listing),observed=max(observed,excluded.observed),
            first=CASE WHEN first IS NULL THEN excluded.first WHEN excluded.first IS NULL THEN first ELSE min(first,excluded.first) END,
            last=CASE WHEN last IS NULL THEN excluded.last WHEN excluded.last IS NULL THEN last ELSE max(last,excluded.last) END,
            start=CASE WHEN start IS NULL THEN excluded.start WHEN excluded.start IS NULL THEN start ELSE min(start,excluded.start) END,
            end=CASE WHEN end IS NULL THEN excluded.end WHEN excluded.end IS NULL THEN end ELSE max(end,excluded.end) END""",
            (key,item.secid,item.isin,int(item.authority=="SOURCE_LISTING"),int(item.authority=="OBSERVED_HISTORY"),
             d,d,str(item.interval_start) if item.interval_start else None,str(item.interval_end) if item.interval_end else None))
        if item.board is not None:
            self.db.execute("INSERT OR IGNORE INTO identity_board VALUES (?,?,?)", (key,item.board,item.authority))
        return key

    def scan(self, market_consumer):
        for query in discovery_queries(self.manifest.policy):
            pointer = self.store.directory(query) / "state.json"
            if not pointer.is_file():
                raise AuditFailure("SOURCE_PARTITION_STATE_MISSING")
            state = self.store.state(query)
            if not state["complete"] or state["failure_code"] is not None or state["page_count"] < 1:
                raise AuditFailure("SOURCE_PARTITION_NOT_COMPLETE")
            self.states.append((pointer, stream_hash(pointer)))
            self.http_attempts += state["http_attempt_count"]
            self.retries += state["retry_count"]
            if query.family == "MARKET" and not state["row_count"]:
                self.empty_dates.append(query.trade_date)
            self.index("partitions", {"query":query,"head":state["last_receipt"],"pages":state["page_count"],"rows":state["row_count"]})
            self.db.execute("DELETE FROM seen")
            prior_mode = prior_total = prior_size = None
            for path, receipt in self.store.receipts(query, state, verify_files=False):
                safe_path(path)
                with path.open("rb") as stream:
                    content = stream.read(LIMIT + 1)
                if len(content) > LIMIT or hashlib.sha256(content).hexdigest() != receipt["file_sha256"]:
                    raise AuditFailure("SOURCE_PAGE_BYTES_INVALID")
                parse_json(content)
                page = DiscoverySourcePage.model_validate_json(content)
                checked(page, "page_sha256")
                self.observation_first=min(self.observation_first,page.observed_at) if self.observation_first else page.observed_at
                self.observation_last=max(self.observation_last,page.observed_at) if self.observation_last else page.observed_at
                if sha(page.query) != receipt["query_sha256"] or page.page_sha256 != receipt["page_sha256"] or content_hash(page) != receipt["content_sha256"]:
                    raise AuditFailure("SOURCE_PAGE_BINDING_INVALID")
                if page.query.model_copy(update={"offset":0}) != query:
                    raise AuditFailure("SOURCE_PAGE_QUERY_INVALID")
                actual = (len(page.rows),page.next_offset,page.complete,page.cursor_total,page.cursor_page_size,page.completion_basis,page.observed_at.isoformat())
                expected = (receipt["row_count"],receipt["next_offset"],receipt["complete"],receipt["cursor_total"],receipt["cursor_page_size"],receipt["completion_basis"],page.observed_at.isoformat())
                # Timestamp equality is semantic UTC equality, not textual Z formatting.
                from datetime import datetime
                if type(receipt["observed_at"]) is not str:
                    raise AuditFailure("SOURCE_RECEIPT_TIMESTAMP_INVALID")
                if actual != expected or datetime.fromisoformat(receipt["observed_at"].replace("Z","+00:00")) != page.observed_at:
                    raise AuditFailure("SOURCE_RECEIPT_PAGE_MISMATCH")
                if prior_mode is not None and (page.completion_basis,page.cursor_total,page.cursor_page_size) != (prior_mode,prior_total,prior_size):
                    raise AuditFailure("SOURCE_CURSOR_DRIFT")
                prior_mode,prior_total,prior_size = page.completion_basis,page.cursor_total,page.cursor_page_size
                self.index("contents", {"query":page.query,"content_sha256":receipt["content_sha256"]})
                for row in page.rows:
                    if query.family in ("MARKET","LISTING"):
                        try:
                            self.db.execute("INSERT INTO seen VALUES (?)", (sha(row),))
                        except sqlite3.IntegrityError:
                            raise AuditFailure("SOURCE_DUPLICATE_PARTITION_ROW") from None
                        try:
                            item = identity(query, row)
                        except Exception:
                            raise AuditFailure("SOURCE_IDENTITY_BINDING_INVALID") from None
                        line = canonical(item)
                        self.index("identities", item)
                        self.identity_rows+=self.db.execute("INSERT OR IGNORE INTO identity VALUES (?,?,?,?,?)", (line,item.secid,item.isin,item.board,item.authority)).rowcount
                        key = self.binding(item, query.trade_date)
                        if query.family == "MARKET":
                            market_consumer(query, row, item, key)
                self.pages += 1
                if max(self.binding_rows,self.identity_rows) > self.budget.limits.max_identity_rows:
                    raise AuditFailure("IDENTITY_OUTPUT_RESOURCE_LIMIT")
                self.budget.check()
            self.db.commit()  # Disposable scratch index only, never source/application state.
        for secid, isin, board in self.db.execute("SELECT secid,isin,board FROM identity"):
            self.index("bindings", {"secid":secid,"isin":isin})
            self.index("secids", [secid,isin])
            if board is not None:
                self.index("boards", board)
        hashes = {}
        for kind in KINDS:
            h = hashlib.sha256()
            for (line,) in self.db.execute("SELECT line FROM canonical WHERE kind=? ORDER BY line", (kind,)):
                h.update(line.encode("ascii"))
            hashes[kind] = h.hexdigest()
        if hashes != self.manifest.index_sha256s:
            raise AuditFailure("SOURCE_INDEX_REPLAY_MISMATCH")
        if sha({"policy":self.manifest.policy,"contents":hashes["contents"],"identities":hashes["identities"]}) != self.manifest.source_content_sha256:
            raise AuditFailure("SOURCE_CONTENT_SHA_MISMATCH")
        self.budget.check()

    def finish(self):
        if stream_hash(safe_path(self.root / "manifest.json")) != self.manifest_bytes_sha or stream_hash(safe_path(self.root / "scope.json")) != self.scope_bytes_sha:
            raise AuditFailure("SOURCE_CHANGED_DURING_AUDIT")
        for path, digest in self.states:
            if stream_hash(safe_path(path)) != digest:
                raise AuditFailure("SOURCE_CHANGED_DURING_AUDIT")
