# Task276 — Credit-Cohort Relative-Value Member Foundation v1

## 1. Decision

`BondCreditCohortRelativeValueMemberService.build_for_bond` returns the computed,
read-only `bond-credit-cohort-relative-value-member-v1` view for one bond and one
explicit source-native rating context. The result joins a Task275 exact cohort
key to an unadjusted Task268 duration-matched OFZ spread. Frozen Pydantic models
reject extra fields and expose typed statuses, availability, compact provenance,
capabilities and mandatory `pit_ready=false`.

## 2. Why this is a member contract, not peer distribution

The atomic member is a building block for separately designed peer aggregation.
Task276 performs no peer scan, cohort discovery, distribution, spread percentile
or ranking. It calls each dependency once for the requested bond. Task268 owns
its existing OFZ benchmark construction; Task276 does not add a bond-universe
iteration or another curve build.

## 3. Task275 dependency

Call `BondCreditComparabilityService.build_for_bond(bond_id, as_of_date)` once.
Require expected version `bond-credit-comparability-v1`, matching request identity
and the non-PIT contract. `DEPENDENCY_EVIDENCE_INVALID`, unknown status or wrong
contract makes credit comparability unavailable. A surviving key from a globally
invalid view is never used. Structurally valid `NO_COMPARABLE_RATING` remains an
available dependency contract: selected-entry gates then describe missing or
unavailable evidence. No direct Task269 call, rating history query, identity
resolution or latest/publication selection is added.

## 4. Explicit target/agency selection

Required keyword-only selectors are exact `BOND`/`LEGAL_ISSUER` and exact
ACRA/EXPERT_RA/NRA/NKR. Search only the selected target family and exact agency;
there is no default, preferred agency, alternate agency or cross-family fallback.
Validate every argument before dependency calls: exact positive int bond ID,
exact date excluding bool/datetime, supported selectors, nonblank string source
and exact nonnegative int market/curve ages excluding bool. Invalid arguments
raise `ValueError`; missing selectors raise Python `TypeError`. A missing bond
retains HTTP 404 `Bond not found` from the dependency.

Defaults are `market_source="moex"`, `max_market_age_days=7`,
`max_curve_age_days=7`. Source strings pass through unchanged; non-MOEX sources
remain supported by Task268.

## 5. Exact cohort-key integrity

Require exactly one matching entry, READY status, nonnull key and exact positive
selected event ID. Validate the key and entry target/agency against the explicit
selectors and compare provider/raw scale/raw value exactly between key and entry.
Provider and value must be nonblank strings; null or blank scale remains valid
and is copied exactly. Whitespace checks never normalize the key. Duplicate
matching entries and contradictory READY keys are `CREDIT_COHORT_EVIDENCE_INVALID`;
no first/last/max-ID selection or repair occurs.

## 6. Task268 dependency

Call `OfzReferenceCurveService.evaluate_bond` once with requested bond/date/source
and both ages. Copy target YTM/duration, reference yield, spreads, interpolation
method, curve date and target snapshot/date without recomputation. Usability
requires READY and both finite Decimal spread fields. Expected relative-value
and curve versions are `bond-relative-value-v1` and `ofz-reference-curve-v1`,
with non-PIT contracts. Contradictory READY numeric or version evidence yields
`RELATIVE_VALUE_EVIDENCE_INVALID`. No interpolation, spread subtraction, unit
conversion or presentation rounding occurs.

## 7. Dependency identity

Require both original bond IDs and dates to match the request with exact int/date
types, and relative market source to equal the exact requested source. Schema
coercion cannot make bool/string IDs or datetime evidence pass. Any mismatch has
highest priority and prevents READY. Each dependency identity remains separate
in provenance; malformed ID/date metadata is null rather than repaired.

Fields from a correctly attributed side remain available when the other side
fails. Fields of a side with wrong request identity are null. Common ISIN/SECID
are exposed only when both identities match. A retained component never makes a
blocked composition a READY member.

## 8. Relative-value semantics

Yields and `spread_to_ofz_pp` are percentage points; `spread_to_ofz_bps` is basis
points; target duration is years. Positive, zero and negative spreads are copied
exactly. No equality such as bps = pp × 100 is recalculated or revalidated here;
Task268 owns the formula. Values do not establish cheapness, attractiveness,
credit-adjusted value or a position in a peer distribution.

## 9. Availability and status

Deterministic precedence:

```text
DEPENDENCY_IDENTITY_MISMATCH
→ CREDIT_COMPARABILITY_UNAVAILABLE
→ CREDIT_COHORT_EVIDENCE_INVALID
→ SELECTED_COHORT_MISSING
→ SELECTED_COHORT_UNAVAILABLE
→ RELATIVE_VALUE_UNAVAILABLE
→ RELATIVE_VALUE_EVIDENCE_INVALID
→ READY
```

All detected limits become sorted unique flags. Preserve only selected-entry
`MULTIPLE_LATEST_EVENTS`, `RATING_VALUE_MISSING`, `PUBLICATION_TIME_UNKNOWN` and
prefixed non-READY Task268 status diagnostics, without mechanically copying flags.

Availability separately reports a structurally usable credit dependency, one
matching rating entry, a usable selected key, usable relative value/spreads and
the complete member. `has_credit_cohort_relative_value_member = status == READY`.
`has_selected_rating_entry` requires exactly one match; duplicates are invalid.
An unavailable leg can leave the other leg's availability true. Valid single
entry metadata and finite correctly attributed market inputs remain available
when composition is blocked. Nonfinite/non-Decimal numeric fields become null.

## 10. Provenance

Keep dependency contract versions and separate identities, requested selectors,
matching-entry count/status, selected event ID/date and exact provider/scale/value,
relative/curve contract versions, target snapshot/date, curve date and requested
source/date. Issuer mapping profile and canonical LegalIssuer IDs are retained.
Compact provenance can retain diagnostic claimed inputs even when top-level
attribution is blocked; the dependency identity makes their origin explicit.
Duplicate selection has no selected event. Full event/artifact objects, bytes,
rating-entry arrays and the full OFZ curve are not duplicated.

## 11. No agency preference or rating ordering

No preferred/primary/best/worst rating, target priority, agency preference table,
ordinal mapping or cross-agency equivalence exists. BOND and LEGAL_ISSUER keys
remain separate; selecting issuer context does not inherit a rating onto a bond.

## 12. No peer distribution

No cohort counts, median/mean/min/max spreads, percentile, spread versus cohort
median, z-score, peer ranking or peer universe discovery are calculated. Those
require a separately authorized aggregation design.

## 13. PIT boundary

`pit_ready=false` remains mandatory. Task275 publication/current identity
limitations and Task268 market/security-master limitations remain inherited.
Combining two non-PIT features does not establish historical observability or
backtest readiness. UNKNOWN selected publication can retain a member READY with
its factual diagnostic.

## 14. Explicitly unavailable capabilities

Capability readiness describes the implementation, separately from per-bond,
selector and date availability:

```text
SOURCE_NATIVE_RATING_COHORT_INPUT_READY=true
SPREAD_TO_OFZ_INPUT_READY=true
CREDIT_COHORT_RELATIVE_VALUE_MEMBER_READY=true
CREDIT_COHORT_PEER_DISTRIBUTION_READY=false
CREDIT_COHORT_SPREAD_PERCENTILE_READY=false
RATING_ORDINAL_MAPPING_READY=false
CROSS_AGENCY_NORMALIZATION_READY=false
UNIFIED_CREDIT_SCORE_READY=false
PD_MODEL_READY=false
CREDIT_ADJUSTED_SPREAD_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

No migration, model, API, frontend, ingestion, scoring, strategy or portfolio
behavior changes. Liquidity adjustment, expected loss and investment scores are
absent. All reads occur through dependencies under a shared `no_autoflush`
boundary. Caller-owned new/dirty/deleted state is preserved, and existing
in-session source state may influence dependency results. No result persistence,
network, live source refresh, production DB or VDS action is performed.

## 15. Verification

Disposable SQLite integration and synthetic views cover A–AU: explicit selectors,
exact keys, null/blank scale, missing/ambiguous/invalid selected evidence, globally
invalid Task275, key contradictions, signed spreads, unavailable Task268, invalid
READY spreads, identity/version mismatch, duplicates, call counts, argument
forwarding, precedence, stable serialization, input/source immutability and caller
Decimal context isolation. SQL capture, mutation guards and AST checks prove
SELECT-only execution, no own queries, no direct Task269, no peer scan and no
financial arithmetic, grade maps, agency preferences, persistence or network.

From `backend`, sequentially:

```text
pytest -q tests/test_bond_credit_cohort_relative_value_service.py
pytest -q tests/test_bond_credit_cohort_relative_value_service.py tests/test_bond_credit_comparability_service.py tests/test_ofz_reference_curve_service.py
python -m compileall app/schemas/bond_credit_cohort_relative_value.py app/services/bond_credit_cohort_relative_value_service.py
git diff --check
git diff --cached --check
```

Review working/staged diffs and verify exactly four new files. Broad backend
suite, production/VDS and live-source checks are skipped by design.

Local acceptance: focused Task276 tests **127 passed**; combined Task276/Task275/
Task268 regression **340 passed** (213 dependency tests). Compileall passed.
Final test runs reported only the existing local pytest-cache permission warning.
Working/staged diff checks and exact-scope review are required before delivery.

## 16. Handoff

Task277 — Credit-Cohort Peer Spread Distribution Foundation v1 requires
independent Task276 verification and a separate request. Task276 ends after local
acceptance, one commit, normal push, remote confirmation and at most one
exact-commit CI snapshot without waiting, polling or retry. Task277 is not started.
