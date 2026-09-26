# Task293 — MOEX Bond Type & Subtype Structural Evidence Foundation v1

## Purpose

Task293 maps the source-native MOEX `BONDTYPE`/`BOND_TYPE` and
`BONDSUBTYPE`/`BOND_SUBTYPE` classifications into existing Security Master
evidence. The resolver remains the only authority for the resulting
`coupon_structure` and `perpetual_structure` profile values.

## Source normalization and identity

MOEX metadata normalization exposes `bond_type` and `bond_subtype` and retains
the original row in `raw`. Alias field names are matched case-insensitively.
Nonblank string values are trimmed at their edges and compared exactly. When
aliases agree, provenance chooses `BONDTYPE` or `BONDSUBTYPE` before its
alternate spelling. Conflicting aliases, or a non-string nonblank value,
block only that classifier's derived evidence. Empty values are ignored.

Task293 evidence is recorded only when any supplied SECID/ISIN agrees with the
target Bond under the existing metadata identity guard. A contradictory
identifier prevents both Task293-derived assertions; the guard and existing
OFZ identity behavior are not broadened.

## Exact mappings

Only these exact trimmed source values are supported:

| Source field/value | Security Master evidence |
| --- | --- |
| `BONDTYPE` or `BOND_TYPE`: `Облигация с фиксированным (известным) купоном` | `coupon_structure=fixed` |
| `BONDTYPE` or `BOND_TYPE`: `Облигация с фиксированным (неизвестным) купоном` | `coupon_structure=fixed` |
| `BONDTYPE` or `BOND_TYPE`: `Облигация с плавающим купоном` | `coupon_structure=floating` |
| `BONDSUBTYPE` or `BOND_SUBTYPE`: `Бессрочные` | `perpetual_structure=perpetual` |
| `BONDSUBTYPE` or `BOND_SUBTYPE`: `До погашения` | `perpetual_structure=dated` |

`До оферты (call)` and `До оферты (put)` establish `dated` only when the
same metadata observation also contains a valid MOEX maturity date. Other,
unknown, fuzzy, or partially matching values create no assertion.

## Evidence, conflicts, and idempotency

Assertions use the existing `BondSecurityMasterEvidence` table, classification
type, actual MOEX source, and narrow deterministic raw provenance. No source
precedence is introduced. Existing explicit boolean assertions and Task290
OFZ-PD assertions remain independent: agreement resolves normally, while
contradictions remain `conflict`. Existing evidence fingerprinting keeps
repeated identical ingestion idempotent.

## Boundaries and declarations

Task293 does not infer structure from names, short names, `TYPE`/`TYPENAME`,
coupon rate, coupon frequency, coupon period, coupon dates, legacy Bond fields,
or offer horizon alone. It does not change coupon-frequency evidence or the
Task272 Modified Duration and Task273 DV01 gates. No schema, migration, data
backfill, deployment, live-source request, or production operation is part of
this foundation.

```text
MOEX_BONDTYPE_NORMALIZATION_READY=true
MOEX_BONDSUBTYPE_NORMALIZATION_READY=true
FIXED_KNOWN_MAPPING_READY=true
FIXED_UNKNOWN_MAPPING_READY=true
FLOATING_MAPPING_READY=true
UNSUPPORTED_BONDTYPE_FAIL_CLOSED=true
PERPETUAL_MAPPING_READY=true
DATED_MAPPING_READY=true
OFFER_SUBTYPE_REQUIRES_MATURITY=true
ALIAS_CONFLICT_FAIL_CLOSED=true
IDENTITY_GUARD_PRESERVED=true
CONFLICT_RESOLUTION_PRESERVED=true
IDEMPOTENT_EVIDENCE_READY=true
LEGACY_STRUCTURAL_PROMOTION=false
FUZZY_CLASSIFICATION=false
COUPON_FREQUENCY_DERIVATION=false
TASK272_CHANGED=false
TASK273_CHANGED=false
DATABASE_MIGRATION_ADDED=false
PRODUCTION_ACCESSED=false
LIVE_MOEX_ACCESSED=false
PRODUCTION_BACKFILL=false
SHADOW_TEST_STARTED=false
PIT_READY=false
```
