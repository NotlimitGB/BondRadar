# Task263 — Credit Rating & Default Evidence Foundation v1

## Status

```text
IMPLEMENTATION=SCHEMA_AND_SERVICE_FOUNDATION_ONLY
ALEMBIC_HEAD=202609150003
SOURCE_ARTIFACT_CONTRACT=credit-risk-source-artifact-v1
RATING_EVENT_CONTRACT=credit-rating-event-v1
DEFAULT_EVENT_CONTRACT=credit-default-event-v1
```

Task263 introduces immutable evidence storage and exact identity resolution. It does not implement source adapters, live ingestion, cross-agency scale normalization, scoring, PIT selection, or production execution.

## Source namespaces and prior research

The permitted provider namespaces are `ACRA`, `EXPERT_RA`, `NRA`, `NKR`, and `MOEX`. Operator research indicates that rating agencies publish issuer- and issue-level rating material, while MOEX publishes bond-oriented default information. Those capabilities are prior research only: Task263 performs no independent live verification and accepts no claim as source-backed without persisted exact bytes and explicit event fields.

## Immutable source artifacts

`credit_risk_source_artifacts` retains exact non-empty bytes, a computed lowercase SHA-256 digest, provider, source kind, URL, content type, and first retrieval time. Identity is `(source_provider, source_url, content_sha256)`:

- exact replay reuses the first immutable row and never rewrites retrieval time;
- the same URL with different bytes creates a separate version;
- rating/default event foreign keys use `ON DELETE RESTRICT`.

## Rating evidence

`credit_rating_events` keeps issuer and bond ratings distinct. Raw scale, rating value, outlook, watch, action, source object identifier, source name, and source identifiers are preserved without interpretation. A null rating value is accepted only when outlook, watch, or action carries the event. No numeric score or comparable cross-agency grade is produced.

Issuer identity is exact canonical ten-digit INN against `LegalIssuer.issuer_inn`, restricted to `resolution_state="verified"`. Bond identity is exact uppercase twelve-character ISIN against `Bond.isin`. Names are evidence only. Fuzzy matching, transliteration, SECID fallback, issuer inference, and LLM matching are absent.

## Default evidence

`credit_default_events` is MOEX-only and bond-only. `TECHNICAL_DEFAULT` remains distinct from `DEFAULT`; obligation types remain `COUPON`, `PRINCIPAL`, `OFFER`, `OTHER`, or `UNKNOWN`. A bond event is never propagated to an issuer-wide fact.

## Publication precision

Publication fields are mutually exclusive:

```text
DATE      => publication_date set, publication_at null
TIMESTAMP => publication_at set, publication_date null
UNKNOWN   => both null
```

`retrieved_at` remains transport provenance and never substitutes for a publication date or timestamp. Task263 does not prove PIT readiness.

## Fingerprints and resolution

Event fingerprints bind artifact provider/URL/content SHA, raw source identifiers, event date, publication fields, and raw rating/default semantics. Canonical database IDs and current resolution state are deliberately excluded from source-semantic identity. Persisted resolution state and foreign keys are nevertheless immutable semantics: a later changed resolution for the same source event fails closed as a collision in v1 rather than silently rewriting or duplicating the evidence.

## Migration and transaction contract

Migration `202609150003` creates only the three Task263 tables and indexes; it inserts no rows. Downgrade drops default events, rating events, then artifacts. SQLite test compatibility accepts only a fully compatible pre-created schema; partial state fails closed. PostgreSQL does not accept pre-existing Task263 tables.

The service performs query-first idempotency with a savepoint/reload concurrency guard. It flushes but never commits. The caller owns commit or rollback.

## Safety state

```text
ISSUER_AND_ISSUE_RATINGS_DISTINCT=true
ISSUER_EXACT_INN_RESOLUTION=true
BOND_EXACT_ISIN_RESOLUTION=true
FUZZY_IDENTITY_MATCHING=false
TECHNICAL_DEFAULT_DISTINCT=true
RATING_SCORING=false
CROSS_AGENCY_NORMALIZATION=false
LIVE_SOURCE_INGESTION=false
PIT_READY=false
CFA_IMPLEMENTED=false
PRODUCTION_DATABASE_ACCESSED=false
PRODUCTION_MIGRATION_APPLIED=false
PRODUCTION_ACTIONS=NONE
```

No later task is started or automatically authorized by this foundation.
