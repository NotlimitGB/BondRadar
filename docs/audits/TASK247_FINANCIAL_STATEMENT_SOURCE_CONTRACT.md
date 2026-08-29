# Task247 — Financial Statement Source Contract & Coverage Audit

## 1. Execution Profile

Task247 is a documentation-only, fail-closed financial-source audit.

```text
TASK_ID=Task247
MODE=DOCUMENTATION_ONLY_SOURCE_AUDIT
MIGRATION=NONE
APPLICATION_CODE_CHANGED=false
PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
NETWORK_USED_FOR_PROBE=false
DB_COVERAGE_RUN=false
DATABASE_MUTATION_EXECUTED=false
FINANCIAL_REPORT_MUTATION_EXECUTED=false
LEGAL_ISSUER_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

Official documentation was researched, but no source coverage probe, protected
endpoint, document download, production database access or financial ingestion
was performed.

## 2. Context

Task241–Task244 established security and legal-issuer identity. Task245 and
Task246A/B established that issuer relationships and disclosure-document
location require separate evidence contracts. Task247 begins after issuer
identity, not after reliable financial coverage.

Prior production figures are context only:

```text
PRIOR_LEGAL_ISSUERS=498
PRIOR_FINANCIAL_REPORTS=1
PRIOR_CONTROLLED_FINANCIAL_ENTITIES=3
PRIOR_CONTROLLED_FINANCIAL_ROWS=30
PRODUCTION_COUNTS_REMEASURED=false
```

## 3. Current Baseline

```text
BRANCH=main
STARTING_COMMIT=70ae11270d3f720f09cfc2677ce0d28163ba091a
TRACKED_WORKTREE_AT_START=clean
ALEMBIC_HEAD=202608280002
```

No pull, merge, rebase, migration or production action is part of Task247.

## 4. Why This Task Exists

A financial number is unsafe for credit analysis unless its reporting subject,
standard, scope, period, revision, currency, units, publication time and source
provenance are known. Maximizing row count without those dimensions can attach
parent financials to an SPV, mix standalone and consolidated reports, or leak a
later restatement into an earlier backtest.

## 5. Goal

Task247 freezes a source-backed decision for a future M2 raw financial-data
layer. It classifies current repository components, evaluates bounded official
source families, defines report and temporal identity, and selects one concrete
source-specific next task. It does not ingest, normalize or score financial
data.

## 6. Core Credit-Data Principle

```text
LegalIssuer != ReportingEntity
parent financials != issuer financials
standalone != consolidated
unknown != zero
missing report != bad credit
source failure != no report
period end != publication date
```

No report from another legal entity or group is automatically attached to a
LegalIssuer.

## 7. Report Scope Taxonomy

The source raw layer must retain `LEGAL_ENTITY_STANDALONE`,
`CONSOLIDATED_GROUP`, `BANK_REGULATORY`, `INSURANCE_REGULATORY`,
`SPV_SPECIAL_PURPOSE`, `SOVEREIGN_PUBLIC_FINANCE`, `OTHER`, and `UNKNOWN`.

The normalized report-level projection must separately retain `STANDALONE`,
`CONSOLIDATED`, `REGULATORY`, or `UNKNOWN`. Task247 defines no preference among
these scopes.

## 8. Accounting Standard Taxonomy

Supported evidence labels are `RAS`, `IFRS`, `BANK_RAS`, `BANK_IFRS`,
`OTHER_REGULATORY`, and `UNKNOWN`. The exact source label is retained alongside
the canonical classification. Language, issuer domicile and file name are not
sufficient to infer a standard.

## 9. Starting Commit and Preflight

Preflight confirmed the exact starting commit, clean tracked tree and current
Alembic head before edits. A different HEAD, required migration, unrelated
change or need to mutate current financial rows is a hard stop.

## 10. Required Repository Investigation

Repository inspection produced this frozen projection:

```text
CURRENT_FINANCIAL_OWNER_MODEL=Company
CURRENT_REPORT_IDENTITY=Company+period_year+period_quarter
CURRENT_PERIOD_MODEL=year+quarter_with_optional_start_end
CURRENT_ACCOUNTING_STANDARD_MODEL=NONE
CURRENT_CONSOLIDATION_MODEL=NONE
CURRENT_RESTATEMENT_MODEL=OVERWRITE_OR_COLLISION
CURRENT_SOURCE_DOCUMENT_MODEL=Company_scoped_without_content_hash_or_stable_source_report_id
CURRENT_PIT_CAPABILITY=PARTIAL_BUT_UNSAFE_LEGACY_FALLBACK
CURRENT_CURRENCY_UNIT_MODEL=currency_with_unsafe_RUB_default_and_no_report_unit_multiplier
```

The inspected boundary includes `FinancialReport`,
`FinancialReportSourceDocument`, `FinancialReportIngestionService`,
`ControlledFinancialStatementValue`, LegalIssuer identity models and current
Company scoring/credit consumers.

## 11. Existing Financial Data Audit

| Component | Classification | Reason |
|---|---|---|
| `LegalIssuer` | `KEEP` | Canonical legal-issuer identity foundation; it is not itself a reporting subject. |
| `FinancialReport` | `LEGACY_ONLY`, `UNSAFE_FOR_M2` | Company-owned normalized values, period collision key, no standard/scope/revision identity. |
| `FinancialReportSourceDocument` | provenance pattern `ADAPT`, current contract `UNSAFE_FOR_M2` | Company-scoped mutable metadata without exact-byte checksum, stable source report ID, standard, scope or revision chain. |
| `FinancialReportIngestionService` | `LEGACY_ONLY`, `UNSAFE_FOR_M2` | Resolves legacy Company, can overwrite a company-period row and applies an unsafe RUB fallback. |
| Company financial CRUD | `LEGACY_ONLY` | Compatibility interface over the legacy model, not a LegalIssuer source boundary. |
| Company credit/scoring consumers | `LEGACY_ONLY`, `UNSAFE_FOR_M2` | Consume legacy Company reports and may use incomplete publication chronology. |
| `ControlledFinancialStatementValue` evidence/checksum mechanics | `KEEP` | Preserves reviewed values, line/page evidence and checksums. |
| `ControlledFinancialStatementValue` identity/period model | `ADAPT` | String company identity, paired years and no canonical ReportingEntity/report/PIT linkage. |

Current Company credit and scoring paths use legacy `FinancialReport`; they are
not a canonical LegalIssuer credit-data pipeline. Existing controlled rows are
not the source raw layer and are not joined to the credit decision timeline.

## 12. Source Families to Investigate

The bounded investigation covers Bank of Russia bank reporting, Bank of Russia
NFO XBRL, FNS/GIRS BO, accredited and issuer disclosure, MOEX reference data,
issuer websites, and future jurisdiction-specific foreign sources. Third-party
finance portals remain `DISCOVERY_ONLY`.

## 13. Source Authority Model

Each source records:

- authority: `OFFICIAL_REGULATOR`, `OFFICIAL_REGISTRY`, `ISSUER_PRIMARY`,
  `ACCREDITED_DISCLOSURE`, or `DISCOVERY_ONLY`;
- data form: `STRUCTURED_API`, `STRUCTURED_FILE`, `XBRL`, `HTML_TABLE`,
  `SEARCHABLE_DOCUMENT`, or `DOCUMENT_ONLY`;
- access: `PUBLIC`, `AUTHORIZED`, `SUBSCRIPTION`, `RESTRICTED`, or `UNKNOWN`;
- automation: `READY`, `REQUIRES_REVIEW`, `RESTRICTED`, or `NO_GO`;
- PIT: `PIT_READY`, `PIT_PARTIAL`, `CURRENT_ONLY`, or `PIT_UNKNOWN`;
- licensing: `CLEAR_FOR_CURRENT_RESEARCH_USE`, `REQUIRES_REVIEW`,
  `RESTRICTED`, or `UNKNOWN`.

Unclear permission is not upgraded to current automation approval.

## 14. Issuer to Reporting Subject Identity

Preferred subject keys are INN, OGRN, LEI, an official registry identifier or a
CBR registration/license identifier. Title-only and fuzzy matching are
forbidden. Every source observation states whether it belongs to the canonical
LegalIssuer, a different entity, a consolidated group or an unresolved subject.

An unresolved join produces `REPORTING_SUBJECT_UNRESOLVED`; it does not create
a financial attachment.

## 15. Legal Issuer vs Reporting Entity Boundary

If issuer A lacks statements while parent or group B has them, BondRadar records:

```text
LEGAL_ISSUER_REPORT_MISSING
POTENTIAL_GROUP_REPORT_DISCOVERED
REPORTING_ENTITY_MAPPING_REQUIRED
```

Task247 performs no parent/group inference. SPVs, SFOs, mortgage agents and
finance subsidiaries remain visible as separate legal issuers even when a
different group report is economically relevant.

## 16. Report Identity Contract

A future `FinancialReportIdentity` requires source family, stable reporting
subject ID, standard, scope, period start/end/kind, source report or document
ID, publication/version identity and source schema version. `issuer + year` is
not a valid identity. Original and revised reports for one period remain
distinct.

## 17. Period Contract

Stock items use `as_of_date`. Flow items use `period_start` and `period_end`.
Period kinds include `FY`, `H1`, `Q1`, `9M`, `Q3`, `OTHER_INTERIM`, and
`UNKNOWN`. No TTM value is inferred, and flows with different lengths are not
summed or compared without a later explicit normalization contract.

## 18. Consolidated vs Standalone Contract

`report_scope` is mandatory and never inferred from issuer size or source file
language. `STANDALONE`, `CONSOLIDATED`, `REGULATORY`, and `UNKNOWN` remain
separate time series. Future credit policy may select a scope by issuer type,
but Task247 does not.

## 19. Restatement Contract

Original, corrected and restated source observations are append-only. Preserve
source revision ID when supplied, publication and observation timestamps, and
snapshot/document integrity. A restatement published at T2 is unavailable at
T1 and never overwrites the evidence that was known at T1.

Where a source exposes only current corrected data and no historical
publication chain, historical depth does not establish point-in-time readiness.

## 20. Currency and Units Contract

Every numeric observation carries explicit currency, source unit, multiplier
and sign convention, or a fail-closed unknown state. Missing currency never
defaults to RUB. Missing multiplier never defaults to `1`. Missing values never
become zero. Task247 performs no conversion, scaling, rounding or normalization.

## 21. Temporal and PIT Contract

Required independent timestamps are `period_start`, `period_end`, `as_of_date`,
`published_at`, `source_updated_at`, `observed_at`, and `retrieved_at`.

```text
financial_information_available_at_T only if published_at <= T
```

If publication time is absent or a source only exposes its current state, the
observation is not backtest-safe. Archive date, fiscal end and retrieval time
are not silently substituted for publication time.

## 22. Source Document Provenance

A document-backed report retains source family, canonical locator, stable
document/report ID, exact-byte SHA-256 when retrievable, media type, content
length, publication/retrieval times, reporting subject identity, period,
standard, scope and version. An authoritative structured file may be primary
evidence without document bytes, but its immutable snapshot/file identity and
checksum remain mandatory.

## 23. Structured Data Contract

Every future `RawFinancialItem` preserves source form and schema version,
source line/code, source label, raw value, source unit, source currency and full
report identity. It is not labelled EBITDA, NetDebt or InterestCoverage unless
a later reviewed normalization contract establishes that mapping.

## 24. Coverage Dimensions

Coverage must be reported separately for resolvable LegalIssuers, any report,
latest annual, latest interim, standalone, consolidated, IFRS, RAS, structured,
document-only, publication timestamp availability and historical depth.

`SOURCE_NOT_APPLICABLE`, `NO_REPORT_FOUND`, `SOURCE_ERROR`, identity unresolved
and authorization blocked are separate counts. Task247 records:

```text
COVERAGE=NOT_MEASURED_DURING_IMPLEMENTATION
```

## 25. Controlled Read-Only Coverage Probe

No probe is implemented. GIRS BO bulk REST access requires a subscription and
configured authorization; the public per-organization website is not treated
as a documented mass API. CBR sources require class-specific adapters and
report-form semantics rather than a generic probe.

```text
PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
NETWORK_USED_FOR_PROBE=false
DB_COVERAGE_RUN=false
```

## 26. Sample Design

A future separately authorized source probe should use at most 20 canonical
LegalIssuers spanning an operating corporate, bank, other financial entity,
SPV/SFO, mortgage agent, foreign issuer, issuer missing INN, and a large issuer
with many bonds. A bulk official file is counted as one bounded source request,
not as per-issuer crawling.

## 27. Source Failure Semantics

Required statuses are `REPORT_FOUND`, `NO_REPORT_FOUND`, `SUBJECT_NOT_FOUND`,
`SUBJECT_IDENTITY_INCOMPLETE`, `REPORTING_SUBJECT_UNRESOLVED`,
`REPORT_SCOPE_UNKNOWN`, `STANDARD_UNKNOWN`, `AUTH_REQUIRED`,
`SUBSCRIPTION_REQUIRED`, `RATE_LIMITED`, `SOURCE_ERROR`, `SCHEMA_ERROR`, and
`SOURCE_NOT_APPLICABLE`.

```text
SOURCE_ERROR != NO_REPORT_FOUND
SOURCE_NOT_APPLICABLE != NO_REPORT_FOUND
AUTH_REQUIRED != NO_REPORT_FOUND
SUBSCRIPTION_REQUIRED != NO_REPORT_FOUND
```

## 28. Licensing and Automation Contract

| Source | Authority | Data/access | Automation | Licensing |
|---|---|---|---|---|
| CBR bank reporting | `OFFICIAL_REGULATOR` | `STRUCTURED_FILE`, `PUBLIC` | `READY` for a bounded source-specific adapter | `CLEAR_FOR_CURRENT_RESEARCH_USE` for documented public files; redistribution remains source-policy-bound |
| CBR NFO XBRL | `OFFICIAL_REGULATOR` | taxonomy `XBRL`, public instance contract unproven | `REQUIRES_REVIEW` | `REQUIRES_REVIEW` |
| GIRS BO subscription | `OFFICIAL_REGISTRY` | `STRUCTURED_API`, `AUTHORIZED`, `SUBSCRIPTION` | `REQUIRES_REVIEW` until access is approved/configured | `REQUIRES_REVIEW` |
| GIRS BO per organization | `OFFICIAL_REGISTRY` | public search/signed download | targeted only; mass automation `NO_GO` | `REQUIRES_REVIEW` |
| Interfax disclosure gateway | `ACCREDITED_DISCLOSURE` | `STRUCTURED_API` plus files, `AUTHORIZED`, `SUBSCRIPTION` | `RESTRICTED` until separate access approval | `REQUIRES_REVIEW` |
| Issuer website | `ISSUER_PRIMARY` | usually `DOCUMENT_ONLY`, access varies | targeted `REQUIRES_REVIEW` | `UNKNOWN` per issuer |
| MOEX | `DISCOVERY_ONLY` for statements | reference/link evidence only | financial-statement truth `NO_GO` | `UNKNOWN` for any downstream document |

No unsupported legal or redistribution conclusion is made.

## 29. Economic and Engineering Gate

```text
CBR_BANK_REPORTING=PRIMARY_BUILD_NOW
GIRS_BO_SUBSCRIPTION=SECONDARY_BUILD_LATER
GIRS_BO_PUBLIC_PER_ORGANIZATION=TARGETED_FALLBACK
CBR_NFO_XBRL=SECONDARY_BUILD_LATER
ACCREDITED_DISCLOSURE_IFRS=TARGETED_FALLBACK
ISSUER_WEBSITES=TARGETED_FALLBACK
MOEX_FINANCIAL_STATEMENT_TRUTH=NO_GO
FOREIGN_FINANCIAL_REGIMES=NO_GO_PENDING_SOURCE_SPECIFIC_CONTRACT
```

CBR bank reporting wins the first build because it provides public dated
structured archives with explicit bank form semantics. GIRS BO is broader for
ordinary Russian entities, but its documented bulk REST service costs RUB
200,000 per year and requires access approval. Public per-organization download
does not authorize a 498-issuer crawler.

## 30. Recommended M2 Architecture

```text
Source Raw Layer
    -> Report Identity / Provenance
    -> Raw Financial Items
    -> Reviewed Normalization Layer
    -> Credit Metrics
    -> Credit Risk Engine
```

Direct `source -> score` processing is forbidden. Different issuer classes use
different source adapters while sharing report identity and provenance rules.

## 31. Persistence Boundary

Task247 adds no schema. A future raw-source design must not force new evidence
into `FinancialReport` or `ControlledFinancialStatementValue`. Persistence must
first represent reporting subject, immutable source snapshot/document, report
identity/revision and raw items; any migration belongs to a separately approved
implementation task.

```text
MIGRATION=NONE
PERSISTENCE_IMPLEMENTED=false
```

## 32. Allowed Scope

Task247 permits official-documentation research, this contract document, and a
pure contract test. It does not create a generic adapter, probe, DTO framework
or financial parser.

## 33. Forbidden Scope

Forbidden actions include database or production mutation, migration, source
ingestion, mass population, Company or LegalIssuer changes, parent/group
mapping, metric normalization, ratio/score/risk calculation, strategy/backtest/
shadow changes, T-Invest work, arbitrary crawling, CAPTCHA/auth bypass, OCR,
LLM parsing, VDS deployment and Task248 execution.

## 34. Required Tests

The documentation contract test verifies all 40 sections, repository audit,
component classifications, source matrix, exact decisions, identity/scope/
standard/period/restatement/currency/PIT contracts, failure semantics,
multidimensional coverage, safety flags and the concrete Task248 handoff.

No live network, database or application fixture is part of the test.

## 35. Acceptance Criteria

Acceptance requires the exact starting commit, no migration or production
mutation, complete current-model audit, bounded CBR/FNS/disclosure/MOEX source
decisions, preservation of LegalIssuer/reporting-subject and scope/standard
boundaries, fail-closed currency/units/PIT semantics, one primary source and one
source-specific Task248 recommendation.

## 36. Local Validation

Only the focused contract compilation/test, Task245/246A/246B documentation
regressions, `git diff --check`, exact changed-file inventory and complete diff
inspection are required. The full backend suite is intentionally not run
because no application or shared source code changes.

## 37. Git, Diff and Scope Validation

The only permitted paths are:

```text
docs/audits/TASK247_FINANCIAL_STATEMENT_SOURCE_CONTRACT.md
backend/tests/test_task247_financial_statement_source_contract.py
```

Required delivery projection:

```text
CHANGED_FILE_COUNT=2
MIGRATION=NONE
APPLICATION_CODE_CHANGED=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

## 38. Commit and Push Rules

After all mandatory checks pass, create exactly one commit named
`Add Financial Statement Source Contract` and push it normally to `origin/main`.
No force, rebase or CI polling is allowed. An advanced origin blocks delivery.

```text
CI=NOT_WAITED_BY_DESIGN
```

## 39. Final Report and Recommended Task248

```text
RECOMMENDED_TASK248=Task248 — CBR Bank Published Financial Forms 0409806/0409807 Raw Source v1
```

Task248 is a read-only, source-specific adapter using exact CBR bank identity,
dated archive/file integrity, raw form/line preservation and explicit units and
periods. It performs no metric normalization, scoring or production population.
GIRS BO and IFRS disclosure remain separate later pipelines and are not silently
folded into the bank source.

Official source locators:

- CBR bank reporting: <https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/>
- CBR XBRL: <https://www.cbr.ru/projects_xbrl/>
- FNS GIRS BO: <https://www.nalog.gov.ru/rn77/bo/>
- GIRS BO subscription: <https://bo.nalog.gov.ru/subscriptions-service>
- Interfax disclosure gateway: <https://e-disclosure.ru/poluchenie-informacii/shlyuz-api>
- MOEX ISS manual: <https://www.moex.com/files/4be999zbzp80bx2bgmwayrtyx0>

## 40. Hard Stop

Stop without commit or push if the baseline differs, a migration or production
mutation is needed, credentials/CAPTCHA bypass or fuzzy subject matching is
required, LegalIssuer is silently equated to a parent/group, scope/period/
publication/currency/unit semantics require guessing, failures cannot be
distinguished from no-report, scoring enters scope, tests fail, or unrelated
changes cannot be isolated.
