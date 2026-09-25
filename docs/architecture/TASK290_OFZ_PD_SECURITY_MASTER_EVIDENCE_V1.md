# Task290 — OFZ-PD Security Master Evidence v1

## Classification boundary

OFZ identity remains canonical and identifier-based: a normalized `SU` prefix
in SECID or ISIN is required. MOEX `name` and `shortname` do not establish
identity. After that gate passes, a bounded `ОФЗ-ПД` or `OFZ-PD` token in
either source-native description identifies the fixed-coupon OFZ-PD family.
Generic OFZ labels, other OFZ families, coupon rate, issuer text, and board do
not establish this subtype.

## Derived structural evidence

`moex_universe` and `moex_description` metadata may add
`coupon_structure=fixed` for canonical OFZ-PD instruments. The evidence keeps
the classification basis, canonical identifiers, family marker, and maturity
value in the existing narrow raw provenance contract. Source identifiers must
agree with the corresponding stored bond identifiers when both are present.
A valid maturity date from the same metadata observation additionally permits
`perpetual_structure=dated`; a previously known date is not substituted.

Explicit MOEX structural flags remain independent evidence. Opposite explicit
and family-derived assertions are both retained and resolve to the existing
`conflict` state. Matching assertions use the existing evidence fingerprint
and idempotency behavior. The existing cashflow classifier remains the sole
source of bullet evidence.

## Eligibility and safety

The OFZ reference curve continues to require its existing Security Master and
market gates. This change adds no schema or migration, changes no sync
workflow, imports no instruments, and makes no production or live-source
requests. A fully eligible example requires canonical OFZ-PD identity,
verified RUB and maturity, fixed coupon, dated structure, bullet cashflow
evidence, and fresh valid market evidence.
