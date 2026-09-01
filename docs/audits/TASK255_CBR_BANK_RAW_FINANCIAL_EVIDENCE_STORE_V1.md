# Task255 — CBR Bank Raw Financial Evidence Store v1

## 1. Status

Task255 implements the local/disposable persistence boundary defined by Task254.
It adds no production execution command, API route, financial normalization,
score, strategy input, or automatic downstream handoff.

```text
CONTRACT_VERSION=cbr-bank-raw-financial-evidence-v1
STARTING_SHA=fde60a04222c30f40b086840e2f02b6ed973ad01
MIGRATION_REVISION=202609010001
MIGRATION_DOWN_REVISION=202608280002
CBR_RAW_EVIDENCE_STORE_IMPLEMENTED=true
PRODUCTION_MIGRATION_EXECUTED=false
PRODUCTION_INGESTION_EXECUTED=false
```

## 2. Raw lexical feasibility

The mandatory pre-model gate passed without a Task251 change. Task251 retains
the exact compressed RAR bytes on each `CbrBankArtifact`. Task255 invokes the
existing bounded archive extractor, validates the approved value-member DBF
schema, reads the DBF header and field descriptors, and slices the selected
numeric field directly from the exact row bytes.

```text
RAW_LEXICAL_FEASIBILITY=PASS
RAW_TEXT_FROM_SOURCE_BYTES=true
RAW_TEXT_FROM_DECIMAL=false
RAW_TEXT_FROM_FLOAT=false
TASK251_CHANGED=false
```

The fixture audit compared every selected lexical value with Task251's existing
Decimal result:

| Form | Value member | Rows | Blank | Zero | Mismatch |
|---|---|---:|---:|---:|---:|
| 0409101 | `072026B1.DBF` | 25,654 | 0 | 2,827 | 0 |
| 0409102 | `072026_P1.DBF` | 10,079 | 6 | 3,316 | 0 |
| 0409123 | `072026_123D.DBF` | 1,400 | 0 | 335 | 0 |
| 0409135 | `072026_135_3.DBF` | 1,709 | 0 | 125 | 0 |

## 3. Domain tables

The independent domain contains exactly:

```text
cbr_bank_reporting_subjects
cbr_bank_source_artifacts
cbr_bank_report_snapshots
cbr_bank_raw_observations
cbr_bank_subject_legal_issuer_evidence
cbr_bank_subject_legal_issuer_profiles
```

`FinancialReport`, `FinancialReportSourceDocument`, `Company`, Task241–Task253
models, and their semantics are unchanged.

## 4. Reporting subjects

The natural identity is `(CBR, CREDIT_ORGANIZATION_REGN, canonical REGN)`.
Task252's exact positive-decimal REGN canonicalizer is reused. Subjects are
created for raw value-member records, including blank observations. Their
first/last observation bounds are mutable current metadata; raw observations
remain append-only.

## 5. Artifacts and exact bytes

Artifacts preserve the exact compressed bytes, official locator metadata,
form/date, Task251 parser/runtime lineage, and first discovery/retrieval times.
Before insertion the service independently recomputes:

```text
sha256(content_bytes) == content_sha256
len(content_bytes) == compressed_size
```

Artifact byte identity excludes URL/name/time. Identical bytes reuse the first
artifact row. Different bytes produce a different fingerprint and never
overwrite the earlier row.

## 6. Snapshots and observations

A snapshot binds one artifact, form, report date, value member, ordered member
schema, parser version and observation/publication envelope. Its fingerprint is
computed before observation fingerprints; the ordered observation fingerprints
then produce the non-circular observation-set checksum.

Every Task251 value-member row, including a blank public row, becomes one raw
observation. Lineage includes the member, 1-based row number, exact source-row
field hash, selected value field, source code/dimensions, source date, unit,
currency, multiplier and disclosure state.

## 7. Decimal and blank semantics

```text
PUBLIC_VALUE_BLANK -> raw_value_text="", parsed_decimal_value=NULL
ZERO -> Decimal("0")
FLOAT_PATH=false
SCALING=false
RATIO_CONVERSION=false
CURRENCY_DEFAULT=false
UNIT_DEFAULT=false
```

`raw_value_text` preserves signs, fractional digits and trailing scale after
removing only DBF NUL/fixed-width space padding. Decimal validation reads the
same field bytes directly. `RUB_THOUSANDS`, `PERCENT`, `RUB` and multiplier
`1000` remain metadata, not transformations.

## 8. PIT semantics

Publication state is constrained to `KNOWN`/`UNKNOWN`; the timestamp must be
present exactly for `KNOWN`. The approved 2026-08-01 fixtures are stored with
unknown publication time unless a separately proven source fact is provided.

```text
PUBLICATION_UNKNOWN_PRESERVED=true
HISTORICAL_BACKCAST_ALLOWED=false
GENERAL_MONTHLY_INGESTION_PROVEN=false
HISTORICAL_BACKFILL_PROVEN=false
```

## 9. Fingerprints and idempotency

All fingerprints use one canonical UTF-8 JSON encoder with sorted object keys,
explicit nulls, UTC datetimes, tagged exact bytes/dates/Decimals and no float.
Separate fingerprints cover artifact bytes, snapshot envelope, raw row,
observation and Task252 identity evidence.

An exact retry reloads every matching fingerprint and verifies semantic equality
before reporting reuse. Fingerprint equality with different semantic content is
a blocking collision, never `ON CONFLICT DO NOTHING`.

## 10. LegalIssuer boundary

Task252 results are projected without another resolver:

```text
VERIFIED -> VERIFIED
LEGAL_ISSUER_NOT_FOUND -> NOT_FOUND
LEGAL_ISSUER_INN_AMBIGUOUS -> AMBIGUOUS
LEGAL_ISSUER_NOT_VERIFIED -> NOT_VERIFIED
LEGAL_ISSUER_NOT_EVALUATED -> NOT_EVALUATED
other source-chain failures -> SOURCE_IDENTITY_BLOCKED
```

Only verified Task252 output can populate the current profile's LegalIssuer FK.
Titles remain diagnostics. Non-matches and blocked identity states never prevent
raw source persistence. Stable source identity survives `ON DELETE SET NULL`.

## 11. Reobservation

Identity evidence includes the source observation time. Therefore
`A@T1 -> B@T2 -> A@T3` produces three append-only evidence rows and resolves the
current profile to A at T3. Repeated raw bytes with a different observation
envelope similarly produce a new snapshot rather than rewriting history.
Non-identical latest evidence tied at one timestamp fails closed.

## 12. Transaction and deletion safety

The store calls `flush()` only. Commit and rollback belong to the caller. An
injected mid-ingestion failure is verified to disappear after caller rollback.
Artifact, snapshot and subject lineage uses `RESTRICT`; LegalIssuer references
use `SET NULL`. The normal service contains no evidence update/delete path.

## 13. Fixture persistence

The approved local fixture proof expects and reads back:

```text
subjects=353
artifacts=4
snapshots=4
observations=38842
```

The retry must insert zero new append-only rows and reproduce the stored byte,
subject and observation checksums.

## 14. Verification and safety

The locked verification is Task255 focused tests, migration tests, directly
affected Task251/Task252 regressions, changed-Python compileall, Alembic
heads/history, `git diff --check`, and exact scope review. The full backend suite,
Docker, frontend and network are intentionally outside the local Task255 run.

```text
TASK255_FOCUSED=17_PASSED
TASK251_TASK252_REGRESSIONS=20_PASSED
CHANGED_PYTHON_COMPILEALL=PASS
ALEMBIC_HEAD=202609010001
GIT_DIFF_CHECK=PASS
LOCAL_BROAD_REGRESSION=SKIPPED_BY_DESIGN
```

```text
LEGACY_FINANCIAL_REPORT_CHANGED=false
COMPANY_CHANGED=false
LEGAL_ISSUER_SEMANTICS_CHANGED=false
TASK251_CHANGED=false
TASK252_SEMANTICS_CHANGED=false
TASK253_CHANGED=false
PRODUCTION_DATABASE_ACCESSED=false
VDS_ACCESSED=false
PRODUCTION_MIGRATION_EXECUTED=false
PRODUCTION_INGESTION_EXECUTED=false
NORMALIZATION=false
SCORING=false
ANALYTICS_WIRING=false
PRODUCTION_ACTIONS=NONE
```

## 15. Next boundary

Task255 does not authorize production migration or ingestion. The sole future
handoff, after code review and CI, is:

```text
Task256 — Controlled Production Migration & 2026-08-01 Raw CBR Evidence Ingestion
TASK256_STARTED=false
```
