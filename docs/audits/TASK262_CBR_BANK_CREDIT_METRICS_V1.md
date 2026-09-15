# Task262 — CBR Bank Credit Metrics v1

## Contract and boundary

Task262 adds immutable reviewed credit facts on top of Task261 normalized CBR
observations.

```text
STARTING_SHA=77b25fe1bf4ca1c5f653c57b2c1889bb054463da
ALEMBIC_PREVIOUS=202609140001
ALEMBIC_TASK262_HEAD=202609150001
CREDIT_METRIC_CONTRACT=cbr-bank-credit-metric-v1
SCHEMA_ONLY_MIGRATION=true
DATA_BACKFILL=false
SUPPORTED_FORMS=0409123,0409135
PIT_READY=false
```

## Task262-FIX1 post-production correction

```text
TASK262_FIX1=true
PRODUCTION_PLAN_DISCOVERED_N18=true
N18_PRODUCTION_ROWS=6
N18_SUPPORTED=true
N18_METRIC_KEY=CBR_135_N18
N18_UNIT=PERCENT
N18_PERCENT_RESCALING=false
```

The count of six is operator-provided prior evidence from a read-only
production PLAN; Task262-FIX1 does not access or independently remeasure the
production database. Task250 correctly recorded `N18` as
`DISCLOSABLE_BY_RULE` while the then-inspected current artifact had
`ACTUAL_STATE=UNKNOWN_NOT_OBSERVED`. The operator-provided production PLAN
reports that later historical normalized observations contain this code, so it
is now added to the explicit source mapping without rewriting the earlier
Task250 conclusion.

## Task262-FIX2 PostgreSQL migration correction

```text
TASK262_FIX2=true
PRODUCTION_MIGRATION_ATTEMPT_202609150002=FAILED_SAFE
FAILURE_REASON=POSTGRESQL_PHYSICAL_CHECK_NAME_WAS_TRUNCATED_AND_HASHED
PRODUCTION_REVISION_AFTER_FAILURE=202609150001
PRODUCTION_CREDIT_METRIC_ROWS_AFTER_FAILURE=0
TRANSACTIONAL_ROLLBACK_VERIFIED=true
FIX=SEMANTIC_REFLECTED_CHECK_DISCOVERY
```

These production facts are operator-provided evidence; Task262-FIX2 does not
access or independently inspect production. PostgreSQL shortened the physical
CHECK name generated from the repository naming convention, so suffix matching
against the untruncated logical name could not locate it. The corrected
migration identifies exactly one reflected CHECK by the invariant Task262
mapping semantics and then uses its actual reflected physical name. Zero or
multiple semantic matches fail closed. No new Alembic revision is created,
because `202609150002` did not advance successfully in production.

The immutable lineage is:

```text
credit metric
→ normalized observation
→ raw observation
→ report snapshot
→ exact source artifact
```

Task262 neither selects an artifact version nor joins a LegalIssuer. Different
artifact versions remain different normalized observations and therefore
different metric rows.

## Reviewed source mappings

Form 0409123 is represented only by conservative source-code identities:

```text
000 → CBR_123_000
102 → CBR_123_102
105 → CBR_123_105
203 → CBR_123_203
family=REGULATORY_CAPITAL
unit=RUB
```

No inferred economic label is attached to code 105. Task261 has already
converted the source RUB-thousands value to RUB; Task262 copies that Decimal
without scaling it again.

Form 0409135 uses the exact published regulatory-ratio code:

```text
N1.0  → CBR_135_N1_0
N1.1  → CBR_135_N1_1
N1.2  → CBR_135_N1_2
N1.3  → CBR_135_N1_3
N2    → CBR_135_N2
N3    → CBR_135_N3
N4    → CBR_135_N4
N15   → CBR_135_N15
N15.1 → CBR_135_N15_1
N16   → CBR_135_N16
N16.1 → CBR_135_N16_1
N16.2 → CBR_135_N16_2
N18   → CBR_135_N18
N27   → CBR_135_N27
family=REGULATORY_RATIO
unit=PERCENT
```

The percentage is copied unchanged. It is not divided by 100, recalculated,
compared with a threshold or classified as good/bad.

Forms 0409101 and 0409102 remain normalized source evidence and are excluded
from canonical credit metrics in v1. No account aggregation, revenue, EBITDA,
debt, coverage, asset-quality, profitability or liquidity metric is inferred.

## Missing values and drift

`SOURCE_VALUE_UNAVAILABLE` creates no credit-metric row. Null, blank, unknown
and unavailable values never become zero.

A `PUBLIC_VALUE` row in form 123 or 135 with a code outside the frozen mapping
is `UNSUPPORTED_METRIC_SOURCE`. PLAN reports it by exact form/code and becomes
not ready; PREFLIGHT blocks. Unknown unavailable rows remain non-numeric and
are retained as diagnostics rather than fabricated metrics.

Every stored metric is a direct projection of exactly one normalized row.
`metric_fingerprint` binds the Task262 contract, Task261 normalization
fingerprint, key, family, exact Decimal value and unit. Exact retries reuse the
row; changed semantics for the same normalized ID are a hard collision. The
store flushes but never owns the caller's commit.

## Controlled runner

`app.services.cbr_bank_financial_evidence.credit_metrics_runner` provides
PLAN, PREFLIGHT and APPLY over PostgreSQL only from the CLI. A private SQLite
adapter exists only for disposable tests.

PLAN/PREFLIGHT verify a read-only transaction and current schema, scan relevant
normalized IDs in ascending batches of 2,000, freeze a deterministic boundary,
and hash the complete classification. APPLY requires the exact boundary/hash,
repeats PREFLIGHT, commits bounded idempotent batches, and verifies complete
one-to-one readback for all supported numeric observations.

Partial or uncertain commits are reported without claiming rollback certainty.
Continuation requires a new PLAN, PREFLIGHT and newly authorized hash.

The migration advances the shared exact-head guards to `202609150001` for raw,
monthly, historical and normalization runners without changing their business
behavior.

## Non-goals and production state

```text
NETWORK_ACCESSED=false
RAR_DBF_PARSED=false
LEGAL_ISSUER_INFERENCE=false
PIT_SELECTION=false
PIT_READY=false
SCORING=false
CFA_IMPLEMENTED=false
PRODUCTION_DB_ACCESSED=false
PRODUCTION_SCHEMA_APPLIED=false
PRODUCTION_CREDIT_METRICS_APPLIED=false
PRODUCTION_ACTIONS=NONE
TASK263_STARTED=false
```

The next operation, if separately authorized, is a controlled production
backup, Task262 migration and read-only credit-metric PREFLIGHT. No production
APPLY or downstream scoring/CFA task is unlocked automatically.
