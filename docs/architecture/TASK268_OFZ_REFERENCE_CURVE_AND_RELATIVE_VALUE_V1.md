# Task268 — OFZ Reference Curve & Relative-Value Feature Foundation v1

## 1. Decision

`OfzReferenceCurveService` exposes `build_curve(as_of_date, *, market_source="moex",
max_curve_age_days=7)` and `evaluate_bond(bond_id, as_of_date, *, market_source="moex",
max_market_age_days=7, max_curve_age_days=7)`. Contracts are
`ofz-reference-curve-v1` and `bond-relative-value-v1`. These are SELECT-only derived
views with no persistence, flush, commit, source client or network access.
All reads run under `Session.no_autoflush`; caller-owned pending state is preserved.
Schemas forbid extra fields and freeze attribute reassignment, following Task267;
list members remain ordinary lists. Invalid arguments raise ValueError (including
boolean IDs/ages and datetime instead of date); missing target preserves Task267 HTTP 404.

## 2. Existing inputs

Bond supplies identity only for benchmark selection. Current resolved
BondSecurityMasterProfile supplies authoritative terms. BondMarketSnapshot
supplies exact-source trade date, YTM and stored duration; duration is never
recalculated. Target inputs come from BondMarketFeatureService.build_for_bond
with the same source/date/market-age arguments. Its legacy structural fields
are not used by Task268. No models or migrations change; head remains `202609160001`.

## 3. OFZ identity boundary

Privately mirror existing compatibility semantics: uppercased name/SECID/ISIN
contains ОФЗ, OFZ or FEDERAL LOAN BOND, or ISIN starts with SU. Identity alone is
insufficient. Explicit ОФЗ-ИН/OFZ-IN, ОФЗ-ПК/OFZ-PK and ОФЗ-АД/OFZ-AD markers in
those fields exclude a candidate even when its resolved profile looks eligible.
No issuer inference, fuzzy classification or unrelated-helper refactor occurs.

## 4. Security Master eligibility

Require verified currency RUB, fixed coupon, bullet principal, dated structure,
and verified maturity on/after the boundary. Missing profile, unknown, conflict
or incompatible terms exclude the candidate. No legacy Bond currency, maturity,
floating/perpetual/amortization compatibility field can establish eligibility.
Explicit-family exclusions belong to the excluded-security-master diagnostic.

## 5. Market snapshot selection

For each eligible candidate select latest `source == market_source` and
`trade_date <= as_of_date`, ordered by trade date DESC then ID DESC. Source equality
is exact with no normalization/fallback. Curve queries select only needed columns.
Reject missing/non-finite YTM or duration and duration <= 0. Finite negative YTM
is allowed; no clipping. Numeric columns are Decimal; other numeric representations
are rejected. An unusable latest row never causes fallback to an older valid row.
Exclude age > max_curve_age_days; equality is fresh. Age counts calendar days.

## 6. Single-date curve construction

After numeric/freshness gates, curve_trade_date is the maximum retained trade date.
Keep only that date; older fresh observations increment the date-mismatch exclusion.
There is no historical-date retry when the latest coherent date has too few nodes.
No synthetic endpoints, zero-duration/key-rate anchors or manually inserted OFZ.

## 7. Duplicate-duration aggregation

Group by exact Decimal stored duration. A singleton uses its YTM (SINGLE);
duplicates use the sorted-YTM median (MEDIAN), averaging the two middle values
for even counts. All calculations use local Decimal Context precision 28,
ROUND_HALF_EVEN, independent of caller context, without extra quantization.
Nodes sort by duration ASC; component lists align in (bond_id, snapshot_id) order.
Component IDs, SECIDs, ISINs and yields remain visible, including all median inputs.

## 8. Duration interpolation

At least two distinct durations are required even for an exact target match.
Exact duration uses node YTM (EXACT_NODE). Otherwise use nearest bracketing nodes:
`Ylo + (D-Dlo)*(Yhi-Ylo)/(Dhi-Dlo)` (LINEAR_INTERPOLATION).
Duration outside the inclusive range blocks derivation. No extrapolation.

## 9. Spread semantics and units

Require fresh target market, finite YTM, finite positive duration, ready curve,
and equal target/curve trade dates. Target source selection is delegated to Task267;
no second target selector exists. Compute `spread_to_ofz_pp = target YTM - reference YTM`
and `spread_to_ofz_bps = spread_to_ofz_pp * 100`. Source YTM is percent, never divided
by 100. Example 15.40% minus 13.85% = 1.55 percentage points = 155 basis points.
Spread may be positive, zero or negative and has no recommendation implication.

## 10. Provenance

Output preserves target bond identity, snapshot ID/date, YTM/duration, coherent
curve date, and lower/upper duration/YTM with all component snapshot IDs.
The nested curve retains complete component identity/yield provenance.
Exact matches populate both lower and upper provenance with the same exact node.
Blocked outputs preserve available target/curve provenance and null derived values.

## 11. Diagnostics and unavailable states

Curve statuses: READY, NO_ELIGIBLE_OFZ (no benchmark-eligible profiles),
NO_FRESH_MARKET_DATA (eligible candidates but no valid fresh observations), and
INSUFFICIENT_DISTINCT_DURATIONS (one coherent duration). Available nodes/date/range
remain visible in insufficient-node results; empty curves have null date/range.

Counts reconcile sequentially: identity = eligible + excluded-security-master;
eligible = with-snapshot + missing-snapshot; with-snapshot = valid + numeric exclusions;
valid = fresh + stale; fresh = coherent-date-members + date-mismatch.
Numeric reasons are mutually exclusive, in yield missing/invalid, duration
missing/invalid, then nonpositive-duration order. Distinct-node count equals node_count.

Target failure precedence: market missing, stale, yield unavailable, duration
unavailable/nonpositive, curve not ready, date mismatch, then outside range.
All observed failure flags are collected, sorted and unique. Nonpositive duration
has an additional descriptive flag. Invalid numeric inputs are exposed as null;
additional invalid-yield/duration flags distinguish them from absent values.
Task267 validation exceptions for corrupt non-finite persisted target inputs propagate
fail-closed before a spread can be produced; Task268 does not bypass or rewrite its schema.
Duplicates have CURVE_DUPLICATE_DURATION_AGGREGATED. Flags are not risk ratings.
Every returned state keeps stable schemas and pit_ready=false.

## 12. PIT boundary

`as_of_date` is a deterministic observation-selection boundary, not proof that
every security-master and market input was known to BondRadar at that
historical instant.

Current resolved Security Master is not a historical reconstruction. Task241
preserves evidence but lacks historical selection; inputs may be ingested after
their economic date. Task266 retains M2_PIT_READY=false. No backtest-safe history
is claimed; every curve and relative-value result has pit_ready=false.

## 13. Explicit non-goals

No production DB/actions, source requests, backfill, migration, model/API/frontend
changes, curve persistence, alternate curve fitting, modified duration, convexity,
DV01, Z/OAS, credit/rating join, PD/default model, liquidity adjustment, tax/cost
adjustment, CFA comparison, strategy, recommendation, ranking, portfolio, Risk
Engine, backtesting, Shadow or broker execution. Legacy spread storage is ignored
and unchanged. Task267 capability declarations remain its own earlier contract.

Verification uses focused Task268 tests, combined Task268/Task267 regressions,
compileall of the two new application modules, diff check and four-file review.
Tests use disposable SQLite, SQL capture and pending-state guards, not production.
Broad local suite and VDS are skipped by design. PostgreSQL runtime validation is
not claimed. Delivery is one commit and normal push; at most one exact-SHA CI
snapshot, no waiting/polling. Terminal CI is verified externally.

Local acceptance: 104 Task268 tests passed; the combined Task268/Task267 run
passed 184 tests (80 Task267 regressions). Both new application modules passed
compileall. SQL capture and clean/pending-session checks confirmed read-only behavior.

## 14. Handoff

Code capability readiness is separate from availability for a specific bond/date.
After local acceptance, declare:

```text
OFZ_REFERENCE_CURVE_READY=true
SPREAD_TO_OFZ_DERIVATION_READY=true
CURVE_INTERPOLATION_METHOD=LINEAR_DURATION
CURVE_EXTRAPOLATION_READY=false
LIQUIDITY_SCORE_V1_READY=false
MODIFIED_DURATION_READY=false
CREDIT_FEATURE_JOIN_READY=false
LIQUIDITY_ADJUSTED_SPREAD_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PIT_READY=false
LEGACY_BOND_TERMS_USED_AS_BENCHMARK_EVIDENCE=false
LEGACY_SPREAD_TO_OFZ_USED_AS_INPUT=false
LEGACY_SPREAD_TO_OFZ_UPDATED=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_ACCESS=false
LIVE_SOURCE_REQUESTS=NONE
```

Only next handoff: **Task269 — Credit Feature Join & Rating Availability Foundation v1**,
subject to its own request. Preserve issue/issuer rating distinction, native agency
values, no silent issuer-to-bond inheritance, no cross-agency normalization/PD,
and PIT_READY=false. No Task269 implementation or runtime gate is enabled here.
