# Task266 — M2 Credit Data Readiness Closure Contract

## 1. Decision

M2 — Credit Data is **CLOSED_FOR_M3**: the production evidence foundation is
sufficient for current-state and forward analytical feature engineering. The
remaining limitations are known and bounded and do not justify blocking Market
& Bond feature development. Closure does not mean that all possible credit-data
work is finished or that historical backtesting is safe.

```text
M2_CREDIT_DATA_STATUS=CLOSED_FOR_M3
M2_PRODUCTION_EVIDENCE_FOUNDATION_READY=true
M2_PIT_READY=false
M2_DEFAULT_INGESTION_COMPLETE=false
M2_CROSS_AGENCY_NORMALIZATION_COMPLETE=false
PIT_READY=false
```

Task266 is documentation-only. Its baseline is clean `main` at
`4c3a2ab8b84a1087ab089ea9ef9d881d7fe07cc9`; the inspected migration head is
`202609160001`. No code, schema, data or existing audit is changed, and no
production query or live source request is performed.

## 2. Scope of M2

The usable foundation comprises canonical LegalIssuer/Bond identity from the
existing security master, CBR bank raw financial evidence, normalized bank
observations, supported deterministic bank credit metrics, the generic
rating/default evidence schema, exact CBR Rating Repository responses, and
distinct issuer/bond rating events from ACRA, EXPERT_RA, NRA and NKR.

The repository contracts establish implementation semantics:

- [Task261 normalization](../audits/TASK261_CBR_BANK_NORMALIZED_FINANCIAL_OBSERVATIONS_V1.md).
- [Task262 credit facts](../audits/TASK262_CBR_BANK_CREDIT_METRICS_V1.md).
- [Task263 rating/default foundation](../audits/TASK263_CREDIT_RATING_DEFAULT_EVIDENCE_FOUNDATION_V1.md).
- [Task264 ACRA adapter boundary](../audits/TASK264_ACRA_RATING_HISTORY_DATA_VERTICAL_V1.md).
- [Task265 CBR repository and identity corrections](../audits/TASK265_CBR_CREDIT_RATING_REPOSITORY_DATA_VERTICAL_V1.md).

Implementation-time audits retain their original non-production safety status.
The later operator snapshot below records production acceptance; it does not
retroactively claim that those implementation tasks queried production. Agency
attribution in the CBR collection does not prove independent agency-site adapter
coverage. Task264's separate live/source acceptance boundary remains intact.

## 3. Production evidence snapshot

The following is **operator-verified production evidence from 2026-09-17**,
supplied by the project lead. Codex has not independently queried production,
verified the backup or re-fetched these source responses during Task266.

Final code/schema and frozen PLAN:

```text
CODE_SHA=4c3a2ab8b84a1087ab089ea9ef9d881d7fe07cc9
DB_REVISION=202609160001
PLAN_HASH=5700fd2bf730b58746196c05eeeaaaf79f35db85f07bb807cb894ecd36b05096
UNIVERSE_SHA256=808f86a043616a3bbb802bb73ef546e388e5cf70bfe084c134e828c385cffd28
ISSUER_UNIVERSE_COUNT=496
ISSUER_QUERY_ELIGIBLE_COUNT=494
ISSUER_QUERY_INELIGIBLE_COUNT=2
ISSUER_QUERIES_ATTEMPTED=494
ISSUER_QUERIES_SUCCEEDED=494
ISSUER_QUERIES_NO_RESULTS=53
BOND_UNIVERSE_COUNT=2995
SEARCH_PAGES_FETCHED=547
SEARCH_ROWS_SEEN=3351
UNIQUE_OBJECT_IDS_SEEN=2397
OBJECT_HISTORIES_FETCHED=1559
HISTORY_ROWS_SEEN=9836
IDENTITY_UNRESOLVED_ROWS=0
IDENTITY_AMBIGUOUS_ROWS=0
SEMANTIC_COLLISION_ROWS=0
UNKNOWN_AGENCY_ROWS=0
```

Final evidence and distributions:

```text
SOURCE_ARTIFACTS=2054
RATING_EVENTS=9829
DEFAULT_EVENTS=0
BOND=6743
LEGAL_ISSUER=3086
ACRA=4823
EXPERT_RA=4225
NKR=592
NRA=189
RESOLVED=9829
DISTINCT_EVENT_FINGERPRINTS=9829
ISSUER_COVERAGE_COUNT=367
ISSUER_COVERAGE_PCT=73.99193548387096774193548387
BOND_COVERAGE_COUNT=1187
BOND_COVERAGE_PCT=39.63272120200333889816360601
EVENT_MIN_DATE=2016-11-15
EVENT_MAX_DATE=2026-09-16
MEDIAN_EVENTS_PER_MATCHED_TARGET=5
```

The target totals `6743 + 3086` and agency totals `4823 + 4225 + 592 + 189`
each reconcile to 9,829 events. Event counts are not counts of unique targets.
The covered-target counts use the full frozen universes, including the two
query-ineligible issuer identifiers. Fifty-three explicit no-result queries are
measured source outcomes, not transport errors or evidence of poor credit.

Operator-reported APPLY and operational safety:

```text
COMMIT_COMPLETED=true
COMMIT_OUTCOME_UNKNOWN=false
RECONCILIATION_REQUIRED=false
NETWORK_SOURCE_REQUESTS_DURING_APPLY=0
BACKEND_STATUS=running
BACKEND_RESTART_COUNT=0
HEALTH=PASS
SMOKE=PASS
BACKUP_NAME=task265-cbr-ratings-pre-apply-20260917T180412Z.dump
BACKUP_SHA256=c8f94be9867e69eaf6920e3f0765f700409e8f47e68c5bf8178925e8a2d07379
BACKUP_BYTES=291423291
```

Backup identity is operational evidence only. Its server location is not a
reproducible repository dependency. These prior APPLY facts are not actions
executed or authorized by Task266.

## 4. Bank financial evidence readiness

Raw evidence preserves exact compressed artifact bytes, selected DBF field
lexical text, finite Decimal/null values, disclosure state, dimensions, source
dates and immutable fingerprints. Blank/unavailable values remain distinct from
Decimal zero; neither missing values nor publication dates are fabricated.

Task261 normalization is a separate immutable layer. Forms `0409101`, `0409102`
and `0409123` convert verified `RUB_THOUSANDS` values by exact Decimal × 1000 to
RUB. Form `0409135` preserves PERCENT unchanged, without division by 100. Report
date and nullable source date are copied independently. No rounding, float,
account aggregation or guessed economic field is introduced.

Task262 provides deterministic direct source-value credit facts for supported
codes in `0409123` and `0409135`, including historical `N18`. It copies normalized
values without another scaling step, threshold or score. Forms 101/102 remain
normalized source evidence, not inferred credit metrics. Code 123/105 has no
invented economic label. Unsupported public metric codes fail closed; unavailable
values do not create a metric or become zero.

The version lineage remains metric → normalized observation → raw observation
→ report snapshot → exact source artifact SHA-256. REGN identifies the reporting
subject; raw evidence existence does not imply a BondRadar issuer match. Any
downstream issuer join requires its own verified exact identity evidence.

The Task261 audit proves 38,842 approved-fixture raw/normalized rows on disposable
databases; that is not a production inventory count. The inspected implementation
audits and supplied closure snapshot do not establish exact final production bank
row totals, so none are asserted here. Layer readiness must not be mistaken for
universal bank, month or item coverage.

## 5. Rating evidence readiness

The technical provider is `CBR_RATINGS`; the actual rating agency is independently
ACRA, EXPERT_RA, NRA or NKR. Immutable exact repository responses support raw
rating value, outlook, watch, action, event date and source identifiers without
interpretation. Withdrawal is a rating event, not a default event.

CBR event scale remains `rating_scale_raw=null` because this source does not
establish it. Direct-agency evidence retains its own explicit scale requirements.
Values such as `AAA(RU)` and `ruAAA` remain source-native strings, not equivalent
ordinal grades or unified scores.

Visible source dates are stored with publication precision `DATE`:
`publication_date` is set and `publication_at` is null. No midnight timestamp or
timezone is invented. Retrieval time is separate transport provenance. The event
range 2016-11-15 through 2026-09-16 describes exposed records, not a complete
historical observation log or exhaustive issue history.

Issuer coverage is approximately 73.99% and bond coverage approximately 39.63%.
Uncovered targets are not erroneous or low-quality merely because a rating was
not found. Future features must distinguish issue rating available, issuer rating
available, both available, and unrated/unavailable. Task266 implements neither
fallback nor issuer-to-bond inheritance.

## 6. Identity and provenance contract

Issuer resolution uses exact LegalIssuer INN and verified identity semantics;
bond resolution uses exact ISIN. Names are evidence metadata only. Fuzzy matching,
transliteration, name-based joins and issuer-wide propagation are absent.

The FIX8 invariant is:

> CBR `objectId` binds to canonical resolved target identity. Raw source INN
> differences are evidence metadata and are not part of canonical target identity.

Canonical target disagreement still blocks readiness. Raw identifiers and
provenance remain preserved; this is not permission to ignore contradictory target
resolution. Issuer and bond events are separate facts even when related by a
verified security-master relationship.

Exact response hashes preserve artifact versions. CBR-origin reconciliation can
reuse the first immutable event linkage for identical source facts from a later
response version; changed facts create new evidence, while changed resolution or
ambiguous immutable lineage is a collision. Consumers must preserve this lineage
rather than treating response count as logical event count.

## 7. Point-in-time boundary

**PIT_READY=false. M2 closure is not historical point-in-time acceptance.**

For bank history, current/restated payloads cannot be treated as contemporaneous
snapshots merely because they carry an old report date. Original publication
timing and revision lineage are incomplete. [Task259B](../audits/TASK259B_CBR_BANK_EXTERNAL_ARCHIVAL_AVAILABILITY_AUDIT.md)
proved raw-data revisions in 0409102/2024-04-01, 0409135/2025-06-01 and
0409123/2025-10-01; later versions must not leak into earlier decisions.

[Task260A](../audits/TASK260A_CBR_HISTORICAL_ARTIFACT_VERSIONING_AND_PIT_SELECTION_CONTRACT.md)
and [Task260B](../audits/TASK260B_CBR_HISTORICAL_ARTIFACT_VERSION_PERSISTENCE_AND_FORWARD_OBSERVATION_FOUNDATION.md)
separate `(form, report_date, artifact_sha256)` version identity from availability.
Only exact-payload-bound evidence establishes a conservative `safe_known_from`;
an eligible boundary must be at or before `as_of`. Capture/retrieval proves
availability no later than observation, not first publication. Missing evidence
does not permit a current/restated fallback. Sparse archives can yield stale
last-proven versions; first-observation boundaries do not reconstruct A→B→A
active state. Existing availability infrastructure alone does not close PIT.

For ratings, source event/publication dates in a present-day repository retrieval
do not prove that every record was observable by BondRadar at its historical
instant. The collection is not a complete PIT reconstruction. `retrieved_at` is
never publication time, and a historical event date cannot authorize look-ahead.

M3/M7 may use this foundation for current credit assessment, exploratory features,
feature-engineering design and descriptive historical research. Forward Shadow
inputs are permissible only subject to separately authorized downstream execution
gates. Leakage-safe historical backtests require a separate PIT validation gate;
Task266 authorizes no strategy, trading or historical reconstruction execution.

## 8. Known deferred capabilities

```text
DEFAULT_EVIDENCE_SCHEMA_READY=true
DEFAULT_EVENTS_PRODUCTION_COUNT=0
DEFAULT_INGESTION_READY=false
RAW_RATING_EVIDENCE_READY=true
CROSS_AGENCY_NORMALIZATION_READY=false
UNIFIED_CREDIT_SCORE_READY=false
PD_MODEL_READY=false
```

The MOEX bond-only default schema exists, but production default ingestion remains
deferred. `TECHNICAL_DEFAULT` is distinct from `DEFAULT`; neither propagates to
an issuer-wide fact. Zero stored default events means no populated default dataset,
not proof of a default-free universe. This gap does not block M3, but prevents
claims of robust default-history coverage or validated default probability.

Cross-agency normalization, rating scoring, PD modelling and historical PIT
reconstruction require separate contracts. No mapping table, model or source
adapter is added here. Query eligibility excludes two issuer identifiers from
Russian-INN searches without removing them from the full universe; foreign issuer
and related bond coverage through that search field is not guaranteed.

## 9. M3 allowed inputs

M3 may consume exact Bond ISIN, canonical LegalIssuer identity and verified
Bond ↔ LegalIssuer relationships. Credit inputs include source-native rating
events, explicit latest/current observations, separate issuer/issue availability,
supported deterministic bank metrics and appropriate normalized observations.

Metadata includes event date, agency, source target kind, raw rating value,
outlook/watch/action where available, disclosure state and missingness. Consumers
must preserve provenance, units, source codes/dimensions and exact artifact-version
lineage. REGN evidence requires a verified join before becoming issuer evidence.
Current-state selection must have explicit semantics; no undocumented latest-row
or issuer-rating fallback is supplied by this closure contract.

## 10. M3 prohibited assumptions

M3 must not implicitly:

1. Treat `PIT_READY=false` data as leakage-safe historical backtest features.
2. Convert agency ratings into one score without an explicit normalization contract.
3. Assume an issuer rating equals an issue rating or copy it as an issue fact.
4. Interpret a missing rating as bad credit.
5. Invent default events or infer default absence from empty tables.
6. Derive PD from rating text without a dedicated model and contract.
7. Resolve issuer/bond identities through fuzzy or name matching.
8. Let an LLM become the source of deterministic credit truth.
9. Replace missing source data silently with fabricated values.

## 11. M2 closure declaration

M2 is closed for the transition to M3 because immutable source evidence, canonical
target identity, explicit transformations and known missingness provide a bounded
analytical foundation. Remaining default, normalization and historical-availability
gaps are retained as constraints, not hidden acceptance claims.

```text
M2_CREDIT_DATA_STATUS=CLOSED_FOR_M3
M2_PRODUCTION_EVIDENCE_FOUNDATION_READY=true
M2_PIT_READY=false
M2_DEFAULT_INGESTION_COMPLETE=false
M2_CROSS_AGENCY_NORMALIZATION_COMPLETE=false
CODE_CHANGED=false
SCHEMA_CHANGED=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_ACCESS=false
LIVE_SOURCE_REQUESTS=NONE
```

The last five flags describe Task266 execution, not the prior operator APPLY.
This document neither fills missing evidence nor grants runtime permissions.

## 12. Handoff to Task267

Next milestone: **M3 — Market & Bond/CFA Features**.
Immediate next task: **Task267 — Bond Market Feature Foundation v1**.

Begin with exact feature definitions and data-source inventory, not model scoring.
Conceptual families are yield/YTM where valid, duration, modified duration, coupon
structure, maturity, amortization, offer/put/call structure, OFZ/reference-curve
spread, relative yield/spread, liquidity, bond structural risk flags, issuer/issue
rating availability, deterministic credit-evidence joins, and missingness/provenance.
OFZ may supply a reference curve; corporate issuers remain the primary universe.

This is a handoff only: no Task267 implementation, CFA implementation, scoring,
strategy or production execution begins in Task266. Each requires its own scope
and authorization; the PIT limitation remains inherited.
