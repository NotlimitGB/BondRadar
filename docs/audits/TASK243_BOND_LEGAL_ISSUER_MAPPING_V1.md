# Task243 — Evidence-Aware Bond ↔ Legal Issuer Mapping v1

## Purpose

Task243 establishes which source legal-issuer reference is currently associated
with a Bond. It introduces an append-only observation layer and a derived current
profile without changing the legacy Company relationship.

Contract version:

`bond-legal-issuer-mapping-v1`

Task242 production coverage showed exact SECID+ISIN corroboration for all 2995
current Bonds, issuer ID/title for all 2995, INN for 2993 and OKPO for 2027.
Those measurements motivate this schema but are not imported or replayed by the
Task243 implementation.

## Domain boundary

`BondLegalIssuerEvidence` records one normalized source observation.
`BondLegalIssuerProfile` is one mutable current-resolution cache per Bond.

This is deliberately separate from:

- `Bond.company_id`, which remains legacy application linkage;
- `CompanyIdentityProfile`, which describes a Company rather than a Bond/source
  mapping;
- Company duplicate resolution, which maps one Company to another;
- Security Master, which resolves bond terms rather than issuer identity.

Task243 does not create an Issuer Master entity. The stable, indexed
`source_issuer_id` is only a future Task244 input.

## Source and evidence contract

The only v1 source is `moex_security_reference`. Evidence preserves:

- requested Bond SECID and expected Bond ISIN;
- matched source SECID and ISIN;
- source issuer ID, title, INN and OKPO with independent missingness;
- the successful Task242 security-match status;
- observation, optional source-effective and ingestion timestamps;
- a SHA-256 evidence fingerprint.

Only `EXACT_SECID`, `EXACT_SECID_ISIN_CORROBORATED` and
`EXACT_ISIN_RECOVERED` may be recorded. All failed, missing, ambiguous,
conflicting and source-error Task242 results fail before persistence.

SECID/ISIN are trimmed and uppercased. Other identifiers are trimmed without
numeric or fuzzy inference. Titles are trimmed and internal whitespace is
collapsed. Missing values remain NULL. No raw MOEX response is persisted.

## Fingerprint and re-observation semantics

The fingerprint binds the contract, Bond, source, requested/matched security
identifiers, all issuer attributes, match status, `observed_at` and nullable
`effective_at`. It excludes DB IDs, `ingestion_at`, paths and runtime state.

Including `observed_at` is intentional and differs from Task241:

- an exact retry reusing the same observation timestamp deduplicates;
- a later observation of unchanged values creates a new evidence row;
- A(T1) → B(T2) → A(T3) persists all three rows and resolves the current profile
  to A;
- concurrent exact retries are protected by the unique fingerprint.

Callers must reuse the original `observed_at` for a retry of the same source
observation. A newly fetched response at a later time is a new observation even
when its values are unchanged.

Evidence is append-only through the service: it is inserted or reused, never
updated or deleted. Bond deletion retains the repository's existing cascade
policy for dependent rows.

## Current resolution

For every source, only rows at that source's maximum persisted `observed_at`
contribute to the current profile. Every row tied at that time is retained.
SQLite-naive persisted timestamps are interpreted as UTC. Historical evidence
remains unchanged.

States:

- `unknown`: no current source issuer ID;
- `observed`: one issuer ID exists, but the match is weaker than the strict
  SECID/ISIN contract or the current title is absent/ambiguous;
- `verified`: one issuer ID and title, exact SECID, and corroborated ISIN when
  the Bond has an ISIN;
- `conflict`: current source issuer IDs/security identities disagree, or one
  source issuer ID has contradictory non-null INNs.

Source issuer ID is the identity key. Older title changes are superseded and do
not create a new issuer. Multiple titles tied at the current observation make
the profile observed with a NULL title rather than an identity conflict.
Contradictory current OKPO values similarly produce a NULL current OKPO and do
not invalidate an otherwise verified mapping.

INN and OKPO are independent completeness dimensions. Their absence never
fabricates a value and does not by itself block a verified Bond-to-issuer
mapping. This keeps foreign and state issuers representable.

`last_observed_at` is the maximum evidence observation time.
`last_resolved_at` is the maximum evidence ingestion time and is never used as
identity precedence.

## Blockers and completeness

The fail-closed blocker helper reports, in stable order:

- missing profile;
- unknown, conflicting or not-yet-verified mapping;
- missing source issuer ID or title;
- non-exact SECID or missing required ISIN corroboration.

Missing INN and OKPO are reported only through the separate completeness
projection. The projection also exposes issuer ID/title presence, exact SECID
and ISIN corroboration.

## Temporal limitation

`CURRENT_RESOLUTION != PIT_RESOLUTION`

The evidence timestamps and append-only chronology support future as-of work,
but Task243 provides no historical query API, source freshness policy or legal
effective-time inference. MOEX reference data supplies no effective timestamp
in the Task242 contract, so MOEX ingestion always records `effective_at=NULL`.

## Migration and transaction boundary

Migration `202608280001` descends directly from `202608260001`. It creates only
the two Task243 tables, constraints and indexes. It contains no backfill and no
Company or Bond update. Downgrade removes evidence before profile.

The service flushes new evidence and the derived profile but does not commit.
The caller owns the transaction. Task243 adds no CLI, API, scheduler or live
fetch path.

## Safety invariants

```text
BOND_COMPANY_ID_MUTATED=false
COMPANY_MUTATED=false
COMPANY_IDENTITY_PROFILE_MUTATED=false
COMPANY_MERGE_EXECUTED=false
SECURITY_MASTER_CHANGED=false
TASK242_MATCHING_CHANGED=false
PRODUCTION_BACKFILL_EXECUTED=false
LIVE_MOEX_USED=false
PRODUCTION_ACCESSED=false
VDS_ACCESSED=false
PILOT_UNIVERSE_CHANGED=false
TASK244_STARTED=false
```

## Production handoff

The only safe future sequence is separately authorized migration, controlled
sample ingestion, audit, and then separately authorized mass population.
Task243 implementation performs none of those production actions.
