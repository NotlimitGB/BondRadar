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
