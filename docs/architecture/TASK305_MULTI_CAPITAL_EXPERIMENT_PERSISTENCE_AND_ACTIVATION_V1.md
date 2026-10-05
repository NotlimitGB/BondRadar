# Task305 РІР‚вЂќ Multi-Capital Experiment Persistence & Atomic Activation v1

## 1. Development boundary

This package implements a separately authorizable mechanism. It does not deploy,
activate production, establish official Day 0, start the 90-day clock, fetch live
sources, or schedule observations. All mutation tests use disposable SQLite.
Starting revision: `1a883fa77139e22fa99adba95ed3b9ea13ca2ea0`.

## 2. One experiment, seven treatments

The frozen Task304A capitals are 50,000; 100,000; 500,000; 1,000,000; 3,000,000;
5,000,000; 10,000,000 RUB, in that ordinal order. These are treatments sharing one
market period, not seven independent periods or an optimal-capital claim.

## 3. API and frozen input

`ShadowScaleActivationService(session_factory).plan(*, request)` and
`apply(*, request, reviewed_plan, authorization)` accept strict immutable Task305
contracts. The request contains only the full READY Task304A matrix.
`validate_scale_genesis` validates every case, its Task303/302/304 evidence and all
hashes before opening a database session. A lossless private copy of validated
input isolates nested upstream lists from caller mutations during the transaction. Unknown fields and PIT claims fail closed.
Blocked, rollback, ambiguous commit and post-commit failure receipts remain typed.

## 4. Tables

New tables are `shadow_experiment_groups`, `shadow_experiment_cases`,
`shadow_experiment_benchmarks`, `shadow_experiment_benchmark_components`.
Group identity and scale identity are unique. Case ordinal/capital/hash are unique
within the group; its nonnull `shadow_run_id` is globally unique and RESTRICT-linked
to the existing `shadow_test_runs`. Benchmarks are one per case. Component ordinal,
Bond identity and component hash are unique within each benchmark. All references
use RESTRICT. Required foreign-key indexes and exact ordinal/capital checks are
part of both the model and frozen migration contract.

## 5. Task303 reuse

No second strategy accounting model exists. The existing run, ledger, daily
snapshot and position tables own units and cash. The mechanical refactor exposes
`build_genesis_plan_in_session(db, *, request)`, with no transaction completion or
session lifecycle operations. Standalone Task303 `_plan` delegates to it.
Task305 calls it seven times inside one session. It uses `genesis_fields` and the
existing no-commit `shadow_ledger_repository.persist`; it never calls standalone
Task303 APPLY seven times. Accounting equations remain unchanged.

## 6. Group identity

`group_key_sha256` binds scale Genesis, scale policy and experiment policy hashes,
genesis/end/common trade dates, source code SHA, source universe SHA, full
Investment batch SHA and the exact ordered capital grid. `input_state_sha256`
binds the complete request. Canonical JSON uses sorted keys, ASCII escaping,
compact separators and disallows nonfinite JSON numbers. No new Shadow primary
keys, technical timestamps, UUIDs or current clock contribute to semantic hashes.
Upstream Bond, snapshot and profile IDs remain source identities.

## 7. Current-state hash

The state reader finds groups by group key or scale identity, their complete
case/benchmark/component graph and the expected and actually linked child runs.
It includes child ledger/snapshots/positions through Task303 state hashes. New
foreign keys are represented by their semantic group/case/benchmark/run keys.
Missing or unresolved links remain distinguishable. Extra rows, altered payloads,
changed provenance and orphan children change the state hash.

## 8. PLAN

PLAN uses narrow scalar SELECTs under `no_autoflush` and a fresh session. It
rebuilds all child plans, checks them against the matrix's original executable
Genesis plans, and validates physical Bond/snapshot/profile references for both
strategy and benchmark. It does not refresh economic source inputs or repair data.
No add, flush, commit, update or delete occurs during PLAN.

## 9. Persistence-state classification

Pristine state is EXECUTABLE. Exactly complete verified activation is
IDEMPOTENT_NOOP. A missing case, benchmark, component or child is
PARTIAL_SCALE_ACTIVATION_STATE. Standalone existing child runs without a complete
matching group cause ORPHAN_CHILD_RUN_CONFLICT, never adoption. Changed complete
history causes HISTORICAL_SCALE_STATE_DRIFT, never automatic repair.

## 10. Explicit authorization and agreed replay clarification

Authorization requires exact `explicit_apply=True`, plan/group/scale/current-state/
input hashes and ordered case, child-run, original-child-plan and benchmark hashes.
The user explicitly chose fresh PLAN and authorization after activation, clarifying
original request section 32. Old authorization is rejected when database state
changes. A fresh verified no-op PLAN authorizes zero writes and zero commits;
original child `genesis_plan_sha256` values remain unchanged.

## 11. Transaction ownership and locking

APPLY owns a fresh session with no active transaction or pending state. PostgreSQL
uses a transaction-scoped try-advisory lock derived from the first 64 bits of the
group hash interpreted as signed int64. It then obtains SHARE ROW EXCLUSIVE NOWAIT
locks in order: four new tables (group, case, benchmark, component), then Task303
run, ledger, daily snapshot, position snapshot. Bonds, market snapshots and
Security Master profiles get SHARE NOWAIT locks, in that order, before rereading
source identities. SQLite isolated tests use BEGIN IMMEDIATE. Other dialects
are blocked. Locks serialize empty-state activation and source-reference checks.

## 12. Atomic persistence

Under locks APPLY rebuilds the entire current plan and requires exact reviewed
plan equality. It inserts one group, seven children, seven cases, seven benchmarks
and all components, with flushes only for IDs. There is at most one commit. Existing
source records and historical runs are never updated or deleted. Task305 exposes
no group lifecycle transition.

## 13. Frozen benchmark authority

Each complete `OfzBenchmarkGenesisViewV1.model_dump(mode="json")` is stored as JSON,
retaining Decimal strings, dates, full representative/curve/Task272/Task273 evidence
and its canonical hash. Reconstruction uses strict JSON schema validation and the
existing Task304 deterministic validator. Current market data never rebuilds or
repairs this payload. `load_persisted_benchmark_genesis(db, *, experiment_case_id)`
is SELECT-only and rejects missing or contradictory persisted evidence.

## 14. Normalized component projection

Each row copies ordinal, Bond ID, snapshot ID, Security Master profile ID, ISIN,
SECID, weight, source-node Macaulay duration, modified duration, source-node yield,
component yield, Genesis dirty value and the full-component canonical SHA. All are
exact projections of the payload; nullable source identifiers stay nullable. The
source profile must exist and belong to the component Bond.

## 15. Pre-commit and post-commit audits

Both audits use the same read-only group audit: exact group context/hashes/capital
grid, seven case identities and bindings, seven Task303 original-Genesis accounting
and provenance audits, all seven strict reconstructed benchmarks and exact normalized
components. The post-commit audit opens a new fresh session after closing the
mutation session. A failed audit after confirmed commit is POST_COMMIT_AUDIT_FAILED,
not rollback. No-op returns existing group/case/run/benchmark IDs.

## 16. Rollback and uncertainty

Any pre-commit failure rolls back the full transaction and verifies the current
state against the actual locked pre-operation hash. Confirmed rollback reports zero
committed mutations. Attempted counts are records submitted for INSERT at flush,
including a failed flush; planned counts describe the requested seven-case set,
even for a no-op. Rollback uncertainty is explicit. A commit exception reports
COMMIT_OUTCOME_UNKNOWN with nullable committed counts; it does not claim rollback
or automatically retry. Confirmed commit followed by failed verification retains
committed counts and requires investigation.

## 17. Migration and exact Decimal storage

Revision `202610040001`, parent `202610030002`, creates only the four tables. It
reuses Task303 ShadowDecimal: unconstrained PostgreSQL NUMERIC, canonical SQLite
Decimal text, no float or scale rounding. SQLite accepts a complete precreated
schema only after exact columns/type/nullability, PK, uniqueness, RESTRICT FKs,
indexes and semantic CHECK validation. Partial or incompatible schemas fail before
DDL. PostgreSQL uses normal CREATE. Downgrade checks every table before DDL and
refuses any history; no data is removed to make downgrade succeed.

## 18. Verification

Focused activation, persistence and migration modules cover SELECT-only PLAN,
strict authorization, deterministic hashes, isolated atomic mutation, rollback,
commit uncertainty, post-audit failure, stale authorization, no-op, orphan/partial/
drift gates, full reconstruction and schema incompatibility. Regression selection
covers the twelve Task303/302/304/304A modules in request section 52. Full local suite
is deferred to exact-commit CI; compile, Alembic heads and working/staged scope
reviews are required. No production validation occurs.

Local acceptance: all 44 unique focused cases passed across final selections
(22 activation cases plus 24 cases, with two overlapping cases). The combined
twelve-module upstream regression passed 283 tests. All 12 migration cases passed.
Targeted compile checks passed and Alembic reports one head, `202610040001`.
An earlier nested-list fixture error was corrected and its final test passed;
no assertion was disabled or skipped. Exact-commit CI remains the broad gate.

## 19. Capabilities and handoff

True: scale/group/case persistence and activation, child Shadow atomic activation,
benchmark Genesis/provenance, grouped PLAN/explicit authorization/APPLY/idempotency/
drift/post-commit audit. False: official Day 0, grouped daily cycle/atomic daily
APPLY, benchmark daily persistence, scale comparison persistence, scheduler,
strategy or benchmark rebalance, broker execution, optimal-capital/monthly-income/
after-tax-profit claims, PIT. Task306 grouped daily cycle needs a separate request,
review and green CI. Official Day 0 remains prohibited in this work package.
