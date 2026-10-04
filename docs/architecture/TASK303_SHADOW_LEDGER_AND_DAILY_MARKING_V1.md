# Task303 — Shadow Ledger & Daily Marking Foundation v1

## 1. Execution boundary

Development foundation only. No production access, VDS, deployment, live sources,
broker tokens, official Day 0, scheduler or production run is authorized here.
SQLite mutation tests use a disposable database. The supplied production E2E
observations demonstrate upstream capability; they are neither constants nor an
official experiment genesis. Task304 needs a separate request.

## 2. Dedicated architecture

The modern Task298–302 chain supplies immutable source evidence. Task303 creates
`shadow_test_runs`, `shadow_ledger_entries`, `shadow_daily_snapshots` and
`shadow_daily_position_snapshots`. Legacy PaperPortfolio, paper cycles, synthetic
ML allocation and their tables are not inputs and are not reused or migrated.

## 3. Public API and contracts

`ShadowLedgerGenesisService(session_factory)` exposes keyword-only
`plan(request=...)` and `apply(request=..., reviewed_plan=..., authorization=...)`.
`ShadowDailyCycleService(session_factory)` exposes keyword-only
`plan(run_key_sha256=..., as_of_date=...)` and
`apply(reviewed_plan=..., authorization=...)`.

Contracts are strict, frozen, extra-forbid and PIT false. Genesis contracts use
`shadow-genesis-request-v1`, `shadow-genesis-plan-v1`,
`shadow-genesis-authorization-v1`, `shadow-genesis-apply-receipt-v1`; daily contracts
use `shadow-daily-plan-v1`, `shadow-daily-authorization-v1`,
`shadow-daily-apply-receipt-v1`. Blocked and rollback receipts remain type-stable.
Internal accounting contract semantics are `shadow-ledger-v1` and
`shadow-daily-mark-v1`.

## 4. Genesis authority and integrity

The request carries a full Task302 ShadowExecutionPlanView and exact lowercase
40-character code SHA. Existing pure strategy and terms validators are reused.
Genesis requires READY, no quality flags, post-rounding PASS, no unavailable
terms, no zero-lot positions, positive integer lots/quantity, positive finite
dirty/cost and verified TQCB terms. Identity, attached source, price diagnostics,
provenance, quantity/cost relationships, floor bounds and full summary are
validated. Strategy selection and risk evaluation are not rerun.

## 5. Explicit authorization

Genesis authorization binds explicit APPLY, reviewed plan SHA, run key, input
SHA, Shadow execution SHA, universe SHA, code SHA and current Shadow DB-state SHA.
Daily authorization binds explicit APPLY, reviewed plan SHA, run key, input SHA
and current DB-state SHA. A request or boolean alone cannot mutate anything.
Repeated execution requires a newly reviewed PLAN and current authorization.

## 6. Genesis accounting

One INITIAL_CAPITAL event credits capital. Each GENESIS_PURCHASE adds supplied
Task302 units and debits its supplied cash cost. Positions are ordered by Bond
ID. Exact reconciliation is:

```text
cash = capital - sum(purchase cost)
market value = sum(quantity * supplied dirty value)
NAV = cash + market value = capital
daily return = cumulative return = 0
```

Residual cash is preserved. No normalization or redistribution occurs.

## 7. Ledger and immutable snapshots

Allowed event vocabulary is INITIAL_CAPITAL, GENESIS_PURCHASE, COUPON,
AMORTIZATION, REDEMPTION. The service exposes no ledger update/delete path.
Global event-key uniqueness and per-run sequence uniqueness prevent duplicate
acceptance. Snapshots are unique per run/date and positions per snapshot/Bond.
Copied accounting evidence and source fingerprints allow independent audits.
Accepted events and snapshots are never rewritten to accommodate rebuilt sources.

## 8. Calendar continuity and horizon

Genesis is elapsed day 0. End is genesis +90 calendar days, not 90 trading days
or 90 snapshots. New cycles require exactly previous accepted date +1, including
weekends. A skipped date, backward date or date after the horizon blocks APPLY.
Accepting the last day changes ACTIVE to OBSERVATION_COMPLETE: horizon reached,
not strategy success. ABORTED is reserved; no abort API is introduced.

## 9. Contractual cashflows

Only persisted MOEX events on the requested date for previous positive holdings
are considered. Order is COUPON, AMORTIZATION, REDEMPTION, then Bond ID. The source
unique constraint allows one event per Bond/date/type/source; contradictory
duplicate input is blocked. Automatic cashflows require RUB and finite
nonnegative supplied amount; zero is valid, missing amount is a blocker.

Coupon credits amount × current quantity without changing units. This compensates
the coupon-date NKD reset in dirty-price marking. Amortization credits the same
product without reducing units: principal per security changes, not unit count.
Redemption credits the supplied amount × quantity and removes all remaining units.
`offer_redemption` produces OFFER_REDEMPTION_NOT_AUTO_EXERCISED without exercise.
`other` blocks an active holding's day. Non-MOEX events are not automatic flows.

## 10. Redeemed continuity

Redeemed positions remain in every later snapshot with zero quantity and market
value. No Task273 valuation is requested for them. Duplicate redemption cannot
re-credit cash through an accepted cycle replay.

## 11. Dirty-value marking

One BondDv01Service call per resulting positive holding uses exact date, moex and
maximum age 7. It reads persisted sources only. Overall Task273 READY is not
required: duration/DV01 can be unavailable while dirty value is independently
available. Required are has_dirty_value, positive finite Decimal dirty value,
verified RUB and positive verified nominal, consistent market/provenance identity,
profile version and valid age 0..7. Price basis and unavailable DV01 status are
copied diagnostically. No pricing formula or legacy price fallback is introduced.

## 12. NAV and simple returns

```text
cash_today = prior_cash + today's ledger cash deltas
market_value_today = sum(resulting_quantity * dirty_value)
NAV_today = cash_today + market_value_today
daily_return = NAV_today / NAV_previous - 1
cumulative_return = NAV_today / initial_capital - 1
```

Formula is SHADOW_SIMPLE_NAV_RETURN_V1. There are no later external cashflows,
annualization, fees, tax, benchmark or money-weighted return assumptions. Zero
previous NAV blocks the next day rather than inventing a division-by-zero result.

## 13. Decimal calculation and storage

Operations use a fresh precision-28 ROUND_HALF_EVEN context and deterministic
ordering, without float or presentation quantization. PostgreSQL uses NUMERIC
without fixed scale; SQLite stores canonical Decimal strings through ShadowDecimal.
Storage roundtrips exactly, including repeating-return results produced by the
calculation context. Database positivity checks complement application finite/type
checks; SQLite arithmetic is always performed in Python Decimal, never SQL float.

## 14. Canonical hashes and provenance

Canonical JSON has sorted keys, ASCII escaping, compact separators and rejects
NaN/Infinity. Equivalent Decimal numeric representations hash identically.
Technical timestamps and new Shadow PKs are excluded. Upstream Bond, market,
cashflow and profile IDs remain source identities. Shadow FK identities in hashes
are replaced by stable run/snapshot keys. No repr, secrets or connection strings
are hash material.

The universe hash freezes Task298 date/source and sorted requested, existing,
missing and candidate partitions. The run key freezes execution hash, universe
hash, code SHA and 90-day policy. Daily input binds prior snapshot/ledger,
quantities, relevant cashflows and complete Task273 valuation views. Event,
position and snapshot hashes cover their deterministic accounting content.
Upstream candidate, investment, risk, strategy, execution, DV01 and Security
Master contract versions are preserved through frozen inputs and provenance.

## 15. Replay and drift

A repeated PLAN reconstructs the requested historical prior-day state and obtains
current source evidence. Exact matching accepted content yields IDEMPOTENT_NOOP;
changed content yields HISTORICAL_SOURCE_DRIFT without overwrite. A reviewed plan
must match the post-lock rebuilt plan exactly. Current-state drift blocks before
insert. A previous authorization is not reusable after the DB state changes.

## 16. Session ownership and locks

Only a fresh Session factory is accepted; active transaction/new/dirty/deleted
state is rejected without closing or rolling back caller-owned state. PLAN uses
no_autoflush and never flushes/commits. APPLY owns its fresh session and transaction.
PostgreSQL locks the four Shadow tables in run→ledger→snapshot→position order
using SHARE ROW EXCLUSIVE NOWAIT. Daily additionally locks bonds, cashflow events,
market snapshots and Security Master profiles in fixed order using SHARE NOWAIT,
preventing concurrent source mutation during revalidation/persistence.
SQLite uses BEGIN IMMEDIATE. Other APPLY dialects are refused.

## 17. Atomicity and audit outcomes

One transaction and at most one commit. Pre-commit audit reconciles persisted
event/position evidence, exact quantities, cash, NAV, counts, returns and hashes.
Post-commit audit runs in a separate read-only Session. Pre-commit error rolls
back everything with committed rows zero. An uncertain commit produces
COMMIT_OUTCOME_UNKNOWN and nullable committed count; a post-commit verification
failure reports POST_COMMIT_AUDIT_FAILED with committed rows retained. No-op
creates no rows and performs no commit. Audit does not claim the absence of all
unrelated database changes.
An unsuccessful rollback is reported separately as ROLLBACK_FAILED with nullable
committed count; it is not labelled a confirmed rollback.

## 18. Migration and retention

Revision 202610030002 follows 202610030001, creates only four Shadow tables and
does not modify upstream or paper tables. Explicit RESTRICT FKs preserve parent,
Bond, market and cashflow references. Downgrade checks every Shadow table before
any DDL and refuses if any row exists. No deletion or automatic history repair is
part of the service API.

## 19. Capabilities, verification and handoff

Run/genesis persistence, explicit atomic APPLY, append-only ledger, contractual
coupon/amortization/redemption accounting, immutable daily marking, simple NAV
returns, idempotency and historical-drift gates are implementation capabilities.
Scheduler, rebalance, reoptimization, costs, slippage, market impact, tax, broker
execution, benchmark/excess return, experiment success, CFA and PIT stay false.

Focused tests cover disposable SQLite accounting/mutation, migration and static
isolation. The six specified Task302/DV01/market/cashflow modules are regressed;
compile, Alembic head and scope/diff checks precede one commit/push. Full local
suite is deferred to exact-commit CI. PostgreSQL locks are checked structurally;
no PostgreSQL production or VDS validation is authorized.

The only named handoff is TASK304_90_DAY_EXPERIMENT_POLICY_AND_BENCHMARK after
independent review. Official Day 0 requires fresh separately authorized production
genesis after the experiment policy and benchmark semantics are frozen.
