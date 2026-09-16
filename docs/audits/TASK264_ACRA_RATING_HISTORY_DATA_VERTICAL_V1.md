# Task264 — ACRA Rating History Data Vertical v1

## Delivery status

```text
STATUS=PASS
IMPLEMENTATION_STATUS=PASS
COMMIT_ACCEPTANCE=OFFLINE_VERIFICATION
PRODUCTION_LIVE_VALIDATION=PENDING
PRODUCTION_INGESTION=PENDING
LIVE_DOM_FULL_COVERAGE=false
START_SHA=bc745a3d29ed4b26655cbbd3e7d3f684128bc195
ALEMBIC_HEAD=202609150003
SCHEMA_CHANGE=false
PRODUCTION_READY=false
```

Task264 implementation is complete under the Task264-FIX2 offline acceptance contract. Offline verification passed; external operator live validation remains required before production ingestion. FIX1 verified TLS and distinguished the sandbox socket failure from a subsequent catalogue access-marker block outside sandbox. Current DOM compatibility, history completeness, and production runtime behavior are not independently proven. No client/parser safety guard was weakened to change acceptance.

## Source and completeness boundary

The adapter targets `https://www.acra-ratings.ru/ratings/issuers/` with `lang=en`. It discovers numeric issuer-detail links and source-provided next-page links, never sequential issuer IDs. Separate securities-issues catalogue and historical press-release capabilities are operator-provided prior research only; this task neither crawls them nor independently verifies them.

```text
SOURCE_PROVIDER=ACRA
ISSUER_HISTORY_SCOPE=ACRA_ISSUER_PAGE_EXPOSED_HISTORY
ISSUE_HISTORY_SCOPE=ACRA_ISSUER_PAGE_EXPOSED_ROWS_ONLY
ACRA_ISSUER_HISTORY_SOURCE=current public issuer page
ACRA_ISSUE_HISTORY_COMPLETE=false
ISSUE_HISTORY_COMPLETE=false
STRICT_PIT_COMPLETE=false
```

## Access policy

The client is HTTPS/GET-only, verifies TLS, uses no environment proxy or authentication, and clears cookies before each request. It identifies itself as `BondRadar-credit-research/1.0`. Requests are sequential with at least one second between attempts, bounded response sizes, same-host redirects, and up to three attempts for selected transient HTTP states/timeouts. TLS and other connection failures are not bypassed.

Robots are evaluated before discovery. Explicit disallow blocks discovery; a technical robots failure also blocks it. Robots 404 is distinguished from technical failure. CAPTCHA/HTTP blocks are not bypassed. Robots permission is not a redistribution licence.

## Semantic parser

BeautifulSoup supplies structural traversal; semantic headings, table headers, captions, metadata labels, and URL patterns define supported structures. The parser excludes ESG and non-credit sections. It retains source rating scale/value/outlook/watch/action after whitespace normalization, including `Withdrawn` and national/international scale separation. Explicit English month parsing does not depend on locale.

Current and history rows are deduplicated by source semantics before planning; their presentation section is not event identity. Explicit issue rows alone may produce bond events. Missing issue scale is never guessed. Structural ambiguity blocks readiness rather than silently losing a potentially in-universe issue.

**Limitation:** the supported HTML structures have been tested synthetically only. Live ACRA DOM compatibility is an external production acceptance gate, not a prerequisite for committing offline-verified code and not a completed claim.

## Task264-FIX2 external semantic evidence and acceptance

The project lead provided the following observations as of 2026-09-16. They are
operator-provided evidence about the semantic source model, not Codex-observed
payloads, saved HTML, or proof that the implemented DOM bindings match these pages.

| Operator-observed source | Provided semantic observation |
|---|---|
| Issuer catalogue `/ratings/issuers/?lang=en` | Issuer discovery/search, including issuer/TIN search |
| Sistema issuer page | INN `7703104630`, current issuer credit rating, history, national scale and issue rows with ISINs |
| Severstal issuer page | INN `3528000597`, current issuer credit rating, history and issue ISIN `RU000A10FPA5` |
| Russian National Reinsurance Company issuer page | Separate international and national rating scales |
| ACRA product sections | ESG/non-credit products separate from Credit rating |

```text
CODEX_LIVE_NETWORK_GATE=ENVIRONMENT_BLOCKED
CODEX_SANDBOX_SOCKET_DENIED=true
CODEX_LIVE_PROBE=ENVIRONMENT_BLOCKED
CODEX_OUTSIDE_SANDBOX_CATALOG_PROBE=ACCESS_MARKER_BLOCKED
LIVE_SOURCE_VALIDATION_RESPONSIBILITY=PROJECT_LEAD_OPERATOR
EXTERNAL_SOURCE_SEMANTIC_VALIDATION=PASS_OPERATOR_PROVIDED
LIVE_DOM_FULL_COVERAGE=false
PLAN_IS_LIVE_GATE=true
PLAN_FAIL_CLOSED=true
PRODUCTION_LIVE_VALIDATION=PENDING
PRODUCTION_INGESTION=PENDING
```

The environment-blocked labels refer to the sandbox diagnostic only; the separate
outside-sandbox marker result below is retained and is not reclassified as a
socket failure. Neither result proves an ACRA automation prohibition.

Production source validation has not yet been performed. No production
PLAN/PREFLIGHT/APPLY is authorized by this delivery. Following separately
authorized operator access validation, PLAN is the real read-only live integration
gate: robots, accessibility, complete discovery, HTML/identity/rating semantics,
universe matching and immutable collisions must all pass. Unsupported structures
fail closed rather than silently discarding relevant evidence. PREFLIGHT/APPLY
replay only the frozen bundle, without source network access. A ready PLAN is not
itself permission for production APPLY.

## Identity and Task263 preview

Issuer candidates require exact canonical INN in the verified LegalIssuer universe; bond candidates require exact canonical ISIN in the Bond universe. Final resolution reuses Task263. Missing issuer INN does not preclude explicit in-universe issue rows. There is no fuzzy, SECID, title, transliteration, or issuer-to-bond inference.

Task263 now exposes an immutable read-only rating preview. Its existing persist method delegates to the same validation/fingerprint path. Source contracts, schema, event fingerprints, resolution collision semantics, and transaction ownership are unchanged.

## Frozen bundle and modes

PLAN uses verified PostgreSQL read-only transactions, reads the universe before network access, and verifies it again before publication. It stores exact HTML payloads under SHA-256 filenames and publishes a strict manifest last, with no overwrite. Page provenance, ordered universe, derived events, initial counts, and canonical hash are frozen together. Source URL is retained alongside the payload hash to avoid conflating identical bytes from different pages.

PREFLIGHT validates all offline inputs before engine construction, re-parses exact bytes, checks the authorized plan hash, and then performs read-only schema/universe/resolution/collision checks. It performs no network or filesystem writes.

APPLY validates offline first and rechecks guards under fixed identity/evidence table locks with a five-second lock timeout. It uses only frozen payloads and the Task263 store, persists contributing pages only, reads back full immutable semantics/counts before one commit, and performs separate read-only post-commit reconciliation. Exact retries reuse rows. Changed resolution or immutable semantics collide rather than update.

Commit uncertainty produces an explicit outcome-unknown action and reconciliation requirement with mutation unknown, never an automatic retry. Known writes remain reported if later reconciliation/cleanup fails. Zero-write retries report `production_actions=NONE`.

Publication precision is `DATE`: visible date is stored in `event_date` and `publication_date`, never fabricated as a timestamp. Retrieval time remains separate. Current-page history does not prove historical payload availability.

## Measured local verification

```text
FOCUSED_AND_TASK263_TESTS=30_PASS
COMPILEALL=PASS
GIT_DIFF_CHECK=PASS
ALEMBIC_HEAD=202609150003
SYNTHETIC_PARSER_PROOF=true
DISPOSABLE_SQLITE_RUNNER_PROOF=true
PRODUCTION_POSTGRESQL_RUNTIME_PROOF=false
```

Command:

```text
python -m pytest -q backend/tests/test_acra_rating_parser.py backend/tests/test_acra_rating_client.py backend/tests/test_acra_rating_runner.py backend/tests/test_credit_risk_evidence.py
```

Coverage includes synthetic current/history/multiscale/withdrawal/ESG parsing, robots/discovery/retry/redirect/size controls, unavailable-robots/access-marker blocking, unsupported in-universe structure blocking without a published bundle, frozen tampering/determinism, universe changes, offline-before-engine rejection, disposable APPLY/readback/idempotency, and commit uncertainty. PostgreSQL table locking is implemented but not production-runtime-proven. Pytest emitted a cache permission warning; all 30 tests passed.

## Bounded live gate

Planning exploration was conservatively charged five attempts (including unsuccessful web fetches and TLS failures). The implemented-client probe was allowed at most five additional attempts and stopped on its first robots transport failure.

```text
LIVE_PROBE=BLOCKED
LIVE_PROBE_ERROR_CODE=SOURCE_TRANSPORT_FAILURE
LIVE_PROBE_HTTP_REQUESTS=1
TASK_TOTAL_CONSERVATIVE_REQUEST_ATTEMPTS=6
LIVE_PROBE_LIST_PAGES=0
LIVE_PROBE_ISSUER_PAGES=0
PARSER_CURRENT_RATING=NOT_VERIFIED_LIVE
PARSER_HISTORY=NOT_VERIFIED_LIVE
PARSER_ISSUES=NOT_VERIFIED_LIVE
MULTISCALE=NOT_VERIFIED_LIVE
LIVE_HTML_SAVED=false
LIVE_PROBE_DATABASE_ACCESSED=false
```

No additional crawl was attempted after this failure. The failure is technical negative evidence about this environment, not evidence of absent ratings or denied automation rights.

## Task264-FIX1 transport diagnosis and bounded live result

Observation date: 2026-09-16 UTC. HEAD and local `origin/main` remained
`bc745a3d29ed4b26655cbbd3e7d3f684128bc195`. The existing 13-file Task264 scope
was preserved. No client or CA compatibility change was justified or applied.

Local diagnostics: Python 3.12.10, OpenSSL 3.0.16, httpx 0.28.1, certifi
2026.02.25. DNS returned IPv4 `81.22.46.142`, with no IPv6 address returned.
`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE` and
`SSL_CERT_DIR` were absent. The clock date agreed with the supplied environment
date; independent clock synchronization was not proven. A standalone OpenSSL
executable was unavailable. The client already uses `ssl.create_default_context()`
with certificate and hostname verification and `trust_env=False`.

The diagnostic actual-client request inside sandbox raised
`AcraError → httpx.ConnectError → httpcore.ConnectError → PermissionError`,
with `WinError 10013` (socket access denied). This is an environment connection
restriction, not proof of a certificate failure. Outside sandbox, the one direct
verified httpx robots request completed with HTTP 404. The one verified curl
request returned `Forbidden`; its status was not recorded, so it is not evidence
of robots policy or a confirmed source-wide block. Earlier reported certificate
errors remain prior observations, not the cause of the current completed GETs.

The execution-time live gate used the actual unchanged client outside sandbox,
with a six-attempt cap, sequential requests, pacing, and verified TLS:

| Resource | HTTP status | Result |
|---|---:|---|
| `/robots.txt` | 404 | Unavailable; existing rules allow both issuer-list paths |
| `/ratings/issuers/?lang=en` | 200 | Existing CAPTCHA/`cf-chl-` detector raised `SOURCE_ACCESS_BLOCKED` |

The blocked catalogue body was neither saved nor passed to discovery/parser.
The detector's marker alone does not prove a genuine challenge page or an
explicit automation prohibition; its specificity is unresolved. The guard was
not weakened and no additional retrieval or issuer crawl followed.

```text
FIX1_LIVE_PROBE=BLOCKED
FIX1_BLOCKER=CATALOG_ACCESS_MARKER
FIX1_HTTP_REQUESTS=2
FIX1_LIST_PAGES=0
FIX1_ISSUER_PAGES=0
FIX1_ROBOTS_HTTP_STATUS=404
FIX1_ROBOTS_RESULT=UNAVAILABLE
FIX1_TLS_VERIFICATION_PASSED=true
FIX1_FOCUSED_AND_TASK263_TESTS=29_PASS
FIX1_COMPILEALL=PASS
FIX1_ALEMBIC_HEAD=202609150003
FIX1_GIT_DIFF_CHECK=PASS
CLIENT_FIX_REQUIRED=NOT_PROVEN
TLS_VERIFICATION_DISABLED=false
LIVE_DOM_COMPATIBILITY=NOT_PROVEN
LIVE_HTML_SAVED=false
COMMIT=NONE
PUSH=NONE
```

A synthetic regression verifies that robots 404 cannot bypass either marker,
even with catalogue HTTP 200, and that no issuer request follows. Further source
access or marker-specific investigation requires a separately bounded decision;
no alternate source is selected automatically.

## Safety and next gate (unchanged invariants)

```text
PRODUCTION_ACTIONS=NONE
PRODUCTION_DB_MUTATION=false
SCHEMA_CHANGE=false
DEFAULT_INGESTION=false
ISSUER_TO_BOND_INHERITANCE=false
RATING_SCORING=false
CROSS_AGENCY_NORMALIZATION=false
PIT_READY=false
CFA_IMPLEMENTED=false
CI_QUERIED=false
CI_WAITED=false
CI_POLLED=false
```

The only next safe step after offline-verified code delivery is separately authorized operator live/source-access validation before production ingestion. Verified TLS succeeded in FIX1, but the catalogue was stopped by the unchanged access-marker guard. No production PLAN/PREFLIGHT/APPLY, deployment, migration, or later task is authorized by these local results. FIX2 permits commit/push after offline gates and scope/remote checks; it does not promote production readiness or PIT quality.
