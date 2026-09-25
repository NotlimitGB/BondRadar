# Task288 — MOEX-Native OFZ Identity v3

## 1. Purpose

Task288 aligns the shared OFZ identity helper with identifiers used by MOEX.
It is a correctness repair for instrument classification and does not add an
OFZ synchronization workflow.

## 2. Evidence boundary

Current MOEX OFZ instruments commonly expose an `SU...` SECID together with a
Russian `RU...` ISIN. A non-`SU` ISIN therefore cannot override a valid
`SU...` SECID.

## 3. Canonical identity rule

Identifiers are stripped and converted to uppercase independently. An
instrument is classified as OFZ when either normalized SECID or normalized
ISIN starts with `SU`:

```text
SU SECID OR SU ISIN = OFZ identity
```

An `SU...` ISIN remains supported for compatibility.

## 4. Task287 supersession

Task287 correctly removed name-only false positives. Task288 supersedes only
Task287's ISIN-precedence assumption. The Task287 history and its protection
against SberIOS false positives remain intact.

## 5. Name evidence

Names containing `ОФЗ`, `OFZ`, or `FEDERAL LOAN BOND` do not establish OFZ
identity. Missing strong identifiers fail closed.

## 6. Consumer contract

The reference curve, corporate universe, live-readiness, financial-report
coverage, and financial-report target script continue to delegate to the same
pure `is_ofz_instrument` helper. They do not maintain private classifiers.

## 7. Curve eligibility

OFZ identity and reference-curve eligibility are separate decisions. Existing
Security Master, RUB, fixed-coupon, bullet, dated, maturity, market freshness,
yield, and duration gates remain unchanged.

## 8. Family exclusions

The existing name-based exclusions for OFZ-PK, OFZ-IN, and OFZ-AD families
remain curve-eligibility filters. They are not positive identity evidence.

## 9. Safety boundary

The helper has no ORM, database, filesystem, network, or persistence access.
Task288 performs no ingestion, import, backfill, migration, or production data
mutation.

## 10. PIT and readiness

The identity rule is a current identifier contract. It does not establish
point-in-time provenance, issuer identity, benchmark eligibility, or market
data availability. `PIT_READY=false` remains unchanged for downstream feature
contracts.

## 11. Verification

Regression coverage includes real MOEX-shaped `RU...` ISIN plus `SU...`
SECID pairs, SU-ISIN compatibility, case and whitespace normalization,
name-only failures, the production-style SberIOS false positives, curve
candidate admission, and preservation of structural exclusions.

## 12. Limitations

The rule does not infer identity from boards, issuer names, company names, the
Ministry of Finance, or fuzzy text. It does not discover or synchronize the
OFZ universe.
