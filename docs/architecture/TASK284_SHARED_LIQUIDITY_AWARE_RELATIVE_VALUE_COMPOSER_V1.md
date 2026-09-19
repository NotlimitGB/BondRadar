# Task284 — Shared Liquidity-Aware Relative-Value Composer Foundation v1

## 1. Decision

Task274 composition is implemented once in the pure
`compose_bond_liquidity_aware_relative_value` function. The existing service remains the
public loader and returns the unchanged `bond-liquidity-aware-relative-value-v1` contract.

## 2. Why extraction is required

Prebuilt Task268 and Task270 views must be composable without reloading market evidence.
The extraction enables that reuse while retaining Task274 behavior byte for byte at the
serialized contract boundary.

## 3. Task274 semantic ownership

Task274 continues to own identity attribution, evidence matching, unavailable-state
precedence, availability, quality flags, and output provenance. Task284 is an implementation
boundary and does not introduce another financial contract.

## 4. Pure composer boundary

The composer accepts only `BondRelativeValueView`, `BondLiquidityFeatureView`, and explicit
request identity. It has no Session, ORM, database, network, filesystem, cache, persistence,
or feature-service dependency. Invalid direct arguments raise `ValueError` before composition.

## 5. Requested identity

The requested `(bond_id, as_of_date, market_source)` remains authoritative. Evidence from a
dependency whose identity differs is hidden, even if both dependencies agree with each other.
Correctly attributed evidence from the other side remains available.

## 6. Liquidity provenance validation

Selected snapshot IDs and dates must be lists of equal length and agree with exact nonnegative
`snapshot_observation_days`. IDs are exact positive integers, dates are exact calendar dates,
and dates increase strictly. READY liquidity requires a nonempty sequence. Empty sequences are
valid for a legitimate unavailable state such as `NO_MARKET_DATA`.

## 7. Market evidence matching

The valid Task268 target snapshot/date pair is compared with the latest valid Task270
snapshot/date pair. A mismatch blocks composition readiness but preserves independently valid
evidence from both sides. Missing evidence in a valid unavailable dependency does not create an
artificial mismatch.

## 8. Relative evidence validation

READY relative value requires a valid target snapshot/date pair and finite Decimal
`spread_to_ofz_pp` and `spread_to_ofz_bps`. The composer does not verify their mathematical
relationship or recompute either value.

## 9. Liquidity evidence validation

READY liquidity requires finite Decimal score and turnover, trade-count, and recency
percentiles in the inclusive range 0 through 100. Values are copied exactly. The composer does
not calculate medians, percentiles, or score weights.

## 10. Status precedence

The unchanged precedence is:

1. `LIQUIDITY_PROVENANCE_INVALID`
2. `MARKET_EVIDENCE_MISMATCH`
3. `RELATIVE_VALUE_UNAVAILABLE`
4. `LIQUIDITY_UNAVAILABLE`
5. `READY`

Quality flags remain sorted and unique and retain the existing dependency-status prefixes and
evidence-invalid diagnostics.

## 11. Evidence preservation

Wrong relative identity hides only relative-attributed fields. Wrong liquidity identity hides
only liquidity-attributed fields. Snapshot drift keeps both correctly attributed sides while
marking market evidence as unmatched. The composer does not repair malformed provenance.

## 12. Thin Task274 service

The public Task274 signature and validation remain unchanged. Under one `no_autoflush` scope,
the service invokes Task268 once and Task270 once. It then invokes the pure composer once and
returns that result. HTTP 404 and caller-owned pending-state behavior remain dependency-owned.

## 13. No financial recalculation

Task284 performs no OFZ interpolation, spread conversion, liquidity percentile calculation,
score weighting, adjusted spread, liquidity premium, transaction cost, slippage, market impact,
credit adjustment, ranking, or recommendation.

## 14. PIT boundary

The existing Task274 capability declarations are unchanged:

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

Implementation readiness does not make any per-bond result available or point-in-time ready.

## 15. Verification

Pure synthetic tests cover READY and unavailable paths, exact Decimal copying, identity and
snapshot mismatch, malformed and empty provenance, status precedence, side preservation,
serialization, immutability, Decimal-context isolation, and AST safety. The Task274 integration
suite verifies full `model_dump()` parity, one call per dependency and composer, SELECT-only
behavior, pending-state preservation, the public signature, and unchanged HTTP behavior.

## 16. Handoff

Task285 may use this composer with prebuilt Task268 and Task270 views. Task284 itself does not
implement batch liquidity or the M3 audit runner. Task285 requires independent Task284 review
and a separate request.
