# Task257A — CBR Bank Recurring Monthly Ingestion Foundation v1

## 1. Execution Profile

Task257A is production-adjacent implementation work performed entirely against local fixtures and disposable test databases. No VDS, production database, migration, deployment, scheduler, or production CBR ingestion was used.

```text
STARTING_SHA=9fa79364590472ba45fdf7ce2adc94a54f6fad02
ALEMBIC_HEAD=202609010001
IMPLEMENTATION=CODE_ONLY_FOUNDATION
PRODUCTION_EXECUTION=false
```

## 2. Context

Task251 provides bounded official-source discovery, HTTPS-only transport, RAR containment, strict DBF parsing, and approved schema fingerprints. Task255 provides append-only raw financial evidence persistence. Task256A remains the immutable-fixture controlled runner for `2026-08-01` and is unchanged.

## 3. Goal

The new monthly runner supports explicit `discover`, `plan`, `preflight`, and `apply` boundaries for one requested report date and all four mandatory forms. It establishes the reusable code contract only; it does not schedule or execute recurring production ingestion.

## 4. Starting State

The implementation started from clean `main@9fa79364590472ba45fdf7ce2adc94a54f6fad02`, with matching local `origin/main`. The existing migration head was `202609010001`. Docker and requirements already contained the required runtime dependencies.

## 5. Problem Statement

`EXPECTED_CURRENT` remains the approved identity registry for the initial four fixtures. It must not grow into a hardcoded list of future monthly hashes. Task256A correctly remains fixture-only. Recurring execution must use a frozen manifest and a versioned image rather than runtime package installation.

## 6. Required Architecture

```text
Official CBR reporting page
→ Task251 discovery and bounded acquisition
→ Task251 archive/DBF/schema validation
→ frozen monthly manifest and exact local bytes
→ Task255 lexical extraction
→ Task255 persistence store
```

There is no second archive parser, DBF parser, Decimal path, lexical extractor, artifact fingerprint implementation, or persistence store.

## 7. Monthly Source Contract

The CLI requires `--report-date YYYY-MM-DD` in every mode. The required forms are exactly `0409101`, `0409102`, `0409123`, and `0409135`. Missing, duplicate, mixed-date, or unsupported-form bundles fail closed.

## 8. Artifact Trust Model

Task251 keeps its original approved `fetch_artifact()` behavior. The additive `fetch_discovered_artifact()` uses the same GET-only transport, host checks, redirects, retry limits, and byte budgets but assigns no pre-existing trusted hash. Trust is established only after Task251 archive/DBF/approved-schema validation and the manifest freezes exact URL/href, filename, size, and SHA-256.

```text
FIRST_OBSERVATION=explicit
EXACT_REOBSERVATION=explicit
CHANGED_SOURCE_BYTES=explicit
SILENT_REFETCH_ON_RETRY=false
```

## 9. Runtime Contract

```text
rarfile==4.5
dbfread==2.0.7
libarchive-tools=true
RUNTIME_SELF_INSTALL=false
```

The runner contains no subprocess or package-manager invocation. Task257A did not alter Docker or requirements because the normal backend image already satisfies the runtime contract.

## 10. Run / Retry Identity

`cbr-bank-monthly-ingestion-manifest-v1` binds the report date, explicit UTC evidence observation time, publication contract, four source references, filenames, content types, byte sizes, content hashes, record/subject counts, subject-set hashes, approved form schema fingerprints, value-member names, and exact source-row fingerprint checksums. The manifest hash uses Task255 canonical JSON/SHA-256.

The same manifest and exact bytes reproduce identical semantic evidence. `ingested_at` may vary because it is execution metadata, not source-evidence identity. Naive or non-UTC observation time is rejected.

## 11. Runner Modes

The module is `app.services.cbr_bank_financial_evidence.monthly_runner`. There is no default mode.

```text
DISCOVER=network_yes_database_no
PLAN=network_no_database_no
PREFLIGHT=network_no_database_read_only
APPLY=network_no_database_explicit_write
```

## 12. PLAN / DISCOVER

`discover` requires an explicit UTC evidence time, an existing artifact directory, and manifest output path. It writes each artifact with create-only semantics; an existing different file is `ARTIFACT_CACHE_CONFLICT`. It emits no artifact bytes.

`plan` consumes only a frozen manifest and exact local bytes. It recomputes byte identities, Task251 parsing/schema results, Task255 lexical evidence, and the complete manifest. Any mismatch blocks.

Known regression truth:

```text
REPORT_DATE=2026-08-01
SUBJECTS=353
ARTIFACTS=4
SNAPSHOTS=4
OBSERVATIONS=38842
RAW_LEXICAL_MISMATCH_COUNT=0
```

These counts are not generic monthly acceptance thresholds.

## 13. PREFLIGHT

Preflight accepts only an environment-variable name and explicit `--confirm-read-only`; raw database URLs are not CLI inputs. PostgreSQL execution preserves this first-statement order:

```sql
SET TRANSACTION READ ONLY;
SHOW transaction_read_only;
```

Only after `on` is verified does it inspect the exact Alembic revision, six Task255 tables, legacy guard tables, aggregate counts, and manifest-specific monthly state. It always rolls back and closes.

## 14. APPLY Boundary

Apply requires `--confirm-write` and the exact lowercase manifest SHA-256. Manifest and local artifact validation complete before engine creation. Apply performs no discovery or HTTP. Within one caller-owned transaction it validates schema/current state, invokes Task255, independently reads back counts, monthly identities, observation counts and ordered checksums, then commits once.

Pre-commit failures roll back. A commit acknowledgement failure remains `COMMIT_OUTCOME_UNKNOWN` with reconciliation required and no automatic retry.

## 15. Schema Compatibility

Existing Task251 approved form schema fingerprints remain authoritative. A new month whose member/schema inventory does not match the approved contract returns `UNSUPPORTED_SCHEMA_VERSION`. Field guessing, fuzzy matching, closest-schema selection, and LLM inference are forbidden.

## 16. Persistence Boundary

Task255 tables and `CbrBankRawFinancialEvidenceStore` are reused unchanged. Raw lexical values, finite Decimal values, blank-versus-zero state, units, currency, multiplier, row identity, exact compressed bytes, source metadata, and publication state are preserved. Identity evidence is not supplied or modified by Task257A.

## 17. Network Rules

Only `discover` constructs the CBR client. It inherits Task251 HTTPS CBR-host allowlisting, GET-only transport, bounded redirects, retry classes, and response budgets. `plan`, `preflight`, and `apply` never construct or call a source client.

## 18. Production Safety

```text
VDS_ACCESSED=false
PRODUCTION_DATABASE_ACCESSED=false
PRODUCTION_EXECUTION=false
PRODUCTION_CBR_INGESTION=false
MIGRATION_EXECUTED=false
DEPLOYMENT_EXECUTED=false
SCHEDULER_CREATED=false
SECRETS_ACCESSED=false
```

## 19. Allowed Scope

The implementation adds the monthly runner, focused tests, and this audit, plus the minimal additive Task251 untrusted-discovery fetch method. No Docker or requirements edit was necessary.

## 20. Forbidden Scope

Task255 schema/migration, legacy financial models, APIs, frontend, normalization, credit metrics, scoring, strategy, risk, backtests, paper/live trading, broker integrations, ML, and historical backfill remain unchanged.

## 21. Tests

Focused tests cover source acquisition boundaries, strict manifest parsing and hashing, known fixture planning, mutation detection, unsupported schemas, output freezing, CLI confirmation, PostgreSQL read-only ordering, all four DB states, first apply, exact retry, rollback, commit uncertainty, sanitization, and runtime dependency declarations. Direct Task251, Task255, and Task256A regressions are required before delivery.

## 22. Acceptance Criteria

```text
MONTHLY_EXPLICIT_REPORT_DATE=true
FOUR_FORM_BUNDLE_REQUIRED=true
MANIFEST_DETERMINISTIC=true
MANIFEST_BINDS_ARTIFACT_BYTES=true
MANIFEST_BINDS_EVIDENCE_TIME=true
IMPLICIT_SEMANTIC_NOW=false
UNKNOWN_SCHEMA_FAILS_CLOSED=true
ARTIFACT_MUTATION_OBSERVABLE=true
TASK255_REUSED=true
SECOND_PARSER_STACK=false
SECOND_STORE_STACK=false
RUNTIME_SELF_INSTALL=false
NEW_MIGRATION=false
NORMALIZATION=false
SCORING=false
```

## 23. Git / CI / Final Report

Delivery requires focused and direct regressions, compileall, `git diff --check`, exact inventory, full diff review, and unchanged `origin/main`. Broad backend testing is `SKIPPED_BY_DESIGN`; GitHub CI is not polled.

## 24. HARD STOP

Any unexpected baseline change, production/VDS/DB access during implementation, migration/schema change, network use outside discovery, raw DB URL CLI, implicit evidence time, silent changed-byte acceptance, unknown schema acceptance, runtime self-install, regression failure, normalization/scoring, or scope contamination blocks commit and push. Task257B remains unstarted and requires separate authorization.
