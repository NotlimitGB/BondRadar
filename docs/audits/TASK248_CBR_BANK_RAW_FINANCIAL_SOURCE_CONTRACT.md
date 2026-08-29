# Task248 — CBR 0409806/0409807 Public-Value Source Blocker

## 1. Execution Profile

Task248 ends as a documentation-only, fail-closed source investigation.

```text
TASK_ID=Task248
STATUS=BLOCKED
REASON=PUBLIC_SCHEMA_EXISTS_BUT_ACTUAL_REPORT_VALUES_ARTIFACT_NOT_PROVEN
IMPLEMENTATION=DOCUMENTATION_ONLY
TARGET_CLIENT_IMPLEMENTED=false
TARGET_PARSER_IMPLEMENTED=false
LIVE_PROBE_IMPLEMENTED=false
MIGRATION=NONE
DATABASE_PERSISTENCE=false
```

The blocked status applies to implementation of a 0409806/0409807 adapter. It
does not mean that the documentation delivery failed.

## 2. Context

Task247 selected official bank reporting as the first source family to
investigate for the M2 credit-data layer. Task248 tested the narrower premise
that public Bank of Russia artifacts expose actual values for published forms
0409806 and 0409807. The premise was not proven.

The required architecture remains:

```text
Source Raw Layer
    -> Report Identity / Provenance
    -> Raw Financial Items
    -> Reviewed Normalization Layer
    -> Credit Metrics
    -> Credit Risk Engine
```

Task248 does not advance beyond source-contract investigation.

## 3. Starting State

```text
BRANCH=main
STARTING_SHA=6eebd5be76cbcf70666b33ce5bd2500fb22939b1
TRACKED_WORKTREE_AT_START=clean
ALEMBIC_HEAD=202608280002
CHANGED_FILE_COUNT=2
APPLICATION_CODE_CHANGED=false
DEPENDENCY_ADDED=false
```

The canonical issuer layer, legacy financial rows and controlled financial
values were neither read from a database nor modified.

## 4. Critical Source Correction

The Bank of Russia XML page documents `800P` as a family of formats used by
credit organizations to submit published reporting to the regulator and lists
0409806 and 0409807. It does not by itself provide actual bank submissions.

The public reporting page provides actual downloadable data, including 101,
102, 0409802, 0409803 and legacy `nfo` archives. The two bounded `nfo` samples
contain forms 0409815–0409818, not 0409806 or 0409807. Schema/template evidence
and actual reported values are therefore kept distinct.

## 5. Goal

The intended goal was a deterministic, read-only chain from official exact
bytes to raw 0409806/0409807 observations. The minimum target artifact was not
proven, so no client, parser or operational probe was created. The safe result
is a reusable source contract and an explicit blocker.

## 6. Non-Goals

Task248 does not persist reports, introduce an M2 schema, normalize financial
concepts, calculate ratios or scores, join reporting subjects to LegalIssuer,
process non-bank issuers, implement GIRS/IFRS pipelines, or alter strategy,
risk, backtests, paper trading or broker integrations.

## 7. Source-of-Truth Hierarchy

Evidence authority is ordered as follows:

1. actual public CBR reporting data artifact;
2. official CBR source-format description;
3. official CBR metadata/classifiers;
4. official regulatory form documentation.

Search results and third-party mirrors are not source data. A schema, template
or regulation cannot be promoted to an actual-values artifact.

## 8. Starting Commit and Preflight

Preflight confirmed `main`, the exact starting SHA, a clean tracked worktree,
and Alembic head `202608280002`. Any different baseline, required application
change, migration, persistence, normalization or unrelated edit is a hard stop.

## 9. Required Repository Investigation

The frozen repository capability projection is:

```text
EXISTING_CBR_REPORTING_CLIENT=NONE
EXISTING_ARCHIVE_PARSER=NONE
EXISTING_DBF_SUPPORT=NONE
EXISTING_XML_SUPPORT=NONE
EXISTING_HASHING_SUPPORT=hashlib.sha256
EXISTING_BANK_IDENTITY_SUPPORT=PARTIAL_LEGALISSUER_INN_ONLY_NO_CBR_REGN_CONTRACT
```

Legacy `FinancialReport` ingestion is unsafe for reuse here: it binds through
Company, has an unsafe RUB fallback, and may overwrite/collide at the same
Company/period identity. None of those semantics may enter a future raw layer.

## 10. Required Live Source Investigation

The bounded official-source investigation used:

- [CBR reporting data](https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/);
- [CBR XML formats](https://www.cbr.ru/development/kliko/xml_f/);
- [CBR legacy nfo format](https://www.cbr.ru/vfs/credit/formats/nfo-20180101.PDF).

```text
LIVE_SOURCE_INVESTIGATION=true
LIVE_NETWORK_USED=true
LIVE_ARTIFACT_COUNT=2
TOTAL_COMPRESSED_BYTES=668005
DATABASE_ACCESSED=false
```

This historical source investigation is not an implemented operational probe.

## 11. Source Artifact Contract

A future proven artifact must retain `source_family`, canonical source URL,
artifact name, report family, optional source as-of/publication dates,
retrieval time, media type, exact compressed size, exact-byte SHA-256, optional
schema version, transport format and ordered blockers. Unknown metadata remains
unknown; it is never synthesized from file names or HTTP headers.

## 12. 800P / 0409806 / 0409807 Contract

```text
SCHEMA_ARTIFACT_PROVEN=true
800P_ROLE=SUBMISSION_SCHEMA_FAMILY
800P_ACTUAL_VALUES_ARTIFACT_PROVEN=false
TARGET_0409806_VALUES_PROVEN=false
TARGET_0409807_VALUES_PROVEN=false
```

`800P` is not relabelled as either target form, and its presence does not prove
that an artifact contains either form. A future parser must identify 0409806
and 0409807 independently and distinguish `FORM_NOT_PRESENT`, `FORM_ID_UNKNOWN`,
`UNSUPPORTED_FORM` and `SCHEMA_ERROR`.

## 13. Reporting Subject Identity

A future raw report requires a stable CBR reporting-subject identifier. The
preferred fields are CBR registration number (`REGN`), an official source
organization ID, and INN/OGRN only when explicitly present. Bank title alone
is insufficient and fuzzy LegalIssuer matching is forbidden.

The repository currently has only partial LegalIssuer/INN support and no CBR
REGN identity contract. Missing stable identity is
`REPORTING_SUBJECT_UNRESOLVED`.

## 14. Form 0409806 Contract

0409806 is retained as the regulatory form identity for a published balance
sheet, but actual target rows were not observed in the proven artifacts.
Future evidence must preserve exact form/version, source row/line code, label,
column code, raw value, date, unit and reporting subject. Task248 makes no
claims about subject, item or value coverage for 0409806.

## 15. Form 0409807 Contract

0409807 is retained as the regulatory form identity for a published financial
results statement, but actual target rows were not observed in the proven
artifacts. Future evidence must preserve flow-period metadata and exact raw
row/column meaning. Task248 does not infer a January 1 start date and makes no
subject, item or value coverage claim for 0409807.

## 16. Report Scope and Accounting Standard

Until an actual target artifact and its authoritative semantics are proven:

```text
report_scope=UNRESOLVED_FOR_TARGET_ADAPTER
accounting_standard=UNRESOLVED_FOR_TARGET_ADAPTER
```

The adapter must not guess `BANK_REGULATORY`, `BANK_RAS`, standalone,
consolidated or IFRS from a form number or file name.

## 17. Raw Item Contract

A future immutable raw item must retain source family/artifact hash/schema,
form and version, source subject identifiers, report/as-of and flow-period
fields, exact row code, column code, label, raw value, raw unit/currency/
multiplier, scope/standard, publication time, observation time and retrieval
time. It must not emit canonical EBITDA, net debt, revenue or other normalized
metrics.

```text
source_artifact_sha256
source_schema_version
cbr_registration_number
source_as_of_date
period_start
period_end
source_row_code
source_column_code
raw_value
raw_unit
raw_currency
raw_multiplier
source_published_at
observed_at
retrieved_at
```

## 18. Currency and Units Contract

```text
missing currency != RUB
missing multiplier != 1
missing value != zero
```

The official legacy `nfo` description states that its values are in thousands
of rubles. That fact applies to the proven legacy family only and cannot be
transferred to unseen 0409806/0409807 artifacts. A future adapter preserves
raw currency, unit and multiplier separately and performs no conversion.

## 19. Period Contract

Artifact date, report as-of date, flow start/end, publication time, observation
time and retrieval time are distinct. An archive label is not automatically a
publication date. Stock `as_of_date` and flow periods must remain separate and
must be sourced from the actual form contract.

## 20. Publication / PIT Contract

Historical archive availability does not prove the exact moment when every
report became public. If publication time is unavailable it remains `None`,
and the source is at most `PIT_PARTIAL`, not `PIT_READY`. Current-only facts
must never be introduced into an earlier backtest.

## 21. Artifact Integrity

The two actual downloaded artifacts were measured over exact compressed bytes:

```text
ARTIFACT_1_URL=https://www.cbr.ru/vfs/credit/forms/nfo-201901.zip
ARTIFACT_1_BYTES=648329
ARTIFACT_1_SHA256=d1834cee43ef0207463d318330242466827175a3bb2f48d106188b936710e073

ARTIFACT_2_URL=https://www.cbr.ru/vfs/credit/forms/nfo-201810.zip
ARTIFACT_2_BYTES=19676
ARTIFACT_2_SHA256=a69be11535a068d43ae7df750b115169c6cf58784875b6021d65226e8771a9e5
```

Future parsing must also hash every extracted member. Hashes do not establish
that an artifact contains the target forms.

## 22. Archive Security

A future archive parser must reject path traversal, absolute paths, `..`,
archive bombs, excessive members, nested archives, executable members,
malformed containers and corrupt DBF/XML. Extraction must remain within a
controlled temporary directory or memory, with size limits justified from
observed artifacts rather than an arbitrarily large ceiling.

## 23. Schema Versioning

Historical layouts may differ. Future observations must retain source schema
version and effective date when proven. An unknown historical layout is
`UNSUPPORTED_SCHEMA_VERSION`, never a best-effort parse. No parser version or
supported date range exists in Task248 because no target artifact was proven.

## 24. Parsing Failure Semantics

The future contract keeps these distinct: `ARTIFACT_RETRIEVED`,
`ARTIFACT_NOT_FOUND`, `FORM_FOUND`, `FORM_NOT_PRESENT`,
`REPORTING_SUBJECT_UNRESOLVED`, `UNSUPPORTED_SCHEMA_VERSION`,
`UNSUPPORTED_CONTAINER_FORMAT`, `INVALID_ARCHIVE`, `INVALID_DBF`,
`INVALID_XML`, `SCHEMA_ERROR`, `VALUE_PARSE_ERROR`, `SOURCE_ERROR`, `TIMEOUT`
and `RATE_LIMITED`.

```text
SOURCE_ERROR != FORM_NOT_PRESENT
VALUE_PARSE_ERROR != zero
REPORTING_SUBJECT_UNRESOLVED != fuzzy title match
```

## 25. Read-Only Source Client

```text
TARGET_CLIENT_IMPLEMENTED=false
```

No endpoint is invented. A future source-specific client may be authorized
only after an actual 0409806/0409807 public-values locator is proven. It would
be limited to bounded retrieval, transport metadata and exact-byte hashing,
with no database, normalization or scoring surface.

## 26. Parser Architecture

```text
TARGET_PARSER_IMPLEMENTED=false
```

No parser is built against `800P` schemas or unrelated legacy DBF layouts. A
future parser must be source/version specific, identify target forms and stable
subjects, and emit raw observations without metric mapping, aggregation,
scaling or LegalIssuer joins.

## 27. Bounded Live Probe

```text
LIVE_PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
LIVE_SOURCE_INVESTIGATION=true
LIVE_NETWORK_USED=true
ARTIFACTS_DOWNLOADED=2
TOTAL_COMPRESSED_BYTES=668005
```

The bounded investigation downloaded and inspected two official samples. It
did not create or run a reusable probe and performed no database operation.

## 28. Controlled Sample

The observed ZIP members prove only the legacy family:

```text
I815 -> 0409815
I816 -> 0409816
I817 -> 0409817
I818 -> 0409818
B818 -> 0409818
REGN=CBR_CREDIT_ORGANIZATION_REGISTRATION_NUMBER
LEGACY_NFO_VALUES_UNIT=THOUSANDS_OF_RUBLES
```

Both samples contained the corresponding `I815`, `I816`, `I817`, `I818` and
`B818` DBF members. No redemption, target-form or substitute semantics are
inferred from file names.

## 29. Coverage Reporting

```text
PUBLIC_DOWNLOAD_PROVEN=true
DATA_ARTIFACT_PROVEN=true
ACTUAL_CONTAINER_FORMAT=ZIP_WITH_DBF_MEMBERS
ACTUAL_REPORT_FAMILY=LEGACY_NONCONSOLIDATED_0409815_0409816_0409817_0409818
CONTAINS_0409806=false
CONTAINS_0409807=false
TARGET_0409806_VALUES_PROVEN=false
TARGET_0409807_VALUES_PROVEN=false
TARGET_SUBJECT_COVERAGE=NOT_MEASURABLE
TARGET_ITEM_COVERAGE=NOT_MEASURABLE
```

Actual 101/102, 0409802/0409803 and 0409815–0409818 data are not substitutes
for 0409806/0409807 and are never reported as target coverage.

## 30. Persistence Boundary

```text
READ_ONLY_SOURCE_CONTRACT=true
MIGRATION=NONE
DATABASE_PERSISTENCE=false
DATABASE_ACCESSED=false
DATABASE_MUTATION_EXECUTED=false
```

No generic raw-financial schema is frozen from an unproven target. Legacy
FinancialReport and ControlledFinancialStatementValue rows are untouched.

## 31. Allowed Scope

The completed scope is limited to one audit document and one deterministic
documentation-contract test. It records bounded official-source evidence,
hashes, fail-closed source semantics, future raw-item requirements and the next
safe investigation. No other output or side effect is authorized.

## 32. Forbidden Scope

No client, parser, CLI, dependency, migration, model, service, persistence,
normalization, scaling, metric calculation, scoring, issuer mapping, OCR, LLM
parsing, browser automation, production/VDS access, broker call or trading
action is implemented. No 101/102, 0409802/0409803 or legacy `nfo` form is
renamed as 0409806/0409807.

## 33. Required Tests

The focused test validates all 39 sections, the exact baseline and capability
projection, official artifact URLs/sizes/hashes, legacy DBF member mapping,
the `800P` schema-only boundary, target absence, raw/identity/unit/period/PIT/
archive contracts, failure distinctions, implementation exclusions, safety
flags and the exact Task249 handoff. It performs no network or database work.

## 34. Acceptance Criteria

Documentation delivery passes only if the exact blocked reason is retained,
both official samples are reproducibly identified, the target forms remain
unproven, schema is not confused with values, no substitute form is relabelled,
all future contracts fail closed, exactly two files change, focused tests pass
and no operational implementation or production action occurs.

## 35. Local Validation

Required local validation is limited to Python compilation of the focused
test, focused Task248 pytest, Task247 documentation regression and diff/scope
inspection. The full backend suite is intentionally not run because no shared
or application code changes.

## 36. Git / Diff / Scope Validation

```text
EXPECTED_CHANGED_FILES=docs/audits/TASK248_CBR_BANK_RAW_FINANCIAL_SOURCE_CONTRACT.md,backend/tests/test_task248_cbr_bank_raw_financial_source_contract.py
MIGRATION=NONE
DEPENDENCY_ADDED=false
APPLICATION_CODE_CHANGED=false
FINANCIAL_REPORT_MUTATION_EXECUTED=false
LEGAL_ISSUER_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

The complete diff must contain no unrelated file and pass whitespace checks.

## 37. Commit / Push Rules

After green mandatory documentation checks, create exactly one commit named
`Document CBR Bank Financial Source Blocker` and push it normally to
`origin/main`. Force, rebase and CI polling are forbidden. An advanced origin
blocks delivery.

## 38. Final Report

The final report must distinguish successful documentation delivery from the
blocked target adapter. It reports exact files and verification, no migration,
no DB or production action, no operational probe, commit/push state and
`CI=NOT_WAITED_BY_DESIGN`.

```text
LIVE_NETWORK_USED=true
LIVE_PROBE_RUN=false
DATABASE_ACCESSED=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

## 39. Hard Stop

The implementation hard-stops because the public schema exists but an actual
0409806/0409807 report-values artifact was not proven. The next work is a
separate suitability contract for proven public forms, not ingestion:

```text
RECOMMENDED_TASK249=Task249 — CBR Forms 101/102 Raw Regulatory Source Suitability Contract
TASK249_AUTOMATICALLY_STARTED=false
TASK249_INGESTION_AUTHORIZED=false
```

Task249 must not rename 101/102 as 0409806/0409807 and cannot automatically
start ingestion or persistence.
