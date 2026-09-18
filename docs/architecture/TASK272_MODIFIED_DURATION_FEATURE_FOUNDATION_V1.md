# Task272 — Modified Duration Feature Foundation v1

## 1. Decision

Add a computed, SELECT-only `BondModifiedDurationService(db).build_for_bond(bond_id,
as_of_date, *, market_source="moex", max_market_age_days=7)` returning a frozen,
extra-forbidden `bond-modified-duration-v1` view. No derived result is persisted.
Only four new schema/service/test/document files are introduced; existing production
files, Task267/271 semantics, models, migrations, API and frontend remain unchanged.

Validate exact positive int bond ID, exact date excluding datetime, nonblank string
source and exact nonnegative int maximum age; bool is not an int argument. Invalid
arguments raise ValueError before market calls. Missing bond preserves Task267's
HTTP 404 `Bond not found`. Source equality follows Task267 without normalization.

## 2. Financial contract

Inputs are source Macaulay duration D in years, annual market YTM/nearest-offer yield
in percentage points and canonical coupon payment frequency n per year. The explicit
Task272 engineering convention is:

```text
MD = D / (1 + (YTM_pct / 100) / n)
```

This is a deterministic interest-rate feature, without a duration high/low label,
investment judgment, pricing engine or cashflow reconstruction.

## 3. Authoritative market inputs

Call BondMarketFeatureService with the requested bond ID, as-of boundary, exact source
and maximum age. Reuse its identity, snapshot ID/date, age/status, duration and YTM.
For MOEX, the market duration already incorporates Task271's raw-days /365 repair.
Task272 does not inspect raw payload, select another snapshot or bypass raw missing,
malformed/conflicting duration. Stored wrong duration and legacy Bond fields cannot
rescue unavailable Task267 duration. Non-MOEX inputs retain Task267's source semantics.

## 4. Authoritative coupon-frequency input

Query a narrow projection of the unique BondSecurityMasterProfile whose bond_id
matches the requested bond exactly. Retain profile ID/version, coupon structure,
frequency state/value and perpetual structure. Frequency must have state `verified`
and an exact positive int value, excluding bool. Do not whitelist, cap, coerce or
infer frequency from coupon rate, formula text, future schedules, date gaps or
market conventions. Unknown/conflicting frequency remains unavailable.

## 5. Structural eligibility

Require canonical `coupon_structure="fixed"` and `perpetual_structure="dated"`.
Floating, unknown/conflict coupon structures and perpetual/unknown/conflict perpetual
structures block this v1. Legacy maturity or Task267's legacy structural fields do
not override canonical eligibility. A missing profile gives unavailable evidence,
not a task-wide failure.

Offers do not automatically block: source yield/duration may already use the relevant
offer boundary. Amortizing bonds are not blocked solely for amortization; no bullet
requirement is introduced. Currency and subordination alone do not change the formula.
No independent reinterpretation of the source cashflow boundary is performed.

## 6. Yield unit semantics

YTM 10 means 10%, converted to Decimal 0.10 through division by Decimal("100").
With D=2 and n=1, MD is 2/1.10, not 2/11 or 2/1.001. Finite negative YTM and
explicit zero YTM remain observed values. Missing/nonfinite inputs do not become zero.

## 7. Formula and Decimal arithmetic

```text
yield_decimal = yield_to_maturity_pct / Decimal("100")
denominator = Decimal("1") + yield_decimal / Decimal(n)
modified_duration_years = macaulay_duration_years / denominator
```

Use a fresh local Decimal context, precision 28 and ROUND_HALF_EVEN, independent of
and without changing caller context. No float intermediate/conversion, epsilon,
clamping or presentation rounding is introduced. Pydantic JSON retains Decimal strings.

Task272 v1 requires a finite nonnegative Decimal Macaulay duration. Zero duration is
valid input and produces zero modified duration when all other eligibility gates pass
and the denominator is positive. Negative or nonfinite duration fails closed.
Finite invalid negative inputs may remain in
diagnostic fields; nonfinite or unsupported synthetic inputs are returned as null
with invalid flags, preserving stable JSON serialization.

## 8. Negative-yield / denominator semantics

Negative finite YTM adds NEGATIVE_YIELD and remains calculable when denominator >0.
Denominator ≤0 gives INVALID_MODIFIED_DURATION_DENOMINATOR and null MD without
division. Yield decimal is calculated whenever YTM is finite. Denominator is calculated
whenever YTM is finite and canonical frequency is verified/valid, even if duration,
freshness or another structural gate blocks MD. Available intermediate diagnostics
remain visible; no modified duration is produced from failed evidence gates.

## 9. Market freshness

Require Task267 FRESH market. Missing market and stale market block MD. Preserve
available stale inputs, age, snapshot lineage and intermediate values. The maximum-age
boundary is inclusive as defined by Task267; no extra snapshot selection is added.
`as_of_date` is a deterministic market-selection boundary.

## 10. Availability and quality flags

Task272 v1 uses the following deterministic status precedence:

```text
MARKET_DATA_MISSING → MARKET_DATA_STALE
→ MACAULAY_DURATION_MISSING → MACAULAY_DURATION_INVALID
→ YIELD_TO_MATURITY_MISSING → YIELD_TO_MATURITY_INVALID
→ SECURITY_MASTER_MISSING → COUPON_STRUCTURE_NOT_FIXED
→ COUPON_FREQUENCY_NOT_VERIFIED → COUPON_FREQUENCY_MISSING → COUPON_FREQUENCY_INVALID
→ PERPETUAL_STRUCTURE_NOT_DATED → INVALID_MODIFIED_DURATION_DENOMINATOR → READY
```

Quality flags are sorted/unique and preserve all detected limitations, including
specific unknown/conflict coupon flags and NEGATIVE_YIELD. Propagate only relevant
Task267 duration flags: DURATION_RAW_MISSING, MALFORMED_RAW_DURATION,
CONFLICTING_RAW_DURATION, STORED_DURATION_MISMATCH, DURATION_MISSING and
DURATION_LEGACY_DAY_NORMALIZATION. Do not mechanically copy unrelated liquidity,
cashflow or legacy structural flags.

Availability independently reports snapshot/freshness, valid duration/YTM, profile,
fixed/dated structures, verified positive frequency, valid denominator and MD.
Only READY returns modified_duration_years. Unavailable states preserve available
inputs, denominator, availability and provenance. For corrupt synthetic frequency,
non-int values are not coerced into schema integers; missing/invalid flags explain
why no authoritative frequency or denominator can be used.

## 11. Provenance

Retain market contract version, snapshot ID/date, exact source, as-of boundary and
maximum age. Retain exact Security Master profile ID/version and frequency state/value.
Use `formula_version="modified-duration-v1"`. Do not return raw payloads, artifacts
or the full Task267 view. No additional historical profile reconstruction is attempted.

## 12. Legacy boundaries

No legacy Bond duration, YTM, coupon rate or maturity fallback. No direct snapshot
duration access, frequency inference, risk/ML/strategy input or derived-result write.
All reads including nested Task267 calls run under no_autoflush. Caller-owned pending
new/dirty/deleted state remains unchanged; the narrow canonical projection reads
persisted state without flushing pending profile edits.

```text
LEGACY_BOND_DURATION_USED=false
LEGACY_BOND_YTM_USED=false
COUPON_FREQUENCY_INFERRED=false
```

## 13. PIT boundary

Every view and capability block has pit_ready=false. Current Security Master frequency
and structural resolutions are not reconstructed historically. Source availability
and historical identity/observability remain incomplete, and Task267 is not a full
PIT engine. No backtest-safe, historically-observable or leakage-safe claim is made.

## 14. Explicitly unavailable rate-risk capabilities

DV01, PVBP/BPV, convexity, effective/key-rate duration, rate/yield shocks, price-shock
estimates, portfolio duration/rate risk and transaction costs remain unavailable.
No OFZ spread, liquidity score, credit feature, recommendation, investment score,
ranking, strategy, Risk Engine, Shadow, backtest, CFA or broker execution is introduced.

## 15. Verification

Use the existing temporary SQLite fixture. Cover A–AS: formula, exact percent conversion,
arbitrary verified frequency, negative/zero yield and denominator gates; repaired raw
duration and no stored fallback; canonical structural/frequency evidence, no schedule
inference and nonblocking offer/amortization/subordination/currency; provenance,
determinism and read-only safety. Add zero/negative/nonfinite duration, Evidence-first
priority, argument validation, freshness boundary and Decimal-context isolation.
Synthetic invalid market/frequency values substitute read results without weakening
DB constraints. Capture SELECT statements and forbid mutation calls with autoflush
enabled; assert unchanged caller state, source rows, payloads and Task267 input views.

From backend run focused test_bond_modified_duration_service.py, then the same module
with test_bond_market_feature_service.py. Compile the two new application modules,
check working/staged diffs and exact four-file scope. Skip the full backend,
Task268/269/270, production/VDS and live-source checks by Task272 instruction.

Local acceptance passed 98 focused tests and 225 tests in the combined Task267
regression; both runs reported only a local pytest-cache write warning. Compileall
passed for the two new application modules.

## 16. Handoff

```text
TASK272_STATUS=MODIFIED_DURATION_FEATURE_FOUNDATION_READY
MACAULAY_DURATION_INPUT_READY=true
MODIFIED_DURATION_READY=true
DV01_READY=false
PVBP_READY=false
CONVEXITY_READY=false
EFFECTIVE_DURATION_READY=false
RATE_SHOCK_APPROXIMATION_READY=false
TRANSACTION_COST_MODEL_READY=false
PIT_READY=false
```

Code capability is separate from per-bond availability. Existing Task267 capability
declarations remain unchanged; the new Task272 view declares its own capability.
After local acceptance, create one task commit and ordinary origin/main push with
concurrency checks, verify remote SHA and take at most one exact-commit CI snapshot
without waiting/polling/retry. Terminal CI verification is external. Task273 needs
independent Task272 verification and a separate request; it is not started here.
