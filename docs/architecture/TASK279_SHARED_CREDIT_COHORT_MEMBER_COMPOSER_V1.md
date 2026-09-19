# Task279 — Shared Credit-Cohort Relative-Value Member Composer Foundation v1

## 1. Decision

Task279 extracts Task276 composition into the pure
`compose_credit_cohort_relative_value_member()` function. It combines one
already-built Task275 credit comparability view and one already-built Task268
relative-value view using explicit target-family and agency selectors. The
result remains the existing computed Task276 member and is not persisted.

`BondCreditCohortRelativeValueMemberService.build_for_bond()` retains its
public interface and validation. It loads Task275 once, loads Task268 once under
the shared `no_autoflush` boundary, and invokes the composer once.

## 2. Performance / duplication problem

Previously Task276 loaded both dependencies and implemented identity, cohort,
spread, status, attribution and provenance rules inside the DB-aware service.
A future batch builder could not reuse those rules without calling Task276 and
therefore rebuilding an OFZ curve per bond, or copying Task276 logic.

The extracted boundary permits future code to combine prebuilt inputs after a
single shared curve evaluation path. Task279 itself does not build a batch,
discover a universe or call the peer-distribution reducer.

## 3. Existing Task276 semantics

The authoritative output remains
`bond-credit-cohort-relative-value-member-v1`. Its schema, capabilities and
public statuses are unchanged. Existing precedence remains:

```text
DEPENDENCY_IDENTITY_MISMATCH
-> CREDIT_COMPARABILITY_UNAVAILABLE
-> CREDIT_COHORT_EVIDENCE_INVALID
-> SELECTED_COHORT_MISSING
-> SELECTED_COHORT_UNAVAILABLE
-> RELATIVE_VALUE_UNAVAILABLE
-> RELATIVE_VALUE_EVIDENCE_INVALID
-> READY
```

For legitimate existing inputs, identity, selected rating, cohort key, market
fields, spread, availability, flags, provenance, capabilities and PIT state are
identical before and after extraction.

## 4. Pure composer

```python
compose_credit_cohort_relative_value_member(
    credit: BondCreditComparabilityView,
    relative: BondRelativeValueView,
    *,
    bond_id: int,
    as_of_date: date,
    target_kind: RatingTargetKind,
    rating_agency: RatingAgency,
    market_source: str,
) -> BondCreditCohortRelativeValueMemberView
```

The request requires an exact positive int ID excluding bool, an exact calendar
date excluding datetime, a supported exact target kind and agency, and a
nonblank source without normalization. Both inputs must be their authoritative
schema object types. Invalid direct inputs raise `ValueError` before composition.

The composer copies dependency facts. It does not calculate yield, reference
yield, interpolation or spread and performs no financial arithmetic.

## 5. Explicit selector semantics

`BOND` inspects only bond entries; `LEGAL_ISSUER` inspects only issuer entries.
Within that family only the explicitly requested agency matches. There is no
fallback family, preferred agency, automatic selection, rating order or
issuer-to-bond inheritance.

Zero matches returns `SELECTED_COHORT_MISSING`. One non-READY match returns
`SELECTED_COHORT_UNAVAILABLE`. More than one match returns
`CREDIT_COHORT_EVIDENCE_INVALID`; no row is selected or deduplicated.

## 6. Credit dependency contract

The expected contract is `bond-credit-comparability-v1` with `pit_ready=False`.
Only `READY` and `NO_COMPARABLE_RATING` are structurally usable dependency
statuses. `DEPENDENCY_EVIDENCE_INVALID`, version drift, PIT contradiction or an
unknown status returns `CREDIT_COMPARABILITY_UNAVAILABLE` after the higher
identity gate.

Credit ID and as-of date compare using original exact types. Independent
correctly attributed rating fields may remain visible in blocked states exactly
as in Task276; a blocked join never becomes a READY member.

## 7. Cohort-key integrity

A READY selected entry requires one source-native key, exact requested family
and agency on both entry and key, exact positive selected event ID, nonblank
provider/value, and null-or-string raw scale. Provider, scale and value on the
key must exactly equal the entry.

Whitespace is used only to determine required-field presence. Case, spaces,
prefixes, suffixes and modifiers are preserved. Null and blank raw scale retain
their existing distinct Task276 meanings. Output receives a strict detached
copy of a usable key. Contradictions are not repaired.

## 8. Relative-value dependency contract

Required versions are `bond-relative-value-v1` and
`ofz-reference-curve-v1`; both relative and nested curve PIT declarations must
be false. A READY relative dependency requires finite Decimal pp and bps
spreads. Nonfinite, missing or wrong-type spread evidence returns
`RELATIVE_VALUE_EVIDENCE_INVALID`.

A non-READY Task268 result returns `RELATIVE_VALUE_UNAVAILABLE` and its public
status is exposed with the `RELATIVE_VALUE_` prefix. Nested Task268 quality
flags are not mechanically copied. The composer never recomputes or checks a
numeric relationship between Task268 fields.

## 9. Identity consistency

Credit bond ID/date and relative bond ID/date/source must independently equal
the requested context with their original types. Any mismatch has the highest
status priority. Credit fields lose attribution only for a credit mismatch;
relative fields lose attribution only for a relative mismatch. Shared ISIN and
SECID require both identities to match.

Provenance keeps both dependency identities separately, including unusable
original evidence represented by stable nullable types.

## 10. Status and availability

All simultaneously detected constraints become sorted unique factual flags;
the first condition in the fixed precedence becomes status. Only the three
rating diagnostics `MULTIPLE_LATEST_EVENTS`, `RATING_VALUE_MISSING` and
`PUBLICATION_TIME_UNKNOWN` may flow from selected entries. Bank and unrelated
dependency flags are excluded.

Availability independently records usable credit, selected entry, usable key,
usable relative value and usable spread. Final member availability is exactly
`status == READY`. Absence or invalidity is not a credit interpretation.

## 11. Provenance

The existing provenance is preserved: credit contract and identity, relative
identity, requested selectors, selected count/status/event/date/provider/raw
labels, LegalIssuer profile and issuer IDs, relative/curve versions, target
snapshot/date, curve date, requested source and as-of date.

Full Task275 entries, selected-event artifacts and nested OFZ curve nodes are
not duplicated into the Task276 member. Input objects and their nested lists are
not modified.

## 12. Behavioral equivalence

Task276 retains the same validation order, dependency argument forwarding,
HTTP 404 behavior, one Task275 call and one Task268 call. It delegates pure
composition exactly once and contains no local cohort-key validator, status
matrix or member-provenance constructor.

Integration tests compare complete `model_dump()` output from direct composition
and service composition for READY and every main unavailable/status family.
All original Task276 tests continue to pass unchanged in expectation.

## 13. Pure/read-only boundary

The composer imports schemas and Python date/Decimal/type helpers only. It has
no Session, SQLAlchemy, DB models, services, source clients, network, SELECT or
mutation calls. It performs no persistence or cache access.

The loader service remains SELECT-only under `no_autoflush`; SQL capture and
pending-state tests continue to protect caller-owned `new/dirty/deleted` state.
There is no migration, model, API or frontend change.

Required capability state:

```text
PURE_MEMBER_COMPOSER_READY=true
BATCH_MEMBER_BUILD_READY=false
PEER_UNIVERSE_DISCOVERY_READY=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
PEER_RANKING_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

## 14. PIT boundary

Every Task276 member remains `pit_ready=False`. Extracting deterministic
composition does not reconstruct historical identity, publication visibility,
Security Master state or a historical peer universe.

## 15. Verification

Pure tests cover explicit family/agency selection, missing/unavailable/duplicate
entries, key contradictions, exact raw strings, dependency status/version/PIT
and identity gates, spread evidence, precedence, provenance, stable serialization,
input immutability, Decimal-context isolation and AST purity.

Local acceptance:

```text
Task279 pure composer: 72 passed
Task279 + Task276 regression: 208 passed
Task277 downstream regression: 146 passed
compileall: passed
```

Pytest reported only the existing cache-permission warning. Full backend,
Task278, production/VDS and live-source checks are skipped by the Task279
contract. Working/staged diff checks and exact five-file review are required
before delivery.

## 16. Handoff

Only Task280 — Efficient Credit-Cohort Batch Member Builder v1 may follow an
independent Task279 verification and separate request. It may reuse Task278 and
Task279 for an explicit bond-ID universe. Task279 does not implement batch
loading, shared-curve orchestration, peer discovery or Task277 orchestration;
their readiness remains false here.
