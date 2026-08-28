# Task244 — Evidence-Aware Canonical Legal Issuer Master v1

## Purpose

Task244 introduces a canonical legal-issuer identity layer above Task243 Bond
mapping evidence. It represents each source issuer identity once and aggregates
the latest observation for every supporting security without treating legacy
Company state as legal-issuer authority.

Contract version:

`legal-issuer-master-v1`

Initial source namespace:

`moex_security_reference`

## Repository findings and boundary

`Company` is a legacy application entity used by Bond, FinancialReport and
scoring workflows. Most production Bonds historically point to placeholder
Companies. `CompanyIdentityProfile` describes one Company and includes review,
role and group-oriented fields. It is not a source-backed mapping between a Bond
and a legal issuer.

`FinancialReport` remains owned by Company through `financial_reports.company_id`.
A legal issuer is not automatically the correct consolidated reporting entity,
so Task244 does not modify or attach financial reports.

No pre-existing model has the natural source identity and append-only evidence
contract required by Task244.

## Domain model

`LegalIssuer` is the current canonical state for one source identity.
`LegalIssuerEvidence` is append-only issuer-level evidence derived from exactly
one validated Task243 `BondLegalIssuerEvidence` row.

The natural identity is:

```text
identity_source + source_issuer_id
```

For v1 this means:

```text
moex_security_reference + MOEX emitent_id
```

`INN_UNIQUE=false`

INN, OKPO and title are resolved attributes. They are never entity keys and
have no unique constraints. Two source issuer IDs sharing an INN remain two
LegalIssuer entities and are reported diagnostically.

Task244 adds no FK from `BondLegalIssuerProfile` to `LegalIssuer`. A current
Bond profile resolves dynamically by `mapping_source + source_issuer_id`, which
avoids stale bindings after a later Task243 issuer change.

## Evidence lineage and chronology

Task244 validates the complete persisted Task243 evidence contract and stores:

- upstream contract version and immutable evidence fingerprint;
- source Bond ID as a provenance value, without a lifecycle FK;
- matched SECID/ISIN and successful security-match status;
- independently nullable title, INN and OKPO;
- upstream observation, effective and ingestion timestamps;
- Task244 ingestion time and evidence fingerprint.

The Task244 fingerprint binds its contract, natural source identity, upstream
contract and upstream fingerprint. The upstream fingerprint already includes
Task243 observation identity.

Therefore:

- an exact retry creates no duplicate evidence;
- a later upstream observation creates new issuer evidence;
- A(T1) → B(T2) → A(T3) remains three observations;
- A and B remain separate LegalIssuer entities;
- the final verified Task243 profile resolves to A.

Historical rows are never rewritten to simulate re-observation. The service
uses unique constraints and savepoints as concurrency guards, flushes only and
never commits for the caller.

## Current resolution

LegalIssuer states are `observed`, `verified` and `conflict`. No empty
`unknown` LegalIssuer is created.

Resolution groups evidence by matched SECID. For each security it selects all
rows tied at the maximum UTC `observed_at`, then aggregates the resulting
current-per-security assertions:

- more than one current non-null INN produces `conflict` and a NULL INN;
- one current title produces the exact normalized title;
- missing or multiple current titles produce NULL title and `observed`;
- multiple current OKPO values produce NULL OKPO but no identity conflict;
- otherwise a title-backed identity is `verified`.

There is no source priority, global-latest winner, fuzzy name matching,
freshness window, Company fallback or lexical tie-breaker.

`first_observed_at` and `last_observed_at` derive from full evidence history.
`last_resolved_at` derives from the latest stored Task244 ingestion time rather
than resolver runtime.

`CURRENT_RESOLUTION != PIT_RESOLUTION`

The append-only timestamps preserve future as-of evidence, but Task244 exposes
no historical PIT query API.

## Bond-profile resolution and blockers

Only a checksum-bound, verified Task243 profile with the supported source and a
source issuer ID can resolve to the master. Resolution queries the natural key;
it does not persist another FK.

Downstream blockers distinguish missing/invalid/non-verified profiles, missing
master, source or identity mismatch, master conflict and master not verified.
In particular:

- `LEGAL_ISSUER_MASTER_MISSING`
- `LEGAL_ISSUER_MASTER_CONFLICT`

Completeness remains separate from blockers and reports issuer ID, title, INN,
OKPO, verified state and conflict state. Missing INN or OKPO does not fabricate
a value or invalidate an otherwise verified source identity.

## Read-only readiness probe

`scripts/legal_issuer_master_readiness_probe.py` analyzes persisted Task243
profiles and evidence only. It never calls MOEX and never populates Task244.

It reports profile/evidence counts, unique natural identities, missing fields,
current attribute ambiguities, INNs shared across source identities, supporting
security distribution, planned row counts and profile resolvability. Samples
are bounded and omit Company names, raw payloads and exception text.

PostgreSQL execution explicitly sets and verifies a read-only transaction. The
CLI always rolls back and closes. Only an explicitly requested report file may
be written.

```text
read_only=true
database_mutation_executed=false
external_source_called=false
company_mutation_executed=false
financial_report_mutation_executed=false
```

## Migration and safety

Migration `202608280002` descends from `202608280001`. It creates only
`legal_issuers`, `legal_issuer_evidence`, their constraints and indexes. It has
no backfill and no update of existing tables. Downgrade removes evidence before
issuers.

```text
BOND_COMPANY_ID_MUTATED=false
COMPANY_MUTATED=false
COMPANY_IDENTITY_PROFILE_MUTATED=false
FINANCIAL_REPORT_MUTATED=false
TASK241_CHANGED=false
TASK242_CHANGED=false
TASK243_CHANGED=false
INN_USED_AS_UNIQUE_KEY=false
FUZZY_ISSUER_MATCHING_USED=false
GROUP_PARENT_GUARANTOR_INFERRED=false
LIVE_MOEX_USED=false
PRODUCTION_ACCESSED=false
VDS_ACCESSED=false
BROKER_USED=false
TRADING_EXECUTED=false
PRODUCTION_POPULATION_EXECUTED=false
```

## Deferred relationships and production handoff

LegalIssuer is not a corporate group, parent, guarantor, beneficiary, SPV
sponsor or consolidated reporting entity. Task244 adds none of those fields or
relationships. Financial-report attachment remains separate future work.

The only authorized future production sequence is:

```text
deploy
→ migration
→ read-only readiness probe
→ controlled issuer-master population
→ audit
→ separately authorized mass population
```

Task244 implementation executes none of those production steps.
