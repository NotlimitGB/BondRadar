# Task296C — Frozen T-Invest Bond Import Batch & Read-Only Preflight v1

## 1. Purpose

Task296C turns a frozen Task296B import-candidate manifest and explicitly
supplied MOEX, Bond, and Company projections into a deterministic preflight.
It records whether each candidate is ready for a separately authorized
controlled import, needs review, is already present, or has an identity
conflict. It never executes an import.

## 2. Frozen source boundary

The sole candidate source is `TInvestBondAdmissionManifestView.import_candidate_manifest`.
Task296C does not discover or add instruments from Task295, MOEX, a database,
or another universe. It carries the Task296B admission-row and candidate-ISIN
hashes forward so a later production gate can compare fresh evidence with an
operator-authorized expected hash.

## 3. Production observations are context only

The Task296C request supplied this observed batch summary:

| Observation | Reported value |
| --- | ---: |
| T-Invest BASE bonds | 1,618 |
| Existing matched UIDs | 1,336 |
| Existing unique Bonds | 1,305 |
| Unmatched UIDs | 282 |
| Unmatched unique ISINs | 274 |
| Import-candidate unique ISINs | 131 |
| Import-candidate UIDs | 131 |
| Review-required unique ISINs | 127 |
| MOEX-not-resolved unique ISINs | 16 |
| Identity-conflict unique ISINs | 0 |

The reported Task296B candidate-ISIN-set SHA-256 is
`075ecfd35c4ca94e76e6dda472b3f64a4507d4f52d5275a197d7d6161bf1c84`.
The request did not specify the observation timestamp. These are historical
operator evidence, not runtime constants, assertions, or authorization. The
planner hashes the actual supplied manifest on every invocation.

## 4. Exact candidate identity

Each candidate must have one supplied MOEX description projection. A ready
row requires exact equality between Task296B's candidate ISIN and the
description ISIN, and between its matched SECID and the description SECID.
Ticker, name, issuer, normalized identifiers, and other fallback matching
are prohibited. Missing or duplicate description projections fail closed or
remain explicitly represented as typed invalid evidence; they may never be
silently omitted.
When a single projection is supplied, the candidate row retains that exact
frozen projection alongside parsed values; duplicate projections are never
arbitrarily selected. Raw HTTP response bodies are excluded.

## 5. Board corroboration

Task296B's exact `primary_board == "TQCB"` is necessary but not sufficient.
Task296C also requires a fresh description projection requested specifically
with `board="TQCB"` and `board_observed == true`.

```text
Task296B primary_board == TQCB
AND requested_board == TQCB
AND board_observed == true
    => board gate passes
```

Missing board observation is `BOARD_OBSERVATION_MISSING`; an explicitly
observed `primary_board` that is not TQCB is `BOARD_CONFLICT`. A missing
description `primary_board` is not itself a contradiction when Task296B's
exact primary-board evidence and the fresh requested-board observation both
pass. Neither missing nor contradictory board evidence is inferred from a
name or ticker.

## 6. Currency and nominal value

Task296B's RUB candidate classification must be corroborated by MOEX
description currency using the existing `canonicalize_moex_currency()`
semantics. Only canonical `RUB` passes. Trading currency is not a fallback
for nominal currency. Nominal value must parse as finite and strictly positive;
missing, malformed, non-finite, zero, or negative values require review. The
source representation and parsed Decimal evidence remain distinguishable.

## 7. Existing-Bond collision gate

The planner compares the candidate SECID and ISIN against the supplied
internal Bond identity projections. If either identifier now exists, the
candidate cannot be marked ready and is reported as
`ALREADY_IMPORTED_OR_COLLISION`. If SECID and ISIN resolve to different Bond
IDs, the result is `IDENTITY_CONFLICT` with `INTERNAL_IDENTITY_CONFLICT`.
This detects drift since Task296B without reading or changing a database.

## 8. Issuer and Company resolution preview

Issuer name and INN are required. INN formatting follows the existing
importer's narrow behavior: trim outer whitespace and require a nonblank
value no longer than 16 characters; Task296C does not add checksum or
digits-only validation. Issuer name follows the importer's trim and
255-character bound. Placeholder issuers are never authorized.

The immutable Company projections are consulted deterministically: exact INN
first; otherwise one unique normalized-name match; otherwise `CREATE_NEW`.
Name normalization for matching is lowercase plus collapsed whitespace. If
the exact INN identifies one Company but the normalized name identifies a
different Company, return `COMPANY_IDENTITY_CONFLICT`; choose neither. No
Company is created, updated, or otherwise persisted.

## 9. Required security fields

A ready import row requires exact nonblank ISIN and SECID, a name or
shortname, canonical RUB currency, positive finite nominal value, and either
a valid maturity date or explicit `is_perpetual == true`. Missing both
`name` and `shortname` is reported as `BOND_NAME_MISSING`. A perpetual
instrument does not need a maturity date. Perpetual status is never inferred
from a name.

Coupon rate is preserved as evidence but does not gate readiness; missing
coupon rate is not interpreted as zero. Offer date, subordination, and
amortization evidence is also preserved without adding new import gates.
Missing `has_amortization` remains unknown.

## 10. Active/traded semantics

Task296C matches the current importer’s soft activity semantics:

| Evidence | Preflight behavior |
| --- | --- |
| Explicit `is_traded == false` | Block; `NOT_ACTIVE_OR_NOT_TRADED` |
| Explicit importer-recognized inactive status | Block; `NOT_ACTIVE_OR_NOT_TRADED` |
| Explicit `is_traded == true` | Allow this gate |
| Missing `is_traded` and no recognized inactive status | Allow, with `ACTIVE_STATUS_UNKNOWN` diagnostic |

The unknown case is never silently rewritten to active. It is diagnostic, not
a readiness blocker. Recognition of inactive status uses the current
importer’s established inactive status values; Task296C does not invent a
new status vocabulary.

## 11. Task295 availability and required tests

Per-UID availability, trade, buy, sell, qualification, and `required_tests`
facts are retained from the Task296B UID admissions. They are not collapsed
into a single personal-eligibility verdict. `required_tests` does not gate
security-master import readiness. Ready rows retain the UID admission records
as well as the deterministic compact `required_tests` summary, so differing
requirements across multiple UIDs remain auditable. Personal qualification and execution
eligibility remain separate concerns.

## 12. Structured and SFO-like names

Names such as `СФО`, `Кредитный поток`, and `Ипотечный поток` do not trigger
security-type classification or rejection. Task296C uses explicit supplied
source fields only and makes no name-based SFO inference. A completed import
preflight does not imply M3, credit, or issuer evidence readiness.

## 13. States and reasons

Per-candidate states are:

- `READY_FOR_CONTROLLED_IMPORT` — every required evidence gate passed;
- `REVIEW_REQUIRED` — evidence is absent, invalid, or requires review;
- `ALREADY_IMPORTED_OR_COLLISION` — an identifier is already represented;
- `IDENTITY_CONFLICT` — supplied exact identifiers contradict each other.

Reason codes are sorted and unique. They include exact description identity,
missing/conflicting description, security identity, board observation or
conflict, missing bond name, currency, internal collision, issuer, Company
resolution, nominal, maturity/perpetual, activity, unknown activity, and ready
outcomes. The full
reason set is emitted per row and in deterministic batch breakdowns.

## 14. Deterministic batch and hashes

The frozen batch identity carries the Task296B manifest version and hashes,
candidate count, sorted candidate ISINs and SECIDs, plus hashes for the
candidate ISIN set, candidate SECID set, and canonical candidate rows. Ready
execution rows sort by `(isin, secid)`; review rows sort by ISIN. SHA-256
inputs use canonical, timestamp-free JSON; maturity and offer evidence is
date-only, and no execution timestamp is introduced. The result exposes:

- `candidate_isin_set_sha256`;
- `candidate_secid_set_sha256`;
- `candidate_row_set_sha256`;
- `ready_for_import_manifest_sha256`;
- `review_manifest_sha256`.

The supplied Task296B hash is retained as provenance; it is not replaced by a
Task296C-generated value.

## 15. Summary, capabilities, and limitations

The summary reports candidate, ready, review, collision, and identity-conflict
counts; existing-Company-by-INN, existing-Company-by-name, and new-Company
plans; and missing nominal, missing maturity, perpetual, and unknown-active
diagnostics. `missing_maturity_count` reports absent/invalid dates even when
explicit perpetual evidence independently satisfies the import gate. The
contract declares frozen import batch and read-only
preflight capabilities, while database persistence, migrations, source
requests, sync, broker writes, Shadow, and PIT remain false.

`IMPORT_READY` means only that the supplied evidence is internally sufficient
for a separately controlled import. It does not mean M3-core-ready and is not
a recommendation, personal eligibility decision, or authorization to trade.

## 16. Read-only boundary and next gate

The core planner is a pure reducer over caller-supplied Pydantic projections.
It has no ORM, SQLAlchemy Session, network, environment, filesystem,
persistence, or source-client dependency. It must not call
`MoexBondUniverseService.sync()` or rely on wrapping that internally
committing importer in a rollback. No Company or Bond rows are created.

The later controlled production preflight must compare a freshly generated
Task296B candidate-ISIN hash with an operator-authorized expected hash and
stop on drift. Task296C does not perform that production check. Independent
review and exact-commit CI are next; only a separately authorized read-only
production preflight may follow. No import occurs without a distinct explicit
authorization.

```text
FROZEN_IMPORT_BATCH_READY=true
READ_ONLY_IMPORT_PREFLIGHT_READY=true
TASK296B_MANIFEST_IS_SOURCE=true
MOEX_DESCRIPTION_EXACT_IDENTITY_REQUIRED=true
EXISTING_BOND_COLLISION_CHECK_READY=true
COMPANY_RESOLUTION_PREVIEW_READY=true
PLACEHOLDER_ISSUER_IMPORT_ALLOWED=false
POSITIVE_NOMINAL_REQUIRED=true
MATURITY_OR_PERPETUAL_REQUIRED=true
REQUIRED_TESTS_GATE_IMPORT=false
NAME_BASED_SECURITY_CLASSIFICATION=false
IMPORT_READY_MEANS_M3_READY=false
READY_IMPORT_MANIFEST_HASH_READY=true
DATABASE_PERSISTENCE=false
DATABASE_MIGRATION=false
MOEX_SYNC_CALLED=false
NETWORK_ACCESS=false
LIVE_TINVEST_REQUEST=false
LIVE_MOEX_REQUEST=false
M3_FEATURE_LOGIC_CHANGED=false
OFZ_CURVE_CHANGED=false
BROKER_WRITE_SURFACE=false
SHADOW_STARTED=false
PIT_READY=false
```
