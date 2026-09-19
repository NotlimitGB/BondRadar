# Task283 — M3 Coverage & Readiness Audit Reducer v1

## 1. Decision

Task283 defines a pure reducer over an explicitly supplied, non-empty sequence of
`BondM3FeatureView` objects. It reports factual feature coverage, composite
integrity, completeness, missingness bottlenecks, and source-status distributions.
The output contract is `m3-coverage-audit-v1`.

## 2. Why the audit reducer is pure

The reducer measures already-built Task282 outputs. It has no database, Session,
network, persistence, universe discovery, or feature-service dependency. It never
repairs evidence or constructs a Task282 view.

## 3. Task282 input universe

Input is one deterministic `Sequence` of exact `BondM3FeatureView` objects. It must
be non-empty, contain unique positive integer bond IDs, and share one exact
`as_of_date` and `market_source`. Every member must use
`bond-m3-feature-view-v1`, declare `pit_ready=false`, and have a self-consistent
outer status/availability envelope. Invalid audit input raises `ValueError` and is
never deduplicated or repaired. Input ordering is normalized by ascending bond ID.

## 4. Coverage dimensions

Coverage is read directly from all eleven Task282 availability booleans:
market freshness, OFZ relative value, rating evidence, comparable cohort,
liquidity score, modified duration, DV01, liquidity-aware relative value, peer
context, peer readiness, and supplied-evidence consistency. Each typed entry
contains available and missing counts, exact percentage, and sorted bond IDs.

## 5. Composite integrity coverage

`CONSISTENT` and `EVIDENCE_INVALID` counts use only the Task282 composite status.
The status must agree exactly with `all_supplied_evidence_consistent`. Invalid but
self-consistent Task282 composites remain in the common audit denominator and are
reported as invalid evidence.

## 6. Core completeness

`CORE_M3_COMPLETE_V1` requires a consistent composite plus all eight required
feature families: fresh market, OFZ relative value, rating evidence, comparable
credit cohort, liquidity score, modified duration, DV01, and liquidity-aware
relative value. Peer context is intentionally optional. Counts, percentage,
complete IDs, and incomplete IDs are returned.

## 7. Extended completeness

`EXTENDED_M3_COMPLETE_V1` requires core completeness, supplied peer context, and
a READY peer distribution. Extended coverage is separate from core coverage and
does not change the core definition.

## 8. Missingness bottlenecks

Nine factual bottlenecks cover the eight core feature families plus invalid
composite evidence. A bond may occur in several bottlenecks. Zero-count entries
are omitted. Entries sort by missing count descending and then key ascending;
affected IDs sort ascending. No severity label or qualitative verdict is added.

## 9. Status breakdowns

The reducer preserves exact underlying status strings for market, Task268
relative value, Task270 liquidity, Task272 modified duration, Task273 DV01,
Task274 liquidity-aware relative value, and supplied Task281 peer orchestration.
Required families use the full universe denominator. Peer statuses and the
explicit peer-distribution readiness percentage use only the peer-context
denominator. Status entries are lexical and their IDs are ascending.

## 10. Percentage semantics

Every percentage is `Decimal(count) * Decimal("100") / Decimal(denominator)` in a
fresh local context with precision 28 and `ROUND_HALF_EVEN`. No float or
presentation rounding is used. With no peer contexts, the peer breakdown is empty
and peer readiness percentage is `Decimal("0")`.

## 11. No readiness threshold

Task283 returns coverage facts and does not classify the universe as PASS, FAIL,
READY_FOR_M4, or NOT_READY_FOR_M4. No arbitrary percentage threshold is part of
this contract. `M4_READINESS_DECISION_READY=false`.

## 12. No investment interpretation

Coverage does not imply credit quality, safety, attractiveness, expected return,
or a buy/sell decision. Task283 creates no investment score, ranking, or
recommendation.

## 13. PIT boundary

Task282 inputs are non-PIT. Task283 therefore always declares `pit_ready=false`
and does not claim point-in-time historical coverage beyond the supplied evidence
semantics.

## 14. Verification

Acceptance uses the focused synthetic Task283 tests, compilation of the two new
application modules, working and staged whitespace checks, full staged review,
and confirmation that exactly four new Task283 files exist. The broad backend
suite, production, VDS, and live sources are intentionally outside this task.

Capability declarations are fixed as follows:

```text
TASK282_COMPOSITE_INPUT_READY=true
M3_COVERAGE_AUDIT_READY=true
FEATURE_COVERAGE_READY=true
FEATURE_STATUS_BREAKDOWN_READY=true
MISSINGNESS_BOTTLENECKS_READY=true
CORE_M3_COMPLETENESS_READY=true
EXTENDED_M3_COMPLETENESS_READY=true

ACTUAL_DB_AUDIT_RUNNER_READY=false
AUTOMATIC_UNIVERSE_DISCOVERY_READY=false
M4_READINESS_DECISION_READY=false
INVESTMENT_SCORE_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

## 15. Performance constraint for future runner

A future DB audit runner must NOT call current single-bond Task270/274 chains
naïvely across the full universe, because those paths repeatedly perform
universe-level work. The runner must use explicit auditable universe selection and
shared or batched evidence while leaving this reducer pure.

## 16. Handoff

The only handoff is Task284 — Efficient M3 Audit Snapshot Runner v1, after
independent verification and a separate request. Task283 does not start Task284,
access production, or deploy to VDS.
