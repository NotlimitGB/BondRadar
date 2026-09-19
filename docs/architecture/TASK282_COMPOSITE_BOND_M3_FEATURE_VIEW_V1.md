# Task282 — Composite Bond M3 Feature View Foundation v1

## 1. Decision

Task282 defines `bond-m3-feature-view-v1`, an immutable composite of already-built M3 evidence. Its only composite statuses are `CONSISTENT` and `EVIDENCE_INVALID`. Consistency means that supplied contracts describe the same bond, date, source, and dependency evidence; it does not mean that every feature is available.

## 2. Why the composite is pure

The composer accepts Pydantic views and returns one Pydantic view. It has no database, ORM, session, model, service, network, filesystem, cache, or persistence dependency. This avoids repeating the overlapping market, curve, liquidity, duration, DV01, credit, and peer pipelines.

## 3. Included M3 feature families

The required inputs are Task267 market evidence, Task268 OFZ relative value, Task269 credit evidence, Task275 source-native credit comparability, Task270 liquidity, Task272 modified duration, Task273 DV01, and Task274 liquidity-aware relative value. Task281 peer-distribution orchestration is optional. Each complete dependency view remains nested as the authoritative representation.

## 4. Identity contract

The request requires an exact positive integer bond ID, an exact `date`, and a nonblank source string. Boolean IDs, `datetime`, and normalized source aliases are rejected. Every bond-scoped input must match the requested bond and date; every source-aware input and the OFZ curve must match the source exactly. Display ISIN and SECID come only from a matching Task267 market identity.

## 5. Contract and PIT integrity

All Task267–275 contract versions and the nested Task268 curve version are checked exactly. A supplied Task281 view must use `credit-cohort-peer-distribution-orchestration-v1`. Every supplied view and the nested curve must declare `pit_ready=false`. Drift is returned as diagnostic `EVIDENCE_INVALID`; malformed direct request arguments or wrong dependency object types raise `ValueError`.

## 6. Market / OFZ consistency

Task268 target snapshot ID and trade date must equal Task267. Target YTM and duration use exact finite `Decimal` equality when both sides contain usable evidence. Task282 does not rebuild the curve, interpolate a yield, or calculate either spread field.

## 7. Credit / comparability consistency

Task275 must identify the same Task269 contract, bond, and date in its provenance. When Task269 has a verified issuer link and both LegalIssuer IDs are present, they must match. Cohort keys, rating labels, rating scales, and agency relationships are not inferred or normalized.

## 8. Liquidity consistency

Task270 bond/date/source identity and provenance date/source must agree. Window observations, medians, percentiles, universe membership, and `liquidity_score_v1` are preserved without calculation.

## 9. Duration / DV01 consistency

Task272 snapshot/date and its provenance must agree with Task267 and its own top-level identity. Task273 must identify the same Task267 snapshot/date, agree with Task272 status, and, when both values are finite Decimals, copy the same modified duration. Its top-level and nested market provenance identities must agree. No duration, dirty value, relative sensitivity, or DV01 formula is evaluated.

## 10. Liquidity-aware relative-value consistency

Task274 must exactly copy the Task268 status, spread fields, reference yield, and interpolation method. It must exactly copy the Task270 score status, score, and three percentiles. Equality includes zeros and matching nulls. No liquidity premium or adjusted spread is produced.

## 11. Optional peer context

Omitting Task281 is valid. When supplied, its target/date/source must match the request. A nested Task277 result must match the target and any non-null date/source. All Task281 result statuses are preserved without rebuilding cohorts, peers, statistics, or percentiles.

## 12. Composite status versus feature availability

Valid unavailable feature views can compose to `CONSISTENT`. Availability independently reports market freshness, ready relative value, rating evidence, comparable cohorts, ready liquidity score, modified duration, DV01, liquidity-aware relative value, presence of peer context, and ready peer distribution. `all_supplied_evidence_consistent` alone mirrors the composite status. None of these fields is investment readiness.

## 13. No financial recalculation

The composer performs identity, version, PIT, status-copy, and exact-equality checks only. It contains no YTM, OFZ interpolation, spread, percentile, median, liquidity score, modified-duration, dirty-value, DV01, peer-statistic, credit-adjustment, liquidity-adjustment, or investment-score arithmetic.

## 14. Capability boundary

The contract declares only the implemented M3 evidence surface:

```text
M3_COMPOSITE_FEATURE_VIEW_READY=true
MARKET_FEATURE_INPUT_READY=true
OFZ_RELATIVE_VALUE_INPUT_READY=true
CREDIT_FEATURE_INPUT_READY=true
CREDIT_COMPARABILITY_INPUT_READY=true
LIQUIDITY_FEATURE_INPUT_READY=true
MODIFIED_DURATION_INPUT_READY=true
DV01_INPUT_READY=true
LIQUIDITY_AWARE_RELATIVE_VALUE_INPUT_READY=true
PEER_RELATIVE_VALUE_CONTEXT_SUPPORTED=true
CROSS_FEATURE_EVIDENCE_VALIDATION_READY=true

INVESTMENT_SCORE_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
LIQUIDITY_ADJUSTED_SPREAD_READY=false
TRANSACTION_COST_MODEL_READY=false
SLIPPAGE_MODEL_READY=false
MARKET_IMPACT_MODEL_READY=false
PORTFOLIO_CONSTRUCTION_READY=false
RISK_ENGINE_READY=false
PIT_READY=false
```

## 15. Verification

Acceptance uses synthetic pure-contract tests for valid and unavailable compositions, version/PIT drift, identity mismatches, every cross-feature evidence relationship, optional peer states, exact zero/null behavior, serialization, immutability, Decimal-context isolation, and sorted unique flags. AST checks prohibit ORM, services, database/network/mutation calls, and financial formula implementations. From `backend`, verification runs:

```text
pytest -q tests/test_bond_m3_feature_composer.py
python -m compileall app/schemas/bond_m3_feature_view.py app/services/bond_m3_feature_composer.py
```

Repository acceptance then runs working and staged `git diff --check`, reviews the complete staged diff, and confirms the exact four-new-file scope. The broad backend suite, production/VDS, and live sources are skipped by design.

## 16. Handoff

Task282 supplies the integrity boundary needed by a later coverage audit. `PIT_READY=false`, and code readiness does not imply that a particular bond has all feature families available. The sole handoff is Task283 — M3 Coverage & Readiness Audit v1, which requires independent Task282 review and a separate request.
