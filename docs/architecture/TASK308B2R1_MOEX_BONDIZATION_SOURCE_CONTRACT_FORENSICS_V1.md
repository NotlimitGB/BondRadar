# Task308B2R1 — MOEX bondization source-contract forensics

## 1. Purpose and request lock

This package adds offline forensics and a separately authorized diagnostic capability. It does not repair the Task308B2 parser, reinterpret its rejected requests, rewrite its contracts, or authorize a live pilot. Eight new files contain the schema, offline service, pair-preserving inspector/HTTP client, diagnostic journal service, runner, two test modules and this document. Task306A4/307/308A/B1/B2, their fingerprints, models and migrations remain unchanged. No application DB, production/VDS, deployment, import, return calculation or official Day 0 is involved.

## 2. Operator-supplied context, not independently replayed production

The request supplied these historical B2 semantic hashes:

| Artifact | SHA-256 |
| --- | --- |
| PLAN | `477c1c1cc2c154503405205aa3f811a2de583b4ca689ba832731da05d0503a52` |
| Authorization | `737a4bec5a271de497aa4ba5877d7bab3b0fa5737cb47a9568136d69dd587d60` |
| Probe | `fe9d14f4e4b69212cff17f7cc0a7b6e423df03f65c282697788dac2545164013` |
| Evaluation | `73cb99d457182d44e5f65a1fcc9d7904a37b73e4081efff22b9081068110b579` |

Operator counts: six authorized SECIDs, 24 partitions/attempts, twelve accepted pages, 86,836 received bytes; PARTIAL probe and `CASHFLOW_SOURCE_FEASIBLE_WITH_LIMITATIONS` v1 evaluation. Coupons and redemptions each had six `DUPLICATE_SOURCE_KEY` errors; six amortization pages completed and six offers pages lacked completion proof. Eleven rows were retained, six passed v1 field checks, five failed; all eleven lacked monetary-unit authority and five lacked monetary amounts.

The supplied offers description says five empty responses, one with one row and one with four rows: seven responses despite six offers partitions. This inconsistency is explicitly unverified context. Runtime counts come from the actual supplied journal/evaluation, never these constants. No production evidence was accessed or independently replayed during development.

## 3. Source-contract review

The reviewed [MOEX ISS developer manual, version 1.4](https://www.moex.com/files/4be999zbzp80bx2bgmwayrtyx0) describes `iss.meta` as metadata inclusion and `iss.only` as response-block selection. This is general protocol documentation, not proof of monetary units or cursor support for these specific bondization tables. No reviewed field-specific unit authority was established. Third-party examples are not authority for changing financial interpretation.

The fixed existing route remains `https://iss.moex.com/iss/statistics/engines/stock/markets/bonds/bondization/{raw_secid}.json`. Whether requesting `<table>.cursor` affects duplicate output is a hypothesis. New response bytes, not documentation or prior receipt categories alone, are needed to diagnose it. Development never calls that source route.

## 4. Public operations and immutable versions

`HistoricalCashflowSourceForensicsService.forensic_plan(...)` accepts explicit original B2 artifact paths, evidence root, `OriginalHashes`, selected SECIDs/tables and optional lowered diagnostic limits. It returns a frozen `ForensicPackage` containing a report and separately hashed plan. `HistoricalCashflowDiagnosticProbeService.authorize(...)` and `.probe(...)` implement the explicit authorization boundary. The offline service's `.diagnostic_evaluate(...)` replays the diagnostic evidence.

Runner modes are exactly `FORENSIC_PLAN`, `DIAGNOSTIC_AUTHORIZE`, `DIAGNOSTIC_PROBE`, `DIAGNOSTIC_EVALUATE`. Each is a separate invocation. Frozen extra-forbid contracts and stable BLOCKED/PARTIAL outputs use new `bondization-source-forensics-v1` and `bondization-diagnostic-*-v1` versions. Existing B2 versions are not aliases or compatibility fallbacks.

Stdout uses one strict `OperationEnvelope` shape across all modes and failure paths: operation, status, blockers and a typed nullable result, with the same false safety declarations. Published artifact JSON remains the corresponding main contract rather than a wrapper.

## 5. Original evidence verification

Four expected semantic hashes are mandatory. The original B2 validator/replay checks authorization, journal chains, request ordering, exact accepted body hashes, cursor decisions and published probe result. The existing evaluator is rerun and its complete result compared with the supplied original evaluation. All file byte fingerprints are checked before and after the operation; the runner repeats the offline verification before publication.

The original root is read only, without creating a lock or checkpoint there. Existing B2 destination hashing requires the original absolute evidence path. A container must mount the original evidence read-only at that same path; copying it to a different logical path is not a reason to relax authorization validation.

The diagnostic plan also binds hashes of the verified physical source directories, without exposing their addresses. These guards reject report/authorization/evidence destinations inside original input directories even if they contain no marker file. Moving original input files to another directory requires a new reviewed plan; output-directory changes do not enter report hashes.

Rejected coupon/redemption bytes were not retained by B2. A SHA/category is not a body. The forensic report records missing bodies and cannot locate their duplicated key or manufacture a JSON response. Accepted amortization/offer schemas, raw row values and metadata are inspected without rewriting their original evaluation or creating events.

## 6. Exact diagnostic cohort and paired profiles

The operator may select one or both of `SU24020RMFS8` and `RU000A0JQAM6`, provided the verified original journal actually requested them. No other SECID is eligible. Allowed tables are coupons, redemptions and offers. Amortizations are inspected from the preserved B2 evidence and never fetched by this workflow.

BASELINE reproduces the B2 route/parameters. TABLE_ONLY changes only `iss.only=<table>,<table>.cursor` to `iss.only=<table>`. Both preserve `iss.meta=on`, `start=0`, `limit=100`, `<table>.start=0`, raw SECID and HTTPS host. No generic URL, extra route, metadata-off profile, unlimited page or offset override exists.

Already accepted offset-zero baselines are reused, including offers. The full supplied shape therefore needs four missing coupon/redemption baselines followed by six table-only requests, sorted by SECID and fixed table order within each profile. Authorization may explicitly narrow this work; it may not enlarge it. Old observations and new observations have different retrieval times, so differences are not presented as causal proof of query-parameter effects.

## 7. Duplicate-key forensics and quarantine

Objects are decoded as ordered member pairs; nested arrays and duplicate occurrences remain distinguishable. Ordinary dictionaries are constructed only after the entire bounded inspection proves there are no duplicate keys. The report retains sanitized key display (at most 128 characters), full key hash, occurrences, bounded structural location, ROOT/METADATA/DATA/OTHER area, value fingerprints and structural equality/difference. Financial conflict or authority is not inferred merely from representational differences.

Inspection is bounded to 100,000 visited nodes, depth 64 and 512 duplicate findings. If safe inspection cannot complete, the result preserves uncertainty instead of truncating findings or accepting the document. Duplicate, malformed or v1-rejected source documents remain diagnostic-only, content-addressed in `quarantine/`. They cannot become accepted pages or financial events. Reports/logs never print complete quarantined bodies.

## 8. Cursor and first-response policy

Only offset zero is authorized. Cursor presence/content and source-reported totals are recorded. Existing strict B2 cursor rules are reused for first-page completeness. A valid first page proving the whole reported table can be COMPLETE; more pages, absent cursor, malformed cursor, empty or short data alone cannot establish completeness. Further pagination is `NOT_EVALUATED`, not silently performed or reconstructed.

## 9. Amounts, units, identity and offers

The diagnostic report preserves source field names, row values, metadata, supplied identifiers, currency, amount, percentage/price, nominal-context and date fields. Accepted current ISIN context remains separate from returned ISIN; null source ISIN is never filled from SECID or context. No amount, percentage conversion, coupon reconstruction, nominal default or currency default is calculated.

Explicit unit claims can support PARTIAL diagnostics; incompatible claims remain CONFLICTING. Without reviewed field-specific authority, DOCUMENTED is not emitted. Missing units remain UNPROVEN. Offers without monetary amount remain visible observations, not invented payments or automatic redemptions. Scheduled source observations do not prove actual payment, source publication date or historical decision availability.

## 10. Authorization and resource envelope

Authorization binds the exact reviewed plan SHA, selected SECIDs/tables/profiles, expanded work, limits and private evidence destination. It is an operator approval record, not a digital signature. Probe additionally requires `--confirm-network`; offline authorization never launches it.

Hard ceilings: two SECIDs, three tables, six logical partitions, twelve HTTP attempts, twelve retained responses (accepted plus quarantine), 2 MiB/body, 12 MiB total received, ninety seconds including waits/downtime, 32 MiB evidence and 8 GiB free reserve. Requests are sequential, at most two starts/second. Only known transient HTTP/transport errors retry, at most three attempts/query, with 0.25/0.50 delays, rate delay and bounded Retry-After up to sixty seconds. The global budget can stop before all pairs complete. The chunk guard conservatively refuses a read that could cross its decoded-byte budget; it is not a TCP-buffer measurement.

TLS verification is required; redirects, environment proxy inheritance, credentials and arbitrary URLs are excluded. No environment token is loaded. All tests use injected HTTPX MockTransport and fake waits/clocks.

## 11. Journal, resume and outputs

New evidence lives in a private root, separate from every prior source. Each attempt writes an immutable intent before the request, then a SHA-bound immutable receipt. Retained bytes are content-addressed under accepted/ or quarantine/ and bound to exact request/profile, authorization, observation timestamp and size. State records a fixed deadline and receipt chain; it is the only operational checkpoint. Unknown pending outcome, orphan files, chain/hash mismatch or scope drift blocks automatic resume. Completed responses, including quarantined duplicates, are not retried.

Replay recomputes inspections and checks their digests. It never promotes a quarantined body. Source errors never become successful empty tables. Original and diagnostic roots are not used as report destinations. Canonical ASCII/sorted/compact serialization binds deterministic semantic hashes, including source hashes and actual observation timestamps, while excluding output paths/runtime measurements.

Report publication uses exclusive output locking and private staging. Conflicting outputs are refused; identical reruns are idempotent. The completion JSON is installed last and controlled failure removes only newly published files. The combined report budget is 32 MiB without silent truncation.

## 12. Future operator A–F commands (NOT EXECUTED)

Use a pinned reviewed commit and verified existing image/dependencies: no automatic pull/build, production checkout edits or source acquisition. Offline containers use `--network none`, read-only filesystem, non-root UID/GID, `--cap-drop ALL`, `no-new-privileges`, original inputs mounted `:ro`, and separate writable report mounts. Mount the B2 evidence at its original absolute path. Below, paths are placeholders the operator must bind explicitly.

A. First separately authorized offline operation:

```bash
python scripts/historical_cashflow_source_forensics.py \
  --mode FORENSIC_PLAN \
  --b2-plan "$B2_PLAN" --b2-authorization "$B2_AUTH" \
  --b2-evidence-root "$ORIGINAL_B2_EVIDENCE_PATH" --b2-evaluation "$B2_EVALUATION" \
  --expected-b2-plan-sha256 477c1c1cc2c154503405205aa3f811a2de583b4ca689ba832731da05d0503a52 \
  --expected-b2-authorization-sha256 737a4bec5a271de497aa4ba5877d7bab3b0fa5737cb47a9568136d69dd587d60 \
  --expected-b2-probe-sha256 fe9d14f4e4b69212cff17f7cc0a7b6e423df03f65c282697788dac2545164013 \
  --expected-b2-evaluation-sha256 73cb99d457182d44e5f65a1fcc9d7904a37b73e4081efff22b9081068110b579 \
  --secids SU24020RMFS8 RU000A0JQAM6 --output "$FORENSIC_OUTPUT"
```

B. Inspect `task308b2r1-source-forensics.json`, its summary and `task308b2r1-diagnostic-plan.json`. Verify original 24 outcomes from actual evidence, evaluation equivalence, missing-byte limitations, fixed scope and plan SHA. Do not assume the operator-supplied counts are already independently proven.

C. After separate review, offline authorization with explicit limits JSON:

```bash
python scripts/historical_cashflow_source_forensics.py --mode DIAGNOSTIC_AUTHORIZE \
  --plan "$FORENSIC_OUTPUT/task308b2r1-diagnostic-plan.json" \
  --reviewed-plan-sha256 "$REVIEWED_R1_PLAN_SHA" \
  --secids SU24020RMFS8 RU000A0JQAM6 --tables coupons redemptions offers \
  --profiles BASELINE TABLE_ONLY --limits "$R1_LIMITS_JSON" \
  --evidence-root "$PRIVATE_R1_EVIDENCE" --output "$R1_AUTH_FILE" --confirm
```

Create the separate evidence destination with restrictive permissions (for example `mkdir -m 700`); do not reuse any old evidence or report directory. Authorization is not permission to execute the next command automatically.

D. Only after an independent explicit live-diagnosis decision:

```bash
python scripts/historical_cashflow_source_forensics.py --mode DIAGNOSTIC_PROBE \
  --plan "$FORENSIC_OUTPUT/task308b2r1-diagnostic-plan.json" --authorization "$R1_AUTH_FILE" \
  --evidence-root "$PRIVATE_R1_EVIDENCE" --confirm-network
```

E. Offline replay, evidence mounted read-only:

```bash
python scripts/historical_cashflow_source_forensics.py --mode DIAGNOSTIC_EVALUATE \
  --plan "$FORENSIC_OUTPUT/task308b2r1-diagnostic-plan.json" --authorization "$R1_AUTH_FILE" \
  --evidence-root "$PRIVATE_R1_EVIDENCE" --output "$R1_DIAGNOSTIC_OUTPUT"
```

F. Review canonical report hashes, immutable receipt/body bindings, original source byte preservation, quarantine exclusions, unit/offer limitations and cross-time comparison caveats. No parser change or wider acquisition follows automatically.

## 13. Verification and handoff

Focused synthetic tests cover original partial replay/evaluation equivalence, all 24 outcomes, missing rejected bytes, duplicate root/metadata/economic keys, cursor absence/empty/complete cases, units, SU→RU context, query-profile scope, strict authorization, limits, retries, interruption/resume, SHA tampering, frozen roundtrip, SELECT/network isolation and publication safeguards. Minimal B2 regressions retain the old behavior; broad regression stays in exact-commit CI. Memory measurements, if any, describe Python allocations in synthetic tests and are not production RSS claims.

Every result keeps actual payment, historical publication/decision availability, PIT, historical backtest, acquisition and total-return execution readiness false. Source feasibility and operation completion are not total-return readiness. After one implementation commit/push and one CI snapshot, stop. Independent review and green CI precede a separately authorized offline FORENSIC_PLAN. Live diagnostics and any future parser repair each require a separate decision.
