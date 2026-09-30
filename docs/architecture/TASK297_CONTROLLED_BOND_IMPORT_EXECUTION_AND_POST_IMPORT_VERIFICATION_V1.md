# Task297 — Controlled Bond Import Execution & Post-Import Verification Package v1

## 1. Scope

This development package provides PLAN → explicit APPLY → audit over a frozen,
nonempty Task296C1 READY subset. It does not authorize production use. Its five
files add contracts, an injected-Session service, focused isolated SQLite tests,
this document, and a mechanical pure-helper extraction in Task296C1. Models,
migrations, API, frontend and existing sync/import paths are unchanged.

## 2. Public API

`ControlledBondImportService(session_factory)` exposes keyword-only
`plan(preflight, admission_manifest, authorization=None)`,
`apply(preflight, admission_manifest, authorization, reviewed_plan,
expected_plan_sha256, confirm_apply=False)` and
`audit(preflight, admission_manifest, execution_result=None)`.
The injected factory must produce fresh SQLAlchemy Sessions. There is no
environment wiring, operator script, external client or global Session import.

## 3. Frozen contracts

Authorization, plan, execution and audit use their respective
`controlled-bond-import-*-v1` versions, frozen strict Pydantic models and
`extra="forbid"`. All paths retain stable types and `pit_ready=False`.
Frozen means attribute immutability; the input is independently validated because
Pydantic `model_copy`/`model_construct` can bypass validation.

## 4. Upstream validation

`validate_frozen_preflight` validates Task296B through its existing validator,
Task296C1 version/PIT, candidate identities, upstream/candidate hashes,
partitions, order, READY/review canonical hashes, counts and reason breakdowns.
Each READY row is replayed through `evaluate_import_candidate_source`, the
mechanically extracted original Task296C1 candidate evaluator. Its source,
admitted values and frozen Company preview must match. Review rows are retained;
they are not promoted or executed. Missing READY evidence and empty READY subsets
fail closed before a Session is opened. Historical DB projections are not
reconstructed; current Company/Bond checks belong to PLAN.

## 5. Authorization

The authorization binds the exact READY SHA and full `BondImportBatchIdentity`,
including upstream hashes, original candidate identities and counts. No counts or
hashes are hard-coded. PLAN without authorization is `NOT_EXECUTABLE`. APPLY
requires the actual boolean `True`, valid authorization and the exact reviewed
plan SHA; truthy values such as integer 1 do not authorize APPLY.

## 6. Canonical plan

Plans sort Bonds by `(ISIN, SECID)` and issuer groups by trimmed authoritative
INN. SHA-256 covers canonical timestamp-free JSON of the entire plan except its
own hash, with sorted keys, ASCII escaping and compact separators. It binds
authorization, relevant current identities, proposed values, defaults, actions
and blockers. Any blocker makes the entire plan non-executable.

## 7. Current Bond identity

Narrow scalar projections check exact ISIN and SECID. Both must point to the same
unique row with the expected Company and admitted properties for
`ALREADY_PRESENT_EXACT`. Partial matches, different rows or mismatching properties
block the batch. Trim/case variants are collision diagnostics only. A global narrow
ID/ISIN/SECID projection applies Python's exact whitespace semantics independently
of SQL TRIM differences. Admitted properties are then read only for matching IDs.
Existing Bonds are never updated; unrelated properties are not loaded.

## 8. Current Company identity

Company scalar projections include identity/name/ticker only. Frozen reuse
requires the same Company ID and trimmed INN. Name-only reuse without corroborating
INN blocks. Frozen CREATE requires no current Company with matching INN,
normalized exact name or proposed ticker. Conflicting names within one INN group
block; several new Bonds of one issuer share one Company. The technical ticker
uses the existing `MOEX_<INN>` normalization; collisions block without suffixes.
An exact already-present Bond with correct Company may be a no-op even when its
original frozen preview requested Company creation.

## 9. Storage boundary

Only exact ISIN/SECID, admitted name (or shortname), Company relation, RUB,
nominal, maturity/perpetual, supplied coupon/offer and structural flags are
persisted. Length limits and Numeric(14,2)/Numeric(7,3) representability are checked
without rounding or truncation. Optional numeric/date values remain null.
There is no raw-metadata inference, Security Master evidence creation or identity
acceptance. Existing market, credit, liquidity and risk values are untouched.

## 10. Unknowns and model defaults

Source unknowns remain visible in frozen evidence and plan
`unknown_source_fields`. Mandatory unknown booleans use existing technical
defaults with `MODEL_DEFAULT_NOT_EVIDENCE`. Floating coupon is never derived from
raw metadata. Unknown subordinated becomes technical false; nullable amortization
remains null. Company country RU and `signal=insufficient_data` are technical
defaults. These values do not become verified evidence. Audit checks technical
defaults on receipt-identified newly created Bonds separately from source facts.

## 11. Session ownership

PLAN/audit open and close their own read-only Sessions and SELECT under
`no_autoflush`, without flush/commit. APPLY rejects a Session factory returning
active transactions or pending new/dirty/deleted state. Rejected non-fresh
Sessions are not closed or rolled back, preserving caller-owned state. Caller
Sessions are not an API input. Unexpected dependency/DB errors are sanitized in
execution diagnostics; source exceptions and credentials are never reported.

## 12. Transaction and locks

PostgreSQL locks `companies` then `bonds` with
`SHARE ROW EXCLUSIVE MODE NOWAIT` before rebuilding the current plan. This excludes
concurrent writes to both tables while allowing normal reads. Lock refusal blocks
without retry. SQLite uses `BEGIN IMMEDIATE` for isolated mutation tests. Other
dialects are blocked for APPLY. The full rebuilt plan must equal the reviewed
plan and SHA before any INSERT. See [PostgreSQL locking](https://www.postgresql.org/docs/current/explicit-locking.html)
and [SQLite transactions](https://www.sqlite.org/lang_transaction.html).

## 13. Atomic persistence

Only planned Company/Bond objects are added, in deterministic order; flush is
allowed to obtain IDs. Pending dirty/deleted or unexpected new objects are
rejected. All rows use one transaction, never per-row commits or continuation
after failure. A read-only batch audit runs before the single commit. The audit
runs again in a fresh Session after commit. Existing rows remain no-ops.

## 14. Failure outcomes

Execution distinguishes planned, attempted and committed creates, reused
Companies and already-present Bonds. A confirmed pre-commit rollback reports
zero committed creates and `db_mutated=False`. A commit exception is conservatively
`COMMIT_OUTCOME_UNKNOWN`, with nullable committed counts/mutation/commit outcome;
calling rollback after an ambiguous commit does not establish non-persistence.
A failed post-commit audit is `APPLIED_AUDIT_FAILED`, never a rollback claim.

## 15. Idempotency

An old plan is rejected when DB state changes. A newly reviewed exact no-op plan
can execute with zero mutations. The original frozen CREATE preview does not
cause duplicate Company creation after a successful exact import. Unknown fields
and technical defaults on pre-existing rows are not treated as source evidence.

## 16. Audit and observability

Audit checks exact unique identifiers, shared Bond identity, Company relation and
authoritative INN, and admitted persisted properties. It reports per-row failures
and expected/verified counts. Receipt-provided historical counts are explicitly
attributed to the receipt, not rediscovered. Without a receipt, historical
created/reused counts are unknown. Global unrelated mutation history, including
external writers/triggers, is not observable from a standalone audit;
`unexpected_mutation_count=None` does not mean zero. Receipts are caller-supplied
application evidence, not signed operator attestations.

## 17. Focused verification

Six critical paths cover authorized SELECT-only PLAN, frozen tampering/storage
and drift gates, atomic shared-Company APPLY, repeat no-op/stale plans, mid-INSERT
rollback/lock/non-fresh Session refusal, and exact audit/source-helper/static
boundaries. Fixtures are synthetic isolated SQLite. Only this focused module,
compile checks and diff/scope checks run locally; full regression is deferred to
exact-commit CI. PostgreSQL lock behavior must be independently reviewed before
separately authorized production execution.

## 18. Capability declarations

```ini
CONTROLLED_BOND_IMPORT_PLAN_READY=true
EXPLICIT_READY_SHA_AUTHORIZATION_READY=true
CURRENT_DB_DRIFT_GATE_READY=true
ATOMIC_CONTROLLED_APPLY_READY=true
IDEMPOTENCY_GATE_READY=true
POST_IMPORT_VERIFICATION_READY=true
VERIFIED_SECURITY_MASTER_CREATION_READY=false
PRODUCTION_IMPORT_AUTHORIZED=false
M3_REBUILD_READY=false
SCORING_READY=false
RANKING_READY=false
RECOMMENDATION_READY=false
TRADING_READY=false
PIT_READY=false
```

## 19. Safety

No VDS, production DB, live MOEX/T-Invest, tokens, deployment, source fetching,
sync, backfill, production preflight/import or shared mutations occur in this
work package. No migrations or new Asset layer are required. Readiness describes
code capability, not operator authorization, personal eligibility or M3 readiness.

## 20. Handoff

Independent code/CI/exact-SHA review comes next. Deployment, read-only production
preflight and explicit frozen READY-SHA authorization each require separate
operator instructions. Only after those gates may controlled PLAN/APPLY be
considered. No downstream task is started here.
