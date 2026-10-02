# Task299 — Investment Model v1

## 1. Purpose

Investment Score v1 measures current cross-sectional attractiveness under the defined policy. It is not a forecast of realized return. It is neither a default-probability model nor an ML model. The foundation evaluates only supplied CORE M3 candidates.

## 2. Scope and baseline

Baseline: `6da174624872511801dea70689f4839f037392b2`. Eight new files provide schemas, Decimal math, reducer, orchestration, three synthetic/offline test modules and this document. Existing Task267–298 implementations, eligibility, schemas, database and API remain unchanged. Temporary directories are preserved.

## 3. Interfaces

`InvestmentModelReducer.build(batch, peer_contexts)` consumes a `UnifiedCandidateBatchView` and deterministic sequence of `InvestmentPeerContext`. `build_peer_contexts(batch)` creates those contexts without acquisition. `InvestmentModelSnapshotService(db).build(bond_ids, as_of_date, *, market_source="moex", max_market_age_days=7, max_curve_age_days=7, liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5)` obtains one Task298 batch, constructs contexts and invokes the reducer once under `no_autoflush`.

## 4. Contracts

Strict frozen Pydantic models forbid extra fields. Policy version is `investment-model-policy-v1`; outputs are `investment-evaluation-v1` and `investment-evaluation-batch-v1`. Each evaluation attaches its exact original candidate and all attempted contexts. Each batch attaches the source batch, preserving requested partitions and Task298 exclusions. No excluded Bond is scored. All outputs declare `pit_ready=False`.

## 5. Validation

Unsupported versions, PIT drift, malformed types, duplicate identities, contradictory partitions/counts, context drift, source/projection mismatch, nonfinite required values and out-of-range inputs raise `ValueError` before partial output. Model-copy/construct bypasses are revalidated. Context validation checks target, selector, exact key, market provenance, expected peer membership, counts, statuses and availability. It does not recompute Task277 statistics or financial formulas. Inputs are never repaired.

## 6. Credit universe and delegation

Peers come only from `batch.candidates`. For each distinct represented target-kind/agency selector, Task279 composes one member for every candidate. Task277 receives that same tuple for each target that represents the selector, including the target and unavailable members. Task277 owns self-exclusion, exact cohort filtering and descriptive mathematics. No Task280 synthetic envelope, additional DB scan or Task281 orchestration is introduced.

## 7. Exact source-native cohorts

The five-field key remains `target_kind`, `rating_agency`, `source_provider`, `rating_scale_raw`, `rating_value_raw`. Strings are copied exactly; null and blank scales differ. There is no inheritance, agency preference, raw-label averaging, grade map or cross-agency rating normalization. All attempted contexts, including unavailable selectors, remain observable.

## 8. Conservative credit selection

Minimum peers is frozen at `2`. The credit component is the minimum percentile among READY Task277 distributions: `MIN_READY_SOURCE_NATIVE_COHORT_PERCENTILE_V1`. Equal minima select the first exact key ordered by target kind, agency, provider, scale presence, scale string and raw value. The whole READY set is retained. Multiple cohort percentiles are compared; rating labels are not interpreted.

## 9. Scorability and normalization universe

Evaluations are `READY` only with at least one READY credit context. Otherwise they are `CREDIT_PEER_CONTEXT_UNAVAILABLE`; total score, batch percentiles and rank are null. Raw evidence, attempted contexts and the independent liquidity value remain available. The model-scorable universe is fixed before normalization, comprises all READY evaluations, and is distinct from the full credit peer universe. There is no iterative universe adjustment. Empty candidate/READY sets are valid and produce empty ranks and null score extrema.

## 10. Batch percentile

For an observation present in a universe of size `N > 1`:

```text
100 × (lower_count + (equal_count − 1)/2) / (N − 1)
```

Singleton returns `Decimal("50")`. Unique extrema receive 0/100; tied extrema share empirical group midrank without stretching; all-equal observations receive 50. This helper normalizes batch OFZ spread and duration only. Task277 retains its distinct target-versus-peers percentile formula unchanged.

## 11. Score policy

```text
score = 0.40 × OFZ percentile
      + 0.30 × minimum READY credit percentile
      + 0.20 × Task270 liquidity score
      + 0.10 × (100 − modified-duration percentile)
```

Policy weights are finite, nonnegative, sum exactly to one and must equal the frozen vector. Public APIs do not accept arbitrary weights or minimum overrides. Higher OFZ spread increases its batch percentile. Lower modified duration is preferred by this policy; it does not impose a Risk Engine duration limit. Liquidity is copied exactly, without re-scoring or normalization. Every component and total lies within 0–100.

## 12. Decimal and ranking

Arithmetic uses fresh `Context(prec=28, rounding=ROUND_HALF_EVEN)` with no binary float, presentation rounding, clock or random identifier. READY ordering is total DESC, credit DESC, OFZ DESC, liquidity DESC, duration DESC, Bond ID ASC. Exact comparisons do not depend on the caller's Decimal context. Ordinal ranks are 1 through N with no gaps. Evaluations themselves are ordered by Bond ID. No attractiveness bands or invented thresholds are supplied.

## 13. Evidence and provenance

Outputs retain YTM, spread, reference yield, modified duration, DV01 and Task270 score verbatim, full candidate M3 evidence, selected and attempted distributions, peer counts, keys and spread-minus-median diagnostics. Provenance records candidate/batch versions, formula/policy versions, percentile method, conservative-selection method, actual distribution versions, candidate/normalization IDs, market snapshot, curve date, M3 date and the exact policy. The source batch retains all loader parameters and request/existence/exclusion identities. No timestamp is introduced.

## 14. Meaning and limitations

Rank is relative to the supplied batch and is not a buy/sell instruction. A changed batch can change scores. Cohort coverage can limit availability even when Task298 CORE readiness holds. There is no return forecast, backtest claim, expected P&L, default probability, expected loss, capital allocation, portfolio quantity or risk budget. Maturity remains Task267 market metadata through the attached Task298 candidate. PIT readiness remains false.

## 15. Verification

Run the three Task299 focused modules, then Task298 reducer/snapshot and Task277/281 regression modules. Compile the four new application modules and check working/staged diffs and exact eight-file scope. Synthetic views and disposable SQLite verify identities, contracts, minimum, multiple cohorts, ties, exact weighted values, all ranking tie-breaks, exceptions, SELECT-only loading, pending-state preservation, immutability, stable serialization and Decimal isolation. Full backend regression is delegated to exact-commit CI; no live source or production checks are authorized.

## 16. Capabilities and handoff

```ini
INVESTMENT_MODEL_READY=true
INVESTMENT_SCORE_READY=true
INVESTMENT_RANKING_READY=true
RECOMMENDATION_READY=false
PORTFOLIO_CONSTRUCTION_READY=false
RISK_ENGINE_READY=false
SHADOW_EXECUTION_READY=false
TRANSACTION_COST_MODEL_READY=false
SLIPPAGE_MODEL_READY=false
MARKET_IMPACT_MODEL_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
DEFAULT_PROBABILITY_READY=false
EXPECTED_LOSS_READY=false
PIT_READY=false
```

Delivery is one commit `Add Investment Model v1 foundation` and one normal push, after baseline, scope and remote-concurrency checks. Exact-commit CI is reported as a snapshot without waiting. Independent review of code, SHA and CI precedes a separately requested Risk Engine v1 foundation. No deployment, production scoring, Shadow, broker operation or downstream implementation is authorized.
