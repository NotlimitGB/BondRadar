# Task271 — MOEX Duration Semantics & Normalization Repair v1

## 1. Decision

Introduce one pure, immutable MOEX duration contract and reuse it for future
regular/history ingestion and current Task267/Task268 reads. Existing snapshots
are not rewritten. No schema, model, migration, API, credit or liquidity-score
change is required. This corrects source units before separate modified-duration
work; it does not calculate modified duration.

## 2. Root cause

Both MOEX mapping paths previously divided source duration by 365 only when it
exceeded 50. Values such as 30 and 50 source days could therefore be stored as
30 and 50 years. Stored values remain potentially unreliable even after fixing
future ingestion, so a read-time compatibility repair is also required.

## 3. Confirmed MOEX duration unit

The authoritative source contract supplied with Task271 defines MOEX DURATION
in days and identifies the duration input as Macaulay duration. BondRadar market
features expose years. No live MOEX verification is performed by this task.

```text
MOEX_RAW_DURATION_UNIT=DAYS
BONDRADAR_DURATION_FEATURE_UNIT=YEARS
```

## 4. BondRadar years normalization

```text
duration_years = source_duration_days / Decimal("365")
```

Every valid value follows this convention: 730 days gives 2 years, 365 gives 1,
50 gives 50/365, 30 gives 30/365, 1 gives 1/365 and 0 remains zero. Arithmetic
uses a fresh local Decimal context with precision 28 and ROUND_HALF_EVEN, without
presentation rounding or modification of the caller's context. Existing database
Numeric precision applies only when ingestion persists a new mapped value.

## 5. Raw-evidence authority

`normalize_moex_duration(raw_payload, *, stored_duration_years=None)` returns a
frozen `MoexDurationResult` with duration_years, source_duration_days, typed status
and sorted unique quality_flags. It has no DB/client dependency, mutation or network.

For exact source `moex`, supported locations are only `moex.DURATION`,
`moex.duration` and the proven history shape `canonical.duration`. No recursive
search, arbitrary alias, legacy Bond duration or mapping-note inference is used.
Missing raw evidence returns RAW_DURATION_MISSING, DURATION_RAW_MISSING and null
duration even when stored duration is present. This deliberately reduces duration
coverage where authoritative source evidence is absent.

For non-MOEX snapshots, consumers retain their existing stored-duration behavior;
the MOEX helper is not invoked. Future MOEX mapping uses known MOEX source semantics,
including when the existing historical request assigns a custom source label.

## 6. Multiple representations and fail-closed behavior

Parse all supported representations independently. Accepted numeric shapes follow
Task267: Decimal, int, finite float through Decimal(str(value)), numeric strings
and decimal comma. Null and blank strings are missing numeric evidence. Reject
bool, negative, nonfinite, unsupported and malformed values; valid zero is preserved.

Agreeing Decimal source-day values give READY. Disagreement gives
CONFLICTING_RAW_DURATION and null duration. Any malformed representation gives
MALFORMED_RAW_DURATION and null duration, even beside valid evidence. If malformed
and conflicting evidence coexist, retain both flags and use malformed status.
Unusable results also return null source_duration_days, avoiding arbitrary precedence.

## 7. Stored snapshot compatibility

With valid raw evidence, normalized raw years always control the result. Non-null
stored values are compared exactly; a different or nonfinite stored value adds
STORED_DURATION_MISMATCH without blocking raw normalization. Null stored values
do not create mismatch. Raw 730/stored 2 agrees; raw 30/stored 30 returns 30/365
with mismatch. Existing Numeric rounding may itself cause an exact mismatch:
this is diagnostic, with no tolerance, rewrite or backfill.

Old `DURATION looked like days and was divided by 365` notes may remain historical
provenance, and Task267 retains DURATION_LEGACY_DAY_NORMALIZATION when present.
The old note is not required for normalization and never substitutes for raw evidence.

## 8. Future ingestion correction

Regular `_map_row` and historical `_map_history_row` both call the shared helper.
History verifies raw and canonical representations together. Valid results use
the constant `MOEX DURATION days divided by 365`; no duration threshold remains.
Invalid optional duration leaves the field null and preserves the existing string
or structured warning format. Unrelated mapping, liquidity fallback, payload shapes
and upsert policies remain unchanged. Tests use only disposable SQLite and fake
clients; no actual sync/backfill or production operation is run.

## 9. Task267 integration

Exact-source MOEX features replace persisted duration with raw-normalized years.
Quality flags include DURATION_RAW_MISSING, MALFORMED_RAW_DURATION,
CONFLICTING_RAW_DURATION and STORED_DURATION_MISMATCH where applicable; an
unavailable result also has DURATION_MISSING and has_duration=false. Price, YTM,
snapshot identity/date, market freshness, liquidity and cashflow contracts retain
their behavior. Non-MOEX duration is not divided by 365. Reads remain SELECT-only
under no_autoflush, including with caller-owned pending state.

## 10. Task268 integration

The narrow selected-snapshot projection now includes raw_payload. MOEX curve nodes
use the same helper as Task267, never a second parser or stored-duration fallback.
Within existing exclusive diagnostics order (yield first), missing raw is missing
duration, malformed/conflicting raw is invalid duration and valid zero is
nonpositive duration. Negative raw is invalid evidence. Raw-valid nodes remain
usable despite stored mismatch or null; no public schema extension is needed.

Target duration continues to come exclusively from BondMarketFeatureService.
Latest exact-source snapshot selection without older fallback, freshness,
single-date membership, two-distinct-duration readiness, duplicate-duration median,
Decimal interpolation, same-date target requirement and no extrapolation remain
unchanged. Curves and relative-value results are still computed views only.

## 11. Legacy boundaries

No legacy Bond duration, maturity, coupon frequency or coupon rate repairs the
source value. No snapshot is rewritten at read time. Existing wrong stored values
may remain; the repaired M3 consumers use available authoritative raw evidence.
Any later operational data repair needs a separate authorized task. Task269 credit
and Task270 liquidity code remain unchanged.

```text
LEGACY_BOND_DURATION_USED_AS_MARKET_EVIDENCE=false
EXISTING_SNAPSHOT_DURATION_REWRITE=false
```

## 12. PIT boundary

PIT_READY=false. Correct units do not establish historical observability, source
availability dates or complete historical identity. No backtest-safety claim is made.
Modified duration, DV01 and convexity remain outside Task271.

## 13. Verification

Cover Task271 scenarios A–AO in the three modified test modules, including short
duration, zero, raw shapes, agreement/conflict/malformed, exact stored diagnostic,
both future mapping paths, non-MOEX compatibility, OFZ interpolation and unavailable
states. Existing fixtures now supply raw source days where they assert MOEX duration.
Also verify local Decimal context, immutable results/payloads, SELECT-only reads,
pending-state preservation and unchanged persisted durations. Static checks verify
helper purity and removal of the duration threshold.

Run from the repository root (the existing MOEX module reads backend/requirements.txt):

```text
pytest -q backend/tests/test_moex_market_data.py backend/tests/test_bond_market_feature_service.py backend/tests/test_ofz_reference_curve_service.py -k task271
pytest -q backend/tests/test_moex_market_data.py backend/tests/test_bond_market_feature_service.py backend/tests/test_ofz_reference_curve_service.py
pytest -q backend/tests/test_bond_liquidity_feature_service.py
```

Compile the four affected application modules, then inspect working/staged diffs,
diff --check and the exact eight-file scope. Full backend, unrelated backfill,
production/VDS and live-source checks are skipped by design.

Local acceptance: Task271-focused selection passed 77 tests; the complete three-module
regression passed 271 tests; unchanged Task270 regression passed 88 tests. Compileall
passed for the four affected application modules.

## 14. Handoff

```text
TASK271_STATUS=MOEX_DURATION_SEMANTICS_REPAIRED
MOEX_RAW_DURATION_UNIT=DAYS
BONDRADAR_DURATION_FEATURE_UNIT=YEARS
MOEX_DURATION_NORMALIZATION_DIVISOR=365
MOEX_DURATION_THRESHOLD_HEURISTIC_REMOVED=true
RAW_MOEX_DURATION_AUTHORITATIVE=true
STORED_MOEX_DURATION_AUTHORITATIVE=false
TASK267_DURATION_READ_PATH_REPAIRED=true
TASK268_CURVE_DURATION_READ_PATH_REPAIRED=true
LEGACY_BOND_DURATION_USED_AS_MARKET_EVIDENCE=false
EXISTING_SNAPSHOT_DURATION_REWRITE=false
MODIFIED_DURATION_READY=false
PIT_READY=false
```

Code readiness does not guarantee usable duration for every snapshot. After local
acceptance, deliver one commit and ordinary origin/main push with concurrency checks,
remote SHA confirmation and at most one exact-commit CI snapshot without waiting,
polling or retry. Terminal CI verification is external. Task272 requires independent
Task271 verification and a separate explicit request; it is not started here.
