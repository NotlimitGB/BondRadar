# Task304A — Multi-Capital Shadow Scale Experiment Matrix v1

## 1. Question and scope

How does the same BondRadar methodology behave as deployable capital grows from
50k RUB to 10m RUB under one common 90-day forward market period? This package
adds immutable application contracts, read-only Genesis orchestration and pure
comparison aggregation. It does not activate or persist an experiment.

Baseline: `cfbbcc128b402f2b8bd43ba249e2b60823b5b21e`; Alembic remains
`202610030002`. Task304 financial semantics remain authoritative and unchanged.

## 2. Frozen policy

`shadow-scale-experiment-policy-v1` freezes this exact ascending Decimal tuple:
`50000, 100000, 500000, 1000000, 3000000, 5000000, 10000000` RUB. Missing,
duplicated, reordered or substituted policy values are invalid. There are no
overrides or preferred capital. The nested `ShadowExperimentPolicyV1` carries
the existing horizon, modified-duration benchmark basis and success rule.
The 3m and 5m cases avoid a blind tenfold jump between 1m and 10m; their inclusion
does not establish an income claim.

## 3. Independent capital inputs

`ShadowScaleExperimentCaseInputV1` contains exact Decimal `capital_rub`, full
Task302 `source_shadow_execution` and reviewed Task303
`reviewed_strategy_genesis`. The service accepts a deterministic Sequence with
exactly seven cases, snapshots it once and orders it by the frozen grid.
Strings, bytes, mappings, sets and iterators are rejected before delegation.
Duplicate, unknown, missing or substituted capital is rejected with ValueError.

Capital must match Task302 summary, Task301 request/summary and post-rounding
Task300 metrics. Task303 has no standalone initial-capital field: its Genesis
snapshot NAV must equal capital when a snapshot exists. A correctly typed
blocked plan may have no snapshot; no capital is fabricated for it.

## 4. Independent reconstruction boundary

Upstream Task300/301/302 must be prepared independently for each capital. This
layer validates their capital bindings and upstream evidence; it does not prove
historical execution events or run the upstream pipeline itself. It never
multiplies another case's quantities, lots, weights, cash or returns. Liquidity
capacity, admissible size, issuer concentration, composition, lot rounding,
residual cash, duration and DV01 can therefore differ between capitals.

## 5. Genesis orchestration

`ShadowScaleExperimentGenesisService(db).build(*, cases, scale_policy)` validates
all outer arguments before dependency calls. Under a shared `no_autoflush`, it
calls `ShadowExperimentGenesisService.build` exactly once per ascending capital
and attaches its exact resulting experiment. There is no own SQL, mutation,
Task302 orchestrator or Task303 APPLY. Unexpected dependency exceptions propagate.
READY cases reuse the existing pure reviewed-Genesis and experiment validators.

## 6. Common evidence gates

All seven cases must agree on Genesis and planned-end dates, exact `moex`, source
code SHA, Task303 source-universe SHA, experiment policy and its SHA, and common
market trade date. Full canonical attached Task299 Investment batch SHA is also
required equal. This prevents equal partition hashes concealing different
economics or rankings. For Bonds selected in multiple cases, the complete
Task302 per-Bond execution terms must agree; different selected sets are allowed.

Each cross-case mismatch blocks the matrix and receives a sorted unique reason.
Common metadata becomes null where equality is not proven. Case READY/BLOCKED
counts describe actual upstream cases: all seven may be READY while a cross-case
gate blocks the matrix. No blocked case is dropped or silently activated.

## 7. Capital-specific benchmark

Each case uses its own existing Task304 Genesis authority, benchmark capital,
post-rounding duration target and benchmark hash. Task304 selects and validates
modified-duration representatives, exact/bracket matching and strict Decimal
invariants. Task304A does not change these rules or require equal portfolios,
durations, component IDs or benchmark hashes between capitals.

## 8. Exact Genesis projection map

| Case field | Sole source |
|---|---|
| strategy_selected_count | Task301 summary.selected_count |
| strategy_target_invested_weight / cash_weight | Task301 summary.invested_weight / cash_weight |
| shadow_planned_position_count | Task302 summary.planned_position_count |
| shadow_planned_invested_rub | Task302 summary.planned_shadow_invested_rub |
| shadow_residual_cash_rub | Task302 summary.shadow_cash_rub |
| shadow_realized_invested_weight / cash_weight | Task302 summary.realized_invested_weight / realized_cash_weight |
| shadow_execution_tracking_gap_rub | Task302 summary.execution_tracking_gap_rub |
| target_duration_years | post-rounding Task300 metrics.invested_weighted_modified_duration_years |
| post_rounding_risk_status | Task302 summary.post_rounding_risk_status |
| max_position_weight_observed / max_issuer_weight_observed | post-rounding Task300 metrics |
| portfolio_dv01_per_100k_rub | post-rounding Task300 metrics |
| benchmark_matching_mode / component Bond IDs | Task304 benchmark matching_mode / components |

These fields are copied, never independently recalculated.

## 9. Daily comparison API and validation

`ShadowScaleExperimentEvaluator.build(*, scale_genesis, comparisons)` is pure.
It requires a validated READY matrix and exactly seven typed Task304 comparison
objects with common as-of date inside the original horizon. Capital, dates,
experiment SHA, strategy observation identity and benchmark identity bind each
comparison to its corresponding case. Full replay through the existing pure
Task304 evaluator must match the supplied comparison, including status, verdict,
returns, flags, source evidence and hash. Malformed inputs raise ValueError
without a partial result. Inputs and caller Decimal context remain unchanged.

## 10. Scale rows and RUB profits

Rows retain the original comparison object and its individual verdict. They
copy strategy/benchmark/excess returns, capital, case and experiment hashes,
initial realized investment/cash weights, initial strategy-selected count and
initial target duration. Only three financial calculations belong to Task304A:

```text
strategy_profit_rub = capital_rub × strategy_return
benchmark_profit_rub = capital_rub × benchmark_return
excess_profit_rub = capital_rub × excess_return
```

They use a fresh Context(prec=28, rounding=ROUND_HALF_EVEN), no floats or quantize.
For unavailable returns the corresponding metrics are null. These are experiment
NAV deltas, not broker realized P&L, guaranteed income, monthly income or
after-tax profit. Return and excess return compare quality across capitals;
absolute RUB profit describes scale and must not replace those metrics.

## 11. Status and verdict authority

With all seven comparisons READY, matrix status is IN_PROGRESS before Day90 and
COMPLETE on Day90. Any unavailable comparison makes the matrix INDETERMINATE on
any date. Its individual pre-Day90 Task304 verdict remains IN_PROGRESS. At Day90
individual PASS/FAIL/INDETERMINATE comes solely from Task304. There is no matrix
PASS, no success-if-any-capital-passes and no ex-post capital selection.

## 12. Descriptive ties

Highest strategy-return and highest excess-return capital tuples exist only
when every comparison is READY. All tied capitals are returned in ascending
order. Otherwise both fields are null. These diagnostics do not recommend an
amount or create an optimal-capital claim.

## 13. Canonical hashes and immutability

Contracts are strict, frozen, extra-forbid and `pit_ready=False`; JSON roundtrip
preserves Decimal/date/tuple values. Existing Task304 canonical hashing uses
sorted keys, ASCII escaping, compact separators and no nonfinite JSON numbers;
technical timestamps are excluded. Policy, case, Genesis, row and comparison
hashes bind their complete timestamp-free content, excluding their own SHA.
Cases bind capital and full execution, reviewed plan, benchmark and experiment.
Matrices bind the frozen grid, common evidence, ordered cases and full sources.
Input ordering does not affect serialized output or hashes.

## 14. Capabilities

True: multi_capital_policy_ready, multi_capital_genesis_ready,
independent_capital_rebuild_ready, multi_capital_benchmark_ready,
scale_comparison_ready, scale_profit_comparison_ready,
scale_evidence_completeness_ready. The rebuild flag denotes required independently
bound inputs, not upstream orchestration performed by this module.

False: persistence_ready, activation_ready, scheduler_ready,
optimal_capital_claim_ready, monthly_income_claim_ready, after_tax_income_ready,
rebalance_ready, broker_execution_ready and PIT. No schema migration, model, API,
frontend, activation, scheduler, pricing formula or legacy path is changed.

## 15. Verification

Hermetic tests build all seven upstream chains independently on a common
synthetic Investment batch and isolated SQLite. They cover different selected
sets, exact projection, delegation order/count, SELECT-only reads, pending-state
preservation, missing/duplicate capitals, policy coercion, blocked cases, every
common-evidence gate, overlapping terms drift, hash/return tampering, unavailable
rows, ties, Day0/89/90 behavior, Decimal isolation and static mutation boundaries.
Focused tests precede Task304, Task302 and Task303 Genesis regressions. Compile,
Alembic head and working/staged checks close local acceptance. Full suite is
deferred to exact-commit CI; pending CI is not reported as green.

## 16. Delivery and handoff

Exactly six new files, one `Add multi-capital Shadow experiment matrix` commit
and one ordinary push after remote concurrency checks. No rebase/amend/force
push. Observe one exact-commit CI snapshot without polling. Production, VDS,
live sources, migration, deployment, official Day0, experiment storage and
activation remain excluded. Next: separately requested
`TASK305_MULTI_CAPITAL_EXPERIMENT_PERSISTENCE_AND_ACTIVATION` after independent
review of code, exact SHA and green CI.
