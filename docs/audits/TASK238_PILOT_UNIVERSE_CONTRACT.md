# Task238 — Pilot Universe Contract

## 1. Purpose

Task238 defines a deterministic, fail-closed diagnostic contract for the future
50,000 RUB / 90-day paper pilot. It classifies current persisted bonds using
only evidence the present data model can prove. It does not select investments,
repair data, or authorize paper execution.

## 2. Contract version

The stable contract version is `pilot-universe-v1`. Any later semantic change
requires a new version.

## 3. Why discovered is not investable

A row in `bonds` proves only that BondRadar has discovered an instrument. It
does not prove the issuer, complete terms, executable market state, contractual
cashflows, credit sufficiency, or portfolio executability. Task238 therefore
begins every row at `DISCOVERED` and evaluates explicit gates.

## 4. Identity gate

The issuer Company must exist and must not use the canonical
`Unknown issuer for ` prefix. Its identity profile must be `verified` and
`accepted`, must contain an INN matching the trimmed Company INN, and must use
the `legal_issuer` or `operating_company` role. SPVs, finance subsidiaries,
parent groups, unknown roles, fuzzy matches, inferred names, and high confidence
without the required statuses all fail closed.

## 5. Plain-vanilla v1 scope

The first diagnostic scope is fixed-rate, non-subordinated, non-perpetual,
non-amortizing, non-offer, positive-coupon RUB bonds with identifiers and a
future maturity. Floaters and more complex structures require later explicit
contracts.

## 6. Legacy terms gate

This gate checks only fields currently stored on `Bond`: currency, ISIN, SECID,
nominal, coupon, maturity, structural flags, amortization, and offer date. A
pass does not mean the security master is complete. Lot size, board, coupon
frequency/formula, outstanding nominal, and historical listing state remain
unproven system capabilities.

## 7. Market gate

Snapshots with `trade_date > as_of_date` are excluded before latest-row
ranking. Within that point-in-time subset, the latest `BondMarketSnapshot` is
selected by trade date descending, MOEX priority, then ID descending, matching
Task236. `FUTURE_MARKET_ALLOWED=false`. Static Bond market fields never provide
fallback. The selected row must be recent enough for the caller-supplied
required trade date, have MOEX as its source, prove dirty price or clean price
plus NKD, and contain YTM, positive duration, nonnegative volume, liquidity,
and spread to OFZ. Task238 performs no market calculation or refresh.

## 8. Observed cashflow gate

For the fixed-rate plain-vanilla scope, persisted events must contain a future
coupon and a `redemption` exactly on maturity. `offer_redemption` is not a
maturity redemption. Missing required events are `NOT_PROVEN`; cross-source
economic duplicates and future amortization or offer-redemption events are
`FAIL`. No source winner is selected and no event is repaired.

## 9. PRE_PILOT_DATA_CANDIDATE semantics

`PRE_PILOT_DATA_CANDIDATE=true` only when identity, legacy terms, market, and
observed cashflow gates all pass. This intersection identifies rows whose
currently measurable evidence is suitable for further repair and qualification
work.

## 10. Why a pre-pilot candidate is not pilot eligible

Task238 always reports `final_pilot_eligibility_evaluated=false` and
`pilot_eligible=false`. A pre-pilot candidate has not passed complete security
master, financial sufficiency, point-in-time research, credit-policy v2,
relative-value, BUY/HOLD/SELL, executable portfolio, bond-ledger, idempotent
paper-cycle, or full qualification gates.

## 11. Global capability blockers

The contract reports the following separately from bond blocker rows: missing
lot size, board, coupon frequency/formula, outstanding nominal, listing and
default/delisting history, terms history, bid/ask, execution ledger, financial
sufficiency gate, qualified point-in-time dataset, complete BUY/HOLD/SELL
policy, and qualified paper idempotency.

## 12. Known Task237 production baseline

Prior production evidence recorded 2,995 bonds, 2,992 placeholder-issuer bonds,
three resolved-issuer bonds, 2,970 bonds with any market row, no latest rows
with complete market core, one company with legacy financial reports, three
controlled financial entities, 1,875 bonds with cashflows, and 1,120 without.
The latest observed market date was 2026-05-19. These are
`PRIOR_PRODUCTION_EVIDENCE`, not a fresh Task238 production run and not proof of
any eligible bond.

## 13. Dependency mapping

- Task239: issuer recovery and explicit credit perimeter.
- Task240: security-master v2 fields and history.
- Task241: fresh executable market evidence.
- Task242: canonical cashflow source and economic-event semantics.
- Task243 and later: financial sufficiency, credit policy, decision policy,
  execution, and final qualification.

Task238 does not begin any of these tasks.

## 14. Explicit exclusions

There is no API wiring, portfolio admission change, ML change, risk or score
recalculation, MOEX call, data import, source download, issuer/cashflow/market
repair, model or migration change, paper execution, deployment, or VDS access.

## 15. Safety and read-only semantics

The service issues bounded `SELECT` queries and never flushes or commits. The
CLI explicitly enables and verifies PostgreSQL read-only transaction mode and
always rolls back before closing. Its output contains aggregate counts and
bounded sanitized samples, never connection details, credentials, raw payloads,
company names, or full production row dumps. Passing Task238 means only that
the diagnostic contract works; paper-pilot readiness remains `NO_GO`.
