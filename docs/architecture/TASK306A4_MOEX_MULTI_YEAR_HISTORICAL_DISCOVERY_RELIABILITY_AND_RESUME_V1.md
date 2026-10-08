# Task306A4 — MOEX historical discovery reliability and resume

## 1. Purpose and research boundary

Extend Task306A3 discovery, independently of archive acquisition and canonical
projection. Frozen evidence range: 2020-09-01 through 2026-09-30. Primary horizon
365 days; secondary horizons 90/180. Entry policy remains the first observed
MOEX trade date per calendar month, with October 2020–September 2025 as the
approximately 60-slot research target. Missing evidence never shortens scope.

## 2. Baseline

Starting main: `01ce220db71b8fa122c5dc751af85bdb949aaff8`. Exact CI run
37670212286 and runner-image run 37670212169 succeeded. Alembic head remains
202610070001. The provided old production revision is context only; no VDS
inspection was performed.

## 3. Confirmed defects versus hypotheses

Existing discovery returned only after the full traversal, retained every page
hash and identity, and lost its temporary pagination index on interruption.
Failures contained family/code but no durable date/offset checkpoint. Runner
artifacts were capped at 8 MiB and output publication was not atomic.

The allegation that `numtrades=0` excludes zero-trade securities is not confirmed.
Source revisions, actual multi-year coverage and production memory remain
unverified; synthetic examples do not establish these properties.

## 4. Official source contracts

[Market history](https://iss.moex.com/iss/reference/497) documents date, start,
limit, history.cursor and a minimum-trades filter whose default is zero.
It explicitly warns that marketprice_board uses the current main board.
[Listing](https://iss.moex.com/iss/reference/489) documents status=all and offset
pagination. [Dates](https://iss.moex.com/iss/reference/463) describes an interval,
not a paginated calendar. [Columns](https://iss.moex.com/iss/reference/441)
describes field metadata. Documentation was inspected without requesting live
historical observations.

## 5. Endpoint-specific completion

DATES/COLUMNS are single metadata responses at offset zero; cursor metadata is
unexpected. LISTING/MARKET cursor pages require exact integer INDEX/TOTAL/
PAGESIZE, consistent total/page size, exact row count and forward continuation.
An empty cursor result with TOTAL=0 is valid. Once cursor traversal starts,
cursor disappearance or contradictory offsets/counts blocks completion.

For non-cursor A4 pages, only a valid empty page terminates offset pagination.
A nonempty short page requires another request. A4 uses a distinct typed
DiscoverySourcePage; existing A3 acquisition pages retain their v1 short-page
contract and hashes. No source pagination correction silently changes existing
archive execution receipts.

## 6. Population integrity

Every calendar date is requested, including weekends. Valid empty dates are
source-absent partitions, never inferred holidays. Market observations require
exact requested TRADEDATE, nonblank SECID and board. All boards and zero/missing
NUMTRADES observations remain in evidence. Listing intervals and observed
identities are separate. Missing ISIN stays missing; multiple ISIN bindings for
a SECID are retained and counted. No current Bond table bounds discovery.

## 7. Source-only authorization

Strict frozen authorization binds policy SHA, canonical evidence root,
implementation fingerprint, exact execution limits and optional probe dates.
It grants DISCOVERY_READ only. Probe authorization cannot run discovery.
Changing limits requires a newly issued authorization. Authorization creation
itself makes no request. Existing ACQUIRE/APPLY authorization is untouched.

## 8. Durable filesystem layout

The explicitly authorized root contains scope.json, an OS writer lock and
query-hash directories. Each partition has an atomic state pointer, numbered
generations, immutable page files and chained receipts. A receipt binds page
SHA, stable content SHA, previous receipt and observation time. The pointer
records offset, rows, page count, completion, failure and attempt counters.

Publication order is page, receipt, then pointer. All published records are
fsynced; POSIX directory changes are also fsynced. A crash before the pointer
leaves unaccepted orphan evidence; it never advances progress. OS locks release
on process death. Filesystem power-loss durability depends on the underlying
filesystem; no distributed or adversarial-storage trust is claimed.

## 9. Integrity and resume

Before a network request, resume verifies scope/fingerprint, accepted page byte
hashes, exact query/offset bindings, receipt chain, totals and final pointer.
The receipt seals the bytes validated at publication. Explicit integrity audit
also reparses the full page contracts and source identities. The active partition
is reparsed for overlap checks; unrelated completed history is not economically
remapped on every resume.
Missing/tampered evidence blocks reuse. Accepted pages are not fetched again.
Exact overlaps are rejected; identity contradictions with different raw rows
remain visible. A failed partition is retried at its unaccepted offset and never
silently skipped. No source immutability is inferred from successful reuse.

## 10. Recheck and source revisions

Explicit partition recheck preserves the previous generation pointer and raw
pages and starts a new generation at zero. Unaffected partitions remain accepted.
Finalized manifests from old heads remain available but fail current-head
validation. Different source versions are never silently merged or overwritten.
Source TOTAL/page-size conflicts require operator analysis/recheck, not retries
that pretend the partition is empty.

## 11. Budgets and pacing

Defaults per invocation: 100 HTTP attempts, 100 accepted pages, 120 seconds.
Limits are exact positive integers with explicit upper bounds. All retries
consume request budget. Existing transient categories, maximum three attempts,
0.25/0.50-second delays, numeric Retry-After capped at 60 seconds, and minimum
0.5-second request-start spacing remain. HTTP-date Retry-After is not supported.

Deadline checks occur during checkpoint validation, before waits/requests and
while reading response chunks; HTTP timeout is capped by remaining time and 30
seconds. Finishing/publishing an accepted page can cross a deadline boundary;
this is cooperative bounded execution, not OS process preemption. Response
and individual checkpoint record limits are 8 MiB. Budget exhaustion preserves
accepted evidence and does not imply source completion.

## 12. Status and accounting

Progress statuses distinguish COMPLETE, IN_PROGRESS, BUDGET_STOP, SOURCE_FAILED,
CHECKPOINT_INVALID, AUTHORIZATION_FAILED and EVIDENCE_INCONSISTENT. Outputs keep
stable frozen types, PIT=false and DB_MUTATION=false on every failure path.
Progress reports requested/completed/incomplete/failed/absent partitions,
accepted pages, accumulated attempts/retries and current invocation attempts.
Exact query identity is available from each partition state and directory key.
The old in-memory discover API rejects ranges over 31 days and directs callers
to durable discovery; it cannot silently run the six-year scope.

## 13. Compact finalized manifest

Manifest index v1 contains policy, source and implementation hashes, observed
dates, partition/page counts, exact identity/binding/board counts, unresolved
bindings and SECID conflicts. Listing/observed identities are separate. Actual,
zero and unknown trade-count rows and usable clean-price/NKD evidence rows are
separate diagnostics, not investment eligibility. Inactive/delisted identity
context remains in raw listing evidence; current status is not backfilled.

Content-addressed NDJSON indexes hold identities, exact bindings, boards,
SECID bindings, stable page content and partition heads. Index hashes and a full
bounded replay against accepted source pages protect counts and memberships.
No millions-of-rows object graph or page-hash array is serialized into manifest.

## 14. Hash material

Reuse A2H canonical chunks: sorted keys, ASCII escaping, compact separators,
finite numeric values and exact Decimal strings. Stable source content excludes
fetch time/attempts but binds exact queries, complete raw rows and continuation.
Fetch receipts and full manifest preserve execution lineage separately. Content
is independent of row order; full execution identity can change on re-observation.
Canonical hashing is tamper detection, not a digital signature or historical
publication authority.

## 15. Memory and storage

Parse/validate one source page at a time. Duplicate checking reads accepted
partition pages sequentially. External sorting uses approximately 1 MiB runs
and at most 32 merge streams; identities are never truncated. Manifest output
uses the same canonical iterator as hashes. Scratch is private to the root and
cleaned; accepted pages, old generations and resumable pointers are preserved.
Disk use grows with retained evidence and source versions; no fixed disk-size
promise is made. Operators must provision and monitor evidence storage.

## 16. Path and process safety

Reject symlinks/reparse points and paths outside the explicit output root.
POSIX roots must be owned by the executing user and not group/world writable.
Windows uses extended local paths for long checkpoint names. Atomic temporary
files are private; reserved checkpoint filenames cannot be used as CLI output.
The root must be controlled by the operator; this is not protection against a
malicious process running as the same filesystem owner.

## 17. Bounded population probe

Probe compares complete row populations with explicit numtrades=0 and the
documented default request, under its own authorized date scope and budgets.
Different populations are reported without choosing the convenient result.
Malformed/overlapping/incomplete pages cannot produce a completed comparison.
Synthetic comparisons leave NUMTRADES_FILTER_SEMANTICS_VERIFIED=NOT_VERIFIED;
sample equivalence never proves complete historical universe membership.

## 18. Acquisition compatibility and readiness

VerifiedDiscoveryIndex validates the frozen manifest and source chain before
read-only planning. The planner adapter does not create a fake legacy COMPLETE
manifest, truncate identities or grant ACQUIRE authorization. Pending source
population authority is an explicit BLOCKED plan. Query materialization has
an explicit 32 MiB serialized-query ceiling and rejects excess, rather than
dropping scope. Archive executor, projection and migrations remain unchanged.

## 19. CLI modes

Existing runner adds DISCOVERY_POLICY, DISCOVERY_AUTHORIZE, DISCOVERY_PROBE,
DISCOVER, DISCOVERY_RESUME, DISCOVERY_PROGRESS, DISCOVERY_VALIDATE,
DISCOVERY_RECHECK, DISCOVERY_FINALIZE and DISCOVERY_SUMMARY. REPORT with an
evidence root inspects source progress. An unconfigured default REPORT does not
load DATABASE_URL. Explicit existing archive run reports remain available.
Summary goes to stderr; machine-readable output stays canonical JSON.

## 20. Future operator commands — not executed here

Run only after independent review, green exact CI and separate operator source-
read authorization. Provision a verified isolated code checkout and Python
environment beforehand; do not replace the running backend, migrate production
or use production DATABASE_URL. Replace CODE_ROOT with that isolated checkout.

```bash
CODE_ROOT=/srv/bondradar-discovery-task306a4
EVIDENCE_ROOT=/var/tmp/bondradar-task306a4
PYTHON="$CODE_ROOT/.venv/bin/python"
RUNNER="$CODE_ROOT/scripts/historical_evidence_foundation.py"

# A: inspect code/policy; these commands do not touch the application DB.
git -C "$CODE_ROOT" rev-parse HEAD
"$PYTHON" "$RUNNER" --mode DISCOVERY_POLICY

# B: explicitly authorize a bounded paired probe, then run it.
"$PYTHON" "$RUNNER" --mode DISCOVERY_AUTHORIZE --evidence-root "$EVIDENCE_ROOT" \
  --probe-dates 2020-09-01 2022-09-01 2026-09-30 --max-requests 250 \
  --max-pages 250 --max-seconds 300 --confirm-network \
  --output "$EVIDENCE_ROOT/probe-authorization.json"
"$PYTHON" "$RUNNER" --mode DISCOVERY_PROBE --evidence-root "$EVIDENCE_ROOT" \
  --authorization "$EVIDENCE_ROOT/probe-authorization.json" --max-requests 250 \
  --max-pages 250 --max-seconds 300 --confirm-network

# STOP for probe review and separate permission for multi-year discovery.
# C: authorize one bounded discovery invocation; this is not ACQUIRE.
"$PYTHON" "$RUNNER" --mode DISCOVERY_AUTHORIZE --evidence-root "$EVIDENCE_ROOT" \
  --confirm-network --output "$EVIDENCE_ROOT/discovery-authorization.json"
"$PYTHON" "$RUNNER" --mode DISCOVER --evidence-root "$EVIDENCE_ROOT" \
  --authorization "$EVIDENCE_ROOT/discovery-authorization.json" --confirm-network

# D: inspect progress, then repeat only the bounded resume command as authorized.
"$PYTHON" "$RUNNER" --mode DISCOVERY_PROGRESS --evidence-root "$EVIDENCE_ROOT"
"$PYTHON" "$RUNNER" --mode DISCOVERY_RESUME --evidence-root "$EVIDENCE_ROOT" \
  --authorization "$EVIDENCE_ROOT/discovery-authorization.json" --confirm-network

# E/F: validate; finalization refuses incomplete discovery; summarize separately.
"$PYTHON" "$RUNNER" --mode DISCOVERY_VALIDATE --evidence-root "$EVIDENCE_ROOT"
"$PYTHON" "$RUNNER" --mode DISCOVERY_FINALIZE --evidence-root "$EVIDENCE_ROOT"
"$PYTHON" "$RUNNER" --mode DISCOVERY_SUMMARY --evidence-root "$EVIDENCE_ROOT"

# Optional explicit stale-partition recheck, after reviewing its exact SHA.
"$PYTHON" "$RUNNER" --mode DISCOVERY_RECHECK --evidence-root "$EVIDENCE_ROOT" \
  --partition-sha256 REVIEWED_PARTITION_SHA256 --confirm-recheck
```

## 21. Request-volume accounting

Base scope has two metadata requests, listing traversal and every requested
calendar-date traversal. Successful cursor pages require ceil(TOTAL/PAGESIZE)
requests; non-cursor partitions require an empty terminator. Retries increase
attempts. Real totals and durations are unknown. Provided sample totals
2057/2520/3446 are observations for three dates only, not runtime constants or
whole-range volume estimates. No licensing completeness is claimed.

## 22. Testing

Hermetic MockTransport tests cover metadata/offset/cursor completion, malformed
tables, source bindings, retries/deadlines, exact attempt counts, crash/orphan
recovery, corruption, recheck, immutable source generations, manifest replay,
symlink/lock refusal, independent population probe and source-only CLI behavior.
Existing A3 source/discovery/planning/execution/audit regressions remain relevant.
No live HTTP acquisition is used.

## 23. Scale measurement

Synthetic scale fixture: 200 dates, 2,003 accepted pages, 10,000 exact identities,
six bounded invocations, cursor continuation and large identity fields.
Tracemalloc records acquisition, aggregation and serialization peaks; each must
be below 64 MiB. A counting sink exercises streaming output without retaining
the JSON. This measures Python allocations, not production RSS, filesystem
cache, HTTP server reliability or realistic multi-year economic coverage.

Observed Windows/Python fixture measurements: acquisition peak 13,137,813 bytes,
aggregation peak 14,350,361 bytes, serialization peak 4,733,934 bytes; test duration
322.25 seconds under tracemalloc. All 2,003 requests were synthetic. An earlier
development measurement was stopped while optimizing repeated validation; it
is not counted as an accepted scale run. These measurements are not production
RSS or network-throughput evidence.

## 24. Readiness and limitations

Mechanism readiness comes from focused tests. Real multi-year traversal,
population semantics, historical universe completeness, publication authority,
PIT, source licensing and economically qualified windows remain unverified.
No source failures are interpreted as missing securities. Observed dates are
facts, not a complete exchange calendar. Current terms and issuer information
are not silently imported or promoted into historical proof.

## 25. Safety declarations

DB_MUTATION=false; PRODUCTION_DB_ACCESS=false; MIGRATION_EXECUTED=false;
DEPLOY_EXECUTED=false; ACQUISITION_EXECUTED=false; APPLY_EXECUTED=false;
HISTORICAL_PROFITABILITY_CALCULATED=false; OFFICIAL_DAY0_STARTED=false.
Only synthetic fixture directories and deliberately authorized filesystem
evidence roots are writable by this workflow. Existing temporary directories
are preserved. No credentials, headers, cookies or raw exceptions are emitted.

## 26. Delivery

One coherent implementation commit, one normal main push, one exact-commit CI
snapshot without polling. Full local backend suite is deferred to CI. Remote
concurrency blocks delivery without rebase/amend/force push. Local acceptance
and CI are reported separately.

## 27. Handoff

Independent review → green CI → separately authorized bounded real probe →
controlled resumable discovery → evidence analysis → acquisition planning.
Migration/deploy, archive acquisition, canonical projection and A2H rerun need
their own explicit approvals. Task306B/C, profitability and official Day 0 do
not start automatically.

## 28. Exact implementation files and local verification

New contracts/store: `backend/app/schemas/historical_discovery_checkpoint.py`,
`backend/app/services/historical_discovery_checkpoint.py`.

Existing extensions: `backend/app/schemas/historical_evidence_foundation.py`,
`backend/app/services/historical_evidence_source_client.py`,
`backend/app/services/historical_evidence_discovery.py`,
`backend/app/services/historical_evidence_plan_service.py`,
`scripts/historical_evidence_foundation.py`.

New tests: `backend/tests/test_historical_discovery_checkpoint.py`,
`backend/tests/test_historical_discovery_runner.py`,
`backend/tests/test_historical_discovery_scale.py`. The eleventh file is this
architecture document. No acquisition executor, model, migration or frontend
file changes.

Local checks (repository root; PYTHONPATH=backend; use a fresh basetemp to avoid
the inaccessible pre-existing Windows pytest temp root):

```text
pytest -q test_historical_discovery_checkpoint.py test_historical_discovery_runner.py
  test_historical_evidence_source.py test_historical_evidence_discovery.py
  test_historical_evidence_plans.py test_historical_evidence_execution.py
  test_historical_evidence_audit.py
Result: 68 passed, 1 skipped (Windows symlink creation privilege unavailable).

pytest -q test_historical_discovery_runner.py test_historical_evidence_normalization.py
  test_historical_evidence_scale_migration.py -k 'not two_million and not million'
Result: 19 passed, 1 deselected (old unrelated two-million-observation test).

pytest -q -s test_historical_discovery_scale.py
Result: 1 passed; resource measurements in section 23.

compileall changed application modules and runner: PASS.
alembic heads: 202610070001.
working/staged git diff --check and exact scope review required before delivery.
```

The Windows symlink test remains enabled for CI on systems that permit creating
the fixture link. Full local backend regression is deferred to exact-commit CI.
