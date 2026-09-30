# Task296C2 — MOEX Transient Resolution Stabilization & Frozen Admission Evidence v1

## 1. Execution profile

Development-only read-source reliability and pure replay foundation. One commit,
one normal push, no deployment or production execution. Baseline:
`e4eeba78589deed25060d3de1985df76fb9f75a7`. Existing temporary directories remain
untouched. Tests use synthetic source rows and HTTPX MockTransport.

## 2. Context

Task295 supplies the current bond universe; Task296 supplies exact identity
evidence; Task296B determines admission. Task296C1 independently verifies issuer,
board and description sources. Task297 authorization and mutation boundaries are
unchanged. This package neither executes nor authorizes those production steps.

## 3. Reported diagnostic evidence

Operator-supplied observations reported `RU000A10FWR5` changing from SOURCE_ERROR
to EXACT_ISIN_RECOVERED between two passes over the same frozen upstream inputs.
The recovered SECID was `RU000A10FWR5`, board TQCB and issuer INN `9703246300`.
Those observations explain the regression shape; they were not independently
queried here and are not runtime constants or expected production counts.

## 4. Cause and limitations

The existing issuer source service catches acquisition exceptions and returns
SOURCE_ERROR. Without typed acquisition classification or bounded retry, an
intermittent source failure can change the finalized evidence supplied to
Task296B. This does not establish nondeterminism in its pure admission logic.
Retry reduces transient failures; it cannot guarantee two future acquisitions
will produce identical market evidence. Freeze and replay provide reproducibility.

## 5. Required separation

The boundary is acquisition → bounded transient retry → unchanged exact resolver
→ finalized resolutions → frozen full evidence → existing Task296B. Semantic
resolution and acquisition diagnostics are distinct. Failures remain explicit,
never inferred no-matches or successful identities.

## 6. Scoped changes

Seven files: MOEX client, issuer source service, issuer source tests, new frozen
schema/service/tests and this document. No Task296B/C1/297 edits, models,
migrations, frontend, operational scripts, ingestion or sync changes.

## 7. Error classification

The Security Reference JSON-request path uses sanitized MoexIssClientError
categories: TIMEOUT, TRANSIENT_TRANSPORT, TRANSIENT_HTTP, HTTP_REJECTED,
TRANSPORT_REJECTED and INVALID_RESPONSE. Unknown exceptions become a sanitized
UNKNOWN_SOURCE_ERROR diagnostic. No raw HTTP exception, URL, body, headers or
credentials are placed in error messages; HTTP exception chaining is suppressed.
TLS, HTTP client injection, timeouts and pagination limits remain unchanged.

## 8. Retry contract

Each acquisition query gets at most three attempts. Retryable failures are HTTPX
timeout/network/remote-protocol errors and HTTP 408/429/500/502/503/504. Delays
before the second/third attempts are 0.25/0.50 seconds; sleeper is injected and
replaced in tests. Other HTTP statuses, unknown exceptions, malformed responses
and pagination exhaustion are not retried. Partial pagination results from a
failed acquisition are discarded before retrying the query from its first page.

## 9. Semantic and fallback behavior

Successful acquisition continues through the unchanged resolve_security_reference
function. Missing identity, valid no-match, ambiguity and conflicts are not retry
conditions. The existing SECID → ISIN fallback remains. Each query has its own
three-attempt bound; a two-query lookup therefore has at most six acquisition
attempts. Fallback failures never refetch the successful first query. Exhaustion
returns SOURCE_ERROR with empty match fields, not authoritative zero candidates.

## 10. Acquisition API and diagnostics

`lookup` retains its interface and adds primitive diagnostic fields to its frozen
resolution dataclass; its `acquisition` property exposes typed diagnostics.
`lookup_isins(*, source_isins)` validates a deterministic sequence of unique
nonblank strings before requests, sorts it, and returns typed finalized rows.
An empty sequence makes no requests. Original source ISIN strings are retained.
attempt_count counts fetch_security_reference_candidates invocations, not HTTP
pages. source_query_count keeps its existing logical-query meaning. Diagnostics
include transient_failure_count, final_status and last_failure_category.

## 11. Artifact schema

`tinvest-frozen-admission-evidence-v1` is strict, frozen, extra-forbid and
pit_ready=False. It stores full Task295 bonds, internal Bond identity projections,
CORE IDs, Task296 bridge, every queried unmatched exact ISIN, final MOEX resolution
and diagnostics, and the full Task296B manifest. Issuer and board facts are retained
even when Task296B candidate aggregates do not contain those fields. Counts and
candidate ISIN/SECID/row hashes accompany actual content rather than replacing it.

## 12. Canonicalization and timestamps

Inputs are ordered by UID, Bond ID, exact source ISIN and sorted CORE IDs. Canonical
JSON uses sorted keys, ASCII escaping, compact separators and allow_nan=False.
artifact_sha256 excludes only itself and captured_at; diagnostics are hashed.
captured_at must be an explicitly supplied UTC-aware datetime and is untrusted,
unhashed capture metadata. Identical content at another timestamp has the same
SHA. Upstream input hashes and candidate hashes use the same canonical primitives.
Candidate hashes retain Task296C ordering and actual Task296B row semantics.

## 13. Validation and safe content

build/validate reject duplicates, missing/extra resolutions, incorrect original
types, invalid diagnostics, contract/PIT drift and inconsistent upstream identity.
Original model types are revalidated because model_copy/model_construct can
bypass Pydantic validation. source_fields must be native finite JSON values;
credential-bearing keys such as authorization/token/password/cookie/headers/API
key/secret are rejected rather than removed. No request/response objects or raw
exceptions are accepted. Typed errors contain fixed codes only. Own deep copies
prevent caller dictionary changes from altering a built snapshot. Later changes
to nested mutable content are detected by validation; frozen models are not an
authorization signature or recursive immutability guarantee.

## 14. Pure replay API

TInvestFrozenAdmissionEvidenceService exposes keyword-only build with source_bonds,
internal_bonds, core_m3_complete_bond_ids, identity_bridge, finalized_resolutions
and captured_at; serialize returns canonical bytes. parse accepts only bytes or
text, rejects duplicate JSON keys/nonfinite values/unknown fields, validates the
schema and hashes, and rebuilds the full admission. validate compares the complete
canonical rebuilt artifact. replay returns the existing Task296B builder output.
There is no filesystem I/O, DB, environment, client or source acquisition in these
helpers. Actual artifact storage remains a separately controlled operator step.

## 15. Readiness and SOURCE_ERROR

An artifact containing SOURCE_ERROR is serializable, valid and diagnostically
replayable but acquisition_complete=False and ready_for_production_freeze=False.
Fully acquired semantic no-match/conflict evidence can be frozen without making
those securities import candidates. Empty queried sets are complete when all
remaining evidence is valid. Readiness refers only to this acquisition boundary,
not C1 board/description readiness, nonempty READY, M3 readiness or Task297 apply
authorization. No source failure is promoted or discarded to stabilize a count.

## 16. Exact diff

diff validates both inputs, then returns sorted added/removed queried ISINs,
changed_resolution_isins, semantic unchanged_count and field-level old/new values.
Acquisition-only changes have separate IDs and field diffs. Candidate additions,
removals and changes compare actual Task296B candidate content. For example an
issuer INN change is resolution drift without candidate-row drift if that field
is absent from the candidate contract. No rows are inferred from hashes alone.

## 17. Tests and acceptance

Focused hermetic tests cover RU000A10FWR5 transient recovery, exhaustion, HTTP and
transport classifications, semantic no-retry, fallback and pagination boundaries,
replay/roundtrip, tampering, permutations, timestamp/hash separation, SOURCE_ERROR
readiness, exact diff, credential rejection and input/Decimal-context preservation.
Static checks protect the pure artifact boundary. Relevant Task296/B/C1 focused
regressions run after the source/artifact suites; full local regression is deferred
to exact-commit CI. Tests inject the sleeper and never use live MOEX.

## 18. Capabilities and safety

```ini
BOUNDED_TRANSIENT_MOEX_RESOLUTION_RETRY_READY=true
FROZEN_FULL_ADMISSION_EVIDENCE_READY=true
OFFLINE_ADMISSION_REPLAY_READY=true
EXACT_FROZEN_EVIDENCE_DIFF_READY=true
SOURCE_ERROR_PROMOTION_ALLOWED=false
AUTOMATIC_PRODUCTION_FREEZE_READY=false
PRODUCTION_IMPORT_AUTHORIZED=false
TASK297_AUTHORIZATION_CHANGED=false
DATABASE_PERSISTENCE=false
SCORING_READY=false
RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

No VDS, production DB, tokens, production preflight/freeze/import, deployment,
Task297 PLAN/APPLY, live requests or real/shared mutations occur in this package.

## 19. Delivery

After focused acceptance, compile and working/staged diff/scope review, verify
remote concurrency and create one `Stabilize MOEX admission evidence` commit.
Push normally once and obtain available exact-commit CI run/status without lengthy
polling. No rebase, amend, force push or deployment. Hashes establish content
integrity and replay agreement, not cryptographic authentication of official data;
source acquisition and operator custody still require independent review.

## 20. Handoff and hard stop

Independent code, exact-SHA and CI review is the next safe step. Read-only
production acquisition/freeze and C1 preflight require separate instructions;
Task297 PLAN/APPLY authorization remains separate. Token rotation is an operator
action outside this package. No downstream execution follows the push.
