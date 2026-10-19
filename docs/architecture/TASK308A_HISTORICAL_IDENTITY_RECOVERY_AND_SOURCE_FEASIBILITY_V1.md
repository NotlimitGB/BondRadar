# Task308A — Historical Bond Identity Recovery & Source Feasibility v1

## 1. Result and boundaries

Task308A adds offline source qualification, a reproducible diagnostic sample,
separate probe authorization, a bounded read-only probe and offline replay.
It neither repairs Task306A4 evidence nor acquires a complete historical universe.
Development uses synthetic responses and HTTPX MockTransport. No operator probe,
production access, application DB, migration, projection, deployment or Day 0 is
part of delivery. All contracts retain `pit_ready=false`, `acquisition_ready=false`,
`db_mutation=false` and `historical_publication_proven=false`.

## 2. Starting state and supplied observations

Base commit: `6c12e4a36889fa17ec7453d2291633398c32a3ab`.
Task307 CI runs 38164463981 and 38164463999 were successful.
Operator-supplied context describes 10,671 historical SECIDs, all ISINs absent,
and zero conflicting bindings. It is context, never a runtime invariant.

Supplied artifact identities:

| Artifact | SHA-256 |
|---|---|
| Task307 audit | `8f4cb3e25b92a02f9e12e8d07e731c1ed90e760e78194f009164c2227e3b2ffa` |
| Task306A4 manifest | `b7a55de16a510b798e1d8da5f72ce757215257c86c9ee8b34afcf67a2783bce7` |
| Source content | `731aa5e01d44576665aba5b961cbc9740fb931d6d7a47a029b977d81e1de16c4` |

Those production artifacts were not opened during development. A real 36-SECID
plan is generated only from an operator-supplied, validated report; synthetic
test samples are not represented as the production sample.

## 3. Root-cause analysis

Confirmed by code: discovery preserves source fields and recognizes supplied
ISIN/ISINCODE. Missing values remain null; no ISIN-removal filter was identified.
Task307 reports mapped bindings, not an inventory proving every original source
table lacked an ISIN column. Therefore universal source-column absence remains
`REQUIRES_SOURCE_PROBE`, rather than an asserted mapper defect.

Confirmed by official documentation: a separate specification route exists.
Contradicted by documentation: `q=SECID` guarantees an exact reference match.
Unknown: historical inactive/matured/delisted population recoverability through
current endpoints. Synthetic responses cannot resolve those unknowns.

## 4. Reviewed source contracts

| Source | Request and evidence | Access/coverage limitations |
|---|---|---|
| [MOEX REFERENCE /205](https://iss.moex.com/iss/reference/205) | `GET /iss/securities.json?q=SECID&start=N&limit=100`; partial search; securities table and optional cursor | Exact post-filter required; no active/market filter; old-security coverage unknown |
| [MOEX DESCRIPTION /193](https://iss.moex.com/iss/reference/193) | `GET /iss/securities/{SECID}.json`; description name/value specification | Returned SECID must be present; request URL is not returned identity evidence |
| [History /497](https://iss.moex.com/iss/reference/497), [listing /489](https://iss.moex.com/iss/reference/489) | Existing discovery contracts; unchanged | Listing/history visibility is not publication proof or exhaustive universe authority |
| [MOEX listing archives](https://www.moex.com/n32224) | Official directory candidate, no automated endpoint enabled | Archive range, exact identity fields, license and authentication requirements need review |
| [ANNA identifiers](https://anna-web.org/identifiers/) | Numbering-authority/registration research candidate | Format validation does not authenticate an instrument; no universal automated lookup assumed |

Only REFERENCE and DESCRIPTION are implemented network sources. Official docs
were reviewed as documents; no live instrument lookup was performed. The two
MOEX routes are not independent authorities. Archive/NNA candidates are not an
automatic fallback and require separate access/coverage research.

## 5. Input validation and hashes

PLAN reads the strict COMPLETE Task307 report, its canonical audit SHA, source
manifest/content/scope hashes and unchanged A4 implementation fingerprint.
Optional expected audit/manifest hashes must match. If the manifest is supplied,
its own SHA, bindings, policy dates, observed dates and population counts must
agree. PLAN does not rescan the multi-gigabyte archive or claim to have independently
replayed its pages. The trusted upstream claim remains the Task307 report.

Identity membership, duplicate exact pairs, conflicting ISIN sets, boards,
date ordering, listing/observation bounds and population counts are checked.
Different ISIN pairs sharing one SECID are retained, not rejected or collapsed.
Missing or incompatible provenance fails closed. Duplicate JSON keys, nonfinite
JSON values, symlinks and Windows reparse points are rejected by reused helpers.

Canonical JSON uses sorted keys, ASCII escaping and compact separators. Hashes
exclude their own field. New files do not alter any of the nine A4 fingerprint
dependencies. Authorization includes a destination-path hash; no path is copied
into source evidence. Authorization is a reviewed scope object, not a digital
signature or a security credential.

## 6. Deterministic diagnostic sample

Default 36 SECIDs; explicit sample size 1..48 changes the plan SHA. Insufficient
population blocks instead of fabricating samples. Each exact raw SECID is sampled
once, with all original identity pairs attached. Published samples are sorted by
SECID. Round-robin stratum order is:

1. LISTING_ONLY
2. POSSIBLE_INACTIVE_PROXY
3. CANONICAL_OFZ
4. MULTI_OBSERVED_BOARD
5. LISTING_INTERVAL_SUPPLIED
6. LISTING_INTERVAL_MISSING
7. EARLY_FIRST
8. LATE_FIRST
9. OLDER_LAST
10. RECENT_LAST
11. ACTUAL_TRADES
12. USABLE_PRICE_NKD
13. SHARED
14. SINGLE_OBSERVED_BOARD

Each round takes the first SECID ASC not already selected in each stratum;
exhausted strata are skipped. Any remaining capacity is filled from SECID ASC.
The published reason is the selecting stratum. All overlapping memberships,
eligible/selected counts, overlap counts and representation are explicit. There
is no equal-quota promise; an unavailable stratum has zero eligible members.
Early/late and older/recent split at the requested source-range midpoint.
Inactivity proxy means last observation more than 90 days before the last global
observation: it does not prove redemption, maturity, delisting or default.

## 7. Exact matching and ISIN validity

SECID equality is raw exact string equality: no trim/case conversion, names,
ticker, FIGI or requested-identifier injection. Search results for other SECIDs
remain diagnostics. A complete result containing an exact SECID may also contain
unrelated fuzzy candidates; only exact rows contribute a binding. Multiple exact
valid ISINs conflict. Missing SECID in description never gains one from its URL.

ISIN must match uppercase ASCII `[A-Z]{2}[A-Z0-9]{9}[0-9]` and pass the expanded
letter Luhn/modulus-10 check. This proves syntax only. Non-string identity cells,
inconsistent aliases and invalid checksums fail closed. Unsupported supplied type
codes remain review-required rather than silently classified as bonds. ISIN
conflicts across sources and against original nonnull bindings are retained.
Malformed strings beyond 64 characters stay in the bounded raw artifact and are
not repeated in compact ISIN candidate lists; their invalid status is retained.

## 8. Authorization and modes

`PLAN` and `PROBE_EVALUATE` are offline. `PROBE_AUTHORIZE` requires explicit
confirmation, exact reviewed plan SHA, a unique selected subset of at most 12
SECIDs, source families, limits and a private existing evidence directory.
`PROBE` additionally requires `--confirm-network`. Neither PLAN nor a sample of
36 automatically grants 36 requests. Changing subset, destination, source family
or budget invalidates authorization binding. Evidence/output directories must be
separate and cannot lie inside the discovery evidence root.

## 9. Fixed transport and resource ceilings

HTTPS `iss.moex.com` only; fixed GET routes; safe path SECIDs limited to
`[A-Za-z0-9._-]{1,64}`. No arbitrary URLs, redirects, environment proxy inheritance,
credentials, generic RPC, concurrency or database calls. TLS verification remains
enabled. Requests explicitly ask for identity encoding; compressed responses are
rejected to avoid unbounded decompression.

| Resource | Default maximum |
|---|---:|
| Approved SECIDs | 12 |
| HTTP attempts, including retries | 72 |
| Accepted pages | 48 |
| Decoded response body | 8 MiB |
| Total counted decoded bodies | 64 MiB |
| Elapsed probe time, including waits | 300 seconds |
| Evidence directory | 128 MiB |
| Free-space reserve | 8 GiB |
| Compact output set | 8 MiB |
| Task307 input / optional manifest | 32 MiB / 8 MiB |
| Suggested future container | 1 GiB RAM / 1 CPU |

Checks run before each request and during streaming. No more than two request
starts per second; retries use 0.25/0.50 seconds plus that rate limit, with numeric
Retry-After capped at 60 seconds. Three attempts maximum per failed page, only
for known timeout/network/protocol and HTTP 408/429/500/502/503/504 failures.
Other HTTP or semantic/table errors do not retry. Pagination and accepted pages
consume global ceilings. No retries after byte/time budget stop, including resume.

The consumer pulls at most 4096 decoded bytes per chunk and refuses a pull that
could cross its remaining budget. An exact full-chunk budget boundary may be
conservatively INCOMPLETE because EOF cannot be established without another pull.
Counters measure consumed decoded body bytes, not TCP/TLS/header traffic or HTTPX
internal receive buffers. Resource gates do not claim a measured production RSS.
Synchronous timeout/deadline checks are cooperative, not a process-kill guarantee.

## 10. Completeness and source errors

REFERENCE cursor values require exact integer INDEX/TOTAL/PAGESIZE, correct
offset/row count, stable total/page size and cursor mode. Without cursor, only
an empty terminal page proves completion; a short nonempty page does not. Missing,
repeated, overlapping or contradictory pages never prove completeness.
DESCRIPTION is one strict specification table, with unique name fields.
All tables require unique case-insensitive column names and aligned row lengths.
Duplicate keys, missing tables, malformed JSON and nonfinite values are invalid,
not successful empty responses. Raw exceptions, headers, cookies and rejected
response bodies are never published.

## 11. Immutable evidence and resume

Each attempted request has an immutable intent before network access, followed by
an immutable receipt containing exact query, offset, hash, observation/retrieval
time and previous receipt SHA. Accepted valid response bytes are content-addressed
and immutable. Failed responses have sanitized categories and available byte/hash
diagnostics, not raw error bodies. Operational state and derived probe manifest
are replaceable checkpoints; accepted intents/receipts/responses are not.

Resume replays all accepted evidence, the request/receipt chain and authorization
scope before making another request. Completed pages are not fetched again.
An intent with unknown outcome blocks automatic retry: operator reconciliation
or a separately reviewed new authorization is required. Limits and elapsed time
persist across resume, including a frozen UTC deadline that counts downtime between
processes. A byte/time budget stop is permanent for that authorization.
Private writer locks exclude concurrent writers. Controlled output failure removes
newly published members of the set; an abrupt process death may leave an incomplete
set, which never has a COMPLETE wrapper and can be reconciled by identical replay.

## 12. Offline evaluation and metrics

Replay validates authorization, raw-byte hashes, chain, source tables, identities,
ISIN syntax, cursor state and manifest counts. The report preserves source-level
unavailable/incomplete/invalid/transient/ambiguous/conflicting results and every
selected SECID, including unapproved/unprobed units. All metric denominators are
explicit: sample, approved units, attempted requests, source queries or candidate
ISIN values as appropriate. Metrics include complete responses, transient attempts,
valid syntax, exact current bindings, missing ISIN, unresolved counts and reference-
only/description-only/mutually consistent matches, with stratum breakdowns.

Feasibility is sample-qualified, never an acquisition grant. No actual attempt gives
NOT_EXECUTED. All approved units recovered without review plus COMPLETE acquisition
gives FEASIBLE_ON_OBSERVED_SAMPLE; some gives PARTIALLY_FEASIBLE_ON_OBSERVED_SAMPLE;
otherwise INCONCLUSIVE. Input/contract failures return a stable BLOCKED wrapper.

## 13. Current observation versus historical proof

Historical trading/listing dates remain in original sample identities. Receipt
times are acquisition completion observations, also exposed separately as retrieval
times; they do not claim the source's effective/publication time. Source-reported
publication/effective strings are preserved when present, with authority UNPROVEN.
Current exact binding can be true while historical publication and PIT remain
false. Decision cutoff remains 00:00 UTC T: neither entry-day market evidence nor
today's issuer/reference observation becomes information known before that cutoff.

## 14. Historical source follow-up

A: exact current mapping found — review binding and instrument type, retain current
provenance. B: exact row without ISIN — investigate dated official directories or
registration/disclosure records. C: exact SECID absent — historical archive research,
not a declaration of irrecoverability. D: contradictory mappings — manual official
identity/version reconciliation. E: incomplete/invalid transport — resolve the
specific source contract/budget problem before interpreting absence.

Archive research must establish exact fields, time range, dated publication
authority, licensing/access and identity binding. Neither MOEX archive nor ANNA
research is assumed sufficient for the historical population. Current bindings do
not authorize canonical projection or verified historical issuer linkage.

## 15. Targeted tests and compatibility

The two new focused modules cover qualification/tampering, deterministic sampling,
different pairs per SECID, syntax, exact/fuzzy/conflicting matching, four runner
modes, scope refusal, strict tables/cursors, transport failures/retries, durable
interruption, immutable replay, resume and resource refusal. HTTP uses only
MockTransport; sleeps and clocks are injected. Static tests exclude SQLAlchemy,
model/mutation/sync/environment entry points. Task307 and existing source/issuer
regressions remain unchanged. Broad regression is delegated to exact-commit CI.

## 16. Separate future operator steps A–F

These commands are documentation only and were not executed. Use a separately
reviewed pinned checkout/image, evidence mounted read-only and distinct writable
report/probe directories. Create the named empty output directories beforehand.
Run A/B/C/E/F with network disabled. D alone requires separate explicit operator
permission and reviewed egress to the one allowed host. No automatic A→F chain.

A. Offline plan (first command; approved paths must exist):

```sh
python scripts/historical_identity_feasibility.py --mode PLAN \
  --task307-report /reports/task307-coverage-audit.json \
  --manifest /evidence/manifest.json \
  --expected-audit-sha256 8f4cb3e25b92a02f9e12e8d07e731c1ed90e760e78194f009164c2227e3b2ffa \
  --expected-manifest-sha256 b7a55de16a510b798e1d8da5f72ce757215257c86c9ee8b34afcf67a2783bce7 \
  --output-dir /reports/task308a-plan
```

B. Inspect the plan, its SHA, strata, source matrix and exact selected SECIDs:

```sh
cat /reports/task308a-plan/task308a-sample-summary.md
```

C. Separately authorize at most twelve reviewed SECIDs (replace the explicit
shell variables with the reviewed values; do not select them automatically):

```sh
python scripts/historical_identity_feasibility.py --mode PROBE_AUTHORIZE \
  --sample-plan /reports/task308a-plan/task308a-sample-plan.json \
  --expected-plan-sha256 "$REVIEWED_PLAN_SHA" --secids "$SECID_1" "$SECID_2" \
  --sources REFERENCE DESCRIPTION --max-requests 72 --max-pages 48 \
  --response-bytes 8388608 --total-response-bytes 67108864 --max-seconds 300 \
  --evidence-dir /probe/task308a --output-dir /reports/task308a-authorization \
  --confirm-authorize
```

D. Only after a separate live-probe decision:

```sh
python scripts/historical_identity_feasibility.py --mode PROBE \
  --sample-plan /reports/task308a-plan/task308a-sample-plan.json \
  --expected-plan-sha256 "$REVIEWED_PLAN_SHA" \
  --authorization /reports/task308a-authorization/task308a-probe-authorization.json \
  --evidence-dir /probe/task308a --output-dir /reports/task308a-probe \
  --confirm-network
```

E. Offline replay and evaluation:

```sh
python scripts/historical_identity_feasibility.py --mode PROBE_EVALUATE \
  --sample-plan /reports/task308a-plan/task308a-sample-plan.json \
  --authorization /reports/task308a-authorization/task308a-probe-authorization.json \
  --evidence-dir /probe/task308a --output-dir /reports/task308a-evaluation
```

F. Inspect all per-source outcomes, denominators, conflicts and limitations:

```sh
cat /reports/task308a-evaluation/task308a-feasibility-summary.md
```

## 17. Delivery and acceptance limits

One Task308A-only commit and one normal push. Record the exact remote SHA and one
immediately available CI snapshot; pending is not success. No existing A4/307 code,
fingerprint, model or migration changes. Implementation readiness does not imply
source feasibility, a real production sample, full-universe recovery or PIT proof.

## 18. Next safe step

Independent code/SHA review and green CI, then an explicitly authorized offline
operator PLAN against the existing Task307 artifact. A small live probe requires
another visible authorization decision. Task308B can be specified only after
reviewing exact sample results, unresolved/conflicting strata, source completeness,
resource costs and realistic archive access. No automatic Task308B, mass acquisition,
canonical APPLY, replay, trading or official 90-day activation follows this package.
