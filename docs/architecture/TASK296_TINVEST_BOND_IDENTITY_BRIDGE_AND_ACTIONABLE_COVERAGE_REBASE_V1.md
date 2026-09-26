# Task296 — T-Invest Bond Identity Bridge & Actionable Coverage Rebase v1

## 1. Purpose

Task296 converts an already-normalized current T-Invest bond universe and
caller-supplied BondRadar bond identity projections into a deterministic,
read-only evidence view. It reports UID-to-Bond identity, source duplicate
ISIN structure, UID-level availability and qualification facts, actionable
coverage against supplied CORE M3-complete Bond IDs, and an audit of
unmatched source rows.

The bridge is a pure reducer. It does not fetch T-Invest data, query a
database, create or update Bond rows, persist its result, or decide which
unmatched securities should be imported.

## 2. Evidence inputs

The source input is the Task295 `TInvestBondUniverseInstrument` sequence.
Each source UID is the identity of a concrete broker instrument. Internal
inputs use the strict `BondIdentityProjection` contract containing only
`bond_id`, `isin`, and `secid`; no ORM object is required. CORE M3-complete
Bond IDs are already-derived input data. Task296 does not rebuild or inspect
M3 features.

The bridge validates exact positive integer IDs and deterministic sequence
inputs, rejects repeated internal Bond IDs, duplicate source UIDs, and CORE
IDs not represented by an internal projection. A duplicate UID raises the
typed `TInvestBondIdentityBridgeError` with code `SOURCE_UID_CONFLICT`.

## 3. Identity model

The three identities remain separate:

| Layer | Identity | Meaning |
| --- | --- | --- |
| Broker instrument | T-Invest UID | Specific instrument exposed by T-Invest |
| Analytical security | `BondRadar Bond.id` | Security used by BondRadar analytics |
| Cross-source bridge evidence | ISIN | Exact v1 link between source and internal rows |

The primary T-Invest identity is UID. Automatic cross-source matching uses
exact string equality of source ISIN and internal Bond ISIN, and only a
unique exact candidate can produce `MATCHED_EXACT_ISIN`. Multiple source
UIDs may legitimately share an ISIN and map to one Bond. Those rows are
preserved individually; they are not a duplicate-UID conflict.

When there is no exact match, `strip().upper()` is used only to report
normalized-only evidence. A single such candidate is
`UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE`; multiple candidates are
`CONFLICT_NORMALIZED_ISIN_AMBIGUOUS`. Neither state selects a Bond. Multiple
internal rows with the exact same ISIN produce
`CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS`.

Missing source ISIN and no internal candidate have separate unresolved
states. Source duplicate-ISIN groups use exact supplied ISIN strings, with
blank or null values excluded; formatting-only variants remain separate
groups. Ticker, name, issuer name, maturity, coupon, nominal, FIGI alone,
substrings, and fuzzy similarity never create an automatic match. Names and
tickers may remain source evidence but are not parsed into security classes.

## 4. Bridge rows and Bond aggregates

Each `TInvestBondBridgeRow` preserves one source UID and its exact source
ISIN, FIGI, ticker, class code, duplicate-ISIN group size, source
availability flags, qualification flag, and required-tests evidence. It
also records match state/method and nullable internal identity fields. A
nonmatched row has no selected `bond_id`, `bond_isin`, `bond_secid`, or OFZ
classification. Normalized-only candidates are reported separately and
never copied into the matched identity fields.

After exact matching only, the bridge calls the shared canonical
`is_ofz_instrument` helper with the matched internal Bond's ISIN and SECID.
It never substitutes a T-Invest ticker for MOEX SECID. A non-OFZ result is
not asserted to mean legally corporate for arbitrary inputs.

`TInvestBondUidAggregate` groups matched rows by unique `bond_id` and keeps
all sorted UIDs. It exposes total matched UIDs, buyable UIDs, buyable UIDs
with `for_qual_investor == false`, and buyable UIDs with that flag true. A
Bond-level buyable fact means at least one matched UID is currently
API-buyable; the source UID set remains explicit so that different UID
availability or qualification facts are not collapsed.

## 5. Availability and qualification

Task295 availability semantics are copied without redefinition:

| Task295 classification | Task296 classification |
| --- | --- |
| `API_BUY_AVAILABLE` | `API_BUY_AVAILABLE` |
| `API_VISIBLE_NOT_BUYABLE` | `API_VISIBLE_NOT_BUYABLE` |
| `API_TRADE_UNAVAILABLE` | `API_TRADE_UNAVAILABLE` |
| `None` | `UNKNOWN` |

Availability belongs to each UID, not to a Bond in isolation. Buy, sell,
API-trade, qualification, and required-tests evidence remain separate.
Qualification is not merged across UIDs and no personal-eligibility result
is inferred. `PERSONAL_QUALIFICATION_RESOLVED=false`.

## 6. Actionable coverage rebase

Coverage distinguishes source UID counts from unique matched Bond counts.
The view reports total and API-buyable source UIDs; matched source UIDs;
matched unique, non-OFZ, and OFZ Bond counts; matched buyable unique Bonds;
matched buyable non-qual-flag-false unique Bonds; and intersections of
matched, buyable, and buyable/non-qual-false Bonds with the supplied CORE
M3-complete IDs.

The unmatched totals separately report all unmatched UIDs, buyable UIDs,
buyable UIDs whose qualification flag is false or true, visible-not-buyable
UIDs, API-trade-unavailable UIDs, and UIDs with unknown availability.

`api_buyable_core_coverage_pct` divides the buyable CORE intersection by
matched API-buyable unique Bonds. The
`api_buyable_nonqual_flag_false_core_coverage_pct` uses the corresponding
non-qual-flag-false intersection and unique-Bond denominator. Both use a
fresh Decimal context with precision 28 and `ROUND_HALF_EVEN`, following
Task283's operation order `count * Decimal("100") / denominator`. A zero
denominator produces `Decimal("0")`; floats and presentation rounding are
not used.

## 7. Unmatched audit

Every unmatched UID remains in `unmatched_rows`, with its exact source
identity evidence, match-state bucket, availability, qualification, and a
factual review priority. Review priorities are
`BUYABLE_NONQUAL_FLAG_FALSE`, `BUYABLE_QUAL_RESTRICTED`,
`BUYABLE_QUAL_UNKNOWN`, `NOT_CURRENTLY_BUYABLE`, and
`AVAILABILITY_UNKNOWN`. These labels direct later investigation only; they
are not import, accept, or reject decisions.

Deterministic match-state and review-priority breakdowns retain sorted UIDs.
Unmatched metadata breakdowns cover availability classification,
qualification, required-tests state, currency, class code, country of risk
and its name, sector, bond type, exchange, real exchange, OTC flag, and IIS
flag. Each breakdown groups one `field`; its entries group equal
`(state, value)` evidence and retain a count plus sorted UID list. Optional fields missing or explicitly null map
to `NOT_SUPPLIED`; malformed optional values map to `INVALID_SOURCE_VALUE`
without copying the malformed value. Valid strings and booleans are kept
exactly as supplied. These fields are audit dimensions only and do not
establish legal or security classification.

## 8. Duplicate ISIN accounting

Duplicate source ISIN groups are permitted. The output reports
`source_duplicate_isin_group_count` and
`source_duplicate_isin_row_count`; the latter counts all source rows whose
non-null ISIN belongs to a group of at least two rows. Every UID row remains
in the bridge and all exact matches contribute to UID counts. Canonical
analytics counts use unique internal Bond IDs.

## 9. Determinism and evidence hashes

Rows, aggregates, unmatched evidence, grouped breakdowns, and UID/Bond ID
collections are sorted deterministically. Reordering the input sequences
does not change the result or hashes. The provenance includes four SHA-256
digests: `bridge_row_set_sha256`, `unmatched_uid_set_sha256`,
`matched_uid_set_sha256`, and `matched_unique_bond_id_set_sha256`.

Hashes use UTF-8 JSON with ASCII escapes, sorted object keys, and compact
separators over sorted rows or sorted identifier arrays. They do not include
timestamps, Python `repr()`, or unordered set iteration. Hash
representations preserve exact source strings; normalized ISIN evidence is
never substituted into a source field.

## 10. Current-only evidence

Task296 describes the current T-Invest universe only:

```text
TINVEST_CURRENT_UNIVERSE=true
TINVEST_HISTORICAL_UNIVERSE_READY=false
TINVEST_UNIVERSE_PIT_READY=false
```

It does not address survivorship bias or historical broker availability.
Historical validation remains a separate M7 responsibility. The result's
`pit_ready` is always false.

## 11. Dated production observation (context only)

The read-only production observation dated 2026-09-26 reported:

```text
BASE_BONDS=1613
BUYABLE_BONDS=1599
MATCHED_ROWS=1339
UNMATCHED_ROWS=274
MATCHED_UNIQUE_NON_OFZ=1300
BUYABLE_MATCHED_UNIQUE_NON_OFZ=1298
CORE_OVERLAP=374
```

The broader probe also observed 780 DFAs, 38 duplicate source ISIN groups,
450 BondRadar CORE M3-complete bonds, 1,331 matched non-OFZ rows, 8 matched
OFZ rows, 1,300 matched unique non-OFZ Bonds, and 1,298 buyable matched
unique non-OFZ Bonds. Of those buyable corporate-context Bonds, 1,030 had a
false non-qualification flag; CORE overlap was 374 buyable and 344
buyable/non-qual-flag-false Bonds. The recorded coverage values were 28.81%
and 33.40% respectively.

Every number above is a dated observation, not a runtime constant or test
expectation. Source universe membership, duplicate groups, matches, and M3
overlap can change. Runtime code contains no hard-coded production counts.

## 12. No persistence or automatic import

No broker-instrument table, identity-bridge table, universe snapshot table,
migration, database write service, or persistence is added. The unmatched
instruments have not yet been classified; the bridge does not freeze
incomplete identity into stored state. No unmatched row is automatically
imported, and no Bond is created or updated.

## 13. Pure execution boundary

The bridge consumes Task295 normalized objects and caller-supplied
projections only. It does not import SQLAlchemy, an ORM/session, an M3
feature or audit service, market or credit feature services, HTTP clients,
`httpx`, `requests`, or `urllib`. It does not read environment variables,
access the filesystem, make network requests, call a broker, or expose a
broker-write method. Task295 remains the only T-Invest network/source
boundary.

## 14. Capability declarations

```text
TINVEST_BOND_IDENTITY_BRIDGE_READY=true
PRIMARY_TINVEST_IDENTITY=uid
AUTO_MATCH_METHOD=EXACT_ISIN
ONE_BOND_TO_MANY_TINVEST_UIDS=true
DUPLICATE_SOURCE_ISIN_ALLOWED=true
DUPLICATE_UID_FAIL_CLOSED=true
TICKER_ONLY_MATCH=false
NAME_MATCH=false
FUZZY_MATCH=false
FIGI_ONLY_AUTO_MATCH=false
NORMALIZED_ONLY_AUTO_MATCH=false
ACTIONABLE_COVERAGE_REBASE_READY=true
UID_COUNTS_SEPARATE_FROM_BOND_COUNTS=true
QUALIFICATION_DIMENSION_PRESERVED=true
PERSONAL_QUALIFICATION_RESOLVED=false
UNMATCHED_AUDIT_READY=true
UNMATCHED_AUTO_IMPORT=false
OPTIONAL_SOURCE_METADATA_AUDIT_READY=true
BRIDGE_ROW_SET_HASH_READY=true
UNMATCHED_SET_HASH_READY=true
TINVEST_CURRENT_UNIVERSE=true
TINVEST_HISTORICAL_UNIVERSE_READY=false
TINVEST_UNIVERSE_PIT_READY=false
DATABASE_MIGRATION_ADDED=false
DATABASE_PERSISTENCE_ADDED=false
NETWORK_ACCESS_ADDED=false
LIVE_TINVEST_REQUEST=false
REAL_TOKEN_USED=false
PRODUCTION_ACCESSED=false
M3_FEATURE_LOGIC_CHANGED=false
MOEX_LOGIC_CHANGED=false
OFZ_CURVE_CHANGED=false
STRATEGY_CHANGED=false
RISK_ENGINE_CHANGED=false
BROKER_WRITE_SURFACE=false
SHADOW_STARTED=false
```

## 15. Verification

Synthetic tests cover exact matching, duplicate source ISINs, duplicate UID
failure, normalized-only diagnostics, prohibited fallback evidence,
availability and qualification separation, many UIDs per Bond, unique-Bond
coverage arithmetic, unmatched metadata and review buckets, OFZ identity
boundary, deterministic hashes, input immutability, and static no-side-effect
checks. The Task295 normalized-universe regression suite protects its source
contract. Exact-commit CI remains the broad regression gate.

## 16. Handoff and stop boundary

After implementation, focused verification, commit, push, and exact-commit CI,
stop. The next action is independent Task296 review followed by a separately
controlled production read-only bridge/unmatched audit against a fresh
T-Invest current universe. This task does not authorize deployment, VDS
access, a production probe, live T-Invest access, bridge persistence,
automatic imports, Task297, CFA work, or Shadow.
