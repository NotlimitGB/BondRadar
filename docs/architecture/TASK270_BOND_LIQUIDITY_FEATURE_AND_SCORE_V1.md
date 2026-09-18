# Task270 — Bond Liquidity Feature & Score Foundation v1

## 1. Decision

Add a computed, read-only `BondLiquidityFeatureService(db).build_for_bond(bond_id,
as_of_date, *, market_source="moex", lookback_calendar_days=30,
min_observation_days=5)` returning `bond-liquidity-feature-v1`.
All Pydantic models are frozen with `extra="forbid"`. Statuses and capability
declarations use Literals. The result is never persisted.

Code readiness does not imply score availability for every bond/window. Existing
production files, models, migrations, Task267–269, ingestion, API and frontend
remain unchanged. This foundation introduces only its explicitly authorized
relative liquidity score, without credit interpretation or investment selection.

## 2. Existing market evidence

Inputs are existing `BondMarketSnapshot` rows joined to existing `Bond` rows.
The snapshot query projects only ID, bond ID, trade date and raw payload; the
identity query projects only bond ID, ISIN and SECID. No live source, production
database or artifact fetch is required. All SELECTs run under `no_autoflush`,
preserving caller-owned new, dirty and deleted objects even with autoflush enabled.

## 3. Window and source selection

The inclusive calendar window is `[as_of_date - (lookback_calendar_days - 1),
as_of_date]`. The default spans 30 calendar days. Source equality is exact;
case and surrounding whitespace are not normalized. No other-source fallback,
outside-window or future snapshot participates in aggregates, recency or scoring.

Bond ID must be an exact positive int; as-of date an exact date, excluding
bool/datetime. Window and minimum observation days must be exact positive ints,
with minimum no larger than window. Source must be a nonblank string. Invalid
arguments and unrepresentable window bounds raise ValueError before querying.
Missing target raises HTTP 404 `Bond not found`.

## 4. Daily observation semantics

For each bond/date select the greatest snapshot ID, then sort days ascending.
Invalid selected evidence never causes fallback to an older same-day snapshot.
Snapshot observation days count selected dates, including dates with no usable
liquidity components. Recency uses the latest selected snapshot date independently
of parsing success; age is calendar days. Calendar window days equal lookback;
no trading calendar or fabricated sessions are introduced. Synthetic query rows
test same-day duplicates while preserving the model's existing unique constraint.

## 5. Raw volume / turnover / trade-count semantics

Directly reuse Task267 `_liquidity`, including its MOEX, canonical and historical
top-level payload semantics. Actual VOLUME is trade volume in source-native
quantity units; VALUE is RUB turnover for the MOEX market contract; NUMTRADES
is an integer count. No rescaling, nominal conversion or currency conversion is
performed, including for explicitly selected alternate sources.

Nonfinite, negative, boolean, malformed and contradictory representations are
unavailable per component; fractional trade counts are unavailable. Missing is
distinct from explicitly observed zero. Legacy persisted volume from VALUE
fallback is not promoted to trade volume. Its factual flag
`VOLUME_VALUE_FALLBACK_NOT_PROMOTED` is retained; independently supplied actual
VOLUME remains usable. Relevant Task267 malformed/conflict flags propagate;
the unrelated duration-normalization flag does not.

## 6. Aggregation

For each metric independently expose non-null observation count, Decimal median,
Decimal mean, total and strictly positive day count. Even medians average the
middle pair; odd medians select the middle value. Total NUMTRADES is int;
turnover/volume totals are Decimal. Empty metrics yield null aggregates; observed
zeros yield zero aggregates. Means divide totals by that metric's observation count.

Positive-turnover share divides positive turnover days by non-null turnover days.
Positive-trade-count share divides positive NUMTRADES days by non-null NUMTRADES
days. Shares are Decimal in 0..1, with null for a zero denominator. Snapshot days,
calendar days and assumed sessions are not these denominators.

All Decimal calculations run in a fresh local context with precision 28 and
ROUND_HALF_EVEN, independent of and without modifying the caller's context.
No additional presentation rounding is applied. Decimal strings are retained by
Pydantic JSON serialization.

## 7. Evidence sufficiency

Eligibility requires snapshot observation days at least the requested minimum,
non-null nonnegative median daily turnover and median daily trade count, and
available observation age. At least one usable observation of each required
component suffices to construct its median; there is no separate component-day
minimum. Trade volume is optional for scoring because source-native quantity
comparability may be weaker than turnover comparability.

## 8. Relative universe

Candidates are all existing Bonds with at least one exact-source snapshot in the
window. Target participates on identical terms. The same daily selection, parser
and aggregation helpers apply to target and peers. No credit, structural, issuer,
currency or strategy filter is added. Candidate and eligible counts are separate.
At least 10 eligible bonds are required. Payload problems in peers affect their
eligibility without becoming target malformed flags. No full universe rows are
returned. Calculation cost depends on window snapshots; this v1 does not add
caching or database materialization.

## 9. Percentile contract

For N > 1 eligible bonds, let worse_count count strictly worse values and equal_count
count equal values, including target. The exact Decimal formula is:

```text
percentile = 100 × (worse_count + (equal_count − 1) / 2) / (N − 1)
```

Greater turnover and trade count are better; smaller age is better. Percentiles
remain in 0..100. Unique worst and best values receive 0 and 100, respectively.
Tied extrema receive their shared empirical midrank; a completely tied component
receives 50. The simplified "worst = 0 / best = 100" applies to unique extrema.
The explicit midrank equation governs tie behavior; tied extrema are not stretched
to 0 or 100. Equal values receive equal percentiles; insertion and query order do
not affect results.

## 10. Liquidity Score v1

```text
liquidity_score_v1 = 0.50 × turnover_percentile
                  + 0.35 × trade_count_percentile
                  + 0.15 × recency_percentile
```

Weights are exact Decimals, sum to one and preserve score range 0..100. Component
percentiles remain individually visible. Weights are a v1 engineering contract,
not a claim of optimal statistical calibration. Score means relative recent
observable trading-liquidity evidence under this contract. It does not establish
immediate sale probability, executable size, market depth, institutional liquidity,
investment quality, safety or a recommendation. No HIGH/MEDIUM/LOW or other
score-range labels are produced.

## 11. Availability and quality flags

The user-selected blocking precedence is:

```text
NO_MARKET_DATA → INSUFFICIENT_TARGET_EVIDENCE → MISSING_SCORE_COMPONENT
              → INSUFFICIENT_UNIVERSE → READY
```

No target snapshots gives NO_MARKET_DATA. Too few target snapshot days gives
INSUFFICIENT_TARGET_EVIDENCE. Adequate days but missing required components gives
MISSING_SCORE_COMPONENT, even when universe is also too small. Otherwise fewer
than 10 eligible bonds gives INSUFFICIENT_UNIVERSE. READY requires all gates.
Every unavailable status returns null score and all three null percentiles,
preserving raw aggregates, recency, counts and provenance.

Availability separately reports snapshots, each raw metric, sufficient observation
days, complete score inputs and score availability. Quality flags are sorted and
unique, reflect target evidence and all discovered readiness limitations, and
include MARKET_DATA_MISSING, metric missing/partial coverage, insufficient target
observations/universe, missing score component and relevant Task267 parsing flags.
Unavailable observations are availability facts rather than investment judgments.

## 12. Provenance

Preserve exact source, window start, as-of boundary, calendar lookback, minimum
observations, candidate/eligible counts and aligned target snapshot IDs/trade dates
in ascending date order. Target lineage is bounded by selected dates in the window.
Raw payloads and full universe rows are not included in the response. No historical
identity reconstruction is attempted.

## 13. Legacy boundary

Neither Bond nor snapshot persisted liquidity scores are score inputs or write
targets. Legacy volume cannot fill missing raw components. Service also ignores
credit scores, ratings, bank evidence and Task268 OFZ spread. Source payloads
remain unchanged. The caller may have pending edits; service does not flush them.

```text
LEGACY_LIQUIDITY_SCORE_USED_AS_INPUT=false
LEGACY_LIQUIDITY_SCORE_UPDATED=false
LIQUIDITY_SCORE_CREDIT_INDEPENDENT=true
```

## 14. PIT boundary

`as_of_date` is a deterministic market-observation selection boundary, not proof that the complete joined state was observable at that historical moment.

PIT_READY=false: Bond identity/metadata is current, historical observations may be
revised, source availability lineage is incomplete, and historical observability
is not proven. This is not a backtest-safety declaration.

## 15. Explicitly unavailable execution/liquidity capabilities

Transaction costs, bid/ask spread, depth, slippage, commission, market impact,
executable quantity and liquidation horizon are unavailable. Score supplies no
position limits, acceptance thresholds, emergency exit policy or portfolio liquidity
budget. Modified duration and liquidity-/credit-adjusted spread are not enabled.

```text
TRANSACTION_COST_MODEL_READY=false
LIQUIDITY_ADJUSTED_SPREAD_READY=false
MODIFIED_DURATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
```

## 16. Non-goals

No migrations, model/API/frontend edits, ingestion, live fetch, backfill, persistence,
rating normalization, unified scoring, PD, convexity, DV01, OAS, CFA, strategy,
recommendations, investment selection ranking, portfolio construction, Risk Engine,
backtest, PIT reconstruction, Shadow, broker execution or production deployment.
Task268 spread remains unchanged; no arbitrary liquidity penalty is applied.

Acceptance uses the existing temporary SQLite fixture with scenarios A–AW,
synthetic duplicates, date validation/underflow, null/zero distinctions, ties,
9/10 universe boundary, context isolation, SELECT capture, pending-state preservation,
payload/legacy-score preservation and narrow AST safety checks. Run focused Task270
tests, then Task270 plus Task267 regression, compile the two new application modules,
and check working/staged diffs and exact four-file scope. Full backend, Task268/269,
backfill, VDS/production and live-source suites are skipped by Task270 instruction.

## 17. Handoff

```text
TASK270_STATUS=LIQUIDITY_FEATURE_FOUNDATION_READY
LIQUIDITY_RAW_INPUTS_READY=true
LIQUIDITY_WINDOW_AGGREGATION_READY=true
LIQUIDITY_RELATIVE_PERCENTILES_READY=true
LIQUIDITY_SCORE_V1_READY=true
LIQUIDITY_SCORE_CREDIT_INDEPENDENT=true
LEGACY_LIQUIDITY_SCORE_USED_AS_INPUT=false
LEGACY_LIQUIDITY_SCORE_UPDATED=false
TRANSACTION_COST_MODEL_READY=false
LIQUIDITY_ADJUSTED_SPREAD_READY=false
MODIFIED_DURATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PIT_READY=false
```

These declarations describe code capability; per-bond/window availability remains
explicit. One commit and ordinary origin/main push follow local acceptance and
concurrency checks. After confirming remote SHA, take at most one exact-commit CI
snapshot without waiting/polling/retry. Terminal CI verification is external.
No subsequent task is started or authorized by this handoff.
