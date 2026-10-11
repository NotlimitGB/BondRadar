# Task307 — Historical discovery coverage, universe and PIT evidence audit v1

## 1. Purpose and delivery boundary

Task307 audits existing finalized Task306A4 files. It performs no acquisition,
application DB connection, projection, scoring, replay, P&L, deployment or Day 0.
Starting main: `1903b8371e5455b4f39bb3345dee378406aa1c59`. Only six new files are
added: schema, read-only reader/verifier, analytics service, REPORT runner,
focused tests and this document. Existing source fingerprint files stay intact.

## 2. Operator-supplied context, not independently inspected evidence

The operator reported discovery for 2020-09-01–2026-09-30: 2,224 requested and
completed partitions; no incomplete/failed partitions; 41,499 pages; 41,579 HTTP
attempts; retry count 6. Validation and finalization were reported PASS.

Manifest: `b7a55de16a510b798e1d8da5f72ce757215257c86c9ee8b34afcf67a2783bce7`.
Source content: `731aa5e01d44576665aba5b961cbc9740fb931d6d7a47a029b977d81e1de16c4`.
Reported identity records 24,241 = 16,280 listing + 7,961 observed history;
25 boards; 3,987,965 market rows; 2,131,304 actual-trade rows; 1,636,330 zero-trade;
220,331 unknown; 2,641,648 usable price/NKD rows; 10,671 unresolved bindings;
zero SECID conflicts; 1,546 observed dates. Archive approximately 4.7 GB; free
space approximately 14 GB on a production-colocated 50 GB-class filesystem.

These are reconciliation references for the future operator run, never algorithm
constants or results claimed by this development task. Codex did not access VDS.

## 3. API, contracts and execution statuses

`HistoricalDiscoveryCoverageAuditService.build(*, evidence_root, scratch_dir,
expected_manifest_sha256=None, limits=...)` returns the strict frozen
`historical-discovery-coverage-audit-v1` contract. Collections are tuples; unknown
fields/types are rejected. COMPLETE means diagnostics and full accepted-generation
integrity checks completed. BLOCKED has stable nullable sections, typed blockers,
no partial authoritative metrics and a deterministic hash.

Integrity, historical completeness, PIT and acquisition readiness are separate.
The last three remain false on every path. Safety declarations reject numeric
boolean substitutes. Generated clocks, paths, runtime, resource counters and free
space do not enter the semantic hash; source observation timestamps are provenance.

## 4. Source-only trust boundary

The service needs no DATABASE_URL, database credentials or API token. SQLite is
private disposable aggregation storage, not the application DB. Source paths are
read-only. The reader reuses only A4 reading methods, schemas and pure functions;
it does not instantiate its writable store or invoke lock/finalize/discovery/
acquisition/execution. Imported existing mappers are called only as pure parsers;
no service constructor, Session or client is created.

No source artifact, lock file, checkpoint, index or scratch is published inside
the evidence root. Source/output/scratch directories must be disjoint, including
ancestor relationships. Symlinks and Windows reparse points are rejected.

## 5. Full accepted-generation integrity

Validate manifest version/schema/canonical SHA, optional expected SHA, scope and
the unchanged A4 implementation fingerprint. The Git SHA and content fingerprint
are different identities. Validate all six sorted canonical NDJSON index files
and hashes, then reconstruct their complete contents in scratch from accepted
pages. Reconstructed index hashes and source-content hash must match exactly.

Check every current partition state, exact query scope, generation, completeness,
receipt hash chain, offsets, page byte hash, canonical page/content hashes, UTC
observation binding, cursor mode/TOTAL/PAGESIZE consistency and exact source
SECID/date/board bindings. Duplicate exact rows within a listing/market partition
are rejected with a disk-backed uniqueness check. Distinct contradictory identity
rows remain evidence. Missing or incomplete evidence is never an empty substitute.

Reconcile all manifest population, page, empty-date, board, market, trade and
usable-price counts plus observed dates. At completion recheck manifest, scope and
every partition pointer. Old generations and unaccepted crash orphans are outside
the accepted manifest, not silently merged. Prior operator PASS is not authority.

Read-only mounting and absence of a simultaneous writer remain operator
requirements. Hashes detect tampering; they do not authenticate a publisher,
prove completeness or defend against an adversarial same-owner mutable filesystem.

## 6. Exact identities and survivorship

Identity records include authority, board and optional interval and are not
unique Bond counts. Report exact SECID, nonnull ISIN, SECID+ISIN (null preserved),
SECID+board and listing/observed pair membership. The unresolved definition is
DISTINCT_EXACT_SECID_PLUS_NULL_ISIN, matching A4 bindings; affected SECIDs and
listing/history record counts are separate.

Each exact pair has listing/observed boards, first/last history observation,
earliest supplied listing start/latest supplied listing end, observed/actual/
usable dates and conflicting nonnull ISIN candidates. Missing interval dates
stay null; min/max do not prove continuity. Different pairs for one SECID can
identify listing/market discrepancies without resolving either from current data.
Pairs absent on the last observed date are diagnostic only, not proven extinct,
delisted, defaulted or matured. Historical universe membership remains unproven.

## 7. Market values and denominators

All row metrics use the complete MARKET row denominator. Trade categories are
mutually exclusive: finite nonnegative integer actual/zero, missing (including
blank), malformed/bool, negative, fractional and nonfinite strings. Their sum
equals total; the last five reconcile to A4 unknown. Missing values never become
zero. Nonfinite JSON numbers are rejected at source contract verification.

Reuse the existing pure market normalization and Task294 NKD aliases without
new hierarchy or formulas. Strict clean price means mapped legal close price is
finite and positive. Missing/invalid strict clean and NKD source assertions have
separate counts. The manifest-compatible price metric uses mapped clean price if
nonnull, otherwise mapped price; a nonpositive nonnull clean price remains
unusable rather than receiving a convenient fallback. NKD must be finite and
nonnegative, including zero. Invalid/conflicting aliases remain unavailable.

Actual+usable, zero+usable and unknown+usable intersections are row intersections,
not joins of unrelated observations. Identity/date flags retain existence of a
qualifying row; actual+usable requires both in the same row. These observations
do not establish bid/ask, order depth, realistic fills, liquidity score or costs.

## 8. Date and board coverage

Report all observed/actual/usable/joint/unknown dates, annual/monthly row and
distinct-pair counts, and valid empty MARKET partitions. Empty calendar dates do
not establish holidays, market closure or global absence of securities.
Every observed listing/history board is retained, including listing-only boards
with zero market coverage. Board execution eligibility is UNVERIFIED, without
current-primary-board selection or ranking.

## 9. Frozen research schedule and descriptive horizons

Emit all 60 entry months October 2020–September 2025. Entry is the first persisted
source MARKET date of its month, without moving to better model/price evidence.
No observed date leaves a NO_ENTRY_DATE row. Include row/identity/actual/usable/
joint counts for the fixed date.

For 90/180/365 days separately, describe whether global observations reach the
target and how many exact entry pairs have any subsequent observation in
(T,T+H]. Counts do not require daily continuity, freshness or a realized exit.
Target beyond source cutoff is BEYOND_CUTOFF with null coverage count; never
shorten the horizon. This table does not establish economic outcome readiness.

## 10. PIT matrix and source families

Decision cutoff stays 00:00 UTC T: decision market inputs must be <T. DATE-only
publication D needs D+1 availability authority. Historical trade date, supplied
listing/effective interval and actual retrieval timestamp are separate facts;
none alone proves historical publication/version availability. Older effective
dates do not backdate facts first observed later. The report preserves first/last
actual source observation timestamps, not the current audit clock.

The matrix covers market history, universe membership, security identifiers,
issuer linkage, contractual cashflows, coupon/amortization/redemption, ratings,
financial statements, default/credit events and bid/ask/execution costs.
Only DATES/COLUMNS/LISTING/MARKET are accepted discovery families. Reference,
description and cashflow query generators are not acquired evidence. Other
families are NOT_ACQUIRED, not current values substituted as historical facts.
No family is promoted to publication-proven or PIT_READY by Task307.

## 11. OFZ source inventory

Use the existing canonical SU SECID-or-ISIN helper. Names do not classify OFZ.
Report OFZ rows, actual trades, usable price/NKD, exact canonical/observed pairs
and observed dates. Current terms, coupon frequency, verified structure,
Task272 readiness, modified-duration matching and investable benchmark
eligibility are unproven/not evaluated. No curve, spread return, benchmark
performance or alpha calculation runs.

## 12. Prioritized roadmap

P0: source integrity, reconciliation or resource failure. No partial PASS.
P1: universe/survivorship, exact binding, historical publication and missing
reference/cashflows. Measured missing/conflicting populations are attached.
P2: execution feasibility and OFZ terms/duration prerequisites.
Recommendations specify missing proof, likely source family, reviewed request
preparation, validation and dependencies. They do not assert a source can deliver
the proof or authorize acquisition. Archive projection, qualified replay and
economic comparison remain later separately reviewed stages.

## 13. Resource envelope

Defaults: 4 GiB private scratch cap; 32 MiB combined report cap; 8 GiB remaining
filesystem reserve. Operator container: 2 GiB RAM / 2 CPU. Limits can be explicitly
configured; reserve=0 is used only in synthetic tests. Production instructions
retain 8 GiB. Exact identity output is additionally bounded at 100,000 records/pairs;
excess gives a resource refusal, not sampling or scope reduction.

Source pages are parsed one at a time (A4 8 MiB record limit). The raw archive is
not copied. SQLite stores canonical identity/page-content memberships, compact
identity-date flags, uniqueness and aggregates. Its cache is 2 MiB; temporary
SQL work is bounded by the identity/report limit and frozen date grid. SQLite
page allocation is capped; actual scratch bytes and remaining disk reserve are
checked per page and at aggregation/publication boundaries. Fixed report tables
and per-identity diagnostics are materialized only within the explicit limits.

This is cooperative resource enforcement, not an OS hard-quota substitute.
Allocation can briefly precede a filesystem reserve check; container memory and
operator scratch volume/quota remain external ceilings. Scratch cleans up after
normal completion and controlled failure. SIGKILL/power loss may leave a private
task307 directory; the operator may remove only that reviewed abandoned directory,
never source checkpoints. No implicit cleanup of existing temporary directories.

## 14. Serialization and publication

One existing canonical iterator supplies sorted keys, ASCII compact JSON,
exact Decimal/date encodings and SHA, excluding audit_sha256. No NaN/Infinity.
Absolute paths, durations, free space and runtime clock are not semantic fields.
Source implementation/content/manifest/scope hashes bind the report.

The runner writes task307-coverage-audit.json and task307-coverage-summary.md from
the validated contract. Both are staged and fsynced first; exclusive same-filesystem
hard links publish each artifact without overwrite, followed by directory fsync
on POSIX. COMPLETE stdout is emitted only after both publications. Controlled
failure removes only artifacts created by that invocation. Crash between the two
links is detected as an incomplete pair on the next run, not repaired silently.
Same source/audit and byte-identical reports are idempotent; a different source,
hash or summary is a conflict. Output filesystem must support atomic hard links.

## 15. Tests and measurement limitations

Hermetic finalized source fixtures exercise reconciliation, exact identities,
trade categories, strict/fallback price, NKD, dates, monthly horizons, OFZ and PIT.
Tampering tests cover manifest/scope/fingerprint/index/page/receipt/pointer and
reconstructed memberships. Read guards reject source writes even with elevated
test privileges. Budget and path failures preserve typed BLOCKED reports.

Generated scale fixture: 30 dates × 1,000 observations; 303 accepted pages,
1,000 exact pairs, no pre-built whole dataset. Processing/serialization Python
allocation peak is measured with tracemalloc and checked below 64 MiB. Fixtures
are not production RSS, realistic archive timing or independently proven source
coverage. Exact measurements and local command results are supplied in delivery.
Full backend tests belong to exact-commit CI; no production or live source test.

## 16. Future isolated operator execution — NOT EXECUTED

Prerequisites: independent code review, green exact-commit CI, separate operator
approval, a verified isolated checkout at the delivered Task307 SHA, and an
existing reviewed backend image ID/digest with compatible Python/Pydantic/httpx/
SQLAlchemy imports. If any prerequisite is missing, stop. Do not update running
production code, pull/build an image, migrate or pass application credentials.

Example (operator supplies reviewed SHA and existing image ID, not a mutable tag):

```bash
set -euo pipefail
: "${REVIEWED_SHA:?exact delivered Task307 commit required}"
: "${REVIEWED_IMAGE_ID:?existing reviewed image sha256 ID required}"
CODE_ROOT=/srv/bondradar-task307-reviewed
EVIDENCE_ROOT=/home/bondradar/.local/share/bondradar/evidence/task306a4-discovery
REPORT_ROOT=/var/tmp/bondradar-task307-reports
SCRATCH_ROOT=/var/tmp/bondradar-task307-scratch
EXPECTED_MANIFEST=b7a55de16a510b798e1d8da5f72ce757215257c86c9ee8b34afcf67a2783bce7
test "$(git -C "$CODE_ROOT" rev-parse HEAD)" = "$REVIEWED_SHA"
test -z "$(git -C "$CODE_ROOT" status --porcelain --untracked-files=no)"
test "$(id -u)" -ne 0
test -d "$EVIDENCE_ROOT" && test -d "$REPORT_ROOT" && test -d "$SCRATCH_ROOT"
# Pre-provision reports/scratch privately with ownership of the non-root operator.
# If paths use different filesystems, check each; preserve 8 GiB after 4 GiB scratch.
test "$(df -B1 --output=avail "$SCRATCH_ROOT" | tail -1)" -ge 12918456320
test "$(df -B1 --output=avail "$REPORT_ROOT" | tail -1)" -ge 8623489024
docker image inspect "$REVIEWED_IMAGE_ID" --format '{{.Id}}'
COMMON=(--rm --pull never --network none --read-only --cap-drop ALL
  --security-opt no-new-privileges --user "$(id -u):$(id -g)"
  --memory 2g --cpus 2 --pids-limit 128 --workdir /code
  --mount "type=bind,src=$CODE_ROOT,dst=/code,readonly"
  --env PYTHONPATH=/code/backend --env PYTHONDONTWRITEBYTECODE=1)
docker run "${COMMON[@]}" --entrypoint python "$REVIEWED_IMAGE_ID" -c \
  'from app.services.historical_discovery_coverage_reader import fingerprint; from app.schemas.historical_discovery_coverage_audit import AuditLimits; print("Task307 offline import compatibility PASS", fingerprint())'
# Use an operator-provisioned scratch quota/volume capped at 4 GiB; monitor df/du
# externally during execution. If such quota cannot be provisioned, STOP rather
# than claim an OS-enforced disk bound. Service checks are an additional safeguard.
docker run "${COMMON[@]}" \
  --mount "type=bind,src=$EVIDENCE_ROOT,dst=/evidence,readonly" \
  --mount "type=bind,src=$REPORT_ROOT,dst=/reports" \
  --mount "type=bind,src=$SCRATCH_ROOT,dst=/scratch" \
  --entrypoint python "$REVIEWED_IMAGE_ID" \
  /code/scripts/historical_discovery_coverage_audit.py --mode REPORT \
  --evidence-root /evidence --output-dir /reports --scratch-dir /scratch \
  --expected-manifest-sha256 "$EXPECTED_MANIFEST" \
  --max-scratch-bytes 4294967296 --max-output-bytes 33554432 --reserve-bytes 8589934592
```

The command prints canonical audit JSON and a short stderr summary, with no raw
logs or credentials. Existing reports from another manifest cause refusal. Free
space figures in this command are capacity prechecks, not inspected VDS facts.
No source root is mounted writable; production containers are untouched.

## 17. Delivery and next safe step

One commit `Add historical discovery coverage and PIT audit`; one ordinary push
after scope/remote checks; one exact-commit CI snapshot without waiting/polling.
Independent review and green CI precede separately authorized operator REPORT.
No automatic acquisition, projection, deployment, replay or official Day 0.

## 18. Observed local acceptance

Final new focused module: 33 passed in 122.21 seconds. The generated fixture
processed 30,000 rows / 303 pages / 1,000 exact pairs. Additional processing
Python allocations peaked at 8,590,999 bytes (approximately 8.19 MiB), measured
with tracemalloc; this is not production RSS. An earlier combined run exercised
Task307 and unchanged A4 checkpoint/runner tests: 48 passed, 1 skipped; the A4
slice contributed 18 passed and one Windows symlink-privilege skip. The skip
remains enabled for suitable CI platforms.

Two intermediate strict-JSON roundtrip/publication failures were diagnosed and
repaired with field-level safety validators; the final focused run is green.
Compile/import smoke and working/staged scope checks are required for delivery.
Full local backend tests were intentionally not run.

Commands from repository root (PYTHONPATH=backend; fresh --basetemp each run):

```text
python -m pytest -q -s backend/tests/test_historical_discovery_coverage_audit.py
python -m pytest -q backend/tests/test_historical_discovery_checkpoint.py backend/tests/test_historical_discovery_runner.py
python -m compileall -q backend/app/schemas/historical_discovery_coverage_audit.py backend/app/services/historical_discovery_coverage_reader.py backend/app/services/historical_discovery_coverage_audit.py scripts/historical_discovery_coverage_audit.py
git diff --check
git diff --cached --check
```

No source fingerprint, model, migration, existing execution path or production
artifact changed. Operator source metrics have not been independently verified
here; the future isolated REPORT is the separately authorized handoff.
