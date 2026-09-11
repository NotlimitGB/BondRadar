# Task260A — CBR Historical Artifact Versioning and PIT Selection Contract

## Problem

Task259B proved that at least three current CBR historical artifacts differ in
raw financial evidence from versions independently captured earlier. Therefore
`(form, report_date)` is not a version identity and current/restated history
cannot automatically represent what was knowable at a historical timestamp.

```text
ARTIFACT_VERSION_IDENTITY_DEFINED=true
AS_OF_SELECTOR_IMPLEMENTED=true
PUBLICATION_TIME_PROVEN=false
PIT_READY=false
```

## Evidence from Task259

| Form | Report date | Archived SHA-256 | Exact Wayback observation | Current/frozen SHA-256 | Proven raw difference |
|---|---|---|---|---|---|
| 0409102 | 2024-04-01 | `52fb24529eff57e480148cbda074a1b7354d727d69c369ebef9723126d254dc7` | 2024-07-31T20:20:29Z | `45512cfbf4050996f8f817e30e77d3fa7bf6a5eee3c6ec845ad1a876708af2ce` | 96 fewer records in current |
| 0409135 | 2025-06-01 | `293d3b1cf2c35281b8cdc6b32961cb2c925b1d178d5a91662f714252a31eb764` | 2025-07-16T01:41:11Z | `9149ff890e1fba8bb2f09d4b2feb87c8cbddd4076527b8356eb6a2136235388e` | 3 changed raw values |
| 0409123 | 2025-10-01 | `298ea0d19874b6ec290a59fa0663318c8c3f4809aaa63c2652ed96c26d3e568d` | 2025-10-31T19:13:11Z | `33b40c5805e10e21b89b33d20ebdf5cbb9c2080e5d3a1df2bfbae2fded5c8c7c` | 6 changed raw values |

No availability timestamp is invented for the current/frozen versions. They
remain PIT-ineligible until separately exact-bound evidence is supplied.

## Terminology

- `report_date` describes the regulatory/accounting period; it is not a
  publication timestamp.
- an artifact version is one exact byte identity;
- an availability observation proves that one SHA was observable at a time;
- `publication_at` means first publication and remains unknown here.

Archive capture time and direct retrieval time are observations, not substitutes
for publication time.

## Artifact identity

The minimum identity is:

```text
ArtifactVersionKey=(form, report_date, artifact_sha256)
```

Different SHA-256 values are different versions. A byte difference alone does
not classify its economic meaning; semantic comparison remains separate.
Repeated observations of the same SHA remain evidence attached to one version.

## Availability evidence

Evidence records exact artifact SHA-256, timezone-aware `observed_at`, source,
exact-payload binding, and an optional diagnostic source reference. Supported
sources are `CBR_DIRECT`, `WAYBACK`, `COMMON_CRAWL`, and `OTHER_ARCHIVE`.

Only `exact_payload_bound=true` evidence is PIT-eligible. An evidence SHA must
equal its version SHA. Timestamps are normalized to UTC; naive timestamps fail
closed. No source receives precedence independent of its exact binding and
observation time.

## safe_known_from semantics

```text
safe_known_from=min(observed_at where exact_payload_bound=true)
```

This is the earliest boundary proven by current evidence, not true publication
time. A version without exact-bound evidence has unknown `safe_known_from` and
is not eligible. A later re-observation of the same SHA does not rewrite its
earliest boundary or prove continuous active intervals.

## AS-OF selection algorithm

For the requested `(form, report_date)`, merge duplicate version keys, discard
unknown and future boundaries, and select the unique SHA having the latest
`safe_known_from <= as_of`.

```text
AS_OF_SELECTION_POLICY=LAST_PROVEN_VERSION_AS_OF
SELECTION_QUALITY=CONSERVATIVE_LAST_PROVEN
LOOKAHEAD_INVARIANT=selected.safe_known_from <= as_of
```

Input order, repeated identical observations, and source ordering cannot change
the result. Two different SHAs at the same winning boundary are ambiguous; SHA
or input order is never a tie-breaker.

## Fail-closed outcomes

- `SELECTED`: one unique conservative last-proven version;
- `NO_KNOWN_VERSION_AS_OF`: no exact-bound version is known by `as_of`;
- `AMBIGUOUS_VERSION_AT_BOUNDARY`: multiple SHAs share the winning boundary;
- `INVALID_EVIDENCE`: the supplied version collection violates the model.

Malformed request parameters raise `ValueError`. Missing evidence never falls
back to the current/restated version, latest database row, nearest future
version, or a file downloaded today.

## Current/restated vs PIT history

`CURRENT_RESTATED_HISTORY` is the latest-known source representation available
to BondRadar at ingestion time. It remains useful for current issuer analysis,
latest-known historical trends, research, feature exploration, and data-quality
work. Task258C data is not invalidated.

PIT history requires exact version evidence and AS-OF selection. Current or
restated rows must not leak backward into historical strategy decisions merely
because `report_date <= backtest_date`.

## Forward version capture

Future version-aware retrieval should preserve form, report date, artifact
SHA-256, observation time, evidence source, and immutable bytes or an immutable
evidence reference. The same SHA creates a new observation for the same version;
a new SHA creates a coexisting version. Earlier versions are never overwritten
or deleted merely because CBR changes a file.

## Historical archive reconstruction

Wayback, Common Crawl, or another archive may contribute a historical version
only when the archived payload is exactly bound to its SHA-256. Capture time
becomes conservative `observed_at`; it never fabricates `publication_at`.

## Backtest integration contract

Required future lineage is:

```text
raw financial observation
  -> exact artifact version SHA-256
  -> (form, report_date)
  -> exact availability evidence
  -> safe_known_from
  -> AS-OF selector
  -> observations belonging to the selected version
  -> later reviewed normalization/features
```

Directly querying current rows by `report_date <= backtest_date` is unsafe and
forbidden for PIT use.

## Known limitations

Archive evidence is sparse. `CONSERVATIVE_LAST_PROVEN` can return a stale
version when an uncaptured revision already existed, so historically exact
source state is not always proven. This conservatism prevents look-ahead but
does not reconstruct active-version intervals. In particular, A→B→A cannot be
inferred from first-observation boundaries alone.

Task260A supplies only a pure selector and contract. No versioned persistence,
historical dataset, publication time, normalization, or backtest integration
exists yet; therefore `PIT_READY=false`.

## Next implementation step

```text
NEXT_RECOMMENDED_TASK=260B_HISTORICAL_ARTIFACT_VERSION_PERSISTENCE_AND_FORWARD_OBSERVATION_FOUNDATION
```

Task260B should persist artifact version identity and availability observations,
link raw observations to exact versions, preserve forward revisions, and plan a
controlled classification of existing current versions as observed-now/current-
restated. Task260A does not create its schema, migration, or backfill.

```text
DATABASE_ACCESSED=false
DATABASE_MUTATION_EXECUTED=false
DATABASE_SCHEMA_CHANGED=false
ALEMBIC_CHANGED=false
TASK259_FILES_CHANGED=false
PRODUCTION_ACTIONS=NONE
```
