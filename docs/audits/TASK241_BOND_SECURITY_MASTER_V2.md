# Task241 — Evidence-Aware Bond Security Master v2

## Purpose

Task241 introduces `bond-security-master-v2`, an evidence-aware foundation for
bond terms. It separates current resolved state from durable source assertions
and does not make a strategy, execution, or Shadow Test decision.

## Profile and evidence boundary

`BondSecurityMasterProfile` is one derived current-resolution cache per Bond.
`BondSecurityMasterEvidence` is the append-only provenance layer. Existing Bond
columns remain compatibility fields and an identity/FK anchor; populated legacy
values are not promoted into verified security-master facts.

Supported Task241 evidence sources are limited to `moex_universe`,
`moex_description`, and `moex_cashflows`. Evidence stores only the normalized
assertion and narrow raw proof needed to justify it. Complete MOEX responses are
not retained.

## Evidence identity and time

The evidence fingerprint binds the contract, Bond, field, source, source key
and table, assertion kind, normalized assertion, and supplied effective time.
It excludes ingestion time, raw formatting, and observation time. A repeated
logical assertion is therefore idempotent and retains the first observation.
A changed assertion or effective time creates a later evidence row.

The timestamps have distinct meanings:

- `effective_at` is an optional source-supplied economic effective timestamp;
- `observed_at` is when BondRadar actually received the source observation;
- `ingestion_at` is when the assertion was persisted.

Task241 never substitutes one timestamp for another or guesses missing source
time. These fields preserve the inputs required for future point-in-time
research, but Task241 does not implement historical selection.

## Resolution and conflicts

The resolver considers all retained assertions deterministically. No evidence
means `unknown`; one distinct valid value means `verified`; incompatible values
mean `conflict`, with the resolved scalar set to NULL. Classifications follow
the same rule. Agreeing independent sources remain as separate provenance rows.
There is no source priority or last-write-wins reconciliation. A conflict stays
fail-closed until a later explicitly authorized reconciliation design exists.

## MOEX mapping

Task239 currency normalization is reused unchanged: `SUR` becomes `RUB`, valid
foreign ISO-like three-letter codes remain uppercase, and malformed or missing
currency remains unknown. It never defaults to RUB.

Metadata assertions are created only from source-present currency, nominal,
coupon rate, maturity, explicit floating/amortization/subordination/perpetual
booleans, explicit offer date, and an actually observed board. Explicit false
is evidence; a missing or null flag is not. Lot size, coupon frequency, coupon
formula, outstanding nominal, and listing status remain unknown until an
explicit supported source contract exists.

The complete fetched cashflow schedule is inspected before persistence date
filters. An observed amortization row proves `amortizing`; an observed offer row
proves `present`. Empty tables do not prove `bullet` or `none`. Maturity and
nominal are never used to synthesize a redemption event or residual payment.

## Research, strategy, and execution boundaries

The broad research helper requires verified RUB currency, positive nominal,
future maturity, and resolved structural classifications. It preserves fixed
and floating, bullet and amortizing, senior and subordinated, dated and
perpetual, and offer-bearing instruments when their structure is known. This
keeps complex securities visible for research and bias analysis.

The separate Strategy-v1 structural helper restricts candidates to fixed,
bullet, senior, dated, no-offer RUB bonds. It does not evaluate issuer quality,
market freshness, liquidity, financials, ratings, expected return, or relative
value. The execution helper reports missing lot size, board, coupon frequency,
and outstanding nominal. None of these helpers authorizes trading.

## Deferred work and safety

Issuer identity, source-priority reconciliation, historical research datasets,
cashflow economics v2, strategy configuration, risk, backtesting, portfolio
construction, broker integration, and the official Shadow Test remain separate
future tasks. Task241 performs no production backfill and does not wire the new
profile into Task238 or the frozen paper stack.

Explicit Task241 invariants:

```text
LEGACY_BOND_FIELD_IS_PRIMARY_EVIDENCE=false
ABSENCE_MEANS_VERIFIED_FALSE=false
POINT_IN_TIME_EVIDENCE_PRESERVED=true
REDEMPTION_SYNTHESIZED=false
PRODUCTION_BACKFILL_PERFORMED=false
SHADOW_TEST_STARTED=false
```
