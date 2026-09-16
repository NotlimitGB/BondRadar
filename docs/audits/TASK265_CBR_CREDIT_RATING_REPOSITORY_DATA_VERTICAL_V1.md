# Task265 — CBR Credit Rating Repository Data Vertical v1

## Acceptance and request lock

Baseline: clean `main@62d986d0b443b9439e0926089ae250d72e2474c2`.
This task implements code and disposable-database proofs, not production execution.
Alembic revision `202609160001`, parent `202609150003`, is an additive,
schema-only extension. Existing contract versions remain unchanged.

The operator-provided successful searchRating HTTP 200/Bitrix success response,
four agency labels, 14 search fields, navigation and history JS contracts are
**prior evidence**, not independently fetched or verified during Task265.
No additional source requests were made. Production DOM/session compatibility
and full coverage require separately authorized external review and source PLAN.

## Provider, agency and immutable semantics

`CBR_RATINGS` is an artifact provider, not a rating agency.
Repository artifacts use `RATING_REPOSITORY_RESPONSE`. Agencies remain exactly
ACRA, EXPERT_RA, NRA and NKR; MOEX/CBR_RATINGS cannot be agencies.
Existing direct-agency enum/string values remain accepted. MOEX default evidence
cannot supply rating events; withdrawal does not create a default.

CBR responses do not establish a rating scale: `rating_scale_raw=null` is required.
Direct-agency persistence continues requiring a nonempty scale. Empty scales are
rejected. No scale, rating score, cross-agency equivalence or issue rating is inferred.
Visible dates, including a validated optional time suffix, remain DATE precision;
`publication_at=null`. They do not prove historical payload availability.

Task263 direct-agency fingerprints and default fingerprints remain unchanged.
New CBR events use `CBR_RATINGS_SEMANTIC_EVENT_V1`, binding agency, exact source
object/target identity, event/publication fields and raw rating semantics, without
artifact SHA or diagnostic title. A unique fingerprint and full persisted-semantic
comparison provide query-first reconciliation plus savepoint/reload protection.
Only CBR-origin linkage is reusable. The first immutable event/artifact linkage is
retained across byte-version re-observations. Changed facts create new evidence;
changed resolution or inconsistent lineage hard-fails as a collision. Artifacts
remain independently versioned by exact bytes and provider/URL/SHA identity.
Stores flush, never commit or update immutable rows.

## Contained source traversal

One verified httpx session, HTTPS `ratings.cbr.ru` only, no credentials or
environment proxy, research User-Agent and explicit 5/30/10/5-second timeouts.
Robots precede bootstrap; explicit disallow, blocks, CAPTCHA and technical failure
stop traversal. Robots 404 means no published rules. TLS verification is never disabled.
Bootstrap accepts only explicit sessid bindings without JavaScript execution.
Session tokens/cookies and bootstrap HTML stay in memory and are not bundled.

POST actions are restricted to searchRating, searchRatingNavigation and
searchObjectHistory with form encoding and the supplied fields. Searches are not
interleaved; navigation uses the same session and exact page/count/sorting context.
Each retained object history is fetched once; out-of-universe bonds are excluded.
No agency site or releaseUrl is fetched. JSON duplicate keys, non-finite numbers,
bool-as-integer counts, unknown agencies and conflicting object identities fail closed.
Bounded extra source keys remain provenance only. History/current equivalence
binds object, agency, target, date and rating value; richer current semantics survive.

Fixed caps: 4 MiB response, 64 KiB headers, 3 redirects/attempts, 1-second minimum
request spacing, retry only timeout/connection reset/429/500/502/503/504 with 2/4s
delays and Retry-After capped at 30s; 10,000 attempts, 100 pages per issuer,
10,000 objects and 128 MiB frozen response bytes. No CLI limit overrides.

## Frozen runner and transaction ownership

Schema: `bondradar.cbr_rating_repository_ingestion_runner.v1`; required mode,
named PostgreSQL URL environment variable and bundle directory. PLAN/PREFLIGHT
require read-only confirmation; APPLY requires write confirmation and an exact
lowercase plan hash. SQLite exists only through private injected test adapters.

PLAN reads verified issuer INNs and exact bond ISINs under verified read-only
transactions, discovers sequentially, then rechecks universe/resolution/collisions.
Universe identity excludes DB IDs. Exact successful JSON responses and secret-free
request semantics are content-addressed; no-overwrite atomic files precede the
manifest. Successful search/navigation/history responses contribute to source audit
lineage even when their measured result is empty; bootstrap never contributes.

PREFLIGHT/APPLY validate paths, bytes, hash, reparsed candidates and counts before
reading DB environment or creating an engine. They make no network requests.
APPLY acquires fixed identity/evidence locks with local lock_timeout=5s, repeats
guards and persists in one transaction. Full bytes/semantic/count readback precedes
the sole commit; separate read-only reconciliation follows. Rollback, close and
dispose cleanup cannot erase known/uncertain commit facts. Zero-write replay reports
NONE; known writes report CBR_RATING_REPOSITORY_APPLY; uncertain commit reports
CBR_RATING_REPOSITORY_APPLY_OUTCOME_UNKNOWN and requires reconciliation, no retry.

## Migration safety and offline verification

Provider/kind/raw-rating checks are selected by unique reflected semantics, retaining
actual PostgreSQL names (including truncation) with op.f. No hash reconstruction.
SQLite uses batch recreation and preserves exact existing artifact/rating/default
rows, FK actions, unique constraints and indexes. Downgrade checks new providers,
kinds and null scales before any DDL, refusing incompatible evidence without deletion.
No migration backfill occurs; existing raw event fingerprints/publication remain intact.

Focused tests cover all four agency projections, null scale, withdrawal, exact
identity and session encoding, pagination, CAPTCHA/robots/redirect/retry containment,
re-observation/collision, frozen tampering, deterministic hashes, schema/universe
drift, read-only/lock protocols, caller rollback and known/uncertain commit reporting.
SQLite migration cycle proves preservation and downgrade refusal; reflected truncated
names, zero and ambiguous matches fail closed. Task263 evidence/migration and small
Task264 parser/client/runner regressions verify compatibility. Broad suite, Docker,
source network and production were intentionally not run.

Final local verification: **68 PASS**, including the three Task265 modules,
Task263 evidence/migration and Task264 parser/client/runner compatibility selection.
Compileall and git diff --check PASS; Alembic heads/history show the single
`202609160001` head. Only pre-existing Alembic path_separator deprecation warnings.
Command selection: `python -m pytest -q` on `test_cbr_rating_repository.py`,
`test_cbr_rating_repository_runner.py`, `test_cbr_rating_repository_migration.py`,
`test_credit_risk_evidence.py`, `test_credit_risk_evidence_migration.py`,
`test_acra_rating_parser.py`, `test_acra_rating_client.py`, `test_acra_rating_runner.py`;
`python -m compileall -q` on changed modules/tests; `python -m alembic heads`;
`python -m alembic history -r 202609150003:202609160001`; `git diff --check`.

Changed-file inventory (14):

- `backend/app/models/credit_risk_evidence.py`
- `backend/app/services/credit_risk_evidence/contracts.py`
- `backend/app/services/credit_risk_evidence/service.py`
- `backend/app/services/credit_risk_evidence/cbr_ratings/__init__.py`
- `backend/app/services/credit_risk_evidence/cbr_ratings/contracts.py`
- `backend/app/services/credit_risk_evidence/cbr_ratings/client.py`
- `backend/app/services/credit_risk_evidence/cbr_ratings/parser.py`
- `backend/app/services/credit_risk_evidence/cbr_ratings/runner.py`
- `backend/alembic/versions/202609160001_cbr_rating_repository_support.py`
- `backend/tests/test_cbr_rating_repository.py`
- `backend/tests/test_cbr_rating_repository_runner.py`
- `backend/tests/test_cbr_rating_repository_migration.py`
- `backend/tests/test_credit_risk_evidence_migration.py`
- `docs/audits/TASK265_CBR_CREDIT_RATING_REPOSITORY_DATA_VERTICAL_V1.md`

Task263's pre-created migration fixture now constructs the old schema through
its original migration before validating it; it does not treat additive current
ORM metadata as the old contract. No old migration or Task264 file is changed.
Existing bank-financial and ACRA exact-head guards remain deliberately unchanged;
their compatibility with a future migrated deployment is outside this scope and
must be reviewed separately before production operations.

## Terminal safety and sole handoff

```text
SCHEMA_ONLY_MIGRATION=true
DEFAULT_INGESTION=false
ISSUER_TO_BOND_INHERITANCE=false
RATING_SCORING=false
CROSS_AGENCY_NORMALIZATION=false
PIT_READY=false
SOURCE_NETWORK_EXECUTED=false
PRODUCTION_DATABASE_ACCESSED=false
PRODUCTION_MIGRATION_APPLIED=false
PRODUCTION_PLAN_EXECUTED=false
PRODUCTION_INGESTION_EXECUTED=false
VDS_ACCESSED=false
PRODUCTION_ACTIONS=NONE
```

Only handoff: independent external review. Production backup, migration and CBR
PLAN/PREFLIGHT/APPLY require separate explicit authorization. Code acceptance is
not production source or ingestion acceptance. One exact-commit CI snapshot only,
with PENDING/SNAPSHOT_UNAVAILABLE reported honestly; no polling or waiting.

## Task265-FIX1 — historical SQLite metadata compatibility

```text
TASK265_FIX1_REASON=current ORM metadata made rating_scale_raw nullable, exposing Task263 SQLite precreated-metadata validator incompatibility
CI_FAILED_RUN=35095834867
CI_FAILED_TESTS=9
CI_PASSED_TESTS=2562
```

CI counts/run are operator-provided prior evidence. The same exception was
independently reproduced in the Task255 historical migration regression at
`d5d248807fdd2351770115242a7ec6c749193ace` before this fix.

Only Task263 SQLite precreated validation accepts both nullability shapes for
`credit_rating_events.rating_scale_raw`. Its type and every other column's exact
nullability remain checked. Historical Task263 creation still emits NOT NULL;
PostgreSQL DDL, Task265 nullable semantics, migration IDs and downgrade guards are
unchanged. No schema revision or migration was added; no data is changed by the fix.

Focused verification: 26 PASS across Task263, Task265 and historical bank-evidence
migration modules. Tests assert historical creation NOT NULL, current metadata
compatibility, unrelated required-column drift rejection, final nullable state,
upgrade/downgrade/re-upgrade preservation and CBR/null-scale downgrade refusal.
Compileall PASS; single Alembic head remains `202609160001`.
Full backend suite: `python -m pytest backend/tests -q` completed with
**2572 passed, 1 skipped, 0 failed, 68 warnings**, exit 0, in 3190.45s.
The sole skip is the existing Windows symlink-unavailable branch in
`test_ops_retention.py`; no test skip or assertion was added/weakened by FIX1.
Warnings are existing Alembic path_separator deprecations and pytest cache-write
permission warnings; no new warning category was introduced by this fix.
`python -m compileall backend/app` and `git diff --check` PASS.

FIX1 scope: this historical migration's validator, its focused migration test,
and this audit only. Production/VDS/source network are not used.
`PRODUCTION_ACTIONS=NONE`, `PRODUCTION_DB_MUTATION=false`, `PIT_READY=false`.

## Task265-FIX2 — Source-specific Russian INN query eligibility

Baseline: `main@171cc9bba3171c65e3ca8bacc6cee3d763f46d48`.

```text
TASK265_FIX2_ROOT_CAUSE=CBR repository INN search received non-Russian 10-digit LegalIssuer identifiers
GLOBAL_CANONICAL_INN_UNCHANGED=true
FULL_UNIVERSE_PRESERVED=true
SOURCE_QUERY_ELIGIBILITY_ADDED=true
FOREIGN_ISIN_SEARCH_ADDED=false
DB_MIGRATION_ADDED=false
ALEMBIC_HEAD=202609160001
```

The following are operator-provided prior production evidence, not independently
verified by this fix. Production code/revision and health were aligned before
the first CBR PLAN. Evidence tables were empty. That PLAN returned
`BITRIX_SOURCE_ERROR` after 3 HTTP requests in a verified read-only transaction;
it caused no DB mutation/persistence and wrote no source bundle.

| Identifier | Operator-provided issuer | Russian INN checksum | Bitrix result |
| --- | --- | --- | --- |
| `0010000025` | Republic of Belarus, Ministry of Finance | false | HTTP 200, status error, code 0/message Array |
| `7707083893` | PJSC Sberbank of Russia | true | HTTP 200, status success, itemCount 60, pageCount 3 |

Generic and issuer-specific bootstrap returned the same error for `0010000025`.
These observations support a query-input eligibility defect, not a demonstrated
TLS/session/Referer/outage/CAPTCHA/WAF/DB defect. No live CBR request or production
DB check was made during FIX2.

The CBR-only helper requires a string of exactly 10 ASCII digits and checksum
weights `2,4,10,3,5,9,4,6,8`, with control `(sum % 11) % 10`.
It tests this source field's eligibility, not issuer nationality. No country,
title or hard-coded issuer exception is used. Generic canonical evidence INN
validation remains exactly 10 digits, including `0010000025`.

Client collection and parser completion recompute the same eligible set from
the full frozen universe. Ineligible identifiers are not queried and are not
represented as failed searches or as measured absence of ratings. Missing
eligible searches still block completion; recorded ineligible queries are
rejected. Eligible-INN Bitrix errors remain fatal, with all existing CAPTCHA,
pagination, agency and identity gates unchanged.

Counts now include `issuer_query_eligible_count` and
`issuer_query_ineligible_count`; their sum equals `issuer_universe_count`.
Successful attempted/succeeded search counts equal the eligible count. Overall
issuer coverage keeps the full universe denominator and its existing string
percentage representation. Full universe serialization/hash remains unchanged.
The pre-first-production-bundle correction keeps bundle schema v1 and requires
both new counts during offline rederivation; rehashed tampering is rejected.

Foreign/query-ineligible issuer bonds may remain undiscovered through this
issuer-INN strategy. Exact ISIN search is deferred, not implemented; no name or
fuzzy fallback is introduced. Production PLAN/PREFLIGHT/APPLY requires separate
operator authorization; this fix does not execute them.

Verification:

- `python -m pytest -q backend/tests/test_cbr_rating_repository.py backend/tests/test_cbr_rating_repository_runner.py`: **50 passed, 0 failed**, 1 existing pytest cache permission warning.
- Synthetic tests cover checksum/ASCII/type failures, mixed and zero-eligible scopes, actual mock HTTP search filtering, missing eligible completion, valid-INN Bitrix failure, full-universe coverage, deterministic bundle round-trip and rehashed count tampering.
- Existing focused tests retain pagination/history, exact INN/ISIN, agency/scale, immutable re-observation/collision, disposable-DB rollback/commit uncertainty and offline mode safety coverage. No live source or production APPLY is used.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (from backend): sole `202609160001 (head)`.
- `git diff --check`: PASS. Exact scope: four CBR Ratings package files, its two focused test modules and this audit.
- `BROAD_BACKEND=SKIPPED_BY_DESIGN`; broad regression remains exact-commit CI-owned, with at most one snapshot and no waiting/polling.

`PRODUCTION_ACTIONS=NONE`, `PRODUCTION_DB_MUTATION=false`,
`LIVE_CBR_REQUESTS=NONE`, `PIT_READY=false`.

## Task265-FIX3 — Explicit empty initial-search result

Baseline: clean `main@4ae20724e1fea40f7a81e4d4a75237efccc5d053`.
No model, migration, schema revision, generic INN, FIX2 eligibility or persistence
behavior changes. Alembic remains `202609160001`.

### Operator-provided production evidence

These facts are prior evidence supplied by the operator, not independently
verified through production DB access or live requests during FIX3:

- FIX2 deployment PASS; code SHA `4ae20724e1fea40f7a81e4d4a75237efccc5d053`, DB revision `202609160001`.
- Backend healthy, restart_count `0`, production smoke PASS.
- Full universe: issuer `496`, bond `2995`; eligible issuer queries `494`, ineligible `2`.
- Failed PLAN: `BITRIX_SOURCE_ERROR`, HTTP requests `5`, database accessed and transaction read-only; mutation/persistence false, source bundle not written, production actions NONE, PIT readiness false.
- Artifact/rating/default evidence counts before and after the failed PLAN: `0|0|0`. No successful production source bundle exists yet.

| Context | INN | Source result |
| --- | --- | --- |
| PLAN request 3, searchRating | `0261012138` | success, itemCount 1 |
| PLAN request 4, searchRating | `0268008010` | success, itemCount 4 |
| PLAN request 5, searchRating | `0273086494` | HTTP 200, error, data null, errors exactly code 0/message Array |
| Same-session controlled comparison | `0273086494` | HTTP 200, error/Array/null |
| Same-session controlled comparison | `0274051582` | success, itemCount 18, pageCount 1, pageNumber 1 |
| Same-session controlled comparison | `0274062111` | success, itemCount 3, pageCount 1, pageNumber 1 |

The accepted adapter interpretation is that this exact signature represents an
empty initial search for at least the observed valid issuer. It is not a general
rule that Bitrix errors mean no ratings; no live confirmation was performed here.

### Action-specific contract

All parsing first shares the existing bounded, strict UTF-8 JSON decode,
duplicate-key/non-finite/depth/size inspection, CAPTCHA and secret guards.
Generic `envelope()` continues requiring success, an empty errors list and dict
data. Only `searchRating` recognizes a top-level object with exactly
`status/data/errors`, status `error`, data null, one error with exactly
`code/message`, a real integer code `0` and exact message `Array`.
Boolean, float or string code, missing/extra fields, multiple errors, non-null
data, whitespace/case message variants and other errors remain fatal.
Navigation/history, transport/HTTP failures, access blocks, pagination, agency
and identity failures remain fail-closed.

Only the internal page is normalized: itemCount/pageCount/pageNumber `0`,
pageSize `25`, objectName/ascending, empty rows. Exact response bytes are never
rewritten to synthetic success JSON. The client continues the next eligible
query; no object, rating candidate or history request comes from the empty issuer.

`issuer_queries_no_results` counts only this recognized signature, not ordinary
successful zero-row pages. Such queries still increment attempted/succeeded:
on complete discovery both equal eligible count, and no-results lies between
zero and succeeded. The full universe/hash and overall coverage denominator
remain unchanged; FIX2 ineligible identifiers are still skipped.

Raw no-result responses participate in response entries, SHA, frozen byte size,
plan hash and unique URL/payload source-artifact counts, without creating rating
events. Repeated identical empty response bytes may deduplicate as one artifact
while remaining separate query entries/counts. Offline PREFLIGHT rederives this
contract; rehashed count tampering is rejected. Bundle schema v1 is retained for
the pre-first-success production correction. Future artifact persistence follows
the unchanged Task265 store; no new APPLY test or production APPLY was executed.

### Verification and handoff

- `python -m pytest -q backend/tests/test_cbr_rating_repository.py backend/tests/test_cbr_rating_repository_runner.py`: **86 passed, 0 failed**, exit 0; one existing pytest cache permission warning.
- Synthetic/mock tests prove exact and near-miss signatures, strict generic/navigation/history errors, HTTP failures, JSON/CAPTCHA/secret guards, success-empty-success continuation, zero histories for empty results, counts, unchanged FIX2 universe/eligibility, exact raw frozen bytes, mixed/all-empty deterministic round-trip, offline PREFLIGHT and rehashed count rejection.
- Existing focused regressions retain nullable scale, exact identity, agency mapping, semantic re-observation/collisions and disposable-DB transaction safety. Runtime writes are limited to existing disposable test databases; no production operation occurs.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (from backend): sole `202609160001 (head)`.
- `git diff --check`: PASS. Scope: parser/client/runner, their two focused test modules and this audit only.
- `BROAD_BACKEND=SKIPPED_BY_DESIGN`; exact-commit CI is queried at most once without waiting/polling.

The sole handoff is independent external review. Production PLAN/migration/APPLY
requires separate authorization and is not unlocked automatically.

```text
EMPTY_SEARCH_ACTION_ONLY=true
GENERIC_ENVELOPE_STRICT=true
RAW_NO_RESULT_RESPONSE_PRESERVED=true
DB_MIGRATION_ADDED=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_MUTATION=false
LIVE_CBR_REQUESTS=NONE
PIT_READY=false
```

## Task265-FIX4 — Optional null customData in empty-search error

Baseline: clean `main@a9d22703d04fbd255e5403deaa3bd12af9a4356c`.
The following production/probe facts are operator-provided prior evidence;
no production DB check or live CBR request was performed during FIX4.

- FIX3 deployed at that SHA, DB revision `202609160001`; backend healthy, restart_count `0`, smoke PASS.
- Artifact/rating/default evidence counts `0|0|0`; failed PLAN `BITRIX_SOURCE_ERROR` after 5 HTTP requests, mutation/persistence false, source bundle not written, PIT readiness false.
- Structural probe for INN `0273086494`: 3 HTTP requests, content type `application/json; charset=UTF-8`, payload bytes `88`.
- Reported payload SHA-256: `ec1554a0d785b2499b59cf745b46d8d7ca10842786307608fcca071608598201`.
- Top-level keys exactly `data,errors,status`; status string `error`, data null, one dict error with keys `code,customData,message`, integer code `0`, null customData, exact string message `Array`.
- `FIX3_EXPLICIT_EMPTY_MATCH=false`; `FIX3_PARSE_SEARCH=BITRIX_SOURCE_ERROR`.

Root cause: FIX3 omitted the source-observed nullable `customData` field from
its exact error-key allowlist. The preceding sanitized diagnostic exposed only
code/message, hiding that structural difference. FIX3 history above is retained.

Only the explicit matcher changes. It permits exactly `code/message` or
`code/message/customData`, and requires absent-or-`is None` customData.
All non-null values, including false/zero/empty containers/empty strings, and
any fourth error key remain fatal. Top-level keys, one-error cardinality,
real integer zero and exact Array message requirements remain unchanged.
The exception remains initial-search-only; generic envelope, navigation/history,
JSON/security guards and HTTP/transport behavior are untouched.

Exact response bytes remain immutable; the synthetic empty page is only derived
interpretation. Existing no-result/attempted/succeeded counts are reused; there
is no new count, schema, bundle version or persistence behavior.
Tests use synthetic equivalent JSON, not a claim to possess or independently
verify the operator's exact 88-byte payload/SHA.

Verification:

- Focused repository/parser/client and runner modules: **99 passed, 0 failed**, exit 0; one existing pytest cache permission warning.
- Both shapes cover canonical empty parsing, strict generic/navigation/history rejection, mock-session continuation, no objects/history/events for the empty issuer, deterministic frozen PLAN/PREFLIGHT, unchanged raw bytes/counts and rehashed tampering rejection.
- Added non-null customData and fourth-key negative tests. Existing FIX2/FIX3 identity, eligibility, security, pagination and disposable-DB regressions remain passing.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (from backend): sole `202609160001 (head)`.
- `git diff --check`: PASS; exact scope is matcher/parser, two focused test modules and this audit.
- Client/runner/contracts/models/migrations unchanged; broad suite skipped by design. At most one exact-commit CI snapshot, no waiting/polling.

`NEW_COUNT_ADDED=false`, `DB_MIGRATION_ADDED=false`, `PRODUCTION_ACTIONS=NONE`,
`PRODUCTION_DB_MUTATION=false`, `LIVE_CBR_REQUESTS=NONE`, `PIT_READY=false`.
The sole next step is independent external review; production PLAN/APPLY is not
automatically authorized by code acceptance.
