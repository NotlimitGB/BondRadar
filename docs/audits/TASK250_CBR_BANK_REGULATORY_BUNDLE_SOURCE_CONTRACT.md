# Task250 — CBR Bank Regulatory Raw Bundle 101/102/123/135 Source Contract

## 1. Execution Profile

Task250 is a documentation-only, fail-closed assessment of the public Bank of
Russia regulatory reporting bundle. It does not implement ingestion.

```text
TASK_ID=Task250
IMPLEMENTATION=DOCUMENTATION_ONLY
MIGRATION=NONE
APPLICATION_CODE_CHANGED=false
DATABASE_PERSISTENCE=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

## 2. Context

Task247 defined the financial-source architecture. Task248 correctly blocked a
0409806/0409807 adapter because public actual-value artifacts were not proven.
Task249 proved that public 0409101/0409102 data is useful but materially
aggregated and requires the 0409123/0409135 complement.

## 3. Starting State

```text
BRANCH=main
STARTING_SHA=12a54027487f29c9813a9d257cf32f5685b3a88f
TRACKED_WORKTREE_AT_START=clean
ALEMBIC_HEAD=202608280002
EXPECTED_CHANGED_FILE_COUNT=2
M2_RAW_FINANCIAL_PERSISTENCE=NONE
```

The intended architecture remains source raw → report identity/provenance →
raw financial items → reviewed normalization → credit metrics → credit risk.

## 4. Task249 Decision

Task249 established that 101 and 102 are usable only as part of a broader bank
bundle. It also proved that current disclosure omits the capital, capital
adequacy, liquidity and prudential-ratio dimensions required for a bank credit
foundation.

## 5. Why Task250 Exists

Machine-readable archives do not by themselves establish analytical
completeness. Task250 verifies actual 123/135 values, current disclosure
reductions, subject overlap, source dates, schema boundaries and the limits of
the combined four-form evidence.

## 6. Goal

Freeze a source-backed future contract for:

```text
CBR_BANK_REGULATORY_BUNDLE
    0409101 balance/account observations
    0409102 financial-result flows
    0409123 regulatory capital
    0409135 mandatory ratios
```

The contract preserves source semantics and authorizes no adapter or database.

## 7. Primary Decision

```text
SOURCE_DECISION=BANK_REGULATORY_BUNDLE_READY_WITH_LIMITATIONS
ECONOMIC_GATE=BUILD_BANK_BUNDLE_ADAPTER_WITH_LIMITATIONS
RECOMMENDED_TASK251=Task251 — CBR Bank Regulatory Bundle Read-Only Source v1
```

The bundle is valuable enough for a separate read-only source implementation.
Its disclosure, history, identity and archive-runtime limitations must remain
first-class states rather than being repaired or defaulted.

## 8. Non-Goals

No source client, parser, RAR/DBF dependency, migration, raw table, persistence,
metric normalization, capital or ratio calculation, scoring, LegalIssuer join,
GIRS/IFRS pipeline, production access, strategy, broker or trading work is in
scope.

## 9. Starting Commit and Preflight

Preflight confirmed the exact SHA, clean `main`, and one Alembic head. A moved
baseline, third file, application change, migration or unrelated diff blocks
delivery.

## 10. Required Repository Investigation

```text
EXISTING_BANK_BUNDLE_CLIENT=NONE
EXISTING_RAR_SUPPORT=NONE
EXISTING_DBF_SUPPORT=NONE
EXISTING_REGN_IDENTITY_BRIDGE=NONE
EXISTING_BANK_RAW_MODEL=NONE
LEGACY_COMPANY_MAPPING_REUSED=false
UNSAFE_RUB_DEFAULT_REUSED=false
MISSING_VALUE_ZERO_REUSED=false
LEGACY_PERIOD_OVERWRITE_REUSED=false
```

Legacy `FinancialReport` and `FinancialReportIngestionService` remain
Company-scoped, default missing currency to RUB and can update a same-period
row. Those semantics are unsafe for M2 raw evidence and are not reused.

## 11. Official Source Baseline

The [CBR reporting page](https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/)
publishes actual dated archives and format descriptions for all four forms.
The [2026 disclosure decision](https://www.cbr.ru/rbr/dir_decisions/rsd_2025-12-19_23_02/)
defines the reduced public projection. Only links exposed by the official page
were used; no archive URL was guessed.

## 12. Bundle Composition

```text
101=0409101
102=0409102
123=0409123
135=0409135
101!=0409806
102!=0409807
```

Each form is a separate source member. A bundle observation never relabels one
form as another.

## 13. Form 0409101 Boundary

Task249 remains authoritative: current 101 has 25,654 data rows, 353 REGNs and
178 published account codes. It exposes first-order/combined opening and
closing balances; current currency-specific and debit/credit turnover fields
are blank. Its status is useful but aggregated balance evidence.

## 14. Form 0409102 Boundary

Task249 remains authoritative: current 102 has 10,079 data rows, 212 REGNs and
49 published codes. `SIM_ITOGO` is the only published value column;
`SIM_R`/`SIM_V` are blank and six total rows are blank. Its status is useful but
aggregated financial-result evidence.

## 15. Form 0409123 Contract

The current archive is a proven actual-value artifact:

```text
FORM_123_CURRENT_ARTIFACT=123-20260801.rar
FORM_123_CURRENT_MEMBERS=072026_123B.dbf,072026_123D.dbf,072026_123N.dbf
FORM_123_CURRENT_SUBJECTS=352
FORM_123_CURRENT_DATA_ROWS=1400
FORM_123_CURRENT_NOMENCLATURE_ROWS=156
FORM_123_CURRENT_CODES=000,102,105,203
FORM_123_NONBLANK_COUNTS=000:352,102:350,105:349,203:349
FORM_123_UNIT=RUB_THOUSANDS
```

`123D` fields are `REGN N(4)`, `C1 C(15)`, `C3 N(16)`; `C1` is the source
row code and `C3` is the unchanged source value. The four codes mean total own
funds, basic capital, additional/basic component, and supplementary capital.
They are not canonical BondRadar metrics in Task250.

## 16. Form 0409135 Contract

The current archive is a proven actual-value artifact:

```text
FORM_135_CURRENT_ARTIFACT=135-20260801.rar
FORM_135_CURRENT_MEMBERS=072026_135_3.dbf,072026_135B.dbf
FORM_135_CURRENT_SUBJECTS=345
FORM_135_CURRENT_DATA_ROWS=1709
FORM_135_ACTUAL_CODES=N1.0,N1.1,N1.2,N1.3,N2,N3,N4,N15,N15.1,N16,N16.1,N16.2,N27
FORM_135_UNIT=PERCENT
```

`135_3` fields are `REGN N(4)`, `C1_3 C(6)`, `C2_3 N(19,3)`,
`C3_3 N(19,3)`, `C4_3 C(12)`. They retain normative code, actual percentage,
optional control percentage and source note code. No ratio is recalculated.

## 17. 2026 Disclosure Reduction Contract

Keep three layers separate:

```text
DEFINED_IN_FORM
DISCLOSABLE_BY_RULE
ACTUALLY_PRESENT_IN_ARTIFACT
FORM_SCHEMA!=PUBLIC_DISCLOSURE
```

For 123, the official rule permits only `000`, `102`, `105`, `203`; all four
are present and nonblank in the current artifact. Other nomenclature rows are
`SUPPRESSED_BY_DISCLOSURE_RULE`.

For 135, the rule permits `N1.1`, `N1.2`, `N1.0`, `N1.3`, `N2`, `N3`, `N4`,
`N15`, `N15.1`, `N16`, `N16.1`, `N16.2`, `N18`, `N27`. Thirteen are observed.
`N18` is `DISCLOSABLE_BY_RULE` but `ACTUAL_STATE=UNKNOWN_NOT_OBSERVED`; it is
not zero, blank, suppressed or automatically not applicable.

## 18. 135 Schema Change Boundary

The [official explanation](https://cbr.ru/explan/0409135_6960-u_6579-u_6993-u_7234-u/)
establishes a regulatory-form regime beginning 1 July 2026. The public DBF
projection has its own version boundary documented from 1 June 2023.

```text
CURRENT_135_REGULATORY_FORM_REGIME_EFFECTIVE_FROM=2026-07-01
PREVIOUS_135_REGULATORY_FORM_REGIME_END=2026-06-30
CURRENT_135_PUBLIC_DBF_FORMAT_EFFECTIVE_FROM=2023-06-01
CURRENT_135_PUBLIC_DBF_FORMAT_PROVEN=true
PREVIOUS_135_PUBLIC_ARCHIVE_PROVEN=true
REGULATORY_SCHEMA_BOUNDARY!=PUBLIC_DBF_LAYOUT_BOUNDARY
```

The current artifact matches the documented public fields. Unknown layouts
must produce `UNSUPPORTED_SCHEMA_VERSION`; regulatory dates may not be used as
a substitute for DBF-layout detection.

## 19. Reporting Subject Identity

```text
101_HAS_REGN=true
102_HAS_REGN=true
123_HAS_REGN=true
135_HAS_REGN=true
TITLE_ONLY_MAPPING_ALLOWED=false
FUZZY_NAME_MATCHING=false
```

Current 123/135 `B` members also carry OGRN, OKPO, BIC, `REGN_S`, title and
observation date. They have no INN field.

## 20. REGN → Legal Identity Bridge

The [CBR credit-organization list](https://www.cbr.ru/banking_sector/credit/FullCoList/)
independently exposes REGN, OGRN and title. Current and sampled 2021 123/135
bank members also embed REGN→OGRN.

```text
REGN_TO_OGRN_PROVEN=true
REGN_TO_INN_PROVEN=false
CURRENT_BRIDGE_AVAILABLE=true
HISTORICAL_BRIDGE_AVAILABLE=PARTIAL
LEGALISSUER_MAPPING_IMPLEMENTED=false
```

`LegalIssuer` currently lacks a CBR REGN/OGRN identity contract. Task250 does
not join, mutate or infer it.

## 21. Cross-Form Subject Overlap

Measured current REGN sets are:

```text
101_SUBJECTS=353
102_SUBJECTS=212
123_SUBJECTS=352
135_SUBJECTS=345
ALL_FOUR_INTERSECTION=170
101_102_INTERSECTION=212
101_123_INTERSECTION=285
101_135_INTERSECTION=345
102_123_INTERSECTION=170
102_135_INTERSECTION=211
123_135_INTERSECTION=278
```

Exact exclusive combinations are: only-123 `67`; 101+135 `26`; 101+123 `7`;
101+123+135 `108`; 101+102 `1`; 101+102+135 `41`; all four `170`. No other
combination is observed. Missing membership is diagnostic and may reflect
applicability, schedule, bank status, disclosure policy or source timing.

## 22. Artifact Identity

| Artifact | Official URL | Bytes | SHA-256 |
|---|---|---:|---|
| `101-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/101-20260801.rar` | 360046 | `7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a` |
| `102-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/102-20260801.rar` | 74392 | `0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21` |
| `123-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/123-20260801.rar` | 33042 | `6da408180123fa6748399acb89c717e3fc32380ee818679248043daa9a60baab` |
| `135-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/135-20260801.rar` | 33181 | `061a00791196d660bdb070de890228c820ea7e0d8af7978309b11b4226ed4776` |
| `123-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/123-20210101.rar` | 153297 | `77da8e43ac061190a6c6eea5ea99fc4ef80ac574e52635ec3abac6333f5bae50` |
| `135-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/135-20210101.rar` | 1351714 | `15e55cb21a555d0e9c62d23c651b05517793048985737d64c3e3fec65477bcde` |

Future identity additionally retains source form, report/as-of date, retrieval
time, container and detected schema version. Filename alone is insufficient.

## 23. Archive / RAR Contract

All six artifacts are RAR. Local `tar`/libarchive inspection proves only local
feasibility, not licensed cross-platform production support.

```text
CONTAINER_FORMAT=RAR
RAR_RUNTIME_STRATEGY=UNRESOLVED
RAR_DEPENDENCY_ADDED=false
PRE_EXTRACTED_SOURCE_AVAILABLE=false
```

Task251 must separately enforce byte/entry limits, malformed archive rejection,
path normalization, traversal prevention, duplicate-member rejection and
Docker/Windows parity before accepting a runtime.

## 24. DBF Contract

Current member inventories are:

```text
101_CURRENT_MEMBERS=072026B1.dbf,072026N1.dbf,NAMES.dbf
102_CURRENT_MEMBERS=072026_P1.dbf,072026NP1.dbf,072026SP1.dbf,SPRAV1.dbf,SPRAV11.dbf
123_CURRENT_MEMBERS=072026_123B.dbf,072026_123D.dbf,072026_123N.dbf
135_CURRENT_MEMBERS=072026_135_3.dbf,072026_135B.dbf
```

Historical inventories are:

```text
123_2021_MEMBERS=122020_123B.DBF,122020_123D.DBF,122020_123N.DBF,122020_123S.DBF
135_2021_MEMBERS=122020_135B.DBF,122020_135_1.DBF,122020_135_2.DBF,122020_135_3.DBF,122020_135_4.DBF,122020_135_5.DBF,122020_135_71.DBF,122020_135_72.DBF,122020_135_8.DBF
123_2021_SUBJECTS=406
123_2021_DATA_ROWS=48133
123_2021_CODES=163
135_2021_SUBJECTS=403
135_2021_SECTION3_ROWS=2921
```

DOS/Cyrillic decoding, blank numeric preservation, field type/precision and
member relationships are schema-versioned. One DBF never implies a complete
form.

Keep transport, disclosure and parsing outcomes distinct:

```text
SOURCE_ERROR
ARTIFACT_NOT_FOUND
FORM_NOT_AVAILABLE_FOR_PERIOD
REGULATORY_DISCLOSURE_RESTRICTED
SUBJECT_NOT_DISCLOSED
INVALID_ARCHIVE
INVALID_DBF
UNSUPPORTED_SCHEMA_VERSION
```

No unavailable form or regulatory restriction is converted to successful
source absence, and no invalid archive/DBF is partially accepted.

## 25. Raw Bundle Observation Contract

A future source-neutral observation may contain:

```text
source_family, source_form, source_form_version
artifact_sha256, artifact_url, artifact_report_date
reporting_subject_regn, reporting_subject_ogrn, reporting_subject_inn
source_section, source_row_code, source_column_code, source_label
raw_value, raw_unit, raw_currency, raw_multiplier
source_as_of_date, period_start, period_end
source_published_at, observed_at, retrieved_at
disclosure_state
```

This shape can retain all four forms only when nullable fields remain unknown
and form-specific rows are never forced into common metric semantics. It is a
conceptual contract, not a model declaration.

## 26. Units and Currency

123 capital values are published in thousands of rubles. Current 135 `C2_3`
and optional `C3_3` are percentages. Current 101/102 limitations remain as in
Task249.

```text
missing currency != RUB
missing unit != assumed unit
missing multiplier != 1
missing value != zero
VALUE_CONVERSION_EXECUTED=false
```

## 27. Period Semantics

- 101 retains balance observation dates; current turnover fields are absent.
- 102 retains source flow/report dates and is not converted to quarters or TTM.
- 123 retains each bank's capital calculation date.
- 135 retains each bank's mandatory-ratio observation date.

Current archives include both `20260701` and `20260801` source dates. Therefore
`same archive month != identical financial period semantics`; no interpolation
or cross-form date substitution is permitted.

## 28. PIT / Historical Availability

```text
FORM_101_PIT=PIT_PARTIAL
FORM_102_PIT=PIT_PARTIAL
FORM_123_PIT=PIT_PARTIAL
FORM_135_PIT=PIT_PARTIAL
REPORT_DATE!=ARTIFACT_DATE
REPORT_DATE!=PUBLICATION_DATE
RETRIEVED_AT!=PUBLISHED_AT
```

Dated archives prove historical availability, not exact first-publication
timestamps. Unknown `published_at` remains null.

## 29. Regulatory Non-Disclosure Periods

```text
PRE_2022=PUBLIC_HISTORY_WITH_SCHEMA_VERSIONING_REQUIRED
2022_TO_2023_RESTRICTION=REGULATORY_NON_DISCLOSURE
2023_TO_2025_REDUCED=REDUCED_DISCLOSURE
2026_REDUCED=REDUCED_DISCLOSURE
REGULATORY_NON_DISCLOSURE!=BANK_DATA_MISSING
```

Unknown periods and uninspected layouts remain unknown. The 2021 123/135
samples demonstrate substantially fuller publication but do not prove every
historical period.

## 30. Bundle Completeness Matrix

| Credit domain | 101 | 102 | 123 | 135 | Bundle status |
|---|---|---|---|---|---|
| balance-sheet scale | aggregated balances | — | — | — | `PARTIAL_RAW_SUPPORT` |
| asset composition | first-order groups | — | — | — | `AGGREGATED_ONLY` |
| customer funding | combined groups | — | — | — | `AGGREGATED_ONLY` |
| bank funding | combined groups | — | — | — | `AGGREGATED_ONLY` |
| accounting capital | partial accounts | — | — | — | `PARTIAL_RAW_SUPPORT` |
| regulatory capital | — | — | four totals | — | `STRONG_RAW_SUPPORT` |
| capital adequacy | — | — | capital base | N1 family | `STRONG_RAW_SUPPORT` |
| mandatory liquidity | — | — | — | N2/N3/N4 | `STRONG_RAW_SUPPORT` |
| mandatory prudential ratios | — | — | — | selected ratios | `PARTIAL_RAW_SUPPORT` |
| profitability | — | section totals | — | — | `PARTIAL_RAW_SUPPORT` |
| interest income/expense | — | totals | — | — | `AGGREGATED_ONLY` |
| provisioning | — | totals | — | — | `AGGREGATED_ONLY` |
| net income | — | retained symbols | — | — | `STRONG_RAW_SUPPORT` |
| concentration | — | — | — | — | `NOT_SUPPORTED` |
| maturity structure | — | — | — | — | `NOT_SUPPORTED` |
| asset quality | insufficient detail | insufficient detail | — | — | `NOT_SUPPORTED` |
| IFRS/group-level view | — | — | — | — | `NOT_SUPPORTED` |

## 31. Bank Credit-Analysis Suitability

The bundle is a useful raw regulatory foundation for reviewed bank-issuer
filtering and trend monitoring before Shadow Test. It is not a complete credit
model. Ratings, default/events, IFRS/consolidated statements, publication-time
controls, normalization and methodology review remain separate prerequisites.
No score or investment signal is produced.

## 32. Bounded Live Investigation

```text
LIVE_NETWORK_USED=true
DATA_ARTIFACTS_DOWNLOADED=6
TOTAL_BYTES=2005672
MAX_ARTIFACTS=6
ADDITIONAL_DOWNLOADS_AFTER_LOCK=0
MASS_ARCHIVE_CRAWL=false
LIVE_PROBE_IMPLEMENTED=false
DATABASE_ACCESSED=false
```

The six-artifact budget is exhausted. Delivery reuses the recorded evidence
and performs no further source request.

## 33. Economic / Engineering Gate

The source is official, compact and materially useful. Reduced disclosure,
schema drift, RAR/DBF handling and incomplete identity/PIT state raise the cost
but do not outweigh the value of a read-only adapter. The next implementation
must preserve all limitations and cannot proceed directly to persistence or
credit scoring.

## 34. Persistence Boundary

```text
RAW_SOURCE_TABLE_CREATED=false
BANK_FINANCIAL_TABLE_CREATED=false
NORMALIZATION_EXECUTED=false
METRIC_CALCULATION_EXECUTED=false
SCORING_EXECUTED=false
```

Database schema remains deferred until a read-only source adapter proves exact
transport, extraction and parsing contracts.

## 35. Allowed Scope

The committed scope is exactly this audit document and its deterministic text
contract test. The already completed bounded official-source investigation is
recorded as evidence; no additional download is authorized.

## 36. Forbidden Scope

No client, parser, CLI, dependency, model, migration, persistence, DB/VDS
access, LegalIssuer/Company mutation, normalization, ratio, score, GIRS/IFRS,
production deployment, strategy, backtest, shadow, broker or trading action is
allowed. Task251 is not started.

## 37. Required Tests

The focused test validates sections 1–42; baseline/head/scope; all artifacts,
hashes, members, schemas and counts; current/historical disclosure; 135 version
boundaries; identity/overlap; units/period/PIT; failure states; matrix; unique
decisions; Task251 handoff and safety.

Tests read local text only and perform no network or database operation.

## 38. Acceptance Criteria

Delivery passes only if both current 123/135 actual artifacts are proven,
public rows remain distinct from full schemas, the 135 boundaries and REGN
overlap are explicit, no missing value is defaulted, exactly two files change,
all focused documentation tests pass and no implementation/persistence leaks
into scope.

## 39. Local Validation

Run only focused compilation, Task250 pytest, Task247/248/249 documentation
regressions, `git diff --check`, exact inventory and complete diff inspection.
The full backend suite is intentionally not run because shared/application code
does not change.

## 40. Git / Diff / Commit / Push

```text
EXPECTED_CHANGED_FILES=docs/audits/TASK250_CBR_BANK_REGULATORY_BUNDLE_SOURCE_CONTRACT.md,backend/tests/test_task250_cbr_bank_regulatory_bundle_source_contract.py
COMMIT_MESSAGE=Assess CBR Bank Regulatory Bundle
FORCE_PUSH=false
REBASE=false
CI=NOT_WAITED_BY_DESIGN
```

`origin/main` must still equal the starting SHA immediately before commit.

## 41. Final Report

The report distinguishes documentation PASS from unimplemented ingestion and
includes exact artifacts, 123/135 public projections, schema boundary,
identity/coverage, units/period/PIT, completeness, decisions, tests, scope,
commit/push and safety.

## 42. HARD STOP

Task250 ends after documentation delivery. Task251 requires separate
authorization and may implement only a read-only source boundary before any
persistence work.

```text
TASK251_AUTOMATICALLY_STARTED=false
TASK251_IMPLEMENTATION_AUTHORIZED=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```
