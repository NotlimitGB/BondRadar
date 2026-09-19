# Task281 — Credit-Cohort Peer Distribution Orchestrator v1

## 1. Decision

Task281 is a pure adapter from one validated Task280 batch and one explicit target
bond ID to one Task277 peer-distribution result. Its contract version is
`credit-cohort-peer-distribution-orchestration-v1`.

## 2. Task280 input boundary

The only universe input is an existing `CreditCohortBatchMemberView`. Task281 does
not call Task280, load bonds, rebuild members, or expand the requested IDs. The
caller also supplies the target ID and minimum peer count explicitly.

```text
TASK280_BATCH_INPUT_READY=true
BATCH_MEMBER_BUILD_READY=false
PEER_UNIVERSE_DISCOVERY_READY=false
```

## 3. Batch integrity validation

Task281 validates the Task280 version and PIT declaration, request context, sorted
unique ID partition, status/count identities, ordered items, Task276 member
envelopes, shared-curve envelope, and critical Task280 provenance/call counts.
Contradictions produce `BATCH_EVIDENCE_INVALID` without a reducer call. It does not
repair malformed input or inspect curve-node mathematics.

## 4. Explicit target selection

`target_bond_id` must be an exact positive integer. Task281 never infers a target
from item order, readiness, spread, rating, or any other property. A target outside
the requested IDs produces `TARGET_NOT_REQUESTED`; a requested missing target
produces `TARGET_BOND_NOT_FOUND`.

```text
EXPLICIT_TARGET_BOND_READY=true
AUTOMATIC_TARGET_SELECTION=false
```

## 5. Built-member candidate set

Candidates are exactly the original members from all `BUILT` items in Task280 item
order. The sequence includes the target, other cohorts, and non-READY members, and
excludes `BOND_NOT_FOUND` items. No member is copied, reconstructed, or prefiltered.

## 6. Task277 reducer reuse

For an existing target member, Task281 calls
`CreditCohortPeerSpreadDistributionService.build()` exactly once with the original
target, all built members, and the exact minimum. Task277 retains ownership of
self-exclusion, member validation, cohort/date/source filtering, duplicate handling,
statistics, percentile, and spread-versus-median calculations.

```text
TASK277_REDUCER_INPUT_READY=true
PEER_DISTRIBUTION_ORCHESTRATION_READY=true
```

## 7. Partial batch semantics

A structurally valid `PARTIAL` batch remains usable when the explicit target exists.
Missing non-target IDs remain visible in provenance but are not represented by fake
candidate members. Batch completeness and distribution readiness are independent.

## 8. Status propagation

When the reducer is called, Task281 propagates its status exactly: `READY`,
`TARGET_MEMBER_INVALID`, `PEER_INPUT_INVALID`, `NO_ELIGIBLE_PEERS`, or
`INSUFFICIENT_PEERS`. When it is not called, Task281 uses only the three orchestration
statuses. The top-level non-READY quality flag equals that status; detailed reducer
flags remain nested in the distribution.

## 9. Availability and provenance

Availability distinguishes valid batch evidence, target request/existence/member,
candidate presence, reducer result presence, and READY distribution. Provenance
preserves safe batch context and ID partitions, target item status, candidate member
IDs, minimum, reducer contract/status, and a reducer call count of zero or one. No DB
provenance is introduced.

## 10. No statistics duplication

Task281 performs no spread arithmetic, ordering, min/max, median, mean, midrank,
percentile, or spread-minus-median calculation. The exact Task277 result is stored as
one nested object and its statistics are not flattened into a second representation.

## 11. Pure boundary

The orchestrator imports schemas and the Task277 reducer only. It has no DB model,
Session, SQLAlchemy, repository, service-loading, network, mutation, cache, or
persistence dependency.

```text
DB_ACCESS=false
SESSION_ACCESS=false
NETWORK_ACCESS=false
PERSISTENCE=false
```

## 12. No automatic universe or target discovery

Task281 neither discovers peers nor chooses a target. It does not call market,
credit, OFZ-curve, Task276, or Task280 services. Universe and target selection remain
explicit caller responsibilities.

## 13. PIT boundary

Every result declares `PIT_READY=false`, including a READY distribution. Pure
orchestration does not establish historical universe membership or repair the
non-PIT Task280/Task276 inputs.

## 14. Explicit non-goals

Task281 does not implement batch construction, peer discovery, statistics, peer
ranking, rating normalization, ordinal mappings, credit-adjusted spreads, investment
ranking, recommendations, strategy, portfolio construction, backtesting, broker
execution, API/frontend work, or deployment.

```text
PEER_DISTRIBUTION_READY=true
PEER_SPREAD_PERCENTILE_READY=true
PEER_SPREAD_VS_MEDIAN_READY=true
PEER_RANKING_READY=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
```

## 15. Verification

Synthetic tests cover all batch-envelope contradictions, target selection, exact
candidate and target object reuse, reducer call counts, partial batches, all reducer
statuses, stable serialization, immutability, Decimal-context isolation, and static
pure-safety rules. Separate Task277 and Task280 regressions verify both dependency
boundaries. The broad backend suite, production/VDS, and live sources are outside
Task281.

## 16. Handoff

Task281 ends with a pure Task280-to-Task277 distribution surface. Task282 may compose
the already verified feature families into one read-only M3 feature view only after
independent Task281 verification and a separate request. Task281 does not begin that
composition.
