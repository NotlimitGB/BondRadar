# Task275 — Source-Native Credit Comparability & Rating Cohort Foundation v1

## 1. Decision

`BondCreditComparabilityService.build_for_bond(bond_id, as_of_date)` returns a
computed, read-only `bond-credit-comparability-v1` view. Comparability means exact
source-native label equality. Implementation readiness is separate from whether
a particular bond has any available cohort key. Models are frozen, reject extra
fields, use typed statuses/agencies, and require `pit_ready=false`.

## 2. Why cross-agency normalization remains unavailable

No evidence-backed agency equivalence, scale inference, modifier conversion or
economic credit ordering is introduced. `AAA(RU)`, `ruAAA` and `AAA` remain
different labels. Equal keys do not establish equal default probability or risk.

## 3. Task269 dependency

The service validates exact positive integer bond ID and exact calendar date
before calling `BondCreditFeatureService.build_for_bond` exactly once under a
shared `Session.no_autoflush` boundary. Bool/datetime arguments are rejected with
`ValueError`; a missing bond retains HTTP 404 `Bond not found`. No independent
SELECT, rating history, publication/latest selection or identity resolution is
performed. Task269 owns all evidence selection. Caller-owned pending ORM state
is preserved; Task269 can observe current in-session identity values.

## 4. Bond versus issuer rating separation

`bond_rating_entries` and `issuer_rating_entries` are independent. Their keys use
`BOND` and `LEGAL_ISSUER` respectively. No absent-agency placeholders are added.
Issuer evidence requires consistent verified linkage, a positive canonical
LegalIssuer ID and issuer profile ID, and verified-issuer availability. An
issuer linkage contradiction invalidates affected issuer entries while a valid
bond entry can retain its key.

## 5. Exact source-native cohort key

`SourceNativeRatingCohortKey` contains exactly:

```text
target_kind
rating_agency
source_provider
rating_scale_raw
rating_value_raw
```

The comparison method is `EXACT_SOURCE_NATIVE_LABEL`. All five fields must match
exactly for key equality. The key is a structured descriptor, not a concatenated
string or numeric credit grade.

## 6. Agency/provider/scale/value semantics

Agency and provider are separate key components. ACRA, EXPERT_RA, NRA and NKR
remain distinct; CBR_RATINGS and ACRA providers remain distinct. Strings retain
case, whitespace, prefixes, suffixes, scripts and modifiers. `.strip()` is used
only to test nonblank presence. Null scale is allowed; blank scale is retained
exactly with `has_declared_rating_scale=false`. No scale is inferred. A missing
or blank provider and non-string raw key fields are structural errors.

## 7. Latest-event ambiguity

A structurally valid group requires nonempty events, exact integer count equal
to list length, matching group/event agency, expected target kind and equal
latest/event dates. READY requires exactly one event. Multiple latest events
remain `MULTIPLE_LATEST_EVENTS` even with identical labels: no first/last/max-ID
selection, label deduplication or action-state interpretation occurs.

Duplicate agency groups within a target family remain separate
`EVIDENCE_INVALID` entries, each retaining its diagnostic event IDs. Entries
sort by agency and then diagnostic date/event IDs. These fields establish output
order, without comparing rating values or choosing a preferred agency.

## 8. Missing rating values

A valid single event with null or whitespace-only value produces
`RATING_VALUE_MISSING` and a null key. Selected event ID, provider, raw fields and
compact event provenance remain available. Outlook, watch and action fields do
not replace a rating value. Multiple/invalid groups have null selected event and
key, while retaining all usable diagnostic IDs.

## 9. Publication semantics

Selected evidence preserves publication precision/date/timestamp and artifact
retrieval time exactly from Task269. UNKNOWN publication can retain READY;
`PUBLICATION_TIME_UNKNOWN` on entries and `RATING_PUBLICATION_UNKNOWN` on the
view are factual diagnostics. Task275 does not refilter publication dates or
claim that an action establishes an active rating.

## 10. Availability and status

Entry priority is `EVIDENCE_INVALID` → `MULTIPLE_LATEST_EVENTS` →
`RATING_VALUE_MISSING` → `READY`. Overall priority is
`DEPENDENCY_EVIDENCE_INVALID` → `READY` → `NO_COMPARABLE_RATING`.
Any structural contradiction makes the overall view invalid. Local errors
invalidate only affected groups; unaffected groups retain READY keys. A
dependency request identity or contract contradiction invalidates both families.

Availability independently exposes evidence presence, comparable cohort presence
and sorted unique comparable agencies for both targets. Evidence presence means
at least one usable event ID was returned, including ambiguous/invalid evidence.
`has_any_comparable_cohort` may be true while the overall status is invalid if an
unaffected key survives. Flags are sorted and unique, and carry only relevant
rating diagnostics; bank diagnostics are excluded.

## 11. Provenance

Provenance retains the Task269 contract version and bond/date identity, requested
date, issuer mapping profile ID, canonical LegalIssuer ID/link status, diagnostic
and selected event IDs separately by target, and represented agencies. Selected
event evidence retains artifact ID/hash/retrieval time, source object ID,
event fingerprint, native fields and publication metadata. Full artifact bytes
and bank metrics are absent. Invalid numeric/date metadata is represented by
null rather than repaired or coerced into usable evidence.

## 12. No ordinal credit interpretation

No grade ordering, notches, investment-grade boundary, preferred agency, numeric
rating score, unified credit score, PD or default inference is produced. Keys
support equality only. There is no credit-cohort spread distribution or adjusted
spread calculation.

## 13. Legacy and inheritance boundaries

Legacy Company/Bond ratings or scores are not comparability inputs. The service
does not persist keys or change ingestion, models, migrations, API, frontend,
strategy or portfolio behavior. Issuer-to-bond and bond-to-issuer inheritance are
both unavailable.

```text
CREDIT_FEATURE_INPUT_READY=true
SOURCE_NATIVE_RATING_COHORT_KEYS_READY=true
EXACT_RATING_LABEL_COMPARABILITY_READY=true
RATING_SCALE_INFERRED=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
UNIFIED_CREDIT_SCORE_READY=false
PD_MODEL_READY=false
ISSUER_TO_BOND_RATING_INHERITANCE=false
BOND_TO_ISSUER_RATING_INHERITANCE=false
CREDIT_COHORT_RELATIVE_VALUE_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PIT_READY=false
```

## 14. PIT boundary

`pit_ready=false` is mandatory on every view and in capabilities. UNKNOWN
publication, current identity mappings, historical source availability and
revisions remain inherited limitations. Exact source-native equality does not
prove historical observability.

## 15. Verification

Disposable SQLite integration and synthetic Task269 views cover A–AK, exact raw
strings, null/blank scales, target/agency/provider separation, ambiguity and
missing values, structural contradictions, duplicate groups, surviving keys,
request/linkage integrity, status priority, validation, stable serialization and
input immutability. SQL capture and mutation guards prove SELECT-only execution
through Task269 and preservation of caller-owned new/dirty/deleted state with
autoflush enabled. AST checks prohibit direct queries, network, grade maps and
scoring. Verification commands from `backend`:

```text
pytest -q tests/test_bond_credit_comparability_service.py
pytest -q tests/test_bond_credit_comparability_service.py tests/test_bond_credit_feature_service.py
python -m compileall app/schemas/bond_credit_comparability.py app/services/bond_credit_comparability_service.py
git diff --check
git diff --cached --check
```

Working/staged diffs must contain exactly the four new Task275 files. Full backend
suite, production/VDS and live-source verification are skipped by design.

Local acceptance: focused Task275 tests **103 passed**; combined Task275/Task269
regression **178 passed** (75 Task269 tests). Compileall passed. Both test runs
reported only the existing local pytest-cache permission warning; no test failed
in the final runs. Diff checks and staged review are required before delivery.

## 16. Handoff

Task276 — Credit-Cohort Relative-Value Foundation v1 requires independent Task275
verification and a separate request. Task275 stops after local acceptance, one
commit, normal push, remote SHA confirmation and at most one exact-commit CI
snapshot without waiting, polling or retry. Task276 is not implemented here.
