# Task285 — Efficient Liquidity Batch Feature Builder v1

## 1. Decision

Task285 adds a read-only batch builder for an explicitly supplied sequence of
bond IDs. It produces Task270-equivalent liquidity views while loading the
global liquidity benchmark window once.

## 2. Performance problem

Calling Task270 once per bond repeats the same full-window market snapshot
query and the same benchmark-universe evaluation. Task285 separates loading
from pure evaluation so one request performs one shared load and one shared
evaluation.

## 3. Explicit output universe

The requested, unique, sorted bond IDs are the exact output universe. Task285
does not discover bonds to return. Missing IDs are retained as
`BOND_NOT_FOUND` items with a null feature.

## 4. Global Task270 benchmark universe

The score benchmark remains every database bond with an exact-source snapshot
inside the inclusive calendar window. The snapshot query is intentionally not
restricted to requested output IDs. This is Task270 benchmark construction,
not automatic discovery of an investment universe.

## 5. Shared pure evaluator

`evaluate_liquidity_features` receives plain immutable identity and snapshot
loader records plus the Task267 liquidity parser. It performs daily selection,
aggregation, eligibility, percentiles, score calculation, status selection and
feature projection without ORM, database, network or persistence access.

The evaluator uses a fresh Decimal context with precision 28 and
`ROUND_HALF_EVEN`. It does not use float arithmetic or presentation rounding.

## 6. Database loading

The service first selects only `Bond.id`, `Bond.isin` and `Bond.secid` for the
requested IDs in ID order. If at least one exists, it selects snapshot ID, bond
ID, trade date and raw payload for the exact source and inclusive window,
ordered by bond ID, date and snapshot ID. Legacy liquidity fields are not read.

All reads run under `Session.no_autoflush` and no session mutation method is
called.

## 7. One-window-scan contract

For at least one existing requested bond, execution uses one requested-identity
query, one market-window query, one universe evaluation and one feature
projection per existing bond. If every requested ID is missing, it uses only
the identity query and performs no market-window query or evaluation.

The diagnostics describe the current service execution; they are not durable
database telemetry.

## 8. Daily observation semantics

For each bond and trade date, the greatest snapshot ID is authoritative. An
unusable selected payload is not replaced by an older same-day row. Parsing
retains Task270/Task267 VOLUME, VALUE, NUMTRADES, decimal-comma, malformed,
conflict, fractional-trade and legacy VALUE-fallback behavior. Raw payloads are
never modified.

## 9. Score-universe semantics

Eligibility requires at least `min_observation_days`, nonnegative available
median turnover and median trade count, and available recency. The minimum
eligible universe remains ten bonds. Requested bonds participate under the
same rules as all other benchmark bonds.

## 10. Task270 status and score preservation

Status precedence remains:

```text
NO_MARKET_DATA
INSUFFICIENT_TARGET_EVIDENCE
MISSING_SCORE_COMPONENT
INSUFFICIENT_UNIVERSE
READY
```

Percentiles retain
`100 × (worse + (equal − 1) / 2) / (N − 1)`, with higher turnover and trade
count preferred and lower age preferred. Fully tied components produce 50.
The score remains `0.50 × turnover + 0.35 × trades + 0.15 × recency`.

## 11. Missing IDs

Missing bonds produce `BOND_NOT_FOUND` items and no synthetic Task270 view.
`COMPLETE`, `PARTIAL` and `NO_EXISTING_BONDS` describe identity/build
completeness, not liquidity-score availability.

## 12. Batch contract

The frozen, extra-forbid contract is
`bond-liquidity-batch-feature-v1`. It includes request context, sorted ID
partitions, item rows, build and readiness counts, factual execution
diagnostics, capability declarations and `pit_ready=false`.

Every `BUILT` item contains the complete `bond-liquidity-feature-v1` view.
`ready_feature_count` counts only Task270 `READY` views; other built views are
reported as unavailable without aborting the batch.

## 13. Task270 compatibility

The public Task270 schema and `build_for_bond` signature are unchanged. The
single service still returns HTTP 404 `Bond not found` before loading the
window. Existing helper names remain importable from the Task270 service.
For identical inputs, each batch feature has the same complete serialized
representation as a direct Task270 call.

## 14. Read-only boundary

Task285 performs SELECT statements only. It does not insert, update, delete,
flush or commit; does not change legacy scores or raw payloads; and does not
call Task274, Task282, Task283, broker, network, ranking or recommendation
logic.

## 15. PIT boundary

The batch and every Task270 feature declare `pit_ready=false`. Calendar-window
filtering and exact source selection do not constitute historical PIT
certification.

Capabilities are:

```text
EXPLICIT_OUTPUT_BOND_UNIVERSE_READY=true
SHARED_LIQUIDITY_WINDOW_LOAD_READY=true
SHARED_LIQUIDITY_UNIVERSE_EVALUATION_READY=true
TASK270_EQUIVALENT_BATCH_BUILD_READY=true
AUTOMATIC_INVESTMENT_UNIVERSE_DISCOVERY_READY=false
TRANSACTION_COST_MODEL_READY=false
LIQUIDITY_ADJUSTED_SPREAD_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

## 16. Verification

Acceptance uses the focused batch suite, the combined Task285/Task270 suite,
the Task284 composer/service regression, compile checks, diff checks and exact
seven-file scope review. It verifies full serialized equivalence, query counts,
global benchmark reuse, deterministic ordering, Decimal isolation, SELECT-only
behavior and preservation of caller-owned session state.

The full backend suite, production/VDS and live-source checks are outside this
task.

## 17. Handoff

Task286 may consume Task285 batch liquidity outputs to construct an efficient
M3 audit snapshot after independent Task285 review and a separate request.
Task285 does not implement Task286.
