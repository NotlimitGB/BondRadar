# Task298 — Unified Candidate v1 / CORE M3 Candidate Projection

## 1. Purpose

Unified Candidate v1 is not an investment decision. It is an immutable projection
of supplied CORE-ready M3 evidence for a future separately specified consumer.
CORE_M3_COMPLETE_V1 is a data/evidence readiness boundary, not proof that a
security is attractive. Production observations are context, never eligibility
counts or hard-coded invariants. Baseline: 1571f93d375e11f1f529b0b20e720ade9b9a85d6.

## 2. Upstream dependencies

Task286 M3AuditSnapshotView is the sole input to the reducer. Task282 composites
contain economic evidence, Task283 audit classifies CORE completeness. The
orchestrator delegates M3 construction once to Task286; it never invokes lower
level M3 loaders. Six new schema/service/test/document files constitute the
package. Existing contracts, models, API, migrations and deployment are unchanged.

## 3. Exact CORE eligibility

A BUILT item must contain an exact BondM3FeatureView with matching bond/date/source,
CONSISTENT status, and true all_supplied_evidence_consistent, market_fresh,
relative_value_ready, has_credit_rating_evidence, has_credit_comparable_cohort,
liquidity_score_ready, modified_duration_ready, dv01_ready and
liquidity_relative_value_ready. This exact set must equal the supplied audit CORE
IDs in both directions. A mismatch raises ValueError; no partial result follows.
Peer context is not required. CORE is not redefined by thresholds or preferences.

## 4. Pure reducer boundary

UnifiedCandidateReducer.build(snapshot) validates versions/PIT, exact original
types, sorted unique requested/existing/missing partitions, counts/status,
ordered items, composite identity and audit universe/CORE partitions. It checks
availability against the Task282 dependency status/availability rules. CORE
inputs additionally require finite mandatory values and upstream valid ranges,
snapshot/provenance consistency, ready source-native credit evidence and Security
Master identity/frequency. Contradictory READY claims raise ValueError.
Legitimate EVIDENCE_INVALID composites remain visible exclusions.

Only schema, date and Decimal dependencies occur in the reducer. It uses no DB,
network, filesystem, environment, clock, randomness or service calls. No M3
formulas, audit percentages or financial relationships are recalculated. Count
and partition operations are factual bookkeeping.

## 5. Read-only orchestrator boundary

UnifiedCandidateSnapshotService(db).build(bond_ids, as_of_date, *,
market_source="moex", max_market_age_days=7, max_curve_age_days=7,
liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5) reuses the
existing Task286 request validator. Validation precedes dependency calls; IDs are
copied into a sorted tuple. Under common no_autoflush it calls Task286 once,
checks returned request context and calls the reducer once. Unexpected dependency
errors propagate. There are no own SELECTs or mutation/transaction completion
calls; caller-owned pending state remains untouched.

## 6. Candidate and batch schema

Strict frozen extra-forbid models use unified-candidate-v1 and
unified-candidate-batch-v1. Candidates preserve the exact source m3 object,
features, identity, eligibility, provenance and capabilities. Candidate and
batch collections are tuples. Frozen upstream models retain their existing
nested-list semantics; Task298 does not mutate them or claim recursive freezing
of pre-existing M3 contracts. Batch counts/IDs include every requested bond.
COMPLETE/PARTIAL/NO_EXISTING_BONDS means existence coverage, never CORE success.
A complete batch can have no candidates. All-missing input has no coverage audit.

Projection map (exact copies, without fallback or rounding):

| Candidate fields | Source |
|---|---|
| market_snapshot_id, market_trade_date, clean_price, nkd, yield_to_maturity_pct, maturity_date, days_to_maturity | m3.market / Task267 |
| reference_ofz_yield_pct, spread_to_ofz_pp/bps, interpolation_method | m3.relative_value / Task268 |
| curve_trade_date | m3.relative_value.curve |
| liquidity_score_v1, medians, latest liquidity date/age | m3.liquidity / Task270 |
| turnover/trade_count/recency percentiles | m3.liquidity.score_components |
| Macaulay/modified duration, coupon structure/frequency | m3.modified_duration / Task272 |
| currency_code, nominal_value, relative 1bp sensitivity, DV01 | m3.dv01 / Task273 |
| LegalIssuer ID/source ID/INN | m3.credit / Task269 |

Maturity fields are explicitly Task267 market metadata, potentially originating
from legacy metadata, not independently verified Security Master structural
evidence. They never override modern coupon/currency/nominal values. Missing
clean_price stays null even when Task273 uses its own permitted price fallback.
Nullable LegalIssuer identity remains valid for bond-specific rating evidence.

## 7. Exclusions

Every noncandidate requested ID has one exclusion. BOND_NOT_FOUND has null
identity/M3 status and only BOND_NOT_FOUND. Built exclusions report each false
CORE feature using the precise canonical reason: EVIDENCE_INVALID,
MARKET_NOT_FRESH, RELATIVE_VALUE_NOT_READY, CREDIT_RATING_EVIDENCE_MISSING,
CREDIT_COMPARABLE_COHORT_MISSING, LIQUIDITY_SCORE_NOT_READY,
MODIFIED_DURATION_NOT_READY, DV01_NOT_READY or LIQUIDITY_RELATIVE_VALUE_NOT_READY.
Reasons are unique, lexicographically sorted; no generic economic interpretation
or CORE_M3_INCOMPLETE replaces the precise missing features.

## 8. Provenance

Candidate provenance retains snapshot/composite versions, exact CORE definition,
requested date/source, market snapshot/date, curve date, issuer profile/ID,
Security Master profile ID and liquidity window/selected snapshot IDs. The
Security Master profile ID comes from Task272 and must agree with Task273.
Unavailable issuer lineage stays null rather than being invented. Full M3 remains
the ultimate detailed lineage. No timestamp or generated identifier is added.

## 9. Determinism and verification

Candidates/exclusions use ascending bond ID, reasons lexical order. Exact Decimal
values, zero, negative spreads and raw credit strings are preserved. No float
conversion, percentage conversion, arithmetic formula or rounding is added.
JSON-mode roundtrip and repeated model dumps preserve equality; caller Decimal
context and source objects remain unchanged.

Focused reducer/orchestrator tests cover CORE failures, envelope/audit mismatch,
READY contradictions, unavailable/missing output, exact projection, provenance,
source-native credit, serialization, immutable Task298 models, delegation,
SELECT-only/pending state and AST boundaries. Task283/286/282 regressions follow.
Compile and working/staged diff checks complete local acceptance. Full suite is
delegated to exact-commit CI, without waiting before final delivery report.

## 10. Absence of investment interpretation

CORE candidate and market/OFZ/credit/liquidity/duration context capabilities are
true as implementation capabilities. Investment model, score, ranking,
recommendation, portfolio construction, Risk Engine, Shadow, transaction costs,
slippage, market impact and cross-agency normalization remain false. pit_ready is
false in every candidate/batch. Task298 does not make PIT_READY=false inputs
historically PIT-safe. Candidate identity ordering is not economic ranking.

## 11. Relationship to PilotUniverseService

PilotUniverseService is not an input, discovery authority or fallback. Task298
accepts an explicit universe through modern Task286. Legacy pilot semantics are
unchanged. No automatic universe expansion or M3 coverage repair is introduced.

## 12. Relationship to portfolio and ML candidates

RawPortfolioCandidate, legacy portfolio construction, ML predictions, risk
assessments and paper-trading selection are not reused or changed. Source-native
rating labels remain in full attached credit/comparability views without extra
cohort copies, ordinal mapping, preferred agency or inherited ratings. Missing
data does not gain a synthetic replacement, score or action.

## 13. Future consumer and delivery boundary

The future Investment Model must be a separate contract/task. Task299 requires
independent review and instructions. One Add Unified Candidate v1 foundation
commit and one normal push follow focused acceptance and remote concurrency
checks; no amend/rebase/force push. Report available exact-SHA CI separately from
local acceptance. Stop after delivery: no VDS, production DB/access/mutation,
live sources, deployment, production candidate audit, Investment Model, Risk
Engine, portfolio or Shadow execution occurs here. Temporary directories remain.
