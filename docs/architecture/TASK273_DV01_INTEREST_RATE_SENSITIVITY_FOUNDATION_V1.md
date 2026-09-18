# Task273 — DV01 / One-Basis-Point Interest-Rate Sensitivity Foundation v1

## 1. Decision

Provide a deterministic, SELECT-only `BondDv01Service` for one bond unit. The
computed `bond-dv01-v1` view is frozen, forbids extra fields and is not persisted.
Its interface is `build_for_bond(bond_id, as_of_date, *, market_source="moex",
max_market_age_days=7)`. Invalid arguments raise ValueError before dependency
calls; an absent bond retains Task267 HTTP 404 `Bond not found`.

## 2. Financial contract

One basis point is Decimal("0.0001") in decimal yield. DV01 is the first-order
absolute monetary sensitivity of one bond unit to that yield move. It excludes
convexity and transaction costs. RUB amounts have no lot or position multiplier.
All calculations use a fresh local Decimal context with precision 28 and
ROUND_HALF_EVEN, without presentation rounding or caller-context dependence.

## 3. Task272 dependency

Reuse `BondModifiedDurationService.build_for_bond` with the requested bond/date
and the same source/age arguments as Task267. Never reconstruct MD, Macaulay
duration, coupon frequency or Task272 structural gates. Require Task272 READY
and finite nonnegative Decimal MD. Zero is available evidence and can produce
READY with zero relative sensitivity and zero monetary DV01.

## 4. MOEX/RUB scope

Only exact lower-case source `moex` is supported; every other source raises
ValueError. Currency must be verified and exactly RUB. No currency conversion,
board inference or expansion to another market-source quote convention occurs.

Task267 and Task272 must agree on bond ID, snapshot ID, trade date and source,
and their bond/source must match the request. MARKET_CONTRACT_MISMATCH has the
highest status priority. It blocks relative sensitivity, clean value, dirty value
and monetary DV01; common output snapshot/date/age fields become null. Both
original market identities remain in provenance. Matching absent snapshots do
not create a mismatch; has_matching_market_snapshot is false without a snapshot.

## 5. Verified nominal and currency

Read the unique `BondSecurityMasterProfile` by exact bond ID, projecting only ID,
contract version, currency state/code and nominal state/value. Nominal must be
verified, Decimal, finite and strictly positive. Unknown/conflict states fail
closed. Missing and invalid values remain distinct. No outstanding nominal,
issue size, legacy nominal or currency can fill a gap.

## 6. Clean quote and NKD

Read price, clean_price and nkd exclusively from Task267. A present clean_price
is authoritative, even when invalid; do not replace it with a valid price.
If clean_price is absent, a present price is an explicit PRICE_FALLBACK with
MARKET_PRICE_FALLBACK_USED. Otherwise the basis is CLEAN_PRICE or null when both
quotes are absent. A selected quote must be finite positive Decimal. Never
average quotes or select their minimum/maximum.

NKD is the accrued-coupon RUB amount from the same Task267 view. Require finite
nonnegative Decimal. Explicit zero is valid; missing NKD never becomes zero.
No coupon/date calculation, other snapshot or future cashflow fills NKD.

## 7. Dirty market value

```text
clean_value_currency = nominal_value × clean_quote_pct / Decimal("100")
dirty_value_currency = clean_value_currency + nkd_currency
```

These are RUB amounts per one bond unit. Clean value requires matching market
identity, verified RUB, verified valid nominal and a valid quote. Dirty value
also requires valid NKD. They may remain available when MD is unavailable.
If NKD is missing, valid clean value survives while dirty value stays null.
Invalid arithmetic fails closed with DIRTY_VALUE_INVALID.

## 8. DV01 formula

```text
ONE_BP = Decimal("0.0001")
relative_price_sensitivity_per_1bp = modified_duration_years × ONE_BP
dv01_currency_per_bond = dirty_value_currency × modified_duration_years × ONE_BP
```

MD=2, nominal=1000 RUB, quote=100%, NKD=10 RUB gives clean value=1000 RUB,
dirty value=1010 RUB, relative sensitivity=0.0002 and DV01=0.202 RUB per bond.
Relative sensitivity is dimensionless fractional sensitivity: do not multiply
its stored value by 100. It can survive nominal/currency/quote/NKD failures when
Task272 MD is usable and market identity agrees. Monetary DV01 requires all gates.

## 9. Sign convention

DV01_SIGN_CONVENTION=MAGNITUDE. READY DV01 is nonnegative, including valid zero.
First-order price direction is inverse to yield; the DV01 field does not encode
a signed rate-rise loss, forecast, recommendation or sensitivity label.

## 10. Availability and status

Primary status is the first detected failure in this order:

```text
MARKET_CONTRACT_MISMATCH → MODIFIED_DURATION_UNAVAILABLE
→ SECURITY_MASTER_MISSING
→ CURRENCY_NOT_VERIFIED → CURRENCY_MISSING → UNSUPPORTED_CURRENCY
→ NOMINAL_NOT_VERIFIED → NOMINAL_MISSING → NOMINAL_INVALID
→ MARKET_PRICE_MISSING → CLEAN_PRICE_INVALID → MARKET_PRICE_INVALID
→ NKD_MISSING → NKD_INVALID → DIRTY_VALUE_INVALID → READY
```

All detected factual limitations appear as sorted unique quality flags. Only the
narrow Task272 duration flags and NEGATIVE_YIELD propagate; unrelated liquidity
and cashflow flags do not. Available finite inputs and intermediate values
survive unavailable states; nonfinite or incorrectly typed numeric inputs become
null with diagnostics. Finite nonpositive invalid inputs remain inspectable.

Availability separately exposes has_modified_duration, has_matching_market_snapshot,
has_security_master, has_verified_currency, is_supported_currency,
has_verified_nominal, has_clean_quote, has_nkd, has_clean_value, has_dirty_value,
has_relative_1bp_sensitivity and has_dv01. Validity and verification gates determine
these flags; they do not merely test field presence. Capability readiness does
not promise per-bond availability.

## 11. Provenance

Preserve Task272 contract/formula versions and its market identity, Task267
contract version and market identity, snapshot ID/date/source, requested date/age,
Security Master profile ID/version, currency/nominal states, price basis and
dv01_formula_version=dv01-v1. Provenance snapshot fields identify the Task267
evidence even on mismatch, while the separate Task272 identity explains drift.
Do not include raw payloads or unrelated credit/liquidity evidence.

## 12. Legacy boundaries

```text
LEGACY_BOND_NOMINAL_USED=false
LEGACY_BOND_CURRENCY_USED=false
LEGACY_BOND_PRICE_USED=false
PERSISTED_DIRTY_PRICE_USED=false
OUTSTANDING_NOMINAL_USED_AS_FACE=false
```

No direct Bond duration/YTM, snapshot dirty_price/duration, feature snapshots,
risk assessments, ML datasets, positions, lot size or legacy score are inputs.
Existing Task267/271/272 files, models, migrations, API and frontend are unchanged.
All composed reads use no_autoflush; caller-owned new/dirty/deleted state and
source rows remain unchanged. No persistence, source client or network occurs.

## 13. PIT boundary

PIT_READY=false. Market selection and freshness come from Task267/272. Security
Master is the current exact profile, without historical identity reconstruction
or historical currency/nominal replay. This view is not a PIT or backtest gate.

## 14. Explicitly unavailable rate-risk capabilities

```text
MODIFIED_DURATION_INPUT_READY=true
RELATIVE_1BP_SENSITIVITY_READY=true
DV01_READY=true
PVBP_READY=true
DV01_PRICE_BASIS=DIRTY_VALUE
DV01_SIGN_CONVENTION=MAGNITUDE
PORTFOLIO_DV01_READY=false
KEY_RATE_DURATION_READY=false
CONVEXITY_READY=false
EFFECTIVE_DURATION_READY=false
ARBITRARY_RATE_SHOCK_SCENARIOS_READY=false
TRANSACTION_COST_MODEL_READY=false
PIT_READY=false
```

PVBP_READY describes this one-basis-point capability, not a second independently
calculated field. No portfolio/lot DV01, CR01, repricing engine, effective/key-rate
duration, arbitrary shocks, hedging, rate forecast, ranking or recommendation is
introduced.

## 15. Verification

The Task273 tests cover A–AP, par/discount/premium formulas, quote priority,
invalid-present clean price, NKD zero/missing/invalid, canonical states and exact
join, MD reuse/unavailability/zero/negative-yield input, identity drift and primary
status precedence. Additional coverage validates arguments, freshness boundaries,
caller-context isolation, empty/unavailable JSON stability and frozen contracts.
Invalid synthetic reads preserve existing DB constraints.

SQL capture verifies SELECT-only, mutation-call guards preserve pending state
with autoflush enabled, and source-row/view comparisons prove input immutability.
Static checks prohibit network/legacy/direct-snapshot inputs and float math.

Run sequentially from backend:

```text
pytest -q tests/test_bond_dv01_service.py
pytest -q tests/test_bond_dv01_service.py tests/test_bond_modified_duration_service.py
python -m compileall app/schemas/bond_dv01.py app/services/bond_dv01_service.py
```

Then working/staged diff checks, staged inspection and exact four-file scope.
Local results: Task273 focused selection 121 passed; combined Task273/Task272
selection 219 passed; compileall passed. Both pytest runs emitted only the local
pytest-cache write-permission warning; no test failed.
Price/NKD regressions are included in Task273 tests; additional Task267 and broad
backend suites are skipped by design. No production/VDS or live-source checks.

## 16. Handoff

Task273 ends after local verification, one task commit, ordinary origin/main
push with remote-concurrency checks and at most one exact-commit CI snapshot.
No CI waiting, polling or retry. Independent verification remains external.
Task274 — Liquidity-Adjusted Relative-Value Foundation v1 requires that review
and a separate request. If review identifies a price-basis gap, a narrow repair
must precede Task274. No next task is executed here.
