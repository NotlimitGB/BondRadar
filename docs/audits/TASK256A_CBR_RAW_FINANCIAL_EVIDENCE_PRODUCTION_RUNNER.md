# Task256A — CBR Raw Financial Evidence Controlled Production Runner v1

## 1. Result

```text
TASK256A_FINAL=PASS
CBR_CONTROLLED_PRODUCTION_INGESTION_RUNNER=READY
PRODUCTION_EXECUTION=NOT_AUTHORIZED
PRODUCTION_EXECUTION=NOT_EXECUTED
TASK256B_STARTED=false
```

Task256A adds safety instrumentation only. No VDS, production database,
network, migration, deployment, or production ingestion action was performed.

## 2. Baseline

```text
STARTING_SHA=e655b42760ac59f3b72b938f42398e09fafba197
STARTING_BRANCH=main
STARTING_WORKTREE=clean
CURRENT_ALEMBIC_HEAD=202609010001
TASK255_STORE_EXISTS=true
PRODUCTION_RUNNER_PREVIOUSLY_EXISTED=false
```

## 3. Request Lock

Only the runner module, its focused test, and this audit are in scope. Task251,
Task252, Task255 models/store/schema, Docker, compose, API, frontend, and all
existing migrations remain unchanged.

## 4. Runner Contract

```text
SCHEMA=bondradar.cbr_raw_financial_evidence_production_runner.v1
RUNNER_CONTRACT=cbr-controlled-production-ingestion-runner-v1
MODES=plan,preflight,apply
DEFAULT_MODE=NONE
RAW_DATABASE_URL_CLI=false
OUTPUT=ONE_COMPACT_JSON_STDOUT_LINE
```

Exit `0` means a completed mode, `1` a sanitized runtime/contract failure,
and `2` invalid arguments. Apply is never the default.

## 5. Approved Fixture Boundary

The runner accepts only report date `2026-08-01` and these repository fixtures:

| Form | Filename | Bytes | SHA-256 |
|---|---|---:|---|
| 0409101 | `101-20260801.rar` | 360046 | `7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a` |
| 0409102 | `102-20260801.rar` | 74392 | `0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21` |
| 0409123 | `123-20260801.rar` | 33042 | `6da408180123fa6748399acb89c717e3fc32380ee818679248043daa9a60baab` |
| 0409135 | `135-20260801.rar` | 33181 | `061a00791196d660bdb070de890228c820ea7e0d8af7978309b11b4226ed4776` |

There is no URL input, discovery, download, cache, or network fallback.

## 6. PLAN Mode

PLAN verifies immutable artifact identities, builds the Task251 bundle, runs
Task255 exact lexical extraction, and emits expected counts and checksums. It
does not read database configuration or construct a database engine.

```text
SUBJECTS=353
ARTIFACTS=4
SNAPSHOTS=4
OBSERVATIONS=38842
RAW_LEXICAL_MISMATCH_COUNT=0
DATABASE_ACCESSED=false
NETWORK_ACCESSED=false
PRODUCTION_ACTIONS=NONE
```

## 7. Per-Form Counts

```text
0409101_RECORDS=25654  0409101_SUBJECTS=353
0409102_RECORDS=10079  0409102_SUBJECTS=212
0409123_RECORDS=1400   0409123_SUBJECTS=352
0409135_RECORDS=1709   0409135_SUBJECTS=345
```

## 8. Subject Set Hashes

```text
0409101=692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd
0409102=90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8
0409123=5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc
0409135=660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619
```

## 9. Evidence Envelope

`--evidence-observed-at` must be an aware UTC ISO-8601 timestamp. It is reused
as fixture discovery/retrieval time and Task255 source observation time. It is
never generated implicitly.

The canonical envelope binds runner and Task255 contract versions, report
date, evidence observation time, `UNKNOWN/NULL` publication state, artifact
identities, record/subject counts, Task251 schema and subject hashes, selected
value member, and ordered exact source-row fingerprint checksum.

For the focused timestamp `2026-08-30T12:00:00Z`:

```text
EVIDENCE_ENVELOPE_SHA256=cb55d3062bdab892b8afa1b34c9c784eca11cbda9d64b49d2bf1482237f360d6
```

Same timestamp and fixtures reproduce this hash. A different timestamp changes
the hash and is not an exact retry. Execution `ingested_at` is not semantic
identity.

## 10. READ-ONLY PREFLIGHT

Preflight accepts only an environment-variable name and explicit
`--confirm-read-only`. It rejects raw URLs and non-PostgreSQL dialects.

The first application statements are exactly:

```sql
SET TRANSACTION READ ONLY;
SHOW transaction_read_only;
```

The verified value must be `on` before bounded SELECTs inspect the Alembic
revision, the six Task255 tables, legacy guard tables, and Task255 row counts.
The transaction is always rolled back and resources are closed/disposed.

## 11. Schema Guard

```text
EXPECTED_ALEMBIC_REVISION=202609010001
TASK255_TABLE_COUNT=6
LEGACY_GUARD_TABLES=companies,bonds,financial_reports,legal_issuers
PARTIAL_TASK255_SCHEMA_ALLOWED=false
RUNNER_EXECUTES_MIGRATION=false
```

Missing, partial, advanced, multiple, or otherwise mismatched schema state
blocks apply before Task255 persistence.

## 12. APPLY Authorization

Apply requires all of:

```text
--mode apply
--task251-fixture-report-date 2026-08-01
--evidence-observed-at <UTC>
--database-url-env <ENV_NAME>
--confirm-write
--expected-envelope-sha256 <LOWERCASE_SHA256>
```

The local plan is recomputed before reading DB configuration. The expected and
actual envelope hashes are compared with `hmac.compare_digest`; mismatch blocks
engine creation.

## 13. Transaction Contract

Apply owns one transaction:

```text
BEGIN
→ revision/schema/count checks
→ Task255 persist_bundle(identity_snapshot=None)
→ independent readback
→ COMMIT ONCE
```

Every pre-commit failure rolls back. Task255 remains caller-transaction-owned
and unchanged.

## 14. Independent Readback

Before commit the runner verifies global count deltas, exact artifact bytes and
hashes, four source snapshots, `UNKNOWN/NULL` publication state, per-form
record/subject/schema lineage, 38,842 observation fingerprints, and each
snapshot's ordered observation checksum. Identity evidence/profile counts must
not change.

No raw financial value is emitted by the runner.

## 15. Idempotency Proof

Disposable SQLite tests exercise the real Task251 parser and Task255 store
behind a private non-CLI adapter.

First execution:

```text
SUBJECTS_INSERTED=353
ARTIFACTS_INSERTED=4
SNAPSHOTS_INSERTED=4
OBSERVATIONS_INSERTED=38842
```

Exact rerun with the same evidence envelope:

```text
ARTIFACTS_INSERTED=0  ARTIFACTS_REUSED=4
SNAPSHOTS_INSERTED=0  SNAPSHOTS_REUSED=4
OBSERVATIONS_INSERTED=0  OBSERVATIONS_REUSED=38842
```

## 16. Rollback and Commit Uncertainty

Injected readback failure rolls back all Task255 rows. A commit exception is
reported as `COMMIT_OUTCOME_UNKNOWN` with `reconciliation_required=true`; the
runner does not claim successful rollback or absence of mutation and does not
retry automatically.

## 17. Secret Handling

Database URLs are read only from the named environment variable. JSON never
contains URL, password, username, host, environment contents, exception text,
SQL, paths, or raw rows. Invalid configuration and runtime errors use fixed
categories.

## 18. Verification

```text
TASK256A_FOCUSED=28_PASSED
TASK255_STORE_AND_TASK251_SOURCE_REGRESSIONS=29_PASSED
CHANGED_PYTHON_COMPILE=PASS
GIT_DIFF_CHECK=PASS
FULL_BACKEND=SKIPPED_BY_REQUEST
DOCKER=SKIPPED_BY_REQUEST
```

## 19. Safety State

```text
NEW_MIGRATION=false
TASK251_CHANGED=false
TASK252_CHANGED=false
TASK255_SCHEMA_CHANGED=false
VDS_ACCESSED=false
PRODUCTION_DATABASE_ACCESSED=false
PRODUCTION_MIGRATION_EXECUTED=false
PRODUCTION_INGESTION_EXECUTED=false
NETWORK_USED=false
NORMALIZATION=false
SCORING=false
```

## 20. Handoff

The sole possible next task is:

```text
Task256B — Controlled VDS Migration & 2026-08-01 Raw CBR Evidence Ingestion
```

Task256B remains unstarted and requires separate explicit authorization.
