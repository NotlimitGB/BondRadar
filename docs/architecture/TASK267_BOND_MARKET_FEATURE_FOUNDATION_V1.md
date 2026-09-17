# Task267 — Bond Market Feature Foundation v1

## 1. Decision

`BondMarketFeatureService` is the authoritative deterministic M3 market/structural
read model, contract `bond-market-feature-v1`. It creates no feature-store table
and does not migrate legacy consumers. All database reads use `no_autoflush`;
the service performs no writes, flush, commit, network or source-client creation.
Caller-owned pending session state is preserved.

## 2. Existing repository inputs

Inputs are `Bond`, `BondMarketSnapshot` and `BondCashflowEvent`. No dependency on
`BondFeatureSnapshot`, `FeatureSnapshotService`, FinancialReport, scores, rating
events, bank credit metrics, defaults or predictions is introduced. Existing
ingestion and storage remain unchanged. Alembic remains `202609160001`.

The public method is `build_for_bond(bond_id, as_of_date, *, market_source="moex",
max_market_age_days=7)`. Invalid arguments raise `ValueError`; a missing Bond
raises the existing HTTP 404 format. Boolean IDs/thresholds and datetime values
in place of calendar dates are invalid. Source equality is exact, not normalized.

## 3. Authoritative source selection

Select the latest snapshot for the exact bond and source with trade date at or
before the boundary, ordered by trade date descending then ID descending. Never
select future evidence, another source or legacy Bond market-field fallback.
No matching snapshot produces a valid MISSING view with null market values.
Provenance source in a missing view identifies the requested source, not a found row.

## 4. Market feature semantics

Price, clean price, NKD, yield-to-maturity percentage points and duration years
are copied from the selected snapshot as Decimal-compatible values. `11.250`
YTM remains `11.250%`, not `0.11250`. No recalculation or rounding occurs.

Market age is calendar days from trade date to the boundary. Age at or below the
nonnegative integer threshold is FRESH; larger age is STALE. Stale values remain
visible. No business-day or holiday calendar is inferred. Missing age is null.

## 5. Volume / turnover / trade-count semantics

`trade_volume` is only proven MOEX VOLUME; `turnover_value` is MOEX VALUE;
`num_trades` is NUMTRADES. Stored snapshot volume alone is not proof of quantity.
The supported payload shapes are existing `moex` raw fields, historical
`canonical` fields and historical top-level `value`/`num_trades`. No recursive
JSON search, inferred unit conversion or generic alias engine is provided.

Exact keys: moex VOLUME/volume, VALUE/value, NUMTRADES/numtrades/num_trades;
canonical volume/value/num_trades; top-level value/num_trades. Empty/null fields
are absent. All present representations are checked: malformed ones null the
affected feature; different valid values null it with a conflict flag. No arbitrary
precedence hides contradictory raw evidence. Other features remain usable.

Decimal, integer, finite float via `Decimal(str(value))`, numeric string and comma
decimal string are supported, without binary float arithmetic. Non-finite,
negative, boolean and unsupported values are malformed. Trade counts must be exact
nonnegative integers. Missing never becomes zero; actual zero remains available.

The exact legacy VALUE-as-volume mapping note produces
`VOLUME_VALUE_FALLBACK_NOT_PROMOTED`. Actual raw VOLUME, if independently present,
can still be exposed. Exact duration-day normalization note produces
`DURATION_LEGACY_DAY_NORMALIZATION`; stored duration is preserved, not recalculated.
Raw payload and stored provenance are never modified.

## 6. Structural and cashflow features

Copy canonical ISIN/SECID, currency, nominal, coupon rate, maturity/offer dates,
floating/subordinated/perpetual flags and nullable amortization metadata. Date
deltas retain negative values; non-perpetual maturity before the boundary means
matured. Perpetual maturity delta is null and matured is false.

Select only MOEX events for the bond on/after the boundary, ordered by date then
ID ascending. Expose total events, coupon/amortization counts, next dates and
presence flags for coupon/amortization/redemption/offer_redemption. Other events
count towards the total. No IRR, YTM, amounts aggregation or schedule rewrite.

Metadata amortization and future amortization remain separate. Explicit metadata
false with a future amortization yields `AMORTIZATION_METADATA_SCHEDULE_MISMATCH`.
Absence of an event does not prove a contradiction. Offer metadata remains separate
from offer-redemption events; no automatic reconciliation or unsupported offer
mismatch inference is added.

## 7. Availability and quality flags

The availability block reports snapshot, price, clean price, NKD, YTM, duration,
trade volume, turnover, number of trades, selected future cashflow schedule,
maturity and offer presence. Schedule availability is not a completeness claim.

Sorted, unique flags include market missing/stale; price/yield/duration and
liquidity-field missing; cashflow schedule missing; perpetual/floating/subordinated/
matured structure; the two legacy mapping notes; and amortization mismatch.
Numeric quality uses `MALFORMED_RAW_VOLUME/VALUE/NUMTRADES` and
`CONFLICTING_RAW_VOLUME/VALUE/NUMTRADES`. Flags describe evidence and structure,
not recommendations or risk judgments. Null/missing does not mean bad credit.

## 8. Provenance

Every result carries contract, bond ID, boundary, requested source, selected market
snapshot ID/trade date and cashflow source `moex` with all selected event IDs in
selection order. Market provenance is also exposed at top level. Output contains
no scoring, ranking or investment recommendation fields. Equal DB state and inputs
produce equal output, including ordering and flags.

## 9. PIT boundary

`pit_ready=false` is contractual for every result. `as_of_date` is a deterministic
selection boundary, not proof of point-in-time observability for every joined
input. Current Bond metadata and cashflow schedules can be revised/restated;
availability timestamps are incomplete. Task266's M2 PIT limitation is inherited.
Excluding future trade dates alone does not make the whole vector backtest-safe.

## 10. Explicitly unavailable derived features

The capabilities block declares raw liquidity input support, not universal data
coverage. OFZ reference curve, authoritative OFZ spread derivation, liquidity score,
modified duration and credit join readiness remain false. `spread_to_ofz` is always
null even if a legacy stored value exists. No random OFZ comparator, interpolation,
one-day liquidity score or legacy Bond liquidity-score substitution is permitted.

## 11. Non-goals

No migration, model change, API/frontend integration, ingestion rewrite, credit
join, rating inheritance/normalization, PD/default model, CFA, score, recommendation,
strategy, portfolio, Risk Engine, backtest, Shadow or broker execution. Production
and live-source access are forbidden. Focused tests use disposable SQLite only;
PostgreSQL production runtime is not claimed as verified.

Verification is the Task267 focused pytest, compileall of the two new application
modules, diff check and exact four-file review. Broad backend, Docker and migrations
are skipped by design. Git delivery and one exact-commit CI snapshot are authorized;
CI is not waited or polled.

Local acceptance: 80 focused tests passed on disposable SQLite; compileall passed.
SELECT-only instrumentation and pending-state tests verify no internal commit,
flush or autoflush. The initial static guard mistook collection `set.add` for a
database mutation; it was narrowed to database calls and the full focused rerun
passed. No production or live-source validation was performed.

## 12. Handoff

```text
TASK267_STATUS=MARKET_FEATURE_FOUNDATION_READY
OFZ_REFERENCE_CURVE_READY=false
SPREAD_TO_OFZ_DERIVATION_READY=false
LIQUIDITY_RAW_INPUTS_READY=true
LIQUIDITY_SCORE_V1_READY=false
MODIFIED_DURATION_READY=false
CREDIT_FEATURE_JOIN_READY=false
PIT_READY=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_ACCESS=false
LIVE_SOURCE_REQUESTS=NONE
```

The only next handoff is **Task268 — OFZ Reference Curve & Relative-Value Feature
Foundation v1**, subject to a separate request and scope. It is not implemented
or executed here. Foundation readiness grants no downstream runtime permission.
