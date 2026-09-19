# Task287 — Canonical OFZ Identity v2

## 1. Purpose

Task287 repairs false-positive OFZ classification in runtime and M3 universe
consumers. It introduces one deterministic instrument-identity rule shared by
the OFZ reference curve, corporate-universe readiness, financial-report
coverage and the financial-report target script.

## 2. Production evidence

The repair was triggered by 37 production bonds classified as OFZ solely
because their product names contained `ОФЗ`. Examples include structured
SberIOS securities with identifiers `RU000A10EXY2` and `RU000A10EF94`. None of
the observed production bonds had an ISIN beginning with `SU`, and none of the
37 false positives appeared in the live MOEX TQOB OFZ set.

## 3. Canonical positive identity

An instrument is OFZ when its normalized ISIN begins with `SU`. Normalization
consists only of trimming surrounding whitespace and converting to uppercase.
There is no fuzzy matching.

## 4. Authoritative ISIN precedence

A nonblank ISIN is authoritative. A non-`SU` ISIN cannot be overridden by an
`SU` SECID or descriptive text. Thus `RU000A10EXY2` remains non-OFZ even when
its name contains `ОФЗ`.

## 5. SECID fallback

SECID establishes OFZ identity only when ISIN is absent or blank. The
normalized SECID must begin with `SU`. No other SECID prefix is accepted.

## 6. Name markers

`ОФЗ`, `OFZ` and `FEDERAL LOAN BOND` are weak descriptive evidence. They never
establish positive OFZ identity by themselves. A record with no ISIN and no
SECID fails closed even when its name is `ОФЗ 26200`.

## 7. No issuer inference

The classifier does not inspect company names, issuer names, government-like
issuer attributes, trading boards or Ministry of Finance text. Government-like
issuer classification remains a separate contract.

## 8. Shared implementation

`app.services.ofz_identity.is_ofz_instrument` owns the v2 rule. It is a pure
function with no ORM, database, filesystem or network dependency. Runtime
consumers pass only ISIN and SECID.

## 9. OFZ reference curve

Task268 curve construction now applies v2 before Security Master or market
eligibility. A non-`SU` false positive cannot increment the OFZ identity count
or become a curve component. A genuine `SU` instrument still must pass all
existing RUB, fixed-coupon, bullet, dated, maturity, freshness, yield and
duration gates.

## 10. Structural subtype exclusions

The existing exclusions for explicit OFZ-IN/ОФЗ-ИН, OFZ-PK/ОФЗ-ПК and
OFZ-AD/ОФЗ-АД markers remain separate from instrument identity. They are
evaluated only after canonical OFZ identity succeeds.

## 11. Corporate-universe consumers

Corporate universe planning, live-data readiness and financial-report coverage
use the same v2 result. A `RU...` SberIOS security containing `ОФЗ` in its name
remains in their corporate working universe subject to their other unchanged
rules.

## 12. Operational script

`financial_report_target_issuers.py` is an active operational consumer and uses
the shared helper. It no longer carries an independent name-substring rule.

## 13. Compatibility boundary

Task287 intentionally supersedes the Task268 identity compatibility boundary
that allowed descriptive names to establish OFZ identity. Historical Task268
documentation is preserved as the record of the original contract.

## 14. Persistence and migrations

The change is computed at read time. It adds no model field, migration,
backfill, database write or production-data mutation. Existing stored bonds
and market snapshots are unchanged.

## 15. PIT and readiness limits

The identity rule makes no point-in-time claim. It does not populate the true
OFZ universe, refresh market data or make a curve available for any particular
date. Operational OFZ population and data refresh remain separate work.

```text
CANONICAL_OFZ_IDENTITY_V2_READY=true
OFZ_NAME_ONLY_IDENTITY_READY=false
OFZ_ISSUER_INFERENCE_READY=false
OFZ_UNIVERSE_SYNC_READY=false
PIT_READY=false
```

## 16. Safety boundary

Task287 does not change ingestion, MOEX board selection, Security Master
persistence, financial formulas, scoring, ranking, recommendations, portfolio
logic, trading or frontend behavior. Verification uses disposable test data
only and performs no VDS, production DB or live-source access.
