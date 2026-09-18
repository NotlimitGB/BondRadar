# Task277 — Credit-Cohort Peer Spread Distribution Reducer v1

## 1. Decision

Add a pure, computed view over prebuilt Task276 schema objects. The reducer
describes peer spread distribution and target position within one exact
source-native cohort. Results are not persisted.

Contract: `credit-cohort-peer-spread-distribution-v1`. All output models are
frozen Pydantic models with `extra="forbid"`. Implementation capability is
separate from availability for a particular target and candidate collection.

Required capabilities:

```text
CREDIT_COHORT_RELATIVE_VALUE_MEMBER_INPUT_READY=true
CREDIT_COHORT_PEER_DISTRIBUTION_READY=true
CREDIT_COHORT_SPREAD_PERCENTILE_READY=true
CREDIT_COHORT_SPREAD_VS_MEDIAN_READY=true
PEER_UNIVERSE_DISCOVERY_READY=false
PEER_BATCH_MEMBER_BUILD_READY=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PEER_RANKING_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

The view exposes the same declarations as typed snake-case capability fields.

## 2. Why this reducer does not discover the universe

The caller supplies one target and a candidate iterable. Task277 has no DB,
Session, ORM, network client, persistence or dependency-service calls. It does
not choose an authoritative universe or build Task276 views. A supplied sample
may be incomplete or biased; the descriptive result does not establish
representativeness or statistical robustness.

## 3. Task276 input contract

```python
CreditCohortPeerSpreadDistributionService.build(
    target, candidates, *, min_peer_count=1,
)
```

The annotated candidates interface is a Sequence of
`BondCreditCohortRelativeValueMemberView`; an iterable is accepted at runtime
and copied once into a local tuple. All outer objects must be Task276 schema
objects. Wrong outer types raise `ValueError`.

`min_peer_count` is validated first: exact positive int, excluding bool.
Validation inspects original evidence before output-schema coercion. A valid
member has contract `bond-credit-cohort-relative-value-member-v1`, READY
status, `pit_ready is False`, exact positive int bond ID, exact date excluding
datetime, nonblank market source, finite Decimal spread in bps, a structurally
valid cohort key, and selectors matching the key's target kind and agency.

Invalid target evidence returns `TARGET_MEMBER_INVALID` without inspecting
peer evidence or calculating statistics. Individually usable target fields
and IDs remain available; unusable fields are null. All candidates are counted
as unprocessed. The output contract is stable across unavailable paths.

## 4. Exact cohort equality

Equality requires all five fields: `target_kind`, `rating_agency`,
`source_provider`, `rating_scale_raw`, `rating_value_raw`. Provider and rating
value must be nonblank strings. Scale may be null or a blank string. Strings
are copied exactly: no trimming, case conversion, modifier removal, scale
inference, prefix handling or partial matching. Whitespace inspection is only
an availability check. BOND and LEGAL_ISSUER remain separate families.

## 5. Peer eligibility

Each candidate receives exactly one primary reason, in this order:

```text
TARGET_SELF -> NOT_READY -> STRUCTURALLY_INVALID -> AS_OF_MISMATCH
-> MARKET_SOURCE_MISMATCH -> COHORT_MISMATCH -> ELIGIBLE
```

READY candidates must pass the full member validation before date, source and
cohort comparisons. Contradictory READY evidence is fatal even if it would
otherwise belong to another cohort or observation date. Non-READY candidates
are ordinary exclusions. Sources compare exactly without normalization.

Candidate count equals eligible row count plus the seven mutually exclusive
reason counts, including `invalid_candidate_count` and
`unprocessed_candidate_count`. Invalid target paths have only the unprocessed
count; normal paths have no unprocessed candidates. Quality flags are factual,
sorted and unique; nested input flags are not mechanically copied.

## 6. Target self-exclusion and duplicate protection

Exact integer candidate IDs equal to the valid target ID are excluded first,
including repeated or otherwise contradictory self rows. Bool IDs cannot
masquerade as self IDs. Self exclusions add `TARGET_SELF_EXCLUDED`.

Duplicate protection applies only to non-self rows that passed every filter.
Repeated excluded candidates are harmless. Duplicate eligible IDs make the
collection `PEER_INPUT_INVALID`; no first/last selection, deduplication or
averaging occurs. Every qualified row is retained, including duplicates, and
sorted duplicate IDs are reported separately. Duplicate rows remain eligible
diagnostic rows and do not increment the structural invalid-candidate count.

Evidence rows sort by bond ID, then the complete serialized compact evidence
for duplicate ties. This is diagnostic ordering, not credit-quality ordering.

## 7. Distribution statistics

Only peers contribute to min, median, mean and max. Spreads and the difference
are measured in basis points. Decimal arithmetic uses a fresh
`Context(prec=28, rounding=ROUND_HALF_EVEN)` without presentation rounding.
The caller's precision, rounding, traps and flags are preserved.

After deterministic evidence ordering, spreads sort ascending. Min/max retain
input values. Odd median is the middle value; even median is the two middle
values' sum divided by `Decimal("2")`. Mean is their deterministic sum divided
by `Decimal(N)`. No float arithmetic, scaling or aggregation of duplicate IDs
is introduced. One peer is mathematically sufficient under default minimum 1;
that does not imply statistical robustness.

## 8. Target percentile semantics

For N > 0 and an accepted minimum:

```text
lower_count = number of peer spreads strictly below target spread
equal_count = number of peer spreads equal to target spread
target_spread_percentile = 100 * (lower_count + equal_count / 2) / N
```

The result is a Decimal on 0..100. Higher means a larger spread only. Target
below all peers receives 0, above all receives 100, and equal to all receives
50. Ties receive empirical midrank. The formula equals inserting the target
once into the sample and applying Task270's midrank convention. No Task270
service call is needed. Input order cannot affect output. Below the requested
minimum the percentile is null, even if distribution statistics are available.

## 9. Spread versus peer median

`spread_minus_peer_median_bps = target_spread_to_ofz_bps - peer_median_spread_bps`.
Negative, zero and positive differences retain their signs. This is a
descriptive difference, not a credit adjustment, required premium, fair-value
estimate or expected alpha. It remains visible below the minimum when N > 0.

## 10. Availability and status

Overall precedence:

```text
TARGET_MEMBER_INVALID -> PEER_INPUT_INVALID -> NO_ELIGIBLE_PEERS
-> INSUFFICIENT_PEERS -> READY
```

Invalid target: nullable usable metadata, no peer rows or calculations.
Fatal peer input: qualified rows/count remain diagnostic, but all statistics,
difference and percentile are null. N = 0: all computed values are null.
Below minimum with N > 0: distribution statistics and difference are available,
percentile is null. READY: all descriptive calculations are available.

Availability separately exposes valid target, retained eligible rows, minimum,
distribution, median, percentile, difference and final readiness. On fatal
input `has_eligible_peers` reflects retained rows; `has_minimum_peer_count` and
all mathematical availability are false. Final readiness always equals
`status == READY`. No data absence is interpreted as a credit verdict.

## 11. Provenance

Compact peer evidence contains bond ID, ISIN, SECID, exact Decimal bps spread,
nullable rating event ID and nullable market snapshot ID. Optional malformed
metadata is represented as null; it does not supply cohort or spread evidence.

Provenance preserves target member contract version, target rating event and
market snapshot IDs, candidate count, ordered eligible peer IDs (including
diagnostic duplicate occurrences), requested minimum, and these methods:

```text
percentile_method=TARGET_VS_PEERS_MIDRANK_V1
median_method=DECIMAL_STANDARD_MEDIAN
mean_method=DECIMAL_ARITHMETIC_MEAN
```

Full input members, artifact bytes, curves and rating histories are omitted.
Output keys and mutable output lists do not alter caller-owned inputs.

## 12. No economic interpretation

No economic labels, percentile thresholds, recommendation, investment score,
ranking, top-N selection, strategy, transaction-cost estimate or fair spread
is produced. A spread percentile is a descriptive relative statistic.

## 13. No cross-agency normalization

Agency, provider, target family and raw labels must match exactly. There are
no grade maps, preferred agencies, ordinal comparisons, inherited ratings,
unified credit scores or PD inference. Similar-looking labels do not establish
credit comparability across sources.

## 14. PIT boundary

Every result declares `pit_ready=False`. Pure statistics inherit Task276's
historical observability limitations. They do not reconstruct identity history,
publication availability or a historical universe and do not repair PIT.

## 15. Verification

Task277 tests use synthetic schema views without a DB fixture and cover A–BE,
malformed original types, precedence, primary counters, eligible-only duplicate
protection, retained duplicate rows, minimum boundaries, exact strings,
Decimal precision, shuffled serialization and input/context immutability.
AST checks enforce pure imports/calls and absence of grade/preference maps,
economic labels and dependency evidence recalculation.

Run sequentially from backend:

```text
pytest -q tests/test_credit_cohort_peer_spread_distribution_service.py
pytest -q tests/test_credit_cohort_peer_spread_distribution_service.py tests/test_bond_credit_cohort_relative_value_service.py
python -m compileall app/schemas/credit_cohort_peer_spread_distribution.py app/services/credit_cohort_peer_spread_distribution_service.py
git diff --check
git diff --cached --check
```

Review staged diff and enforce exactly four new requested files. The existing
Task276 regression may use its disposable SQLite fixture; Task277 tests and
application code have no database integration. Full backend suite,
production/VDS, live sources and deployment are skipped by design.

Local acceptance: focused tests 146 passed; combined Task277 + Task276 tests
273 passed (127 Task276 regression tests). Compileall passed. Pytest reported
only the existing cache-permission warning; no cache repair was in scope.
Working/staged whitespace checks and the exact four-new-file scope are
required before delivery.

## 16. Handoff

Only Task278 — Efficient Credit-Cohort Peer Universe & Batch Member Builder v1
may follow independent Task277 verification and a separate request. Task277
does not implement that orchestration. Delivery is one task commit, normal
push to origin/main after remote concurrency checks, remote SHA confirmation
and at most one exact-commit CI snapshot with no waiting, polling or retry.
