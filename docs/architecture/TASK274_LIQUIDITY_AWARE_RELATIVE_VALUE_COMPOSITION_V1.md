# Task274 — Liquidity-Aware Relative-Value Composition Foundation v1

## 1. Decision

Provide a computed SELECT-only `BondLiquidityAwareRelativeValueService` composing
Task268 OFZ-relative spread and independent Task270 liquidity evidence.
Contract version is `bond-liquidity-aware-relative-value-v1`. Frozen Pydantic
models forbid extra fields; the service has no own queries or persistence.

Interface: `build_for_bond(bond_id, as_of_date, *, market_source="moex",
max_market_age_days=7, max_curve_age_days=7, liquidity_lookback_calendar_days=30,
liquidity_min_observation_days=5)`.

Validate exact positive int bond ID, exact calendar date, nonblank string source,
exact nonnegative int ages and exact positive int window/minimum. Bool/datetime
are rejected, minimum cannot exceed window, and date underflow raises ValueError.
Validation precedes all dependency calls. Missing bond retains Task267 HTTP 404
`Bond not found`. Exact source is forwarded without normalization; arbitrary
nonblank sources retain the dependencies' existing semantics.

## 2. Why this is not a liquidity-adjusted spread

Turnover, trade count, recency and a relative liquidity score do not establish an
empirically calibrated conversion to transaction-cost, slippage, market-impact or
required liquidity-premium basis points. Task274 returns the two evidence families
together. It does not subtract a penalty, multiply spread by a score or divide
spread by a score. No composite investment score or liquidity label is introduced.

## 3. Task268 dependency

Call `OfzReferenceCurveService.evaluate_bond` once with the requested bond/date,
source, market age and curve age. Reuse status, target YTM/duration, reference
yield, spread pp/bps, interpolation method, target snapshot/date and curve date.
Do not directly rebuild a curve, choose a benchmark, interpolate, extrapolate or
recalculate spread. Require dependency READY and finite Decimal spread pp/bps
for relative-value readiness. A claimed READY without target snapshot/date fails
market consistency. Numeric formula relationships are not recomputed.

## 4. Task270 dependency

Call `BondLiquidityFeatureService.build_for_bond` once with the requested
bond/date/source and liquidity window/minimum. Reuse score status, score, three
percentiles, turnover/trade-volume/trade-count medians, latest trade date, age,
snapshot observation days and window provenance. Require dependency READY and
finite Decimal score plus all three percentiles in 0..100 for score readiness.
No daily parsing, aggregation, percentile, universe or score formula is reproduced.

## 5. Market-evidence consistency

Each dependency must match requested bond ID, as_of_date and exact market source.
Check original exact-int bond/exact-date values before schema coercion.
Task270 snapshot ID/date arrays must be lists of equal length, corresponding to
its nonnegative observation-day count; IDs must be positive exact integers and
dates exact calendar dates in strictly increasing order. READY requires nonempty
arrays. Matching empty arrays are valid without observations and an unavailable
score. Do not sort, repair or substitute malformed provenance.

For valid nonempty arrays, the last aligned ID/date pair identifies the latest
Task270 evidence. Compare it with Task268 target snapshot/date. Different
available pairs, a partial/invalid target pair, or READY Task268 without a target
pair fail closed with MARKET_EVIDENCE_MISMATCH. Ordinary absent evidence from an
unavailable dependency does not create an artificial mismatch.

Fields from each side survive when that side's bond/as_of/source matches the
request. During snapshot/date drift, both sides remain separately visible and
composition is unavailable. A side with wrong request identity has its numeric,
method, date and count fields set to null; original status/identity/provenance
remain diagnostic. Neither dependency is chosen as a preferred market snapshot.

## 6. Relative-value fields

Expose relative_value_status, target_yield_to_maturity_pct, target_duration_years,
reference_ofz_yield_pct, spread_to_ofz_pp, spread_to_ofz_bps, interpolation_method
and curve_trade_date. Yield is in percent, duration in years, spread in percentage
points and basis points. Preserve Decimal inputs and spread sign exactly:
positive, zero and negative spread are valid evidence, without an attractiveness
verdict. No scaling, formula cross-check or presentation rounding occurs.

## 7. Liquidity fields

Expose liquidity_status, liquidity_score_v1, turnover_percentile,
trade_count_percentile, recency_percentile, median_daily_turnover_value,
median_daily_trade_volume, median_daily_num_trades, latest_liquidity_trade_date,
latest_liquidity_observation_age_days and liquidity_snapshot_observation_days.
Score/percentiles are relative 0..100 values; age is in calendar days. Medians
retain Task270 source-native units without currency conversion or rescaling.
Zero score is available evidence. No HIGH/MEDIUM/LOW liquidity interpretation.

## 8. Readiness and status

```text
LIQUIDITY_PROVENANCE_INVALID
→ MARKET_EVIDENCE_MISMATCH
→ RELATIVE_VALUE_UNAVAILABLE
→ LIQUIDITY_UNAVAILABLE
→ READY
```

The first detected failure determines primary status; all detected conditions
remain flags. READY requires both usable READY dependencies, valid liquidity
provenance, matching request identities and matching latest snapshot/date.
An unavailable score does not erase raw liquidity evidence; unavailable relative
value does not erase a usable liquidity score. A numeric contradiction in claimed
READY produces the corresponding unavailable status and evidence-invalid flag.

## 9. Availability and quality flags

has_relative_value and has_spread_to_ofz require correctly attributed READY
relative value, finite pp/bps and a valid target snapshot/date. has_liquidity_feature
requires correctly attributed observations and valid provenance, independently of
score readiness. has_liquidity_score additionally requires READY and the complete
valid score/component set. has_matching_market_evidence reports a factual
matching pair independently of dependency numeric readiness.
has_liquidity_aware_relative_value equals composition status READY.

Flags are sorted and unique. Preserve unavailable statuses as
RELATIVE_VALUE_<Task268 status> and LIQUIDITY_<Task270 score status>. Claimed READY
with unusable numeric evidence adds RELATIVE_VALUE_EVIDENCE_INVALID or
LIQUIDITY_EVIDENCE_INVALID. Do not mechanically copy nested quality flags.
Nonfinite/wrong-type numeric inputs become null with readiness diagnostics;
finite values, including out-of-range diagnostic score inputs, remain inspectable.

## 10. Provenance

Keep relative-value, OFZ-curve and liquidity contract versions; separate
dependency bond/as_of/source identities; Task268 target snapshot/date; Task270
latest snapshot/date; curve date; liquidity window start/lookback/minimum;
liquidity provenance-valid indicator; requested date and source. Invalid arrays
leave the liquidity latest pair null. Actual dependency identities remain visible
even when their feature fields are hidden. The full curve, raw payloads and full
provenance arrays are not duplicated in the output.

## 11. No synthetic liquidity premium

No liquidity_penalty_bps, liquidity_premium_bps, liquidity_adjusted_spread_bps,
net_spread_bps, tradable_spread_bps or execution_adjusted_spread_bps field exists.
No calibrated bps model is claimed. A composed view is not an execution-quality
or investment-score gate.

## 12. Legacy boundaries

No legacy Bond spread/liquidity score/volume, snapshot spread/liquidity score,
feature snapshot, risk assessment, ML dataset or portfolio output is read by
Task274. Existing Task268/270/271/272/273 production files, models, migrations,
API, frontend and strategy are unchanged. The service delegates both reads under
no_autoflush and never calls add/add_all/delete/flush/commit/merge. Caller-owned
pending state and source rows/views remain unchanged. No network or refresh.
Inherited dependency reads can observe caller-owned pending ORM profile values;
preserving that state does not override those values or promise a READY result.

## 13. PIT boundary

PIT_READY=false for every result. This inherits current identity/security terms,
historical revision and historical availability limitations from both
dependencies, without a complete observability timeline. No PIT reconstruction
or backtest-safe claim is introduced.

## 14. Explicitly unavailable capabilities

```text
SPREAD_TO_OFZ_INPUT_READY=true
LIQUIDITY_SCORE_INPUT_READY=true
LIQUIDITY_AWARE_RELATIVE_VALUE_READY=true
LIQUIDITY_ADJUSTED_SPREAD_READY=false
LIQUIDITY_PREMIUM_BPS_MODEL_READY=false
TRANSACTION_COST_MODEL_READY=false
SLIPPAGE_MODEL_READY=false
MARKET_IMPACT_MODEL_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PIT_READY=false
```

True flags describe implementation capabilities, separately from per-bond/window
availability. No rating normalization, PD, credit adjustment, execution-capacity
model, portfolio construction, Risk Engine, backtest or broker execution occurs.

## 15. Verification

Task274 A–AD coverage includes exact copied values, signed/zero spread, score
extrema, identity/date/source drift, latest snapshot drift, malformed/empty/
unsorted provenance, partial availability, contradictory READY numerics, primary
status precedence, compact provenance and absence of synthetic scores/bps models.
Additional cases cover argument validation before reads, date underflow, precise
forwarding, Decimal context independence and stable unavailable serialization.

Use real disposable SQLite integration plus synthetic dependency views. SQL
capture and mutation guards verify SELECT-only and unchanged pending state with
autoflush enabled. Compare source rows and views to prove immutability. Narrow
AST checks prohibit direct model/SQL/network inputs and feature recalculation.

Run sequentially from backend:

```text
pytest -q tests/test_bond_liquidity_relative_value_service.py
pytest -q tests/test_bond_liquidity_relative_value_service.py tests/test_ofz_reference_curve_service.py tests/test_bond_liquidity_feature_service.py
python -m compileall app/schemas/bond_liquidity_relative_value.py app/services/bond_liquidity_relative_value_service.py
```

Then working/staged diff checks, staged review and exact four-file scope. Broad
backend suite, production/VDS and live-source checks are skipped by design.
Local acceptance: focused Task274 140 passed; combined Task274/Task268/Task270
338 passed; compileall passed. Final pytest runs emitted only the local cache
write-permission warning, without test failures.

## 16. Handoff

End after local acceptance, one task commit, normal origin/main push with remote
concurrency checks, at most one exact-commit CI snapshot and the Task274 report.
No CI waiting, polling or retry. Task275 — Credit Relative-Value / Normalization
Gate requires independent Task274 verification and a separate request, including
its own decision on source-native cohorts versus cross-agency normalization.
Task275 is not implemented or otherwise started here.
