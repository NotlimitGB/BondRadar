# Task280 — Efficient Credit-Cohort Batch Member Builder v1

## 1. Decision

Task280 provides a read-only batch builder for an explicit caller-supplied bond-ID
universe. It builds Task276-equivalent members while reusing one Task268 OFZ curve.
The contract version is `credit-cohort-batch-members-v1`.

## 2. Explicit universe boundary

`bond_ids` is the complete universe. The service accepts a nonempty deterministic
sequence of unique exact positive integers, copies it once, and sorts it ascending.
It does not discover, extend, deduplicate, or infer a universe. Strings, bytes-like
values, mappings, sets, generators, bools, non-integers, nonpositive IDs, and
duplicate IDs are rejected before database access.

```text
EXPLICIT_BOND_ID_UNIVERSE_READY=true
PEER_UNIVERSE_DISCOVERY_READY=false
```

## 3. Performance problem solved

Calling Task276 separately for N bonds would build the OFZ curve N times. Task280
instead performs one explicit existence query, builds the curve once when at least
one requested bond exists, and reuses that immutable view for every pure Task278
evaluation. It never calls Task268 `evaluate_bond()` or Task276 per member.

## 4. Existing-bond validation

One narrow `Bond.id` projection is constrained by the normalized requested IDs and
ordered by ID. No other Bond field is read. Existing and missing IDs are returned
ascending, and every missing ID receives a `BOND_NOT_FOUND` item with `member=null`.
Missing IDs do not trigger per-bond dependency calls.

## 5. Shared OFZ curve

For a nonempty existing universe, Task268 `build_curve()` is called exactly once
with the request date, exact market source, and curve-age limit. READY and
non-READY curves are both preserved and reused. When all requested IDs are absent,
the curve is not built and `shared_curve=null`.

```text
SHARED_OFZ_CURVE_BATCH_REUSE_READY=true
```

## 6. Per-bond Task267 market input

Each existing bond invokes `BondMarketFeatureService.build_for_bond()` exactly once
with the shared date, source, and market-age limit. Task280 does not query market
snapshots directly and does not read persisted legacy market fields.

## 7. Per-bond Task275 credit input

Each existing bond invokes `BondCreditComparabilityService.build_for_bond()` exactly
once. Task280 does not query rating events, repeat publication selection, infer a
rating scale, or reconstruct cohort keys.

## 8. Task278 evaluator reuse

The pure `evaluate_market_against_ofz_curve()` function receives each Task267 view
and the same shared curve object. Interpolation, no-extrapolation, spread arithmetic,
status ordering, flags, and relative-value provenance remain owned by Task278.

## 9. Task279 composer reuse

The pure `compose_credit_cohort_relative_value_member()` function receives Task275
and Task278 outputs plus the explicit selectors. Task280 does not duplicate cohort
selection, exact-key checks, status precedence, defensive nulling, or member
provenance.

## 10. Task276 equivalence

For a stable database state and identical request parameters, every built member's
full `model_dump()` equals the result of direct Task276 construction. The batch
optimization changes curve orchestration only; it does not change member semantics.

```text
TASK276_EQUIVALENT_MEMBER_BUILD_READY=true
BATCH_MEMBER_BUILD_READY=true
```

## 11. Missing bond semantics

An existing bond produces `BUILT` with a complete member, including legitimately
unavailable members. A missing bond produces `BOND_NOT_FOUND` and no synthetic
member. Batch status is `COMPLETE` when all IDs exist, `PARTIAL` when existing and
missing IDs coexist, and `NO_EXISTING_BONDS` when all IDs are missing.

## 12. Batch status and diagnostics

Batch status measures build completeness rather than financial readiness. The output
reports requested, existing, missing, built, READY, and unavailable counts plus
`curve_built`. A `COMPLETE` batch may have zero READY members. Items and all ID lists
are ascending, so serialization is independent of caller ordering.

## 13. Call-count contract

For R unique requested IDs and E existing requested IDs:

```text
explicit existence query = 1
OFZ build_curve = 1 if E > 0 else 0
Task267 market calls = E
Task275 credit calls = E
Task278 evaluator calls = E
Task279 composer calls = E
Task268 evaluate_bond calls = 0
Task276 service calls = 0
Task277 reducer calls = 0
```

The provenance records these factual counts and the shared curve contract, status,
trade date, and node count once.

## 14. Read-only and PIT boundaries

The existence query and dependency pipeline run under `Session.no_autoflush`.
Task280 issues no mutation calls, preserves caller-owned pending state, performs no
network or source refresh, and persists no result. Efficient orchestration does not
repair historical observability.

```text
READ_ONLY_SERVICE=true
PIT_READY=false
```

The following capabilities remain unavailable:

```text
PEER_DISTRIBUTION_ORCHESTRATION_READY=false
PEER_RANKING_READY=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
```

## 15. Verification

Focused tests cover input validation, deterministic normalization, partial and
all-missing universes, exact call counts, shared object reuse, unavailable evidence,
full Task276 equivalence, SELECT-only SQL, pending-state preservation, frozen schema
behavior, stable serialization, Decimal-context isolation, and AST safety. Task278,
Task279, and Task276 regressions provide the dependency boundary checks. The full
backend suite, production/VDS, and live sources are intentionally outside Task280.

## 16. Handoff

Task280 ends with an explicit-universe batch of Task276-equivalent members and one
shared curve. It does not call Task277 or create peer distributions. Task281 may
consume this batch and orchestrate the pure Task277 reducer only after independent
Task280 verification and a separate request.
