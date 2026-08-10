# Task239 — MOEX Security-Master Source Contract

## Purpose

Task239 fixes one proven MOEX storage-normalization defect and introduces a
standalone source probe for later controlled execution. It does not repair
production data, complete the security master, or qualify any pilot bond.

## Proven currency contract

MOEX `FaceUnit=SUR` denotes the Russian ruble. New MOEX mappings therefore use
the explicit storage rule `SUR -> RUB`. `RUB` remains `RUB`; other valid ASCII
three-letter currency codes are uppercased and preserved. No FX conversion is
performed and no speculative `RUR -> RUB` mapping exists.

Missing, empty, numeric, non-ASCII, or malformed codes are unresolved. They are
never replaced with RUB. New universe rows fail with
`bond_currency_unresolved`; rebuilds preserve the existing Bond currency and
warn. Cashflow rows use their valid source currency, or a safely canonicalized
Bond currency only when the row currency is absent. Invalid or unresolved rows
are skipped.

## Prior production observations

Prior Task238 production evidence reported 2,995 bonds with currencies
`SUR=2784`, `USD=140`, `CNY=52`, `EUR=17`, and `CHF=2`. It also reported all
2,995 Bond amortization fields as NULL, all floating flags as false, 563 bonds
with amortization cashflow rows, and no redemption rows. Task239 does not claim
that any of these persisted observations changed.

## Evidence versus application defaults

A source field or recognized cashflow table is source evidence. A model default
or importer fallback is not. In particular, the current persisted
`is_floating_coupon=false` state is not treated as verified fixed-rate evidence,
and absence of amortization rows is not treated as verified bullet structure.

The probe reports explicit values without applying them to `Bond`, `Company`,
or `CompanyIdentityProfile`. Issuer name and INN evidence remains unreviewed;
it does not become `verified` or `accepted` identity.

## Probe contract

The standalone script emits
`bondradar.moex_security_master_source_probe.v1`. It accepts at most 100
canonical SECIDs and calls only `fetch_bond_description` and
`fetch_bond_cashflows`. It requires no application database.

For each SECID it reports independent fetch statuses, issuer evidence, raw and
canonical currency, maturity and offer values, explicit structural fields,
cashflow counts, recognized source-table names, evidence states, and sanitized
warnings. Results are ordered by SECID. The report never contains the complete
MOEX payload or arbitrary unrelated raw description fields.

## Amortization semantics

An explicit positive field or observed amortization rows produce
`AMORTIZATION_POSITIVE_EVIDENCE`. A recognized explicit false source value may
produce `AMORTIZATION_EXPLICIT_NEGATIVE_EVIDENCE`. With neither, the only safe
state is `AMORTIZATION_NOT_PROVEN`. Zero observed rows alone never proves false.

## Floating-coupon semantics

The probe exposes a floating-coupon field only when matching narrow source
evidence exists. `CURRENT_FLOATING_CLASSIFICATION_TRUSTED=false` remains fixed;
the current Bond default is never promoted into source truth.

## Redemption semantics

The existing client recognizes redemption/maturity source-table aliases. The
probe reports table presence, row count, and exact table names retained on
nonempty parsed rows. An empty recognized table can be reported as present even
though the current client does not retain its raw table name. If no recognized
table is observed, the state is `REDEMPTION_SOURCE_NOT_OBSERVED`.

Task239 never synthesizes redemption from maturity date, nominal value, or any
other Bond field. Zero production redemption rows remain an unresolved source
or parser question until the real probe is independently run.

## Raw-key evidence boundary

Description `raw` content is inspected only through fixed allowlists for
issuer, currency, maturity, offer, amortization, floating coupon,
subordination, and perpetual status. Only bounded scalar values and matching
key names may be emitted. Collections, unrelated fields, exception text, URLs,
credentials, and secrets are excluded.

## Safety and limitations

Task239 implementation and tests use fake source clients only:

- `LIVE_MOEX_PROBE_RUN=false`
- `PRODUCTION_ACCESSED=false`
- `PRODUCTION_MUTATED=false`
- `DOMAIN_DATA_REPAIRED=false`
- `REDEMPTION_SYNTHESIZED=false`
- `PAPER_EXECUTED=false`

The probe is intentionally network-capable for a later authorized VDS run, but
Task239 does not run it live. It adds no model, migration, dependency, security
classification, issuer recovery, or production rebuild.

## Task240 boundary

Task240 must use independently reviewed real probe evidence to decide whether
new nullable security-master fields, provenance, or source contracts are
required. Task239 does not begin that design and does not infer facts that the
current MOEX response has not proven.
