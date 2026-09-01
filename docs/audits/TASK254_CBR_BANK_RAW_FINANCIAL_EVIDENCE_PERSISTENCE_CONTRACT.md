# Task254 — CBR Bank Raw Financial Evidence Persistence Contract v1

## 1. Execution Profile

Task254 is a high-risk financial-data architecture task delivered as a static
contract only. It defines a future persistence boundary but does not implement
or execute it.

```text
TASK_ID=Task254
IMPLEMENTATION=DOCUMENTATION_ONLY
CONTRACT_ONLY=true
CONTRACT_VERSION=cbr-bank-raw-financial-evidence-v1
MIGRATION_REQUIRED_IN_TASK254=false
MIGRATION=NONE
MODEL_CHANGES=false
DATABASE_ACCESS=false
DATABASE_PERSISTENCE=false
PRODUCTION_ACTIONS=NONE
```

## 2. Context

The completed source and identity chain is:

```text
Task247 source architecture
Task248 target-form blocker
Task249 101/102 suitability
Task250 101/102/123/135 bundle contract
Task251 exact read-only raw bundle
Task252 REGN -> OGRN -> INN -> LegalIssuer bridge
Task253 production read-only coverage
```

Task254 defines storage for raw CBR facts without connecting those facts to
credit metrics, scores, strategies, or application financial reports.

## 3. Starting Commit and Repository Projection

```text
STARTING_SHA=2bd302d5d5106e5e5bb40f8942993cf35b030443
BRANCH=main
CURRENT_ALEMBIC_HEAD=202608280002
EXPECTED_CHANGED_FILES=docs/audits/TASK254_CBR_BANK_RAW_FINANCIAL_EVIDENCE_PERSISTENCE_CONTRACT.md,backend/tests/test_task254_cbr_bank_raw_financial_evidence_persistence_contract.py

LEGACY_FINANCIAL_REPORT_OWNER=Company
LEGACY_FINANCIAL_REPORT_SAFE_FOR_CBR_RAW_DATA=false
CBR_RAW_REPORTING_SUBJECT_MODEL_EXISTS=false
CBR_RAW_ARTIFACT_MODEL_EXISTS=false
CBR_RAW_OBSERVATION_MODEL_EXISTS=false
REPORTING_ENTITY_SEPARATE_FROM_LEGALISSUER=true
```

## 4. Prior Production Evidence

Task253 supplied prior read-only production evidence; Task254 does not repeat
that production run:

```text
TASK251_FINANCIAL_REGNS=353
SOURCE_RESOLVED_REGNS=353
SOURCE_IDENTITY_FAILURES=0
LEGAL_ISSUER_TOTAL=498
LEGAL_ISSUER_VERIFIED=498
LEGAL_ISSUER_WITH_INN=496
LEGAL_ISSUER_WITHOUT_INN=2
REGN_LEGALISSUER_VERIFIED=26
REGN_LEGALISSUER_NOT_FOUND=327
LEGAL_ISSUER_IDENTITY_QUALITY_BLOCKERS=0
MATCHED_LEGAL_ISSUERS=26
MATCHED_BONDS=647
```

The 327 unmatched REGNs are valid source subjects outside the present
BondRadar LegalIssuer universe. They are not source failures and must remain
persistable.

```text
RAW_SOURCE_EXISTENCE!=BONDRADAR_ISSUER_EXISTENCE
IDENTITY_REMEDIATION_REQUIRED=false
BANK_FINANCIAL_DATA_BRANCH_ECONOMICALLY_RELEVANT=true
```

## 5. Goal

The future store must retain a durable chain for all four supported forms:

```text
exact CBR artifact
  -> report/form snapshot
  -> REGN reporting subject
  -> raw observation
```

An independent, optional identity chain may connect a reporting subject to a
LegalIssuer. Forms `0409101`, `0409102`, `0409123`, and `0409135` remain raw
regulatory evidence and are not normalized financial statements.

## 6. Architectural Boundary

The authoritative raw-storage boundary is:

```text
SOURCE ARTIFACT -> RAW REPORT SNAPSHOT -> RAW REPORTING SUBJECT -> RAW OBSERVATION
```

The separate identity boundary is:

```text
RAW REPORTING SUBJECT -> EVIDENCE-AWARE IDENTITY LINK -> LEGAL ISSUER
```

`LegalIssuer -> normalized financial report` is explicitly not the raw source
model. Raw ingestion remains valid when `LegalIssuer match=NONE`.

## 7. Legacy FinancialReport Rejection

```text
LEGACY_FINANCIAL_REPORT_REUSED=false
LEGACY_FINANCIAL_REPORT_CHANGED=false
```

The legacy `financial_reports` table cannot represent CBR raw evidence because:

- ownership is mandatory `company_id`, while a CBR subject is keyed by REGN;
- uniqueness is `company_id + period_year + period_quarter`, which collapses
  form, artifact, code, dimensions, revision, and observation chronology;
- columns such as revenue, EBITDA, debt, and ratios are normalized economic
  concepts rather than source form rows;
- ingestion supports create/update/skip and `rebuild_existing`, not immutable
  source history;
- missing currency is defaulted to RUB;
- company scoring, credit health, feature snapshots, diagnostics, and ratio
  services consume the table directly.

`financial_report_source_documents` is also Company-scoped, mutable, lacks an
exact-byte content contract, and cannot supply the required source identity or
row-level lineage. Existing legacy tables remain untouched.

## 8. Logical Domain Model

The contract defines six independent logical entities:

1. `CbrBankReportingSubject` — stable CBR REGN identity.
2. `CbrBankSourceArtifact` — immutable exact compressed source bytes.
3. `CbrBankReportSnapshot` — one observed form/member/parser projection.
4. `CbrBankRawObservation` — one retained source value row.
5. `CbrBankSubjectLegalIssuerEvidence` — append-only Task252 identity outcome.
6. `CbrBankSubjectLegalIssuerProfile` — mutable derived current link state.

They must not be collapsed into a generic report table or a generic
issuer-related-company relationship.

## 9. Reporting Subject Identity

```text
PRIMARY_SOURCE_IDENTITY=REGN
REGN_PRIMARY_SOURCE_IDENTITY=true
SOURCE=CBR
SUBJECT_TYPE=CREDIT_ORGANIZATION_REGN
```

REGN uses Task252 canonicalization: decimal digits, positive value, leading
zeroes non-semantic, persisted as `str(int(value))`. INN is not the subject key.
OGRN, INN, and CBR title are observed identity evidence and may be projected by
the link profile; they are not guessed or stored as primary master truth.

## 10. Optional LegalIssuer Linkage

```text
LEGALISSUER_LINK=OPTIONAL
TITLE_IDENTITY=false
FUZZY_MATCHING=false
```

Allowed current link states are exactly:

```text
VERIFIED
NOT_FOUND
AMBIGUOUS
NOT_VERIFIED
SOURCE_IDENTITY_BLOCKED
NOT_EVALUATED
```

Only `VERIFIED` is usable by a future consumer. Verification reuses Task252's
exact `REGN -> OGRN -> INN -> LegalIssuer.issuer_inn` contract. Names remain
diagnostic only. `NOT_FOUND` never blocks raw financial persistence.

## 11. Source Artifact Contract

`CbrBankSourceArtifact` preserves the exact approved compressed RAR bytes as
database `BYTEA`, not merely a locator. It also preserves source URL, filename,
form, report date, SHA-256, byte count, content type, first discovery and
retrieval timestamps, parser contract, and archive-runtime contract.

The artifact is immutable. Equal source and content SHA identify one content
object. A later download with the same apparent filename/date but different
bytes is a distinct artifact and must trigger explicit revision review rather
than overwrite.

```text
ARTIFACT_BYTES_PRESERVED=true
ARTIFACT_SHA256_LINEAGE=true
```

## 12. Raw Observation Contract

Every `CbrBankRawObservation` preserves:

```text
snapshot identity and reporting-subject identity
form, report_date, canonical subject_regn
archive_member_name, source_row_number, source_row_fingerprint
source_value_field, source_code, source_subcode, ordered source_dimensions
source_fields_sha256, exact decoded raw_value_text
nullable parsed_decimal_value
disclosure_state, source_unit, source_currency, source_multiplier
source_date, parser_contract_version, ingested_at
observation_fingerprint
```

The compressed artifact plus member, row, field, and schema lineage permits
independent reconstruction of the DBF field. `raw_value_text` is decoded from
the exact source field bytes; it is never reconstructed from float or a rounded
numeric value.

## 13. Report and Form Snapshot Contract

One `CbrBankReportSnapshot` binds exactly one artifact, form, report date,
value-bearing member, ordered member-schema inventory, form-schema fingerprint,
parser contract, and fixed observation/retrieval envelope.

It retains record count, value-bearing subject count, subject-set SHA-256, and
ordered observation-set SHA-256. The current fixture evidence remains:

```text
0409101_SUBJECTS=353
0409102_SUBJECTS=212
0409123_SUBJECTS=352
0409135_SUBJECTS=345
```

These counts are verified evidence, never runtime constants.

## 14. Value Semantics

```text
blank!=zero
invalid!=zero
missing!=zero
FLOAT_ALLOWED=false
```

Numeric source fields follow `raw field bytes -> finite Decimal`. Blank public
values retain empty lexical evidence, `parsed_decimal_value=NULL`, and
`disclosure_state=PUBLIC_VALUE_BLANK`. Source zero remains Decimal zero.
Malformed and non-finite numeric values fail under the parser/source contract.
`Decimal(str(float))` is forbidden.

## 15. Unit Semantics

Raw persistence records source unit, nullable source currency, and nullable
source multiplier without applying them:

```text
0409101=RUB_THOUSANDS
0409102=RUB_THOUSANDS
0409123=RUB_THOUSANDS,RUB,multiplier_1000
0409135=PERCENT,currency_NULL,multiplier_NULL
RAW_VALUE_SCALING=false
NORMALIZATION=false
```

No amount is multiplied by 1000 and no percentage is converted to a decimal
ratio. Missing unit, currency, multiplier, or value receives no default.

## 16. PIT and Publication Semantics

The model keeps `report_date`, nullable source context date, nullable
`publication_at`, `retrieved_at`, and `ingested_at` distinct.

```text
report_date!=publication_at
PIT_PUBLICATION_STATUS=UNKNOWN
HISTORICAL_BACKCAST_ALLOWED=false
```

Publication time is never inferred from report date, filename date, HTTP
Last-Modified, discovery time, or retrieval time. Unknown publication permits
raw retention but blocks automatic point-in-time backtest use.

## 17. Restatements and Reobservations

All source revisions are append-only. A sequence `A(T1) -> B(T2) -> A(T3)`
creates three snapshot observations while artifact content A remains one
deduplicated byte object. Source facts are never overwritten in place.

```text
RESTATEMENT_POLICY=APPEND_ONLY
REOBSERVATION_A_B_A_SUPPORTED=true
NO_BACKCAST=true
```

Snapshot identity includes the fixed source observation/retrieval timestamp,
so a genuine later reobservation is not lost. An exact retry must reuse the
original immutable ingestion envelope timestamp.

## 18. Idempotency and Fingerprints

All fingerprint payloads use canonical JSON, ordered keys/inventories, explicit
nulls, UTF-8, and SHA-256. Mutable database IDs are never sole inputs.

- Artifact fingerprint: contract, source, content SHA-256, compressed size.
- Snapshot fingerprint: contract, artifact fingerprint, form, report date,
  member, ordered schema inventory, form schema, parser version, observed and
  retrieved times, publication status, nullable publication time.
- Observation fingerprint: snapshot fingerprint, REGN, member, row identity,
  row number, value field, ordered dimensions, raw lexical value, canonical
  Decimal/null, disclosure state, unit/currency/multiplier, source date.
- Identity-link fingerprint: contract, REGN, Task252 contract/state, OGRN/INN,
  stable LegalIssuer source namespace/ID, registry date, FinOrg update,
  observed/retrieved time, and ordered diagnostic codes.

Exact retry creates zero duplicate semantic rows. Observation-set SHA is the
hash of ordered observation fingerprints and is verified against the snapshot;
it does not participate circularly in observation identity.

## 19. Provenance and Lineage

Every value must support both queryable chains:

```text
RawObservation -> ReportSnapshot -> SourceArtifact -> source URL + exact bytes SHA
RawObservation -> ReportingSubject -> LegalIssuerLinkEvidence -> optional LegalIssuer
```

No financial observation may exist without artifact/snapshot/subject lineage.
The stable LegalIssuer source namespace and source issuer ID survive even if a
nullable database FK is later cleared.

## 20. Concrete Database Schema Proposal

All identifiers use integer primary keys unless noted. All timestamps are
timezone-aware. SHA-256 columns are fixed 64-character strings.

### `cbr_bank_reporting_subjects`

| Column | Null | Contract |
|---|---:|---|
| `id` | no | primary key |
| `contract_version` | no | fixed v1 contract |
| `source` | no | fixed `CBR` |
| `subject_type` | no | fixed `CREDIT_ORGANIZATION_REGN` |
| `subject_regn` | no | canonical positive decimal string |
| `first_observed_at` | no | first source observation |
| `last_observed_at` | no | derived current bound |
| `created_at`, `updated_at` | no | audit timestamps |

Unique natural identity: `(source, subject_type, subject_regn)`.

### `cbr_bank_source_artifacts`

| Column | Null | Contract |
|---|---:|---|
| `id` | no | primary key |
| `contract_version`, `source` | no | v1 / `CBR_BANK_REPORTING` |
| `source_url`, `artifact_filename` | no | official provenance |
| `form`, `report_date` | no | supported form/date |
| `content_bytes` | no | immutable compressed `BYTEA` |
| `content_sha256` | no | exact byte hash |
| `compressed_size`, `content_type` | no | transport facts |
| `first_discovered_at`, `first_retrieved_at`, `ingested_at` | no | timestamps |
| `parser_contract_version`, `archive_runtime_contract` | no | runtime lineage |
| `artifact_fingerprint` | no | unique fingerprint |

Unique constraints: `(source, content_sha256)` and `artifact_fingerprint`.

### `cbr_bank_report_snapshots`

| Column | Null | Contract |
|---|---:|---|
| `id` | no | primary key |
| `artifact_id` | no | artifact FK `RESTRICT` |
| `contract_version`, `form`, `report_date` | no | identity |
| `value_member_name` | no | exact member |
| `member_schema_inventory` | no | ordered canonical JSON |
| `form_schema_fingerprint`, `parser_contract_version` | no | schema lineage |
| `observed_at`, `retrieved_at`, `ingested_at` | no | chronology |
| `publication_status` | no | `KNOWN` or `UNKNOWN` |
| `publication_at` | yes | only proven source time |
| `record_count`, `subject_count` | no | nonnegative counts |
| `subject_set_sha256`, `observation_set_sha256` | no | aggregate hashes |
| `snapshot_fingerprint` | no | unique fingerprint |

### `cbr_bank_raw_observations`

| Column | Null | Contract |
|---|---:|---|
| `id` | no | primary key |
| `snapshot_id`, `reporting_subject_id` | no | FKs `RESTRICT` |
| `contract_version`, `form`, `report_date`, `subject_regn` | no | immutable access identity |
| `archive_member_name`, `source_row_number`, `source_row_fingerprint` | no | exact row lineage |
| `source_value_field` | no | selected DBF field |
| `source_code`, `source_subcode` | yes | source identifiers |
| `source_dimensions` | no | ordered canonical JSON, empty allowed |
| `source_fields_sha256` | no | complete ordered source-row hash |
| `raw_value_text` | yes | exact decoded lexical value; empty blank retained |
| `parsed_decimal_value` | yes | unscaled arbitrary-precision `NUMERIC` |
| `disclosure_state`, `source_unit` | no | source semantics |
| `source_currency`, `source_multiplier`, `source_date` | yes | no defaults |
| `parser_contract_version`, `ingested_at` | no | lineage |
| `observation_fingerprint` | no | unique fingerprint |

### `cbr_bank_subject_legal_issuer_evidence`

| Column | Null | Contract |
|---|---:|---|
| `id` | no | primary key |
| `reporting_subject_id` | no | subject FK `RESTRICT` |
| `contract_version`, `subject_regn` | no | identity |
| `bridge_contract_version`, `bridge_state` | no | Task252 lineage/state |
| `observed_ogrn`, `observed_inn`, `observed_cbr_name` | yes | source observations |
| `legal_issuer_id` | yes | FK `SET NULL` |
| `legal_issuer_identity_source`, `legal_issuer_source_issuer_id` | yes | stable identity |
| `registry_as_of`, `finorg_last_update`, `observed_at`, `retrieved_at`, `ingested_at` | no | chronology |
| `diagnostic_codes` | no | ordered sanitized JSON |
| `evidence_fingerprint` | no | unique fingerprint |

### `cbr_bank_subject_legal_issuer_profiles`

| Column | Null | Contract |
|---|---:|---|
| `reporting_subject_id` | no | primary key and subject FK `RESTRICT` |
| `contract_version`, `link_state` | no | current projection |
| `current_evidence_id` | yes | evidence FK `RESTRICT` |
| `legal_issuer_id` | yes | FK `SET NULL` |
| `legal_issuer_identity_source`, `legal_issuer_source_issuer_id` | yes | stable identity |
| `current_ogrn`, `current_inn`, `current_cbr_name` | yes | evidence-derived values |
| `last_observed_at`, `last_resolved_at`, `created_at`, `updated_at` | no | resolution audit |

Only the profile is mutable. It never authorizes financial normalization or
downstream use by itself.

## 21. Constraints and Indexes

Required checks include fixed contract/source/form/state inventories, canonical
positive REGN, 64-character lowercase hexadecimal hashes, positive byte size,
`octet_length(content_bytes)=compressed_size`, positive row number,
nonnegative counts, and `publication_status=KNOWN` iff `publication_at` is
non-null. `PUBLIC_VALUE_BLANK` requires nullable Decimal; a nonblank public
value requires both raw lexical text and finite Decimal.

The link profile permits a usable LegalIssuer only for `VERIFIED`; all other
states remain blocked. Missing INN/LegalIssuer does not invalidate the subject.

Required indexes:

```text
subjects(subject_regn)
artifacts(content_sha256)
artifacts(form,report_date)
snapshots(form,report_date)
observations(snapshot_id)
observations(report_date,subject_regn)
observations(form,source_code)
link_evidence(reporting_subject_id,observed_at)
link_evidence(legal_issuer_id)
link_profiles(legal_issuer_id)
```

No unique constraint is allowed on `REGN+form+report_date+source_code`; source
row dimensions, revisions, and repeated observations make that identity false.

## 22. Retention and Append-Only Rules

```text
APPEND_ONLY=cbr_bank_source_artifacts,cbr_bank_report_snapshots,cbr_bank_raw_observations,cbr_bank_subject_legal_issuer_evidence
RESOLVED_CURRENT_VIEW=cbr_bank_reporting_subjects,cbr_bank_subject_legal_issuer_profiles
DELETE_BY_NORMAL_APP_FLOW=false
UPDATE_SOURCE_FACTS=false
```

Artifact, snapshot, observation, and identity evidence services may insert or
return an exact existing row only; they may never update or delete source facts.
Artifact/snapshot/subject FKs use `RESTRICT`. LegalIssuer FKs use `SET NULL`,
while stable source identity columns preserve lineage. No LegalIssuer deletion
may cascade into regulatory evidence.

## 23. Read Model Boundary

Task254 does not wire the proposed domain into `FinancialReport`, Company,
credit risk, issuer or bond score, strategy, screeners, API, frontend, feature
generation, backtests, Shadow Test, or trading. A separately reviewed
normalization layer must consume raw evidence explicitly.

## 24. Task251 Compatibility

Task251 remains the sole current raw reader. Task254 changes none of its artifact
hashing, RAR containment, DBF Decimal parsing, member roles, schema fingerprints,
row semantics, subject membership, or fixture truth.

Task255 can obtain exact `raw_value_text` without changing Task251 by using the
already retained compressed artifact bytes, approved schema, value member, row
index, and selected value-field contract. It must compare the decoded scalar to
Task251's parsed Decimal before persistence.

```text
TASK251_CHANGED=false
```

## 25. Task252 and Task253 Compatibility

Task252 remains the only identity semantics. The optional link evidence stores
its exact outcome and timestamps rather than reimplementing title or fuzzy
matching. Task253 prior production counts must be representable without dropping
the 327 unmatched subjects.

```text
TASK252_CHANGED=false
TASK253_CHANGED=false
353 source subjects
353 source-resolved identities
26 verified LegalIssuer matches
327 LegalIssuer NOT_FOUND
0 identity-quality blockers
```

## 26. Historical Backfill Boundary

The schema supports multiple report dates, artifacts, revisions, observations,
and parser versions, but current source support remains limited to approved
Task251 artifacts/layouts.

```text
SCHEMA_HISTORICAL_CAPABLE=true
CURRENT_SNAPSHOT_PERSISTENCE_SUPPORTED_BY_DESIGN=true
GENERAL_MONTHLY_INGESTION_PROVEN=false
ARBITRARY_MONTH_INGESTION_PROVEN=false
HISTORICAL_BACKFILL_PROVEN=false
```

Recurring schemas/artifacts and historical publication timelines require
separate Task255/Task256 evidence. Historical files must never be accepted by
shape similarity alone.

## 27. Allowed Scope

Task254 may add only this document and its deterministic static contract test.
The only allowed side effects are local test/cache artifacts plus the explicitly
authorized Git commit and normal fast-forward push after validation.

## 28. Forbidden Scope

Task254 forbids migration/model/service creation, DB/VDS/production access,
source downloads, Task251 data persistence, changes to Company, FinancialReport,
LegalIssuer or Tasks251–253, unit conversion, normalization, metric or ratio
calculation, scoring, strategy, risk, backtest, paper/shadow trading, broker and
T-Invest work.

```text
NETWORK_USED=false
DATABASE_ACCESS=false
VDS_ACCESS=false
NORMALIZATION=false
SCORING=false
TASK255_STARTED=false
```

## 29. Required Contract Tests

The focused test statically verifies all 36 sections, repository projection,
the six table contracts, field/constraint/index inventories, optional identity
link, preservation of unmatched REGNs, byte/hash lineage, raw lexical and
Decimal semantics, no scaling, PIT failure semantics, append-only A→B→A,
separate fingerprints, Task251 compatibility, Task253 counts, historical
boundaries, and safety flags.

## 30. Local Test Policy

Run only:

```text
python -m py_compile backend/tests/test_task254_cbr_bank_raw_financial_evidence_persistence_contract.py
python -m pytest backend/tests/test_task254_cbr_bank_raw_financial_evidence_persistence_contract.py -q
git diff --check
```

Docker, full backend, database, frontend, network, source probe, and production
coverage tests are skipped by design because application/shared code is not
changed.

## 31. Acceptance Criteria

PASS requires exact baseline and scope; rejection of legacy FinancialReport;
independent REGN subject identity; complete unmatched-source retention; exact
artifact bytes and lineage; raw text and Decimal preservation; blank/missing/
zero separation; no scaling; fail-closed publication semantics; append-only
restatements and A→B→A; explicit idempotency; concrete six-table schema;
non-destructive LegalIssuer linkage; unchanged Tasks251–253; no unsupported
history claim; green focused test; and no production action.

## 32. Git, Diff, and Scope Validation

Before commit verify:

```text
CONTRACT_ONLY=true
MIGRATION=NONE
MODEL_CHANGES=false
DATABASE_ACCESS=false
VDS_ACCESS=false
LEGACY_FINANCIAL_REPORT_CHANGED=false
TASK251_CHANGED=false
TASK252_CHANGED=false
TASK253_CHANGED=false
NORMALIZATION=false
SCORING=false
PRODUCTION_ACTIONS=NONE
```

`git diff --check` must pass and the changed-file inventory must contain exactly
the two Task254 files.

## 33. Commit and Push Rules

After all local gates pass and `origin/main` still equals the starting SHA,
create exactly one commit:

```text
Define CBR Raw Financial Evidence Contract
```

Push normally to `origin/main` without force or rebase. Do not poll CI.

```text
CI=NOT_WAITED_BY_DESIGN
```

## 34. Final Report Contract

The delivery report states status, starting/ending SHA, exact files, legacy
rejection, six proposed entities, REGN/optional LegalIssuer boundary, raw value,
PIT, restatement/fingerprint semantics, historical boundary, focused test,
scope, commit/push, and CI state.

On successful documentation delivery:

```text
CBR_RAW_FINANCIAL_EVIDENCE_PERSISTENCE_CONTRACT=READY
TASK254_FINAL=PASS
LOCAL_BROAD_REGRESSION=SKIPPED_BY_DESIGN
PRODUCTION_ACTIONS=NONE
```

## 35. Recommended Task255

The sole recommended next task is:

```text
NEXT_TASK=Task255 — CBR Bank Raw Financial Evidence Store v1
```

Task255 may separately implement SQLAlchemy models, one Alembic migration,
append-only storage, exact controlled `2026-08-01` ingestion, idempotency proof,
and no normalization/scoring. It must not implement broad historical backfill
without separately proven source support.

## 36. Hard Stop

Stop without commit/push if the baseline changes; legacy FinancialReport is
reused; Company or LegalIssuer becomes mandatory raw ownership; unmatched
subjects are discarded; bytes/raw values are not auditable; values are scaled;
publication is inferred; evidence is overwritten; A→B→A cannot be represented;
migration/model/persistence or production access appears; Tasks251–253 change;
arbitrary history is claimed; focused tests fail; or unrelated changes cannot
be isolated. Task255 is not started inside Task254.
