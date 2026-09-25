# Task292 — MOEX Coupon Frequency Evidence Foundation v1

## Purpose

This change connects the source-native MOEX description field
`COUPONFREQUENCY` to the existing Security Master coupon-frequency evidence
and resolver. The source reports payments per year directly; Task292 does not
derive frequency from coupon periods or schedules.

## Evidence contract

The MOEX description parser exposes `COUPONFREQUENCY` as
`coupon_frequency_per_year` and preserves the original source row in `raw`.
Security Master records a positive integer as a `scalar_value` assertion under
the actual metadata source (`moex_description` for targeted description
ingestion). Raw provenance remains narrow:

```json
{"source_field": "COUPONFREQUENCY", "value": "2"}
```

Positive integer values and their integer-string representations are
canonicalized to `int`. Missing, blank, non-positive, fractional, boolean,
non-finite, and malformed values create no frequency assertion.

## Resolution and boundaries

The existing Security Master resolver remains authoritative: no current
evidence yields `unknown`; one distinct current value yields `verified`; and
conflicting current values yield `conflict` with no resolved frequency.
Evidence fingerprinting and repeated-ingestion idempotency are unchanged.

`COUPONPERIOD`, coupon dates, coupon rate/value, legacy Bond fields, and other
metadata are not frequency evidence. Task292 does not infer fixed or floating
coupon structure, dated or perpetual structure, or any other structural fact.
Task272 and Task273 readiness rules remain fail-closed and unchanged; frequency
evidence alone does not make either feature available.

## Capability and safety declarations

```text
MOEX_COUPONFREQUENCY_ALIAS_READY=true
DIRECT_FREQUENCY_EVIDENCE_READY=true
POSITIVE_INTEGER_VALIDATION_READY=true
MISSING_VALUE_FAIL_CLOSED=true
MALFORMED_VALUE_FAIL_CLOSED=true
CONFLICT_RESOLUTION_PRESERVED=true
IDEMPOTENT_EVIDENCE_READY=true
COUPONPERIOD_DERIVATION=false
LEGACY_FREQUENCY_PROMOTION=false
COUPON_STRUCTURE_DERIVATION=false
PERPETUAL_DERIVATION=false
TASK272_GATE_WEAKENED=false
TASK273_GATE_WEAKENED=false
DATABASE_MIGRATION_ADDED=false
PRODUCTION_BACKFILL_PERFORMED=false
SHADOW_TEST_STARTED=false
PIT_READY=false
```

No schema, table, migration, production data, or live-source behavior is
introduced by this foundation.
