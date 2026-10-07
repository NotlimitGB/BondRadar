# Task306A3 — Multi-Year Historical Evidence Foundation v1

## 1. Purpose

Provide discovery, acquisition planning, separately authorized archival acquisition,
projection planning, separately authorized canonical projection, audit and resume.
This is a development foundation. MockTransport and disposable SQLite fixtures
prove mechanisms, not historical MOEX coverage or investment outcomes.

## 2. Frozen research policy

`HistoricalEvidencePolicy` starts on 2020-09-01. `research_policy(cutoff=...)`
freezes an exact cutoff; without it, one SELECT obtains the persisted MOEX frontier.
An absent frontier blocks planning. Primary horizon is 365 calendar days,
secondary horizons are 90 and 180. Entry policy is
`FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH`; annual entry targets are October 2020
through September 2025, approximately 60 calendar slots. Source absence never
shortens the requested calendar range.

## 3. Workflow and APIs

The public services expose keyword-only `discover`, `plan_acquisition`,
`acquire_batch`, `plan_projection`, `apply_batch`, `audit` and `resume_plan`.
Plans use a factory of fresh SQLAlchemy Sessions. Discovery uses an injected
`HistoricalEvidenceSourceClient`; its HTTPX client has no environment credentials
or general RPC entry point. Acquisition completes network reads before DB writes.
Canonical APPLY accepts archived observations and performs no network requests.
The initial run stores exact partition scope and reviewed representative bindings;
resume never reconstructs a shortened universe from whichever pages happened to
succeed first.

## 4. Source boundary

The adapter permits only market history dates/columns, market-wide history by
calendar date, historical listing with `status=all`, security description/reference,
and contractual cashflow tables. Market pages request SECID ordering and include
zero-trade observations. `marketprice_board=1` is never sent: that flag uses the
current main board, which is unsuitable for historical board selection.

Official endpoint contracts: [MOEX daily history](https://iss.moex.com/iss/reference/497)
and [historical listing](https://iss.moex.com/iss/reference/489). No live source
availability was established by this work package.

## 5. Population

Discovery unions listing identities and observed history identities, preserving
all boards, missing identifiers and delisted/inactive securities. Current Bond rows
do not restrict discovery. Exact current linkage is a separate diagnostic. A
successful traversal proves completion of those endpoint partitions, not every
historical security or a complete historical listing universe.

## 6. Source contracts and pagination

Frozen strict, extra-forbid DTOs carry query partitions, rows, UTC observation time,
attempt diagnostics, cursor facts and SHA. Columns are unique strings, cells are
source-native scalars and row widths must match. A defensive width limit is 1,024
columns. Pages are at most 100 rows. Cursor INDEX/TOTAL/PAGESIZE must be exact
integers; offset, row count and completion must agree. TOTAL cannot change or
disappear during pagination. Without a cursor, a short/empty page completes the
partition; full pages require the next offset. Repeated and overlapping exact rows
block completion. Date/identity contradictions do not become COMPLETE.

## 7. Resource and retry policy

Page size is 100, response body cap is 8 MiB, batch caps are 1,000 observations and
8 MiB canonical payload. Calls are sequential. At most three attempts are made;
retry delays are 0.25/0.50 seconds, with numeric Retry-After capped at 60 seconds.
Timeout/network/remote-protocol failures and HTTP 408/429/500/502/503/504 retry.
Malformed tables/JSON, rejected requests and unknown failures do not retry.
Attempts are paced to at most two per second. HTTP failures expose only safe codes.

## 8. Bounded discovery and output

Discovery retains identities and partition hashes, never the full history payload.
A private temporary SQLite uniqueness index replaces an unbounded in-memory set
of row hashes; it is cleaned up on completion/failure. Page generators must be
consumed or closed by callers. The A2H canonical iterator supplies streaming hash
and runner output. Page/batch JSON copies are bounded; no report holds millions of
observations. Existing temporary directories are untouched.

## 9. Economic mapping

`map_moex_history_row` is a mechanical extraction of the existing market-history
mapper. Legacy backfill delegates to it. Price precedence, duration normalization,
Task294 ACCINT/ACCRUEDINT parsing, warning semantics and original raw payload
remain unchanged. The new archive adds exact SECID/date/board gates, source field
names, units, mapping version and request partition. Invalid/missing NKD remains
null; no coupon-derived, current-value or zero fallback is used.

## 10. Source times and PIT

Observation time is supplied by the adapter, ingestion time is stored independently
on each page. Event/effective dates and publication availability are separate.
Trading/event dates are dated source observations, not proof of historical
publication time. `available_at` is unknown unless authority is established;
v1 does not invent it. Current description/reference is explicitly
`CURRENT_OBSERVATION`. Historical terms and issuer versions remain in the archive.
They never update current Security Master or verified LegalIssuer linkage.

## 11. Cashflows

Preserve source-native table rows and revisions. Coupons, amortizations and
redemptions require explicit nonnegative finite amounts and RUB for projection.
Offers remain nonautomatic; missing amounts are not replaced by nominal. Duplicate
natural event keys, conflicting currencies and unsupported evidence block a batch.
Missing redemption and zero persisted events do not establish contractual
completeness. v1 does not reconstruct coupons or assess investment returns.

## 12. OFZ structural boundary

Structural description acquisition for canonical OFZ is limited to explicitly
reviewed `RepresentativeBinding` inputs, with source A2H SHA and exact Bond/snapshot
identifiers. PLAN checks those persisted bindings and freshness against selection
date; representative inputs are included in the plan hash and run identity.
Changing selection requires new reviewed input and a new run. No permanent set
of eight securities is hard-coded. Non-selected OFZ do not generate structural
repair requirements. Reference/contractual event inventory remains independent.
Task272 and final duration matching are `NOT_EVALUATED`.

## 13. Independent persistence

Revision `202610070001`, parent `202610040001`, creates six tables:
`historical_evidence_runs`, `historical_evidence_work_items`,
`historical_evidence_source_pages`, `historical_evidence_securities`,
`historical_evidence_observations`, `historical_evidence_application_receipts`.
Existing canonical tables require Bond FKs and cannot hold extinct/unregistered
identities, independent source revisions, pagination checkpoints or batch receipts.
Additional raw keys cannot solve those constraints safely.

## 14. Persistence guarantees

Archive observations, source pages and receipts are append-only through the service
APIs. Checkpoints advance only with the successful archival transaction. Tables
have explicit uniqueness, indexes, checks and RESTRICT FKs. Exact decimal values
in archive JSON are strings. No old migration changes. SQLite metadata precreation
is accepted only after complete schema comparison; partial/incompatible schemas
block before DDL. PostgreSQL uses the ordinary CREATE path. Downgrade checks all
six tables before dropping any and refuses when any contains rows.

## 15. Acquisition planning and authorization

Acquisition PLAN binds policy/range, discovery manifest SHA, remaining exact query
scope, reviewed representatives and current archive-state hash. Authorization
requires `explicit_authorization=True`, operation, plan SHA, source manifest SHA,
scope SHA and current DB SHA. Query dates outside policy and duplicate queries are
invalid. Each bounded execution may select only query offsets present in that plan.
Missing/absent pages, failed reads and not-yet-attempted partitions remain distinct.

## 16. Projection board binding

Projection requires one exact, explicitly reviewed board binding for the event
date. A listing observation from the same run must corroborate the board/period.
Overlapping bindings or contradictory source board block; current primary board
is not substituted. If history omitted ISIN, only independent exact reference
SECID/ISIN plus the explicit binding can link it. The raw missing identifier is
retained and this current reference is not historical linkage proof.

## 17. New historical securities

Missing canonical Bonds require exact SECID/ISIN, reliable issuer title/trimmed
INN and corroborating description ISIN/name/RUB/positive nominal. Company reuse
uses exact INN. Name/ticker collision or ambiguous issuer assertions block.
No placeholder Company or name-only identity is created. Multiple Bonds with the
same consistent authoritative INN share one Company. Unknown mandatory Bond flags
use existing technical defaults, labelled `MODEL_DEFAULT_NOT_EVIDENCE`; signal is
`insufficient_data`. No LegalIssuer or Security Master verification is synthesized.

## 18. Existing canonical rows

`RETAIN` means equality or absence of a new field; `ENRICH_NULL` fills only nulls.
Any nonnull difference is `CONFLICT`; lossy Numeric values are
`STORAGE_UNREPRESENTABLE`. No authoritative replacement, truncation or rounding
is allowed. Existing snapshot IDs and original raw payload remain unchanged.
Receipts retain before/after fingerprints and source observation identity. Raw
source corrections form separate archive versions and cannot overwrite canonical
nonnull facts. Historical terms never run through today's resolver.

## 19. Transaction ownership and locks

One fresh service-owned Session/transaction per bounded batch. Unsuitable factory
Sessions are rejected without clearing caller pending state. PostgreSQL uses
NOWAIT SHARE ROW EXCLUSIVE locks in archive-table order; projection additionally
locks Companies, Bonds, market snapshots and cashflows. SQLite fixtures use
BEGIN IMMEDIATE; other mutation dialects block. After locks the state/plan is
rechecked. Any blocker rolls back the entire batch. Receipt, checkpoint and writes
share one commit. A new read-only Session performs the post-commit audit.

## 20. Pre-commit and post-commit audit

Archive audit checks page SHA, query/offset, row count/ordinal, raw binding and
recomputed mapping, not just a mutable row hash. Projection re-reads persisted
values before commit, avoiding cached pre-bind Decimal values (important with
SQLite Numeric adapters). Audit preserves original nonnull canonical facts and
detects missing rows or drift. Stored amounts must survive the actual DB roundtrip.

## 21. Failure and resume

Receipts distinguish BLOCKED, ROLLED_BACK, COMMITTED, IDEMPOTENT_NOOP,
COMMIT_OUTCOME_UNKNOWN and POST_COMMIT_AUDIT_FAILED. Unknown committed counts are
null, not false zero. A failed read returns exact failed partition hashes and
attempt count without a DB write; its checkpoint remains pending and resumable.
Only durable successful receipts are stored, so `failed_work_count` is a count of
persisted FAILED checkpoints, not a historical count of every failed HTTP attempt.
Operators must retain failed attempt receipts; pending work is never silently
skipped. Resume audits durable receipts/fingerprints before deriving offsets.
An exact accepted acquisition batch reconciles to no-op without another INSERT.
Canonical repetition needs a fresh plan/authorization against current DB state.

## 22. Hash material

Canonical JSON uses sorted keys, ASCII escaping, compact separators and rejects
nonfinite values. Request partitions, mapping/source identities, policy, source
manifest, representatives and current state are bound. Page observation time is
source provenance, not a generated report clock. Technical ingestion timestamps
are excluded from DB fingerprints. Hashing/output use the same A2H iterator.
Credentials, cookies, headers, raw HTTP errors and connection details are excluded.

## 23. Report semantics

Audit returns requested policy, observed range, per-family earliest/latest dates,
observation counts, exact unresolved archive IDs/SECID/ISIN, pending/failed work,
exact pending/absent/failed partition hashes, projection action counts,
application count, calendar slot capacity and proof limitations. Calendar capacity
is a range-planning fact; it is not data-qualified research windows. Full contractual
history, publication availability and historical universe remain unproven. The
unchanged A2H can only observe canonical Bonds; unresolved archive population is
explicitly retained in the denominator and limits later canonical-only audits.

## 24. Runner

`scripts/historical_evidence_foundation.py` defaults to REPORT, streaming stdout.
Persistent output exists only with explicit `--output`. DISCOVER/ACQUIRE require
`--confirm-network` and exact authorization; ACQUIRE/APPLY also require
`--confirm-apply`. PLAN/REPORT do not acquire sources. APPLY never creates a source
client. Artifacts are size-limited and reject duplicate JSON keys/nonfinite JSON.
Connection/output failures are sanitized. A write failure after commit retains the
operation receipt and truthful/unknown mutation flag, never claims rollback.

## 25. Verification

Seven focused modules cover source contracts, discovery, normalization, plans,
execution, audit and scale/migration. SQLite tests include delisted identities,
multi-board refusal, raw/date binding, current-reference limitations, exact null
enrichment, lossless storage gates, source correction retention, cashflow/offer
rules, authorization/drift, rollback, locks, pending Sessions, ambiguous commit
reconciliation, SELECT-only PLAN/REPORT, resume and deterministic serialization.
The existing market-history test verifies mechanical mapper parity. Relevant
market, NKD, cashflow, Security Master, issuer, Task304 and A2H regressions remain.
Full backend regression is delegated to exact-commit CI.

## 26. Scale measurement and limitations

The processing/serialization fixture streams 2,000 batches of 1,000 synthetic
observations through the real canonical iterator: 2,000,000 observations and
166,002,000 bytes hashed. Measured additional `tracemalloc` peak: 229,822 bytes;
runtime on this environment: 191.85 seconds including migration tests.
The 64 MiB aggregation/serialization limit passes for this shape. This is not a
production RSS measurement, database throughput test or a guarantee about every
possible 8 MiB response shape. DB resume/atomicity is verified separately with
bounded SQLite batches. No millions-row DB import was performed.

## 27. Capabilities and acceptance

Discovery/acquisition planning, explicit archive acquisition, controlled projection,
receipt reconciliation and archival audit mechanisms are implemented.
`FIVE_YEAR_RESEARCH_TARGET_SUPPORTED` describes the frozen research range, not
60 economically usable windows. Real data availability, PIT-ready replay, credit
model readiness, profitability, activation, live trading and production readiness
are not established. `pit_ready=False` throughout.

## 28. Handoff

After independent review and green exact-commit CI, the only safe next step is
separately authorized production read-only discovery/preflight. Migration/deploy,
archive acquisition, canonical projection and a later A2H rerun each require
separate explicit authorization. This work package performs none of them and
does not start Task306B/C.
