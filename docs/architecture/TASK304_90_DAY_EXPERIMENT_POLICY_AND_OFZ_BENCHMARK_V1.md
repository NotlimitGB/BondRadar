# Task304 — 90-Day Experiment Policy and OFZ Total-Return Benchmark v1

## 1. Scope

Eight new files provide immutable contracts, read-only benchmark and experiment loaders, and a pure comparison evaluator. Task268/272/273/298–303 remain unchanged. No tables, migration, API, activation, scheduler, official Day 0, live requests or production access are introduced.

## 2. Frozen policy

`shadow-experiment-policy-v1` fixes 90 calendar days; source `moex`; curve and market age 7; no strategy/benchmark rebalance or cashflow reinvestment. The benchmark is `DURATION_MATCHED_OFZ_TOTAL_RETURN_V1`, `FRACTIONAL_FULLY_INVESTED`. Common Genesis trade date is required. Success is `POSITIVE_ABSOLUTE_AND_POSITIVE_OFZ_EXCESS`. There are no overrides.

## 3. Immutable contracts

All new models use strict validation, frozen fields, `extra="forbid"`, and `pit_ready=False`. Tuples preserve deterministic evidence ordering. READY/BLOCKED Genesis, READY/UNAVAILABLE observations and daily views, and IN_PROGRESS/PASS/FAIL/INDETERMINATE comparison verdicts preserve stable nullable fields. Full upstream views remain attached.

## 4. Public interfaces

`OfzTotalReturnBenchmarkService(db).build_genesis(shadow_execution=…, policy=…)` and `.build_daily(genesis=…, as_of_date=…)`; `ShadowExperimentGenesisService(db).build(reviewed_genesis_plan=…, shadow_execution=…, policy=…)`; `ShadowExperimentComparisonService(db).build(experiment_genesis=…, as_of_date=…)`; pure `ShadowExperimentEvaluator.build(experiment_genesis=…, strategy_observation=…, benchmark_daily=…)`.

## 5. Source ownership

Task268 owns eligible OFZ nodes and original Macaulay duration. Task272 owns modified duration; Task273 owns dirty value. Task303 owns strategy accounting and accepted cumulative return. No pricing, modified-duration, strategy or risk formulas are reconstructed. Task303's existing dirty-value validator is reused; overall DV01 READY is not required.

## 6. Representative selection

Build exactly one Task268 curve with source moex and age 7. Validate contract, PIT, date, ordered finite positive durations, node count, range and all component-array alignment. For each node choose minimum `(abs(component yield − node yield), Bond ID, snapshot ID)`. Load Task272 exactly once for every representative. A single unavailable or contradictory representative blocks the entire Genesis.

## 7. Modified-duration basis

The agreed refinement is `MODIFIED_DURATION_TASK272`. The target is exclusively Task302 post-rounding `invested_weighted_modified_duration_years`. Preserve both original node Macaulay duration and authoritative modified duration. Sort representatives by modified duration, Bond ID, snapshot ID. Exact duration selects one; otherwise choose nearest lower/upper modified durations. Equal-duration collisions resolve by minimum Bond/snapshot IDs. Outside-range targets block, without extrapolation.

## 8. Strict weights

For lower/upper durations Dl/Du and target Dt, `Wl=(Du−Dt)/(Du−Dl)` and `Wu=(Dt−Dl)/(Du−Dl)`. Exact matching uses weight 1. All calculations use fresh Decimal precision 28, HALF_EVEN. Require strictly `Wl+Wu=1` and `Wl×Dl+Wu×Du=Dt`; failure is `BENCHMARK_DECIMAL_INVARIANTS_FAILED`. No tolerance, quantize or complement correction is allowed.

## 9. Genesis valuation

Load Task273 only for selected one/two representatives. Snapshot IDs, identifiers, profile IDs and dates must agree with Task268/272. Every strategy and benchmark Genesis trade date must agree. Weekend request dates are allowed when age ≤7. Genesis factors equal 1; `NAV0=capital×Σweights=capital`, with both returns 0.

## 10. Experiment binding

Validate the reviewed Task303 EXECUTABLE plan, no blockers, canonical SHA, initial empty Shadow state, source universe and execution hashes, positions/events and snapshot evidence. Bundle policy, reviewed strategy plan, Shadow execution, universe, benchmark, dates, capital and target hashes. Task303 APPLY is never called by Task304.

## 11. Daily evidence

Dates must lie between Genesis and Genesis+90 inclusive. One narrow SELECT reads selected Bonds' persisted MOEX cashflows in `(Genesis, date]`; Day 0 needs no query. Current and previous-calendar-day valuations reuse that evidence and frozen Genesis. Task273 is called once for each active component/date with age 7; no price is requested after redemption.

## 12. Cashflow accounting

Order events by date, coupon/amortization/redemption, then Bond ID. Coupon and amortization add supplied RUB amount per original unit without reinvestment. Redemption adds supplied amount and ends market valuation. Duplicate automatic events or automatic events after redemption block. Offers add no cash and remain diagnostic. Unknown relevant types, invalid amounts/currency or missing amounts make valuation unavailable; nominal is never substituted.

## 13. Total return

Active value is dirty value plus accumulated contractual cash; redeemed value is accumulated cash. `factor=value/Genesis dirty value`; `NAV=capital×Σ(weight×factor)`. No fractional quantities, lot rounding, rebalance or real-fill claim is used. `daily_return=NAVt/NAVprevious−1`; `cumulative_return=NAVt/capital−1`. Zero previous NAV blocks. All unavailable calculated values remain null.

## 14. Strategy observation

Read Task303 run/history through its repository under no_autoflush. Audit original Genesis provenance and every accepted snapshot; require the continuous calendar chain Day 0 through requested day. Strategy cumulative return is copied, never recomputed. Existing strategy residual cash remains part of its accounting; the reference benchmark is fully invested.

## 15. Comparison and verdict

`excess=strategy cumulative return−benchmark cumulative return`. Before Day 90 verdict is always IN_PROGRESS, while evidence availability is separate. Day 90 complete evidence passes only when strategy return >0 and excess >0; zero/equality fails. Missing, mismatched or unaudited evidence gives INDETERMINATE with null comparison values. Dates after the horizon are rejected. This is an experiment criterion, not statistical alpha, a recommendation or a success claim from a short sample.

## 16. Hash material

SHA-256 uses timestamp-free canonical JSON: sorted keys, ASCII escaping, compact separators, finite Decimal canonical strings, no NaN/Infinity. Source IDs remain identities; technical timestamps are excluded. Policy, benchmark Genesis, daily inputs/output, experiment bundle and comparison are independently bound. No clock, UUID, filesystem or credential input is used.

## 17. Verification

Three hermetic focused modules cover real isolated SQLite dependency chains, source gates, representative/weight decisions, cash events, Day 0 and terminal verdicts, immutable serialization, caller Decimal context, SELECT-only operation and pending-state preservation. Regress Task268, Task272/273, Task302 and Task303 modules. Full backend regression is deferred to exact-commit CI.

## 18. Capabilities

Experiment policy, duration matching, OFZ total return/cashflow/daily valuation, comparison, excess return, success verdict and experiment Genesis planning are ready. Benchmark investability, persistence, activation, scheduler, rebalance, reinvestment, execution costs/slippage/impact/tax, broker execution, official Day 0 and PIT are false.

## 19. Delivery and handoff

One commit `Add 90-day experiment policy and OFZ benchmark`, one normal push after concurrency/scope review, one exact-commit CI snapshot without polling. Local acceptance and CI status are reported separately. Task305 persistence/activation requires independent review and a separate request. No production/VDS, deploy, live source or official experiment start occurs here.
