# Task278 — Shared OFZ Curve Evaluation Foundation v1

## 1. Decision

Task278 extracts Task268 target-against-curve calculation into the pure
`evaluate_market_against_ofz_curve()` function. It consumes an already selected
`BondMarketFeatureView` and an already built `OfzReferenceCurveView`, returning
the existing `BondRelativeValueView`. Results remain computed views and are not
persisted.

`OfzReferenceCurveService.evaluate_bond()` retains argument validation, Task267
market selection, curve construction, the shared `no_autoflush` boundary and
Task267 HTTP 404. It delegates exactly once after both inputs are available.
There is one implementation of readiness, interpolation, spread and relative
value provenance.

## 2. Performance problem

Previously every `evaluate_bond()` call rebuilt the OFZ curve and then performed
target evaluation in the same method. Repeating that method for a future batch
would repeat an identical OFZ scan for every target with the same date, source
and curve-age boundary.

The extracted function permits a future caller to build one curve and evaluate
multiple prebuilt market views against it. Task278 provides reusable computation,
not caching, orchestration or a batch service.

## 3. Existing Task268 semantics

Public contracts remain `ofz-reference-curve-v1` and
`bond-relative-value-v1`. Public schemas and statuses are unchanged. The
existing precedence remains market missing, stale, yield missing, duration
missing/nonpositive, curve unavailable, trade-date mismatch, then duration
outside the curve. All detected limitations remain sorted unique flags.

Task268 curve construction is unchanged: OFZ identity and Security Master
eligibility, exact-source snapshot selection, Task271 MOEX duration semantics,
freshness, coherent latest date, duplicate-duration median, diagnostics and
component provenance retain their existing behavior. Legacy bond terms and
persisted `spread_to_ofz` remain unused.

For valid Task267/Task268 inputs the complete delegated model is equivalent to
the former service output: identity, status, market values, curve, reference,
spreads, method, provenance, flags and PIT declaration are preserved.

## 4. Pure target-against-curve evaluator

```python
evaluate_market_against_ofz_curve(
    target: BondMarketFeatureView,
    curve: OfzReferenceCurveView,
    *,
    as_of_date: date,
    market_source: str,
) -> BondRelativeValueView
```

The date must be an exact calendar date and source a nonblank string. Inputs
must be the two authoritative schema object types; wrong external types raise
`ValueError`. Versions, `pit_ready=False`, requested date and exact source must
agree. No source normalization occurs.

A contradictory target contract maps to the existing
`TARGET_MARKET_MISSING` status and adds `TARGET_CONTRACT_INVALID`. A FRESH
target must have an exact positive snapshot ID and exact trade date. The
selected mapping avoids a new Task268 public status. Valid available market
values remain visible in blocked output.

## 5. Curve integrity

The curve version, PIT declaration, as-of date and exact source must agree with
the request. A READY curve additionally requires:

```text
node_count == len(nodes) and node_count >= 2
exact curve_trade_date
finite positive Decimal node durations
strictly ascending unique durations
finite Decimal node yields
min/max equal the first/last node durations
```

Contradictory READY evidence is not repaired, reordered, deduplicated or
coerced. It returns the existing `CURVE_NOT_READY` status with
`CURVE_INTEGRITY_INVALID`; the original malformed curve remains nested in the
result. Ordinary valid non-READY curves retain their existing status-specific
flag, such as `CURVE_NO_ELIGIBLE_OFZ`.

## 6. Target market contract

Task267 remains authoritative for bond identity, snapshot selection, source,
freshness, YTM and repaired Macaulay duration. The evaluator reads only the
provided Task267 view. It does not query snapshots, inspect legacy Bond fields,
reparse MOEX raw payload or replace a missing input.

Only finite Decimal YTM and duration are usable. Nonpositive duration retains
`TARGET_DURATION_MISSING` plus `TARGET_DURATION_NONPOSITIVE`. Present invalid
numeric evidence remains null in the output and adds the existing invalid flag.

## 7. Exact-node semantics

Exact Decimal equality selects the node yield and
`interpolation_method=EXACT_NODE`. There is no epsilon or approximate search.
Both lower and upper provenance identify the same node, including the same
component snapshot IDs.

## 8. Linear interpolation

A duration strictly inside two nodes uses the nearest lower and upper nodes:

```text
reference = lower_yield
          + (target_duration - lower_duration)
          * (upper_yield - lower_yield)
          / (upper_duration - lower_duration)

spread_to_ofz_pp = target_yield_pct - reference
spread_to_ofz_bps = spread_to_ofz_pp * 100
```

Arithmetic uses a fresh local Decimal context with precision 28 and
`ROUND_HALF_EVEN`. There is no float conversion, tolerance, scaling change or
presentation rounding. Caller precision, rounding, flags and traps are not
modified.

## 9. No extrapolation

Duration below the minimum or above the maximum returns
`TARGET_DURATION_OUTSIDE_CURVE`. Reference yield, both spreads, interpolation
method and node-side provenance remain unavailable. Boundary equality is an
exact node. `CURVE_EXTRAPOLATION_READY=false`.

## 10. Behavioral equivalence

The service builds the same target and curve under `no_autoflush`, then calls
the pure evaluator once. It contains no second exact-node, interpolation or
spread implementation. Existing Task268 tests retain their financial
expectations.

Integration tests compare full `model_dump()` results from direct pure
evaluation and delegated `evaluate_bond()` for READY exact/interpolated,
market-missing, stale, date-mismatch, outside-range and curve-unavailable
states. A delegation spy and AST assertion prove the single call and single
implementation.

## 11. Provenance

Existing provenance is unchanged: target snapshot ID/date, curve trade date,
lower and upper durations/yields and complete component snapshot ID lists.
Blocked results retain available top-level target and curve provenance while
derived provenance is null/empty. Output list construction does not mutate
caller-owned node component lists.

## 12. Read-only boundaries

The evaluator imports schemas and Python date/Decimal utilities only. It has no
Session, SQLAlchemy, ORM model, service dependency, source client, network,
filesystem access or mutation call. It performs no SELECT because the caller
already supplies both views.

The existing service remains SELECT-only and preserves caller-owned
`new/dirty/deleted` state with autoflush enabled. No curve table, persisted
cache, Redis, memoization store, migration, model, API or frontend change is
introduced.

## 13. PIT boundary

Every output remains `pit_ready=False`. A pure reusable calculation does not
repair the current Security Master historical-observability boundary or prove
that all inputs were known at the requested historical instant.

## 14. Explicit non-goals

Task278 does not discover a bond universe, build Task276 members, load credit
cohorts, call Task277, rank peers, calculate adjusted spreads, produce an
investment score or recommendation, or implement strategy, portfolio, Risk
Engine, backtest, Shadow or broker execution.

Capability declarations remain:

```text
PURE_EVALUATOR_READY=true
CURVE_EXTRAPOLATION_READY=false
BATCH_MEMBER_BUILD_READY=false
PEER_UNIVERSE_DISCOVERY_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PEER_RANKING_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

## 15. Verification

Tests cover exact/interpolated signed spreads, every existing unavailable
family, argument and contract validation, all required READY-curve integrity
contradictions, provenance, stable serialization, input immutability, local
Decimal isolation and AST purity.

Local acceptance:

```text
Task278 pure evaluator: 64 passed
Task278 + full Task268 focused regression: 183 passed
Task276 direct regression: 127 passed
compileall: passed
```

Pytest reported only the existing cache-permission warning. Full backend suite,
production/VDS and live sources are skipped by the explicit Task278 contract.
Working/staged diff checks and exact five-file review are required before
delivery.

## 16. Handoff

After independent Task278 verification, the only named next task is Task279 —
Efficient Credit-Cohort Batch Member Builder v1, under a separate request.
Task279 may build one OFZ curve for an explicit bond-ID universe and reuse this
evaluator. `BATCH_MEMBER_BUILD_READY=false` and
`PEER_UNIVERSE_DISCOVERY_READY=false` remain false here. Task279 is not started
by Task278.
