# Task296C3 — TQCB Board Snapshot Completeness v1

## 1. Execution profile

Development-only acquisition correctness repair. Baseline is
`35446e733af31321f07c8ef3053cafcd61479ec1`. One focused commit and normal push;
no deployment or production execution. Existing temporary directories remain.

## 2. Context

Task296B supplies frozen candidates. Task296C2 freezes complete upstream inputs
and resolutions for offline replay. Task296C1 acquires independent issuer,
board and description evidence. This change corrects only the board acquisition
boundary; Task297 authorization remains separate.

## 3. Operator-reported evidence

The supplied production report described a valid, replayable 132-candidate
artifact, successful issuer and description acquisition, and a board scan with
100 requests and 303300 accumulated rows. All candidates required review because
the scan was incomplete. These observations were not independently queried here.
They are contextual evidence, not runtime counts, hashes or acceptance constants.
An empty READY subset cannot authorize Task297.

## 4. Root cause

The C1 adapter assumed that a response with at least the requested limit required
another page. A full securities snapshot could consequently be fetched and
appended repeatedly until the page bound. Row count alone does not establish
pagination on this endpoint.

## 5. Goal

Determine completeness from explicit endpoint-specific snapshot/cursor semantics,
bound acquisition, retain exact source facts and prevent repeated accumulation.
Complete acquisition does not itself establish candidate eligibility.

## 6. Starting state

The client already exposes the board securities JSON route, sanitized JSON
request handling and metadata normalization. The adapter owns a row-count-based
scan. The normal universe importer also uses the old public loader; its behavior
is intentionally outside this repair. No production endpoint was queried.

## 7. Architecture

`fetch_bond_board_snapshot(board, *, limit=100)` owns acquisition and completeness.
A frozen `MoexBondBoardSnapshotResult` carries rows, sanitized warnings,
completion_status, request_count and cursor diagnostics. C1 consumes this result
once per nonempty batch. The pure preflight classifier is unchanged.

## 8. Board snapshot contract

The initial request uses start=0, limit=100 and requests securities plus
securities.cursor. A valid first securities table without the applicable cursor
is a complete snapshot, including an empty table. A large table does not imply
additional pages. Columns must be unique strings and each data row must have
exactly the column count. Malformed responses cannot establish COMPLETE.

## 9. Cursor semantics

An applicable cursor has one row with exact-integer INDEX, TOTAL and PAGESIZE.
INDEX/TOTAL are nonnegative; PAGESIZE is positive. INDEX equals requested start,
TOTAL stays fixed and data count equals min(PAGESIZE, TOTAL-INDEX). Continuation
uses INDEX+PAGESIZE; reaching TOTAL terminates. Missing cursor after pagination,
contradictory cursor/table data or page-bound exhaustion yields INCOMPLETE.
The client's existing max_pages bounds cursor requests. No retries are added.

## 10. Duplicate protection

Canonical full source-row JSON defines exact equality; keys are sorted, ASCII
escaped, compact and nonfinite values rejected. Identical rows within a single
non-cursor snapshot are collapsed. Differing facts sharing identifiers are
retained. Cursor pages containing duplicates, repeating a page in any row order,
or overlapping prior exact rows fail closed before appending that page. Previously
accepted rows remain diagnostic evidence. Output unique rows are canonically
sorted. Identity differences are never normalized away.

## 11. C1 evidence contract

The adapter transfers completion status, actual request count, unique row count
and warning count to its existing DTO. Warnings turn a declared COMPLETE into
INCOMPLETE. Exact SECID AND ISIN remains mandatory for membership. Rows matching
either identifier remain available to detect contradictions. Empty candidate
sets make no board request. Missing evidence cannot become READY.

## 12. Compatibility

`fetch_bond_universe` retains its signature, request parameters, normalization
and return type. Normal importer, history loading, issuer resolution and
description acquisition remain unchanged. Task296B/C2 schemas, candidates and
hashes, C1 classifier/schema and Task297 code/authorization are unchanged.
ACTIVE_STATUS_UNKNOWN remains diagnostic; explicit inactivity still blocks.

## 13. Allowed scope

Only moex_iss_client.py, moex_bond_import_evidence_service.py, its existing test
module and this document change. The typed result resides in the client module;
no new public schema or separate runtime consumer is required.

## 14. Forbidden scope

No models, migrations, frontend, M3, sync/import paths, company matching,
admission rules, frozen artifact semantics or authorization gates change.
No production counts/hashes are hard-coded. No VDS, production DB, real token,
live request, production artifact write, deploy or Task297 PLAN/APPLY occurs.

## 15. Tests

Hermetic HTTPX MockTransport tests cover the 3033-row regression shape, a READY
C1 candidate in a large snapshot, cursor 100+50, terminal original page size,
empty tables, cursor inconsistencies, repeated/overlapping pages, page exhaustion,
malformed tables, exact-only deduplication, warnings, sanitized source errors,
partial identifiers, contradictory identities and input-order determinism.
Existing adapter safety tests and legacy loader assertions remain.

Focused adapter tests run first, followed by normal universe, preflight, issuer
source, frozen artifact and controlled import test modules. Compile checks cover
both changed application modules. Working/staged diff and four-file scope are
reviewed. Full local regression is deferred to exact-commit CI. Controlled import
tests use isolated fixtures; no real execution or production connection occurs.

## 16. Acceptance and capabilities

```ini
NON_CURSOR_BOARD_SNAPSHOT_SUPPORTED=true
CURSOR_BOARD_PAGINATION_SUPPORTED=true
REPEATED_BOARD_PAGE_GUARD=true
ROW_COUNT_ONLY_PAGINATION=false
INCOMPLETE_BOARD_READY_ALLOWED=false
EXACT_SECID_AND_ISIN_MEMBERSHIP_REQUIRED=true
PRODUCTION_EXECUTION_AUTHORIZED=false
DEPLOYMENT_EXECUTED=false
PIT_READY=false
```

Implementation readiness is distinct from actual source completeness, candidate
readiness and authorization. Completion requires focused regressions and green
exact-commit CI; pending CI is reported as pending rather than passed.

## 17. Delivery

Check baseline and scope before changes and remote concurrency before delivery.
Create one `Fix MOEX board snapshot completeness` commit and push normally once
to main. No rebase, amend or force push. Remote drift is a hard stop.

## 18. Final report

Report start/final/commit SHA, changed files, root cause, acquisition modes,
regression/identity/determinism tests, focused results and exact CI run/status.
Explicitly declare Task296B/C2 schema/Task297 unchanged, production access and
DB mutation false, Task297 PLAN/APPLY false and deployment false.

## 19. Hard stop and handoff

After code, tests, commit, push and CI report, stop. Independent code/SHA/CI review
is the only next safe step. Production deployment or rerunning C1 against the
existing frozen artifact requires separate instructions. Task297 remains outside
this package; no new production freeze or automatic import is authorized.
