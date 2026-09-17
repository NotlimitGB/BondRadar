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

## Task265-FIX5 — Bond identity by exact ISIN across issuer succession

Baseline: clean `main@1f372710a2e12e52cd8eed502f106ea044d44a9e`;
actual `origin/main` matched before editing. The following deployment, trace
and corporate-action details are **operator-provided prior evidence**, not
independent production/source verification performed by this task.

- FIX4 deployed at that SHA; DB revision `202609160001`, backend running/healthy, restart count `0`, smoke PASS.
- Artifact/rating/default counts before and after failed PLAN: `0|0|0`. PLAN failed with `FOREIGN_ISSUER_INN` after `83` requests; verified read-only transaction, no mutation/persistence or source bundle, PIT readiness false.
- Full universe: `496` issuer identifiers, `494` eligible and `2` ineligible issuer queries; `2995` bond ISINs.
- Trace: query index `78`, active query INN `4401116480`, page `1`, row `7`, object ID `222534`, source INN `7735057951`, ISIN `RU000A103760` (in universe), object type `TBND - облигационный займ`, agency `АКРА (АО)`, country `РОССИЯ`.
- Source subject: ООО “Хоум Кредит энд Финанс Банк”; object: ООО “ХКФ Банк”, БО-04.
- Operator-researched MOEX evidence: issue organization changed from HCF Bank to Sovcombank effective `2025-04-10`; Sovcombank succeeded HCF Bank rights/obligations from `2025-04-07`. Historical HCF INN `7735057951` differs from current Sovcombank INN `4401116480`.

Root cause confirmed by inspection: client and offline derive compared source
INN with active query INN before distinguishing a bond row by its ISIN.
An exact bond identity must not depend on the current issuer query context.

Only the identity branching changes, in client and parser. A nonempty ISIN
must pass the existing canonical validator and defines `BOND` by exact ISIN.
Its optional source INN remains generically format-validated (10 ASCII digits),
without query-equality or Russian checksum eligibility requirements. An empty
source INN is valid for an exact bond. Out-of-universe valid bonds remain
excluded from histories/candidates and use the existing outside-universe count.
For issuer rows without ISIN, exact query-INN equality remains mandatory;
both identifiers empty remains `UNRESOLVED_SOURCE_IDENTITY`.

The existing object binding still includes canonical target, exact returned
source INN and ISIN. Changed source-INN/ISIN/target bindings fail with
`OBJECT_IDENTITY_COLLISION`. Consistent duplicate bond objects across eligible
queries retain one history request and one final semantic event. No issuer
rating inheritance, succession inference, corporate-action master, name-based
matching or production special case is introduced.

Exact response bytes, full universe/hash, FIX2 query eligibility, FIX4 explicit
empty-search handling, count keys, manifest/hash algorithm, schema v1,
resolution/store/APPLY behavior and revision `202609160001` are unchanged.
Frozen PLAN/load/PREFLIGHT rederive the same exact bond target and preserve the
historical source INN as raw provenance, never as an inferred issuer target.

Verification:

- Focused parser/client and frozen runner suites: **114 passed, 0 failed**, exit `0`; one existing pytest cache permission warning.
- Synthetic/mock cases cover cross-INN/same-INN/empty-INN bonds, format-valid checksum-ineligible source metadata, outside-universe history exclusion, issuer mismatch, malformed INN/ISIN, both-empty identity, duplicate objects and conflicting bindings. Frozen disposable-DB PLAN/load/PREFLIGHT prove raw-byte preservation, deterministic hash/counts, exact ISIN resolution and offline read-only behavior. Existing FIX2/FIX3/FIX4 and disposable transaction regressions remain passing.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (backend directory): sole `202609160001 (head)`.
- `git diff --check`: PASS. Exact scope: client, parser, two existing focused test modules and this audit; no contracts/models/migrations/runner implementation change.
- `BROAD_BACKEND=SKIPPED_BY_DESIGN`. At most one exact-commit CI snapshot; no waiting/polling.

```text
BOND_IDENTITY_BY_ISIN=true
BOND_QUERY_INN_MISMATCH_ALLOWED=true
BOND_SOURCE_INN_FORMAT_VALIDATED=true
ISSUER_QUERY_INN_MISMATCH_FATAL=true
OBJECT_IDENTITY_COLLISION_STRICT=true
ISSUER_TO_BOND_INHERITANCE=false
ISSUER_SUCCESSION_INHERITANCE=false
NEW_COUNT_ADDED=false
DB_MIGRATION_ADDED=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_MUTATION=false
LIVE_CBR_REQUESTS=NONE
PIT_READY=false
```

The sole handoff is independent external review. Production PLAN/APPLY requires
separate explicit authorization; code readiness does not authorize it.

## Task265-FIX6 — Issuer identity from exact INN query context

Baseline: clean `main@d33922ef1203c4f6d4a67da0d13e3f41932d118c`;
actual remote `origin/main` matched before editing. The following deployment,
trace, taxonomy and external identity facts are **operator-provided prior
evidence**, not independently fetched or production-verified during FIX6.

- FIX5 production code SHA `d33922ef1203c4f6d4a67da0d13e3f41932d118c`; DB revision `202609160001`. Backend running/healthy, restart count `0`, health and smoke PASS.
- Artifact/rating/default counts before and after failed PLAN: `0|0|0`. No successful production Task265 bundle exists. Failed PLAN: `UNRESOLVED_SOURCE_IDENTITY`, `69` HTTP requests, verified read-only transaction, mutation/persistence false, source bundle not written, production actions NONE, PIT readiness false.
- Universe: `496` issuers, `494` query-eligible issuer INNs, `2995` bond ISINs; FIX2 eligibility remains mandatory.
- Diagnostic: query index `64`, active exact INN `3900045916`, page `1`, row `2`, object ID `221711`, type `BNFC - нефинансовая компания`.
- Raw row: object name `Международная компания Публичное акционерное общество Озон`, empty subject name, country `Международные компании САР (остров Русский, остров Октябрьский)`, empty INN/ISIN/koNumber, agency `АО "Эксперт РА"`, rating `ruA`, outlook `STA - стабильный`, date `17.11.2025`.
- Operator-researched MOEX identity for MKPAO Ozon and Expert RA's same-issuer/same-date `ruA` record both identify INN `3900045916`; CBR taxonomy identifies BNFC as a non-financial organization. These facts explain the source contract but introduce no name-specific production rule.

Root cause: an exact-INN issuer search can return an organization-level object
whose result row omits its redundant INN. The previous absent-INN/ISIN branch
therefore blocked a source-context-identifiable issuer. FIX6 supersedes only
that branch of FIX5; it does not treat every identifier-free object as an issuer.

The shared source-contract allowlist is exactly:
`CBNK|FINS|FNPF|FMFO|FLSG|FFCT|FMC|FDEP|FOFO|BNFC|BNFH|CGRP|CO`.
The helper recognizes only an uppercase ASCII code of 2–4 letters followed by
the literal ` - ` separator and a nonempty single-line description. It performs
no stripping, case conversion, fuzzy/description/name matching or country-based
inference. Malformed shapes, unknown codes, instruments and public authorities
are not organizations for fallback purposes.

Identity precedence remains explicit:

1. Nonempty ISIN: BOND by exact canonical ISIN, optional source INN format-only validation, no query-INN equality, retain only in-universe bonds (unchanged FIX5).
2. No ISIN, explicit INN: LEGAL_ISSUER by canonical row INN, exact active query equality required; contradictions remain `FOREIGN_ISSUER_INN`.
3. Both absent: only an allowlisted organization may use the active exact eligible query INN. Otherwise `UNRESOLVED_SOURCE_IDENTITY` remains fatal.

Client traversal establishes the context through `searchRating` with the fixed
`formSearh=advanced` request and FIX2-eligible INN; its navigation belongs to that
same query. Offline derive independently validates exact request keys, advanced
mode, eligibility and pagination before applying the fallback. Navigation
without a valid pending search cannot supply identity. No history response,
unknown source context or ineligible query can initiate the fallback.

For this branch `RatingEventInput.source_issuer_inn` is derived deterministically
from exact request metadata, **not copied from `itemList[].inn`**. The original
row remains `inn=""`, `isin=""`; frozen exact response bytes are never rewritten.
The request's INN and advanced-query metadata remain separately preserved.
Object name, subject name, country, agency and koNumber do not resolve identity.
No bank REGN mapping, issuer succession or issuer-to-bond inheritance is added.

Object collision binding is unchanged: canonical target plus raw returned INN
and ISIN. The same blank-identifier object under different query INNs collides;
even a later explicit INN with the same canonical target retains the strict raw
binding check. Repeated consistent page rows use existing semantic deduplication.

No new count is added. Existing issuer-object/event counts naturally include
resolved organizational rows. Full universe/hash, query eligibility, Bitrix/
CAPTCHA/security/empty-search guards, bundle schema v1, fingerprints, store,
APPLY transactions, models and migrations remain unchanged.

Verification:

- `python -m pytest -q backend/tests/test_cbr_rating_repository.py backend/tests/test_cbr_rating_repository_runner.py`: **162 passed, 0 failed**, exit `0`; one existing pytest cache permission warning.
- Synthetic/mock tests cover the exact production-style BNFC case, every allowlisted code, raw empty identifiers, explicit same/conflicting INN, bond precedence, all excluded instrument/sovereign/unknown types, malformed/null object types, advanced/eligible context guards, page-2 context, cross-query non-leakage/collisions and repeated-row deduplication.
- Disposable-DB frozen PLAN/load/PREFLIGHT prove exact bytes and request metadata, deterministic plan hashes/counts, unchanged issuer target, no offline network access and no evidence writes. Existing FIX2/FIX3/FIX4/FIX5 and disposable transaction regressions remain passing.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (backend directory): sole `202609160001 (head)`.
- `git diff --check`: PASS. Exact scope: CBR contracts/helper, client, parser, two existing focused test modules and this audit.
- `BROAD_BACKEND=SKIPPED_BY_DESIGN`; at most one exact-commit CI snapshot, no waiting/polling.

```text
EXACT_QUERY_CONTEXT_ISSUER_IDENTITY=true
ORGANIZATION_ALLOWLIST_STRICT=true
RAW_ROW_IDENTIFIERS_UNCHANGED=true
EXPLICIT_ISSUER_INN_MISMATCH_FATAL=true
BOND_IDENTITY_BY_ISIN_PRESERVED=true
OBJECT_IDENTITY_COLLISION_STRICT=true
NO_NAME_MATCHING=true
KO_NUMBER_IDENTITY_ADDED=false
ISSUER_TO_BOND_INHERITANCE=false
ISSUER_SUCCESSION_INHERITANCE=false
NEW_COUNT_ADDED=false
DB_MIGRATION_ADDED=false
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_MUTATION=false
LIVE_CBR_REQUESTS=NONE
PIT_READY=false
```

The sole handoff is independent external review. Production PLAN/APPLY remains
separately authorized; implementation readiness grants no production execution.

## Task265-FIX7 — Explicit EN DASH objectType separator

Baseline: clean `main@2e386b6cfa16322e938adfe15b2f55e8f1dfcfe8`;
actual `origin/main` matched before editing. All production, source-probe and
external MOEX facts below are **operator-provided prior evidence**; FIX7 performs
no independent live source or production verification.

- FIX6 code SHA `2e386b6cfa16322e938adfe15b2f55e8f1dfcfe8`, DB revision `202609160001`; backend running, restart count `0`, health/smoke PASS.
- Evidence before/after PLAN `0|0|0`; PLAN failed `UNRESOLVED_SOURCE_IDENTITY` after `72` HTTP requests. Verified read-only transaction, mutation/persistence false, source bundle not written, production actions NONE, PIT readiness false.
- Probe range query indexes `64..100`, `41` HTTP requests, no histories or DB mutation; exactly one unresolved row/type: `BNFC – нефинансовая компания`.
- Row: query index `67`, active INN `3906394938`, page `1`, row `1`, object ID `221567`, objectTypeCode null under FIX6, empty INN/ISIN/koNumber and subject name, country `Международные компании САР (остров Русский, остров Октябрьский)`.
- Object name: `МЕЖДУНАРОДНАЯ КОМПАНИЯ ПУБЛИЧНОЕ АКЦИОНЕРНОЕ ОБЩЕСТВО "ОБЪЕДИНЁННАЯ КОМПАНИЯ "РУСАЛ""`; agency `АКРА (АО)`, rating `A+(RU)`, outlook `STA – стабильный`, release date `26.03.2026`.
- Operator-researched MOEX evidence identifies MKPAO UC RUSAL with INN `3906394938` and ACRA `A+(RU)` dated `26.03.2026`. No production-name special case is introduced.

Root cause confirmed by inspection: FIX6 recognized only the ASCII ` - `
separator. The source-observed ` – ` uses EN DASH U+2013, so the same BNFC
organization code failed lexical recognition. **FIX7 changes lexical recognition
only. Organization taxonomy and identity semantics are unchanged.**

`object_type_code()` now accepts exactly U+002D HYPHEN-MINUS or U+2013 EN DASH,
each surrounded by one ASCII space. Uppercase ASCII code length 2–4, nonempty
single-line description and leading/trailing strictness remain intact. No
Unicode normalization, punctuation rewriting, whitespace stripping or fuzzy
matching is added. U+2014, U+2212, U+2011 and U+2010 remain rejected. Instruments
and sovereign codes may be recognized lexically but never enter the unchanged
13-code organization allowlist.

Client/parser production logic is unchanged: ISIN precedence, explicit issuer
INN equality, eligible advanced-query context, navigation and object collision
guards retain FIX5/FIX6 behavior. Raw objectType, prediction, ratingAction,
names and response bytes are untouched; no count, bundle version, model,
schema revision, migration or store change is made. The preceding FIX6 audit
describes its historical ASCII-only contract; this section supersedes only that
separator restriction.

Verification:

- Both existing Task265 focused modules: **212 passed, 0 failed**, exit `0`; one existing pytest cache permission warning.
- All 13 organization codes cover both separators through the actual mock client and offline derive. Near-miss punctuation/spacing/case/newline/null inputs remain rejected; EN DASH TBND/SCO without identifiers remain fatal. The synthetic RUSAL case requests history, derives the exact query issuer and preserves EN DASH source fields. Synthetic EN DASH ratingAction is a preservation test, not a claim to possess the production action value.
- Frozen PLAN/load/offline PREFLIGHT cover ASCII and EN DASH cases, deterministic plan hash/counts, exact raw bytes and manifest response SHA, no offline source access and no evidence writes. Existing FIX2/FIX4/FIX5/FIX6 and disposable transaction regressions pass.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (backend directory): sole `202609160001 (head)`.
- `git diff --check`: PASS. Exact scope: contracts helper, two existing focused test modules and this audit. Client/parser/store/models/migrations unchanged.
- `BROAD_BACKEND=SKIPPED_BY_DESIGN`; at most one exact-commit CI snapshot, no waiting/polling.

`ORGANIZATION_ALLOWLIST_UNCHANGED=true`, `IDENTITY_SEMANTICS_UNCHANGED=true`,
`RAW_SOURCE_BYTES_PRESERVED=true`, `NEW_COUNT_ADDED=false`,
`DB_MIGRATION_ADDED=false`, `PRODUCTION_ACTIONS=NONE`,
`PRODUCTION_DB_MUTATION=false`, `LIVE_CBR_REQUESTS=NONE`, `PIT_READY=false`.

The sole next step is independent external review. Production PLAN/APPLY is not
executed or automatically authorized by this code delivery.

## Task265-FIX8 — Canonical objectId binding; raw INN is evidence metadata

Baseline: clean `main@052a629b0a2954398a5b010bb4d3e295c81cb7d7`;
actual remote matched before editing. All deployment, full-universe diagnostic
and external corporate-action facts below are **operator-provided prior
evidence**, not independently production/source-verified during FIX8.

- FIX7 deployed at that SHA; DB revision `202609160001`, backend running, restart count `0`, health/smoke PASS. Evidence `0|0|0`; full read-only PLAN failed `OBJECT_IDENTITY_COLLISION` after `2108` HTTP requests. Mutation/persistence false, source bundle not written, no APPLY, production actions NONE, PIT readiness false.
- Universe: `496` issuer identifiers, `494` eligible and `2` ineligible queries, `2995` bond ISINs.
- Completed search-only diagnostic (no histories): `HTTP_REQUESTS=549`, `ISSUER_QUERIES_COMPLETED=494`, `SEARCH_PAGES_FETCHED=547`, `SEARCH_ROWS_SEEN=3351`, `UNIQUE_OBJECT_IDS=2397`, `REPEATED_OBJECT_IDS=850`.
- `TRUE_CANONICAL_TARGET_COLLISION_COUNT=0`, `MULTIPLE_NONEMPTY_SOURCE_INN_SAME_TARGET_COUNT=3`, `MULTIPLE_NONEMPTY_SOURCE_ISIN_SAME_TARGET_COUNT=0`, `OPTIONAL_SOURCE_INN_OMISSION_COUNT=19`.
- Diagnostic safety: `HISTORY_REQUESTS=0`, mutation/persistence false, source bundle not written, production actions NONE, PIT readiness false.

The complete current source-search universe contained no observed objectId
mapping to more than one canonical target, according to the operator's audit.
This empirical observation is **not an assumption encoded in production code**;
future canonical disagreements remain fatal.

Optional-INN examples: object `222180`, bond `RU000A0JXR43`, empty or
`1435027673`; object `222228`, bond `RU000A10BF48`, empty or `3900019850`;
issuer object `221711`, canonical INN `3900045916`, explicit or absent row INN.
The three multiple-nonempty-INN cases are:

- `222534 / RU000A103760`: `7735057951 → 4401116480`.
- `222546 / RU000A102RF3`: `7735057951 → 4401116480`.
- `224117 / RU000A100YT4`: `7729065633 → 7708397772`.

Operator-researched official MOEX evidence documents HCF Bank → Sovcombank
issuer-parameter changes while retaining both ISINs. Historical MOEX evidence
places `RU000A100YT4` with ООО Экспобанк (2019) and АО Экспобанк (2022), while
CBR documents the legal-entity transformation/replacement. These facts explain
legitimate source INN variation; no succession inference, issuer propagation
or special-cased identifier is added to production code.

**Post-implementation correction:** FIX5/FIX6 sections above historically
required identical raw identifier bindings. Production evidence invalidated
that assumption; those sections are preserved, not silently rewritten.
The corrected rule is:

> objectId collision safety is defined by disagreement in canonical resolved
> target identity, not differences in redundant/raw source identifier fields.

The only production change replaces `(identity[:3], inn, isin)` with
`identity[:3]`: BOND/None/exact ISIN or LEGAL_ISSUER/exact resolved INN/None.
Different bond ISINs, different canonical issuers and bond-versus-issuer targets
still raise `OBJECT_IDENTITY_COLLISION`. Raw source identifiers remain immutable
evidence, not the target binding. ISIN is still included in canonical bond
identity; only its redundant raw copy is removed from the collision key.

Source-row validation and `_event()` are unchanged. Bond INNs remain optional
but format-validated when present; explicit issuer INN mismatch stays fatal;
absent identifiers require the unchanged organization allowlist and exact
eligible advanced-query context. EN DASH parsing, pagination, unknown-agency,
history and object-limit guards remain intact. Client still requests one
history per retained object; distinct agency/current facts remain candidates,
and the existing semantic dedupe handles identical facts. Bond events never
promote raw issuer INN; issuer events use canonical resolved INN as before.

Verification:

- Both existing Task265 focused modules: **228 passed, 0 failed**, exit `0`; one existing pytest cache permission warning.
- Synthetic cases cover both omission/presence examples, all three historical bond shapes, Ozon/RUSAL explicit/omitted issuer rows in both orders, distinct current agency facts, history target lineage, one history per object and canonical order independence. True different-ISIN/target-kind/issuer collisions, malformed metadata and explicit issuer mismatch remain fatal. Existing event dedupe and object-limit guards pass.
- Frozen PLAN/load/offline PREFLIGHT combine optional bond INN, historical nonempty bond INNs and blank/explicit issuer context. Exact response bytes/manifest SHA, deterministic plan hash/canonical events, no offline network and no evidence writes are proven on disposable SQLite.
- New test iterations exposed duplicate synthetic company ticker and an object-limit fixture reaching the earlier shared JSON list guard; both test setups were corrected within scope, without bypassing guards or changing production behavior. The complete final focused rerun passes.
- `python -m compileall backend/app`: PASS.
- `python -m alembic heads` (backend directory): sole `202609160001 (head)`.
- `git diff --check`: PASS; exact scope parser, two existing focused test modules and this audit. Client/contracts/store/models/migrations unchanged; no schema/contract/bundle/count version change.
- `BROAD_BACKEND=SKIPPED_BY_DESIGN`; at most one exact-commit CI snapshot, no waiting/polling.

`OBJECT_ID_BINDING_CANONICAL_TARGET_ONLY=true`,
`TRUE_CANONICAL_COLLISION_STILL_FATAL=true`, `RAW_SOURCE_BYTES_PRESERVED=true`,
`RATING_EVENT_IDENTITY_UNCHANGED=true`, `NEW_COUNT_ADDED=false`,
`DB_MIGRATION_ADDED=false`, `PRODUCTION_ACTIONS=NONE`,
`PRODUCTION_DB_MUTATION=false`, `LIVE_CBR_REQUESTS=NONE`, `PIT_READY=false`.

The sole handoff is independent external review. Production PLAN/APPLY and
subsequent ingestion require separate explicit authorization.
