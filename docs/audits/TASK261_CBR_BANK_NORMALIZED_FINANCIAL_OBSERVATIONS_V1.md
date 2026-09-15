# Task261 — CBR Bank Normalized Financial Observations v1

## Status and boundary

Task261 adds the first reviewed normalization boundary for persisted CBR bank
regulatory evidence. It does not calculate credit metrics and does not select an
artifact version for point-in-time use.

```text
STARTING_SHA=eb92701d55715591375d3cd9a5d5190eab893e31
ALEMBIC_PREVIOUS=202609110001
ALEMBIC_TASK261_HEAD=202609140001
NORMALIZATION_CONTRACT=cbr-bank-normalized-financial-observation-v1
NORMALIZED_TABLE=cbr_bank_normalized_observations
SCHEMA_ONLY_MIGRATION=true
MIGRATION_DATA_BACKFILL=false
NORMALIZATION_PIT_SELECTION=false
PIT_READY=false
```

The data flow is:

```text
source artifact
→ report snapshot
→ immutable raw observation
→ immutable normalized observation
→ future reviewed credit metrics
```

The normalized row keeps a required `ON DELETE RESTRICT` reference to exactly
one raw observation. The existing raw-to-snapshot-to-artifact chain therefore
remains the exact artifact-version lineage. No source bytes are copied and no
RAR or DBF is opened by normalization.

## Normalization rules

Forms 0409101, 0409102 and 0409123 require the persisted source contract
`RUB_THOUSANDS`, `RUB`, multiplier `1000`. A public value is multiplied using
exact `Decimal` arithmetic and stored as RUB. There is no float conversion,
rounding or sign change.

Form 0409135 requires `PERCENT`, null currency and null multiplier. Its value is
stored unchanged as PERCENT; Task261 does not divide it by 100.

`PUBLIC_VALUE` requires a finite Decimal. Every non-value disclosure state
requires a null Decimal and produces `SOURCE_VALUE_UNAVAILABLE`. The original
disclosure state is copied separately, so blank, suppressed, not-present and
unknown remain distinguishable. Null is never converted to zero.

`report_date` and nullable `source_date` are copied independently. Neither is a
publication timestamp or an inferred period boundary.

## Source-faithful item identity

The v1 item identity is the existing CBR form, source code, nullable subcode and
the complete form-specific source dimensions. It does not introduce concepts
such as assets, debt, revenue, EBITDA or net profit.

Dimensions are retained in their persisted order, while the item fingerprint
uses the fixed form-specific dimension order. The required keys are:

```text
0409101: PLAN, NUM_SC, A_P
0409102: CODE
0409123: C1
0409135: C1_3
```

One source semantic was confirmed during fixture validation: 0409135 DBF rows
preserve the Cyrillic code prefix `Н` in `C1_3`, while Task251 deliberately
exposes the canonical `source_code` with Latin `N`. Task261 reuses only that
exact Task251 equivalence (`Н…` to `N…`) for validation and never changes the
stored dimension. No fuzzy, title or taxonomy matching exists.

`item_fingerprint` is independent of database IDs. `normalization_fingerprint`
binds the raw observation fingerprint, item fingerprint, disclosure/value
state, exact normalized Decimal, unit, currency, transformation and multiplier.
An existing raw observation with different normalized semantics is a hard
collision; rows are never updated in place.

## Persistence and version behavior

Contract v1 permits exactly one normalized row per raw observation. Query-first
idempotency and database uniqueness make exact retries reusable. Concurrent
inserts are guarded by a savepoint and semantic reload. The service flushes but
does not commit.

Different raw observations for different artifact versions normalize into
different rows even when their report date, subject and source item are equal.
Task261 does not choose latest, current, original, restated or PIT-eligible
versions. Task260A selection must happen before a future consumer reads the
normalized rows belonging to an eligible artifact SHA.

## Controlled runner

The DB-only module `app.services.cbr_bank_financial_evidence.normalization_runner`
provides PLAN, PREFLIGHT and APPLY. It accepts only a named PostgreSQL URL
environment variable and has no network, artifact, URL, issuer or PIT options.

PLAN and PREFLIGHT verify `SET TRANSACTION READ ONLY` and process raw IDs in
ascending batches of 2,000. The plan freezes `through_raw_observation_id` and
hashes both aggregate counts and the ordered raw/normalized semantic scope.
PREFLIGHT requires an exact hash, current schema, zero unsupported inputs and
zero semantic collisions.

APPLY repeats PREFLIGHT, commits bounded idempotent batches, performs a final
readback and reports partial or uncertain commit outcomes without claiming a
rollback that cannot be proven. After a partially successful APPLY, the
operator must run a new PLAN and PREFLIGHT and explicitly authorize the new
hash. Completed rows are then reused.

The additive Task261 migration becomes the repository Alembic head. Existing
raw-ingestion runners use a shared exact-head guard, so that shared guard moves
to `202609140001`; their raw evidence semantics do not otherwise change.

## Fixture and safety evidence

The approved August 2026 fixtures establish the required one-to-one boundary:

```text
0409101_RAW_ROWS=25654
0409102_RAW_ROWS=10079
0409123_RAW_ROWS=1400
0409135_RAW_ROWS=1709
APPROVED_FIXTURE_RAW_ROWS=38842
APPROVED_FIXTURE_NORMALIZED_ROWS=38842
0409102_SOURCE_VALUE_UNAVAILABLE_ROWS=6
```

Migration tests require zero normalized rows immediately after upgrade even
when raw rows already exist. Population is possible only through the separately
confirmed runner APPLY mode.

```text
LEGACY_FINANCIAL_REPORT_CHANGED=false
LEGAL_ISSUER_INFERENCE=false
SOURCE_NETWORK_ACCESSED=false
PRODUCTION_DB_ACCESSED=false
PRODUCTION_MIGRATION_APPLIED=false
PRODUCTION_NORMALIZATION_APPLIED=false
CREDIT_METRICS_CALCULATED=false
SCORING=false
PRODUCTION_ACTIONS=NONE
TASK262_STARTED=false
```

The only next operation is a separately authorized production backup,
Task261 migration and normalization PREFLIGHT. No production APPLY or credit
metric work is authorized by this task.
