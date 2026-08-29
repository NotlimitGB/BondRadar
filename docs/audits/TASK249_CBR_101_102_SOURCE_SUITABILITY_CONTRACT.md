# Task249 — CBR Forms 101/102 Raw Regulatory Source Suitability Contract

## 1. Execution Profile

Task249 is a documentation-only, fail-closed assessment of official Bank of
Russia regulatory data. It does not ingest or normalize financial data.

```text
TASK_ID=Task249
IMPLEMENTATION=DOCUMENTATION_ONLY
MIGRATION=NONE
APPLICATION_CODE_CHANGED=false
DATABASE_PERSISTENCE=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

## 2. Context

Task247 selected CBR bank reporting for bounded source investigation. Task248
correctly blocked a 0409806/0409807 adapter because only submission schemas,
not public actual-value artifacts, were proven. Task249 assesses the distinct
public actual-value forms 0409101 and 0409102 without treating them as
substitutes for 0409806 or 0409807.

## 3. Starting State

```text
BRANCH=main
STARTING_SHA=5065c284f0d8052bc33122ebd3ce5b64d88deba0
TRACKED_WORKTREE_AT_START=clean
ALEMBIC_HEAD=202608280002
EXPECTED_CHANGED_FILE_COUNT=2
```

No canonical M2 raw-financial persistence exists. Legacy `FinancialReport`,
Company-scoped ingestion and controlled normalized values remain unchanged.

## 4. Why Task249 Exists

The official reporting page exposes dated actual DBF archives for forms 101
and 102, but downloadability alone does not prove suitability. Public-detail
reductions, historical gaps, subject identity, period interpretation, RAR/DBF
operations and missing capital/normative dimensions must be evaluated before
any adapter is authorized.

## 5. Goal

Task249 establishes whether forms 101/102 are a primary source, part of a
broader bank bundle, secondary evidence, or no-go. It preserves exact source
identity and measured disclosure limitations and selects one safe follow-up.

## 6. Primary Decision

```text
SOURCE_DECISION=101_102_READY_AS_PART_OF_BANK_BUNDLE
ECONOMIC_GATE=BUILD_BANK_REGULATORY_BUNDLE_NOW
RECOMMENDED_TASK250=Task250 — CBR Bank Regulatory Raw Bundle 101/102/123/135 Source Contract
```

101/102 provide useful balance and P&L source facts, but current disclosure is
materially aggregated. Forms 123 and 135 are required complements for capital
and mandatory-ratio coverage. This decision authorizes no implementation.

## 7. Non-Goals

Task249 does not implement a source client, archive or DBF parser, database
schema, raw-data persistence, LegalIssuer mapping, chart/symbol normalization,
financial ratios, credit scoring, GIRS, IFRS, backtests, strategy, shadow,
broker or trading work.

## 8. Starting Commit and Preflight

Preflight confirmed the exact branch, starting SHA, clean tracked tree and
Alembic head. A different baseline, application-code change, third tracked
file, migration, dependency, persistence or production action is a hard stop.

## 9. Required Repository Investigation

```text
EXISTING_101_102_CLIENT=NONE
EXISTING_DBF_SUPPORT=NONE
EXISTING_RAR_SUPPORT=NONE
EXISTING_CBR_REGN_MAPPING=NONE
EXISTING_BANK_RAW_FINANCIAL_MODEL=NONE
EXISTING_HASHING_SUPPORT=hashlib.sha256
```

Legacy ingestion is unsafe for M2: it maps through Company, defaults missing
currency to RUB and can update/collide at a Company/year/quarter identity.
Those behaviours are explicitly excluded from Task249 and its handoff.

## 10. Official Source Baseline

The authoritative [CBR reporting page](https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/)
publishes actual DBF archives and format descriptions for 101, 102, 123 and
135.

```text
FORM_101_PUBLIC_DATA=true
FORM_102_PUBLIC_DATA=true
FORM_123_PUBLIC=true
FORM_135_PUBLIC=true
```

The tested URL pattern is evidence from listed links, not a license to guess
unlisted historical URLs.

## 11. Live Source Investigation

The bounded investigation downloaded exactly four official artifacts, the
maximum authorized by the contract:

| Artifact | Official URL | Bytes | SHA-256 |
|---|---|---:|---|
| `101-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/101-20260801.rar` | 360046 | `7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a` |
| `102-20260801.rar` | `https://www.cbr.ru/vfs/credit/forms/102-20260801.rar` | 74392 | `0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21` |
| `101-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/101-20210101.rar` | 2352938 | `d1a54ad2aabaf47263f2fb233430013d149c3962251c050666741fff1de3552c` |
| `102-20210101.rar` | `https://www.cbr.ru/vfs/credit/forms/102-20210101.rar` | 2278946 | `e82a18dccff959823c0821184b09ed720f9cad22ae64e86cecd715a329f6ef94` |

```text
LIVE_NETWORK_USED=true
DATA_ARTIFACTS_DOWNLOADED=4
TOTAL_BYTES=5066322
DATABASE_ACCESSED=false
```

## 12. Form Identity Boundary

```text
101=0409101
102=0409102
101!=0409806
102!=0409807
```

Every future artifact and row retains its exact form. Similar financial themes
do not permit relabelling one regulatory form as another.

## 13. Form 0409101 Semantics

0409101 is the turnover balance sheet of a credit institution. The current
data table retains `REGN`, `PLAN`, `NUM_SC`, `A_P`, opening-balance fields,
debit/credit turnover fields, closing-balance fields, `DT` and `PRIZ` in its
schema. Actual publication is narrower than this schema.

The current sample contains 25,654 rows, 353 distinct REGNs and 178 published
account codes. `PLAN` and the active/passive flag remain source attributes;
account codes are never converted to canonical metrics in Task249.

## 14. Form 0409102 Semantics

0409102 is the financial results report of a credit institution. Its data
table retains `REGN`, source `CODE`, `SIM_R`, `SIM_V`, `SIM_ITOGO` and `DT`.
The current sample contains 10,079 rows, 212 distinct REGNs and 49 published
codes. The 49 public rows are explicitly marked by `VALUE_PUBL=1` in the
current `SPRAV11` reference table.

Source symbols are not renamed to revenue, profit or another canonical metric.

## 15. Public Disclosure Reduction / Aggregation

The [2026 CBR disclosure decision](https://www.cbr.ru/rbr/dir_decisions/rsd_2025-12-19_23_02/)
limits 101 to first-order aggregated active/passive balances and selected
combined account groups. In the current DBF, only `VITG` and `IITG` are
populated; `VR`, `VC`, `VG`, `VM`, all debit/credit turnover fields and
`IR`, `IC`, `IG`, `IM` are blank.

For 102, the decision publishes section totals, further aggregates selected
sections and preserves a small set of result symbols. In the current DBF,
`SIM_ITOGO` is populated for 10,073 of 10,079 rows; `SIM_R` and `SIM_V` are
blank. Six blank totals remain missing and never become zero.

```text
SCHEMA_FIELD_EXISTS!=PUBLIC_FIELD_POPULATED
CURRENT_101_DETAIL=FIRST_ORDER_AGGREGATED_BALANCES
CURRENT_102_DETAIL=SECTION_TOTALS_AND_SELECTED_RESULTS
```

## 16. Reporting Subject Identity

`REGN` is the primary raw reporting-subject identity. Current 101 and 102 also
contain subject reference tables with REGN and bank title, but title-only or
fuzzy matching is prohibited.

The official CBR credit-organization register provides REGN, OGRN and title.
The current BondRadar `LegalIssuer` contract has no CBR REGN/OGRN identity, so:

```text
IDENTITY_STATE=IDENTITY_BRIDGE_REQUIRED
TITLE_ONLY_MAPPING_ALLOWED=false
LEGAL_ISSUER_JOIN_EXECUTED=false
```

## 17. Raw Data Artifact Identity

A future artifact identity must retain source family, exact form, official
URL, source reporting-date label, filename, exact byte length, SHA-256,
retrieval time, container type and format/schema version. Filename or HTTP
timestamp alone is not canonical report identity.

## 18. Archive / Container Contract

All four inspected artifacts are `RAR` containers. No Python RAR package or
dedicated binary exists in current application dependencies. Local Windows
`tar`/libarchive inspection succeeded, but a future production contract must
separately establish licensed, cross-platform and Docker-compatible bounded
RAR handling.

```text
CONTAINER_FORMAT=RAR
RAR_PRODUCTION_SUPPORT=REQUIRES_SEPARATE_CONTRACT
RAR_DEPENDENCY_ADDED=false
```

## 19. DBF Contract

Official format documentation specifies DOS encoding for character fields.
The current and historical member inventories are:

```text
101_CURRENT_MEMBERS=072026B1.dbf,072026N1.dbf,NAMES.dbf
101_HISTORICAL_MEMBERS=122020B1.DBF,122020N1.DBF,NAMES.DBF
102_CURRENT_MEMBERS=072026_P1.dbf,072026NP1.dbf,072026SP1.dbf,SPRAV1.dbf,SPRAV11.dbf
102_HISTORICAL_MEMBERS=42020_P1.DBF,42020NP1.DBF,42020SP1.DBF,SPRAV1.DBF
```

101 data fields are `REGN,PLAN,NUM_SC,A_P` plus version-specific balance and
turnover columns, `DT,PRIZ`. Current 102 data fields are
`REGN,CODE,SIM_R,SIM_V,SIM_ITOGO,DT`; subject, checksum and nomenclature files
remain separate. The added current `SPRAV11` projection proves schema drift and
contains `CODE_PUBL`/`VALUE_PUBL` publication metadata.

## 20. Units and Currency

The [101 format](https://www.cbr.ru/Content/Document/File/159279/101-20240201.pdf)
defines published numeric balance values in thousands of rubles. The
[102 format](https://www.cbr.ru/vfs/credit/formats/102-20171001.pdf) distinguishes
ruble amounts, foreign-currency/precious-metal amounts expressed in ruble
equivalent and total amounts. Current public 102 exposes only the total column.

```text
missing currency != RUB
missing unit != assumed unit
missing multiplier != 1
missing numeric != zero
```

Task249 performs no conversion and never reconstructs native-currency amounts.

## 21. Period Semantics

101 opening and closing balances are tied to the per-row `DT`; turnover fields
describe the source reporting interval only when present. Current public
turnovers are absent.

102 is a regulatory flow report. Task249 retains the per-row `DT` and source
form/version period semantics but performs no subtraction, discrete-quarter or
TTM derivation. The current archives contain both `20260701` and `20260801`
row dates, proving that archive date cannot replace row-level period identity.

## 22. PIT / Historical Availability

```text
PIT_CLASS=PIT_PARTIAL
REPORT_DATE!=PUBLICATION_DATE
HTTP_LAST_MODIFIED!=SOURCE_PUBLICATION_TIME
```

The reporting page proves dated archive availability, not exact first-public
timestamps. `published_at` therefore remains unknown unless a future source
contract proves it independently.

## 23. Historical Disclosure Gaps

Official CBR decisions establish non-disclosure for 2022 and reporting dates
through 1 May 2023. From June 2023, only reduced/aggregated public projections
return; annual decisions continue aggregation through 2026.

```text
PRE_2022_HISTORY=PAGE_LISTED_WITH_2021_FULL_DETAIL_SAMPLE
2022_THROUGH_2023_05=REGULATORY_NON_DISCLOSURE
2023_06_THROUGH_2026=AGGREGATED_DISCLOSURE
REGULATORY_NON_DISCLOSURE!=BANK_DATA_MISSING
```

Uninspected historical layouts remain unproven rather than silently assigned
the 2021 schema.

## 24. Raw Financial Usefulness Matrix

| Credit domain | Current source | Status | Limitation |
|---|---|---|---|
| asset composition | 101 first-order balances | `PARTIAL_RAW_SUPPORT` | combined and first-order aggregates |
| cash/liquidity proxy | 101 balances | `AGGREGATED_ONLY` | no currency detail or contractual liquidity |
| loan book | 101 account groups | `AGGREGATED_ONLY` | borrower/risk segmentation absent |
| securities portfolio | 101 account groups | `AGGREGATED_ONLY` | detailed instruments absent |
| interbank assets | 101 account groups | `AGGREGATED_ONLY` | resident/non-resident groups combined |
| customer funding | 101 account groups | `AGGREGATED_ONLY` | material account combinations |
| bank funding | 101 account groups | `AGGREGATED_ONLY` | material account combinations |
| debt-like liabilities | 101 balances | `PARTIAL_RAW_SUPPORT` | not a canonical debt measure |
| equity/accounting capital | 101 balances | `PARTIAL_RAW_SUPPORT` | regulatory capital requires 123 |
| interest income/expense | 102 section totals | `AGGREGATED_ONLY` | symbol detail suppressed |
| fee income | 102 section totals | `AGGREGATED_ONLY` | detailed symbols suppressed |
| operating expenses | 102 section totals | `AGGREGATED_ONLY` | detailed symbols suppressed |
| impairment/provisions | 102 section totals | `AGGREGATED_ONLY` | detailed symbols suppressed |
| net income | 102 retained result symbols | `STRONG_RAW_SUPPORT` | normalization still requires review |
| FX effects | 102 combined sections | `AGGREGATED_ONLY` | ruble/FX columns suppressed |
| capital adequacy | 123/135 | `NOT_SUPPORTED` by 101/102 | bundle complement required |

## 25. Credit-Analysis Suitability

101/102 can support later reviewed balance composition, funding, profitability
and trend evidence. They cannot independently establish capital adequacy,
mandatory liquidity ratios, asset-quality detail, concentration, maturity
structure, standalone-versus-group comparability or IFRS credit metrics.

No metric or score is computed in Task249.

## 26. 123 / 135 Complement Assessment

The official reporting page publishes both forms. The 2026 disclosure decision
retains selected 123 total/basic/additional capital lines and 135 mandatory
ratios including capital adequacy and liquidity indicators.

```text
FORM_123_PUBLIC=true
FORM_135_PUBLIC=true
FORM_123_135_PARSED=false
FORM_123_135_COMPLEMENT=MATERIALLY_REQUIRED
```

They are assessed only at capability level and are not downloaded or parsed.

## 27. LegalIssuer Mapping Boundary

The future preferred chain is CBR REGN → official CBR organization identity →
OGRN/INN → LegalIssuer. Task249 proves only that REGN is present in report
artifacts and that an official register exposes REGN/OGRN/title.

```text
CURRENT_MAPPING_STATE=IDENTITY_BRIDGE_REQUIRED
FUZZY_NAME_MATCHING=false
COMPANY_MUTATION_EXECUTED=false
LEGAL_ISSUER_MUTATION_EXECUTED=false
```

## 28. Source Failure Semantics

Keep these distinct: `ARTIFACT_FOUND`, `ARTIFACT_NOT_FOUND`,
`FORM_NOT_AVAILABLE_FOR_PERIOD`, `REGULATORY_DISCLOSURE_RESTRICTED`,
`SUBJECT_NOT_DISCLOSED`, `SOURCE_ERROR`, `RATE_LIMITED`, `TIMEOUT`,
`INVALID_ARCHIVE`, `INVALID_DBF`, and `UNSUPPORTED_SCHEMA_VERSION`.

```text
SOURCE_ERROR!=FORM_NOT_AVAILABLE_FOR_PERIOD
REGULATORY_DISCLOSURE_RESTRICTED!=BANK_DATA_MISSING
blank numeric!=zero
```

## 29. Coverage Contract

Measured current coverage is reported by form and artifact rather than one
vague percentage:

```text
FORM_101_CURRENT_DATA_ROWS=25654
FORM_101_CURRENT_SUBJECTS=353
FORM_101_CURRENT_ACCOUNT_CODES=178
FORM_102_CURRENT_DATA_ROWS=10079
FORM_102_CURRENT_SUBJECTS=212
FORM_102_CURRENT_PUBLISHED_CODES=49
FORM_102_CURRENT_BLANK_TOTAL_ROWS=6
```

The historical comparison is 82,698/406/1,224 for 101 and
788,327/406/2,139 for 102. It demonstrates disclosure drift, not full-universe
or PIT coverage.

## 30. Bounded Live Investigation

```text
MAX_ARTIFACTS=4
DATA_ARTIFACTS_DOWNLOADED=4
FORM_101_ARTIFACTS=2
FORM_102_ARTIFACTS=2
TOTAL_BYTES=5066322
MASS_ARCHIVE_CRAWL=false
LIVE_PROBE_IMPLEMENTED=false
```

No reusable probe, production DB, VDS or additional period crawl exists.

## 31. Economic / Engineering Gate

101/102 are small, official and structurally useful, but current aggregation
and missing capital/normative dimensions make a two-form implementation an
incomplete foundation. RAR runtime support and schema versioning add bounded
engineering work; the greater risk is semantic incompleteness.

```text
ECONOMIC_GATE=BUILD_BANK_REGULATORY_BUNDLE_NOW
RATIONALE=101_102_USEFUL_BUT_123_135_REQUIRED_FOR_CREDIT_FOUNDATION
```

The build decision points to a separate source contract, not immediate data
ingestion.

## 32. Persistence Boundary

```text
MIGRATION=NONE
DATABASE_PERSISTENCE=false
RAW_SOURCE_TABLE_CREATED=false
BANK_FINANCIAL_TABLE_CREATED=false
```

Persistence waits until the 101/102/123/135 bundle, identity, archive, schema
and temporal contracts are separately authorized and proven.

## 33. Allowed Scope

The committed scope is exactly this document and its deterministic contract
test. Official-source research and four bounded temporary artifacts are the
only external evidence. No additional source download is needed during
delivery.

## 34. Forbidden Scope

No client, parser, CLI, RAR/DBF dependency, migration, model, application
service, persistence, Company/LegalIssuer mutation, normalization, metric,
ratio, scoring, 123/135 implementation, production/VDS access, broker call or
trading action is allowed. Task250 is not started.

## 35. Required Tests

The focused test verifies all 40 sections, baseline/head, exact four artifact
URLs/sizes/hashes, member inventories and schemas, measured counts, form
identity, current suppression versus the 2021 samples, REGN boundary, units,
period/PIT and disclosure distinctions, usefulness matrix, 123/135 complement,
the single source/economic decisions, Task250 handoff and safety invariants.

Tests read local text only and perform no network or database operation.

## 36. Acceptance Criteria

Delivery passes only when the source/economic decisions are unique, 101/102
remain distinct from 0409806/0409807, aggregation and missingness are explicit,
PIT is partial, REGN mapping remains unresolved, no normalization or persistence
appears, exactly two files change, focused tests pass and the complete diff is
clean.

## 37. Local Validation

Run only focused compilation, Task249 pytest, Task247/Task248 documentation
regressions and diff/scope inspection. The full backend suite is intentionally
not run because application/shared code is unchanged.

## 38. Git / Diff / Commit / Push

```text
EXPECTED_CHANGED_FILES=docs/audits/TASK249_CBR_101_102_SOURCE_SUITABILITY_CONTRACT.md,backend/tests/test_task249_cbr_101_102_source_suitability_contract.py
COMMIT_MESSAGE=Assess CBR 101 102 Financial Source
FORCE_PUSH=false
REBASE=false
CI=NOT_WAITED_BY_DESIGN
```

Origin advancement before commit blocks delivery.

## 39. Final Report

The final report distinguishes documentation PASS from unimplemented source
ingestion. It includes starting/ending SHAs, exact files and artifacts,
disclosure content, identity/unit/period/PIT findings, usefulness, decisions,
tests, scope, commit/push and safety.

```text
DATABASE_ACCESSED=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

## 40. Hard Stop

Task249 stops without client/parser/persistence work. The sole next contract is:

```text
RECOMMENDED_TASK250=Task250 — CBR Bank Regulatory Raw Bundle 101/102/123/135 Source Contract
TASK250_AUTOMATICALLY_STARTED=false
TASK250_IMPLEMENTATION_AUTHORIZED=false
```

Task250 must preserve the exact forms, current aggregation, missing values,
REGN identity, schema versions and PIT limitations established here.
