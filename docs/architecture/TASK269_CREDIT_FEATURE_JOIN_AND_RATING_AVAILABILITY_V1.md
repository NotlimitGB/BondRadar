# Task269 — Credit Feature Join & Rating Availability Foundation v1

## 1. Decision

`BondCreditFeatureService(db).build_for_bond(bond_id, as_of_date)` exposes a
SELECT-only `bond-credit-feature-v1` projection. It performs no flush, commit,
writes, network or source-client calls and stores no feature view. All reads
and projection run under `Session.no_autoflush`, preserving caller pending state.
Positive exact int IDs and exact calendar dates are required; bool/datetime are
invalid and raise ValueError. Missing Bond preserves HTTP 404 / Bond not found.
Models forbid extras and freeze reassignment; nested lists remain ordinary lists,
following Task267/268 conventions. Market/OFZ availability is not a prerequisite.

## 2. Existing M2 evidence inputs

Read Bond, BondLegalIssuerProfile, LegalIssuer, CreditRatingEvent and
CreditRiskSourceArtifact. Bank inputs are CbrBankSubjectLegalIssuerProfile,
CbrBankReportingSubject, CbrBankCreditMetric, CbrBankNormalizedObservation,
CbrBankRawObservation, CbrBankReportSnapshot and CbrBankSourceArtifact.
No model, migration, API or existing ingestion changes. Alembic remains
`202609160001`. Task266 remains CLOSED_FOR_M3 with PIT, default ingestion and
cross-agency normalization incomplete; Task267/268 retain their own contracts.

## 3. Canonical Bond → LegalIssuer join

Require exact BondLegalIssuerProfile.bond_id, mapping_state=verified,
mapping_source=moex_security_reference and non-null source_issuer_id. Resolve
LegalIssuer by exact identity_source/source_issuer_id with resolution_state=verified.
No names, INN, Company, rating metadata or fuzzy resolver may establish this link.

Statuses are VERIFIED, MAPPING_MISSING, MAPPING_NOT_VERIFIED, MAPPING_CONFLICT and
LEGAL_ISSUER_NOT_VERIFIED (including absent canonical issuer). Nonverified links
retain bond evidence but expose no issuer/bank evidence. Provenance includes the
mapping profile ID/state/source/source issuer ID and verified canonical source.

## 4. Bond vs issuer rating semantics

Bond events require BOND / RESOLVED / exact bond_id. Issuer events require
LEGAL_ISSUER / RESOLVED / exact verified legal_issuer_id. There is no inheritance
in either direction. Separate blocks and availability facts preserve target kind.
No singular rating or consensus field hides issue/issuer/agency distinctions.

## 5. Per-agency latest-date rating evidence

Filter event/publication boundaries before latest-date selection. For each target
and agency, retain all eligible events on that agency's maximum event_date;
sort agency blocks alphabetically and tied events by id ASC. Multiple latest
events add the corresponding MULTIPLE_LATEST_BOND/ISSUER_RATING_EVENTS flag.
Do not choose an action/ID/provider winner or prefer an agency. ACRA, EXPERT_RA,
NRA and NKR source-native strings and scales remain unchanged and separate.

## 6. Rating publication and missingness semantics

Require event_date <= as_of_date. DATE publication must not be after the boundary;
TIMESTAMP uses its UTC calendar date. M2 stores normalize timestamps to UTC;
naive SQLite timestamps are read as saved UTC. UNKNOWN remains visible with
BOND/ISSUER_RATING_PUBLICATION_UNKNOWN and never proves historical observability.

Withdrawal/action events without rating_value_raw remain visible. Event presence
and nonempty raw value presence are separate booleans; value presence is not a
claim that an active rating exists. No raw-action state machine is implemented.
Known future publication can leave an older eligible event selected. Missing
rating is neutral availability, not bad credit.

## 7. Verified CBR bank subject join

For a verified canonical issuer inspect all current profiles with bridge_state=VERIFIED
and exact legal_issuer_id. Zero means NO_VERIFIED_SUBJECT, not proof of a non-bank;
one means VERIFIED; more than one means AMBIGUOUS_VERIFIED_SUBJECT with no metrics
or selected REGN. Without verified issuer the status is ISSUER_NOT_VERIFIED.
Read exact REGN from the linked reporting subject, never from names/sector/INN.
Provenance preserves verified candidate subject IDs and the unique bridge's
current_evidence_id. A missing subject behind a verified FK raises ValueError.
No succession, merging or historical bridge reconstruction is attempted.

## 8. Bank metric report-date selection

Read existing Task262 metrics for the exact subject REGN, with report_date <= as_of_date.
Follow outer-joined normalized/raw/snapshot/artifact FK lineage. Missing or
contradictory rows are omitted with BANK_METRIC_LINEAGE_MISMATCH, without repair.
Check REGN and raw reporting subject ID, report dates through artifact, forms
through artifact, metric source code against normalized source code, finite Decimal
metric/normalized value equality, unit equality and public disclosed value state.
Do not regenerate calculations or infer raw source-code equivalence.

Block known snapshot publication after the UTC calendar boundary. Then select
only the maximum eligible report_date across supported metrics. Older evidence
stays unselected. Return bank_report_age_days, without fresh/stale thresholds.
No eligible evidence means empty metrics and null report date/age; the verified
subject can still remain available. UNKNOWN publication has an explicit flag.

## 9. Bank metric units and evidence multiplicity

Copy metric values unchanged as Decimal: 11.25 PERCENT remains 11.25, normalized
RUB is not multiplied by 1000 again. There is no binary float math or rounding.
At the chosen date retain every eligible row, including revised artifact versions.
Order by metric_key, source_form, source_code, normalized_observation_id, metric_id.
Repeated keys add BANK_METRIC_DUPLICATE_KEY_EVIDENCE; do not average, sum or
collapse them into a canonical scalar. No economic or regulatory threshold verdict.

## 10. Provenance

Each rating item retains event/artifact IDs, provider distinct from agency,
artifact SHA256/retrieved_at, source object/target identifiers, event and
publication dates/precision, raw rating fields and event fingerprint.
Incomplete rating artifact lineage raises ValueError rather than fabricating evidence.

Each bank item retains metric ID/key/family/value/unit/fingerprint, subject/date/form/code,
normalized ID/fingerprint/dimensions/disclosure, raw ID, snapshot ID/publication/retrieval,
and artifact ID/hash. This traces exact metric → normalized → raw → snapshot → artifact.
Queries project artifact metadata only, excluding compressed/raw content bytes.

## 11. Availability and quality flags

Availability independently reports verified issuer, bond/issuer event and value,
verified bank subject and metrics, with deterministic agency lists. Missing values
remain missing, not zero. Sorted unique flags describe mapping missing/nonverified/conflict,
issuer not verified, missing ratings, unknown publication, same-day event multiplicity,
bank subject absent/ambiguous, missing metrics, metric multiplicity and lineage mismatch.
Unknown/multiplicity flags on evidence refer to selected rows; lineage mismatch
can describe excluded candidate rows. Flags are provenance/availability facts.

## 12. PIT boundary

`as_of_date` is a deterministic selection boundary. It is not proof that all
selected evidence was observable to BondRadar on that historical date.

Every result has contractual pit_ready=false. Current issuer/subject resolution
may contain later-known identity; source repositories/artifacts may revise history,
ingestion can occur later and publication timing may be UNKNOWN. Event/report
boundaries and future-publication blocking do not establish backtest-safe history.

## 13. Explicitly unavailable credit capabilities

Capabilities distinguish code support from per-bond evidence availability.
Source-native ratings and bank metric joins do not enable normalization, a unified
credit score, PD, default feature joins or credit/liquidity-adjusted spreads.
Default schema readiness remains true, default ingestion/join false. Default
events are not queried; zero production events cannot imply a default-free issuer.

## 14. Non-goals

No production/source access, deployment, backfill, writes, migrations, model/API/frontend
changes, default ingestion/inference, issuer/REGN succession, fuzzy identity, rating
normalization/ranking, credit score, PD/LGD/expected loss, bank threshold evaluation,
spread adjustment, liquidity score, modified duration, CFA, strategy, recommendation,
portfolio, Risk Engine, historical PIT reconstruction, backtest, Shadow or broker execution.
Task269 neither calls nor rewrites Task267/268 feature services.

Verification is focused Task269 pytest plus the five named direct regression tests
for rating raw/publication semantics, issue-specific evidence, Decimal metric copying,
N18 units and distinct lineage versions. Compile the two new application modules
and check working/staged diffs and four-file scope. Broad local backend, Task265,
backfill, Docker and VDS checks are skipped by design. Tests use disposable SQLite;
PostgreSQL production runtime validation is not claimed. Delivery is one commit,
normal push, verified remote SHA and at most one exact-commit CI snapshot, with
no CI waiting, polling or retry. Terminal verification remains external.

Local acceptance: 75 focused Task269 tests passed. The five selected direct
regression scenarios passed 22 parametrized tests. Both new application modules
passed compileall. SQL capture confirmed SELECT-only, no artifact-byte/default-event
queries, and preservation of clean and caller-owned pending session state.

## 15. Handoff

After local acceptance:

```text
TASK269_STATUS=CREDIT_FEATURE_JOIN_READY
CREDIT_FEATURE_JOIN_READY=true
BOND_RATING_EVIDENCE_READY=true
ISSUER_RATING_EVIDENCE_READY=true
ISSUER_TO_BOND_RATING_INHERITANCE=false
BOND_TO_ISSUER_RATING_INHERITANCE=false
BANK_CREDIT_METRIC_JOIN_READY=true
CROSS_AGENCY_NORMALIZATION_READY=false
UNIFIED_CREDIT_SCORE_READY=false
PD_MODEL_READY=false
DEFAULT_EVIDENCE_SCHEMA_READY=true
DEFAULT_INGESTION_READY=false
DEFAULT_FEATURE_JOIN_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
LIQUIDITY_ADJUSTED_SPREAD_READY=false
PIT_READY=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_ACCESS=false
LIVE_SOURCE_REQUESTS=NONE
```

Stop after delivery and the single CI snapshot. No next task is started or
authorized here; it requires independent external verification and a separate request.
