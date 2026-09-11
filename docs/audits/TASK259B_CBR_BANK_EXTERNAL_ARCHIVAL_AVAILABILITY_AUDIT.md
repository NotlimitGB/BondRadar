# Task259B — CBR Bank External Archival Availability Audit v1

## Status

```text
TASK_ID=259B
STARTING_SHA=4acb030ffe42bd1396383f1ffff70a58f7541bc9
IMPLEMENTATION=READ_ONLY_AUDIT
STATUS=PASS
LIVE_ARCHIVAL_AUDIT=PARTIAL_COMPLETE_BY_DESIGN
OVERALL_ARCHIVAL_STATUS=ARCHIVAL_EVIDENCE_PARTIAL
HISTORICAL_REVISION_RISK_PROVEN=true
CURRENT_CBR_HISTORICAL_ARTIFACTS_POINT_IN_TIME_FAITHFUL=DISPROVEN
FROZEN_TASK258B_BINDING=PROVEN
PIT_READY=false
PUBLICATION_BACKFILL_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

Task259B is complete as a deliberately partial archival audit. Wayback covered
all 104 targets, the semantic audit proved three raw-data revisions, and an
operator-observed read-only VDS check bound those three frozen Task258B
artifacts to the later/current representations. The remaining 94 Common Crawl
checks are preserved as unresolved and deferred to historical-version
reconstruction; they are not relabelled as no-capture evidence.

## Scope and binding

The fixed target is the Cartesian product of these 26 report dates and four
forms:

```text
2023-07-01, 2023-10-01,
2024-01-01, 2024-04-01, 2024-07-01, 2024-10-01,
2025-01-01, 2025-02-01, 2025-03-01, 2025-04-01, 2025-05-01,
2025-06-01, 2025-07-01, 2025-08-01, 2025-09-01, 2025-10-01,
2025-11-01, 2025-12-01,
2026-01-01, 2026-02-01, 2026-03-01, 2026-04-01, 2026-05-01,
2026-06-01, 2026-07-01, 2026-08-01

0409101, 0409102, 0409123, 0409135
```

Every slot binds report date, form, exact official CBR URL, validated filename,
current target SHA-256, and compressed size. The completed current-official
inventory had:

```text
TARGET_ARTIFACTS=104
VERSION_BINDING_TARGET=CURRENT_OFFICIAL_REPRESENTATION
TARGET_INVENTORY_SHA256=c4c7ea9b5ffb19de757b6ff9a3518d8c033fb171ed8e11a77ae6af5ef0264efe
LOCAL_LIVE_INVENTORY_FROZEN_TASK258B_BINDING=false
OPERATOR_OBSERVED_FROZEN_TASK258B_BINDING=PROVEN
```

The local live audit was bound to the current official representation, not to
the frozen Task258B batch. The later frozen binding is separate
operator-observed read-only VDS evidence and is identified as such; Codex did
not execute the VDS commands. Current CBR bytes are never presented as proof
that those same bytes were historically available.

## Input contract

The CLI accepts exactly one mode:

- `--target-inventory <json>` reads a strict
  `bondradar.cbr_bank_archival_target_inventory.v1` document;
- `--current-official-representation` obtains the official CBR catalog and the
  current 104 artifact representations in memory.

The inventory requires exact keys, exact 26-date/four-form scope, unique sorted
slots, lowercase SHA-256 values, bounded positive compressed sizes, exact CBR
hosts and paths, and a canonical inventory checksum. Arbitrary URLs, output
files, database arguments, credentials, and date expansion are absent.

## Network containment

The audit uses GET only, `trust_env=false`, an explicit User-Agent, HTTPS, and
role-specific host allowlists:

```text
CBR=cbr.ru,www.cbr.ru
WAYBACK=web.archive.org
COMMON_CRAWL_INDEX=index.commoncrawl.org
COMMON_CRAWL_DATA=data.commoncrawl.org
```

Non-standard ports, user-info, fragments, private/local destinations, foreign
redirects, and index-supplied arbitrary data hosts fail closed. There are no
credentials, proxy inheritance, Save Page Now calls, submission APIs, archive
writes, or filesystem cache.

Fixed bounds:

```text
REDIRECTS=3
ATTEMPTS=3 for timeout/429/5xx only
TIMEOUTS=connect:5s,read:30s,write:10s,pool:5s
CATALOG_BYTES=2MiB
INDEX_BYTES=1MiB
WAYBACK_CANDIDATES_PER_ARTIFACT=64
COMMON_CRAWL_COLLECTIONS=64
COMMON_CRAWL_CANDIDATES_PER_ARTIFACT=128
PAYLOAD_ATTEMPTS_PER_SOURCE_ARTIFACT=3
ARTIFACT_PAYLOAD_BYTES=32MiB
WARC_RANGE_BYTES=40MiB
CURRENT_CBR_INVENTORY_BYTES=512MiB
TOTAL_ARCHIVAL_PAYLOAD_BYTES=1GiB
```

Common Crawl collections are queried sequentially only when the official
collection end is not earlier than the target report date. This removes
temporally impossible captures without suppressing any capture that could
provide a valid conservative bound. There is a minimum two-second interval
between index requests; no parallel executor is used.

## Wayback evidence contract

The CDX request uses the exact original URL, `matchType=exact`, fixed fields,
and a bounded result limit. A CDX row is usable only if its returned `original`
is exactly the target CBR URL. Canonical or alias rows with a different
`original` are ignored rather than reinterpreted.

Evidence is taken from identity replay bytes, not replay HTML. A 200 capture is
usable only when the locally computed SHA-256 of the replayed HTTP payload
equals the target SHA-256. Redirect captures, unavailable replay, and byte
mismatches remain explicit non-proof states.

Reference: [Internet Archive CDX server documentation](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md).

## Common Crawl evidence contract

The audit reads one bounded `collinfo.json`, queries each temporally relevant
collection through an exact-URL CDXJ request, and accepts only validated rows
whose URL is exactly the target URL. Payload retrieval uses only validated
`filename`, `offset`, and `length` values against `data.commoncrawl.org`.

The standard-library WARC reader validates gzip framing, WARC headers, record
length, target URI, record type, original HTTP status, transfer/content
encoding, and payload bounds. Only a single valid response or an unambiguous
revisit linked to an available exact-URL payload with the same digest can be
used. An index digest by itself is diagnostic and never establishes byte
identity.

Reference: [Common Crawl CDXJ index documentation](https://commoncrawl.org/cdxj-index).

## Evidence semantics

Each bounded capture projection contains archive source, capture timestamp,
exact original URL, archived status, diagnostic index digest, locally computed
payload SHA-256, target SHA-256, exact-match flag, sanitized locator, evidence
class, and sanitized error code.

Only this equality is authoritative:

```text
SHA256(archived original HTTP payload) == target artifact SHA256
```

The earliest valid exact-version capture on or after the report date supplies
only a conservative availability bound. It does not establish a publication
timestamp. A capture before the report date is a temporal anomaly and is not
used. Missing captures do not prove non-publication, and no lag interpolation
or imputation is allowed.

```text
ARCHIVE_CAPTURE_TIME!=PUBLICATION_AT
PUBLICATION_AT=null
PIT_READY=false
```

## Output contract

The module entry point is:

```text
python -m app.services.cbr_bank_financial_evidence.external_archival_availability_audit
```

It emits one compact JSON object with schema
`bondradar.cbr_bank_external_archival_availability_audit.v1`. Artifacts are
ordered by report date and form; captures are ordered by timestamp and source.
The report preserves all 104 artifact rows, source-specific counts, grouped
coverage, lag diagnostics, provenance, warnings, and type-stable safety fields.

Exit codes:

- `0`: technically complete positive or negative scientific result;
- `1`: technically incomplete audit;
- `2`: invalid arguments.

Completed scientific classification is deterministic:

```text
104/104=ARCHIVAL_EVIDENCE_SUFFICIENT_FOR_NEXT_PIT_CONTRACT
1..103/104=ARCHIVAL_EVIDENCE_PARTIAL
0/104=ARCHIVAL_EVIDENCE_INSUFFICIENT
```

Unmeasured values remain `null`. Explicit zero is used only after the relevant
source check completed successfully.

## Resumable recovery contract

Task259B-R1 adds an operational checkpoint with schema
`bondradar.cbr_bank_external_archival_checkpoint.v1`. It freezes the audit
observation time, complete target inventory, selected Common Crawl collection
inventory, and independent state for every one of the 104×2 artifact/source
checks. Its semantic SHA-256 excludes only checkpoint creation/update times;
inventory identity, candidates, source states, cursors, and locally verified
payload hashes remain covered.

The source state machine is monotonic:

```text
PENDING
INDEX_CONFIRMED_NO_CAPTURE
PAYLOAD_PENDING
EXACT_VERSION_VERIFIED
VERSION_MISMATCH
VERSION_UNBOUND_COMPLETE
TECHNICAL_FAILURE_RETRYABLE
TECHNICAL_FAILURE_FINAL_FOR_RUN
```

Exact matches, completed mismatches, completed unbound evidence, and confirmed
no-capture results are terminal and never repeated on resume. Technical and
pending states are retried without being converted to negative evidence.
Index and payload cursors are persisted after each completed step. The
checkpoint is written through a same-directory temporary file, flushed and
fsynced, atomically replaced, then parsed and checksum-verified again.

CLI recovery modes are deliberately narrow:

- a fresh run supplies exactly one target inventory selector and may add
  `--checkpoint`;
- a resumed run supplies `--checkpoint --resume`, taking inventory only from
  the checkpoint;
- `--retry-incomplete-only` is valid only with resume;
- `--source wayback|commoncrawl|all` selects a bounded recovery phase.

Live recovery uses the ignored operational path
`artifacts/task259b/archival-audit-checkpoint.json`. The application neither
invokes Git nor stages or commits this file. A fresh run refuses to overwrite
an existing checkpoint. Incompatible schema, contract, inventory, or checksum
fails as `CHECKPOINT_INCOMPATIBLE`.

Both archives use at most three attempts for timeouts, connection reset, 429,
and 500/502/503/504, with deterministic 2s/4s delays and a 30s cap; bounded
`Retry-After` is honored. Exhausted Common Crawl 429/503 stops that source for
the current run while retaining its cursor for a later resume. A resumable
source report is derived only from checkpoint state. Its original full-source
completion condition remains `unresolved_technical_findings=0`; Task259B's
later delivery decision does not rewrite checkpoint states. R3 closes the
architectural audit under the revised partial-completion semantics documented
below because the historical-version assumption has already been disproven.

## Task259B-R1 bounded live recovery

The fresh current-representation recovery rebuilt all 104 target identities
and created the ignored operational checkpoint. Its target inventory is the
same current representation previously measured:

```text
TARGET_INVENTORY_SHA256=c4c7ea9b5ffb19de757b6ff9a3518d8c033fb171ed8e11a77ae6af5ef0264efe
CHECKPOINT_SCHEMA=bondradar.cbr_bank_external_archival_checkpoint.v1
CHECKPOINT_EVIDENCE_SHA256=22ec6f642a7912ecc957d9ad60246aadd0a6afeee27d7b78c4fccd7596bd892a
SEMANTIC_EVIDENCE_SHA256=47f16e715cf433c21d210c80fe4be4ccc10d6ce7a8b80dd9cf18104dd047f065
```

Wayback was reproduced from machine-readable CDX and replay responses rather
than copied from the previous prose report. It reached a complete 104/104
source projection:

```text
WAYBACK_SOURCE_AUDIT_COMPLETE=true
WAYBACK_COMPLETE_ARTIFACT_CHECKS=104
WAYBACK_CONFIRMED_NO_CAPTURE=79
WAYBACK_INDEX_HIT_ARTIFACTS=25
WAYBACK_PAYLOAD_AVAILABLE_ARTIFACTS=25
WAYBACK_EXACT_VERSION_MATCH_ARTIFACTS=22
WAYBACK_VERSION_MISMATCH_ARTIFACTS=3
WAYBACK_UNRESOLVED_CHECKS=0
```

The sequential Common Crawl recovery was deliberately stopped after a bounded
local interval, at an atomically verified cursor boundary, rather than left as
an uncontrolled multi-hour process. The checkpoint retains:

```text
COMMON_CRAWL_SOURCE_AUDIT_COMPLETE=false
COMMON_CRAWL_COMPLETE_ARTIFACT_CHECKS=10
COMMON_CRAWL_CONFIRMED_NO_CAPTURE=10
COMMON_CRAWL_EXACT_VERSION_MATCH_ARTIFACTS=0
COMMON_CRAWL_UNRESOLVED_CHECKS=94
UNRESOLVED_TECHNICAL_FINDINGS=94
R1_CHECKPOINT_LIVE_AUDIT_STATUS=INCOMPLETE_TECHNICAL
```

The incomplete Common Crawl projection is not a negative archival finding.
The next resume starts from the saved artifact/collection cursor and does not
repeat the 104 terminal Wayback checks or ten terminal Common Crawl checks.

The 22 exact-current-version artifacts remain partial archival evidence; the
unfinished second source prevents a full-coverage claim:

```text
PROVISIONAL_EXACT_VERSION_ARTIFACTS=22
PROVISIONAL_UNPROVEN_OR_UNMEASURED_ARTIFACTS=82
PROVISIONAL_COVERAGE_RATIO=0.211538
TEMPORAL_ANOMALIES=0
VERSION_MISMATCH_CAPTURES=7
EARLIEST_VERIFIED_CAPTURE=2024-07-31T20:16:58Z
LATEST_OF_EARLIEST_VERIFIED_CAPTURES=2026-04-25T02:21:09Z
```

Provisional exact-version coverage is 3/6/5/8 for forms
0409101/0409102/0409123/0409135 and 5/6/11/0 for report years
2023/2024/2025/2026. Observed exact-version lag statistics in days are
`min=56`, `median=286.5`, `p75=479`, `p90=669`, and `max=886`. These values
cannot be interpolated to uncovered or technically unresolved artifacts.

## Safety result

## Task259B-R2 Wayback mismatch semantic audit

The checksum-valid checkpoint selected exactly three terminal Wayback
`VERSION_MISMATCH` artifacts. A bounded read-only comparison retrieved only
their three identity-replay payloads and three current CBR representations.
Every replay SHA-256 matched its checkpoint lineage and every current payload
matched the checkpoint target identity. Common Crawl was not called and the
checkpoint was not modified.

| Report date | Form | Capture at | Archived SHA-256 | Current SHA-256 | Classification | Record delta | Schema difference | Raw-data difference |
|---|---|---|---|---|---|---:|---|---|
| 2024-04-01 | 0409102 | 2024-07-31T20:20:29Z | `52fb24529eff57e480148cbda074a1b7354d727d69c369ebef9723126d254dc7` | `45512cfbf4050996f8f817e30e77d3fa7bf6a5eee3c6ec845ad1a876708af2ce` | `RAW_DATA_DIFFERENCE` | -96 | false | true |
| 2025-06-01 | 0409135 | 2025-07-16T01:41:11Z | `293d3b1cf2c35281b8cdc6b32961cb2c925b1d178d5a91662f714252a31eb764` | `9149ff890e1fba8bb2f09d4b2feb87c8cbddd4076527b8356eb6a2136235388e` | `RAW_DATA_DIFFERENCE` | 0 | false | true |
| 2025-10-01 | 0409123 | 2025-10-31T19:13:11Z | `298ea0d19874b6ec290a59fa0663318c8c3f4809aaa63c2652ed96c26d3e568d` | `33b40c5805e10e21b89b33d20ebdf5cbb9c2080e5d3a1df2bfbae2fded5c8c7c` | `RAW_DATA_DIFFERENCE` | 0 | false | true |

Detailed bounded diagnostics:

- 0409102: archived/current records `16345/16249`, subjects `354/352`,
  removed semantic rows `96`; ordered source-row checksums
  `9b7aaf47f21d16e994ae0c9f452834518b626c9df2bdc60d2fc31ad7744507c6`
  and `a1134dc14bf6571d16138fc2361894c71adfae8794ad978508252944cb130692`.
- 0409135: records `1734/1734`, subjects `338/338`, three removed and
  three added/changed raw observations; checksums
  `64fbdaeb09024ba1c481636163afe4d8d5aa207b3021d6c78b428abbd4169bda`
  and `61e4cb82a905aae9788376aeedb5c6a285b6803235f77d56a27a7b9f5ed78105`.
- 0409123: records `1374/1374`, subjects `345/345`, six removed and six
  added/changed raw observations; checksums
  `981a8b2b54316e894e56dfe982d6de355b16cebc9e3eca50b655e523c670cc76`
  and `4b427c676d77fc141913166b918de6fdf77b3922892998d15f3acd7a125cf9d5`.

All three value-member and form structural schemas were unchanged, while the
raw lexical observation multisets differed. Therefore these are not RAR
timestamp, compression, ordering, casing, or container-only differences.

```text
MISMATCH_ARTIFACTS=3
CONTAINER_ONLY_DIFFERENCES=0
METADATA_ONLY_DIFFERENCES=0
STRUCTURAL_SCHEMA_DIFFERENCES=0
RAW_DATA_DIFFERENCES=3
UNKNOWN_DIFFERENCES=0
HISTORICAL_REVISION_RISK_PROVEN=true
CURRENT_CBR_HISTORICAL_ARTIFACTS_CANNOT_BE_ASSUMED_POINT_IN_TIME_FAITHFUL=true
CURRENT_CBR_HISTORICAL_ARTIFACTS_POINT_IN_TIME_FAITHFUL=DISPROVEN
LOCAL_R2_FROZEN_TASK258B_BINDING=false
PIT_READY=false
```

This finding does not invalidate the current raw store for present-day
analysis. It blocks treating newly downloaded current CBR historical files as
unquestioned point-in-time snapshots in backtests. A separate historical
version reconstruction contract is required before historical APPLY/PIT use.

## Historical Revision Discovery

Wayback captured three artifact representations whose raw financial evidence
differs from the files currently exposed by CBR. All three comparisons were
checksum-bound at both ends and parsed through the existing historical archive,
DBF Decimal, dynamic-member, and exact lexical evidence boundaries.

The result proves revision risk for at least these three artifacts. It does not
prove that all 104 artifacts were revised, identify a first publication time,
or make an inference about the 79 Wayback no-capture results or 94 unresolved
Common Crawl checks.

Four concepts remain separate:

1. **CURRENT CBR REPRESENTATION** is the artifact served by CBR when the audit
   acquired the target bytes.
2. **HISTORICALLY ARCHIVED REPRESENTATION** is the exact payload independently
   retained by an external archive.
3. **ARCHIVE CAPTURE TIME** proves only that an exact captured version existed
   no later than that observation.
4. **FIRST PUBLICATION TIME** remains unknown and is not derived from archive
   capture time.

```text
archive_capture_at != publication_at
current CBR version != guaranteed historical version
exact archived payload capture = proof that exact artifact version existed no later than capture_at
first archive capture != first publication
no archive capture != proof of non-publication
```

## Frozen Task258B Binding

The following evidence was supplied from a separate read-only operational
verification on VDS. Its provenance is explicit:

```text
EVIDENCE_ORIGIN=OPERATOR_OBSERVED_READ_ONLY_VDS_BINDING
FROZEN_TASK258B_BINDING=PROVEN
```

Codex did not independently execute those VDS commands and did not query the
production database during Task259B-R3. The operator-observed frozen artifact
hashes are:

| Report date | Form | Frozen Task258B SHA-256 | Binding |
|---|---|---|---|
| 2024-04-01 | 0409102 | `45512cfbf4050996f8f817e30e77d3fa7bf6a5eee3c6ec845ad1a876708af2ce` | equals current; differs from archived `52fb24529eff57e480148cbda074a1b7354d727d69c369ebef9723126d254dc7` |
| 2025-06-01 | 0409135 | `9149ff890e1fba8bb2f09d4b2feb87c8cbddd4076527b8356eb6a2136235388e` | equals current; differs from archived `293d3b1cf2c35281b8cdc6b32961cb2c925b1d178d5a91662f714252a31eb764` |
| 2025-10-01 | 0409123 | `33b40c5805e10e21b89b33d20ebdf5cbb9c2080e5d3a1df2bfbae2fded5c8c7c` | equals current; differs from archived `298ea0d19874b6ec290a59fa0663318c8c3f4809aaa63c2652ed96c26d3e568d` |

Together with the previously established Task258C lineage from frozen
artifacts to raw ingestion, this supports only the bounded conclusion:

```text
FROZEN_TASK258B_CONTAINS_LATER_CURRENT_REPRESENTATIONS=true
PRODUCTION_RAW_LAYER_REVISION_RISK_PROVEN=true
PRODUCTION_RAW_LAYER_CONTAINS_LATER_REVISIONS=PROVEN_FOR_3_IDENTIFIED_ARTIFACTS
```

It is not a claim that all production historical artifacts are revised.

## Common Crawl disposition

```text
COMMON_CRAWL_SOURCE_AUDIT_COMPLETE=false
COMMON_CRAWL_COMPLETE=10/104
COMMON_CRAWL_NO_CAPTURE=10
COMMON_CRAWL_EXACT_VERSION=0
COMMON_CRAWL_UNRESOLVED=94
COMMON_CRAWL_DEFERRED=true
DEFERRED_TO_HISTORICAL_VERSION_RECONSTRUCTION=true
```

The remaining checks were not converted to no-capture. The key architectural
question was already resolved by Wayback, semantic revision comparison, and
the frozen Task258B binding. Additional Common Crawl evidence is now an input
to historical-version reconstruction rather than a prerequisite for closing
this availability audit. The ignored operational checkpoint is retained for
that future work and is not committed.

## Impact on BondRadar Backtesting

Current/restated historical CBR data remains valid for latest-known historical
analysis. It must **not** be treated as guaranteed historical point-in-time
data. Historical strategy and backtest inputs require version-aware
reconstruction and an explicit version-selection policy.

The evidence does not supply `publication_at`, does not promote PIT readiness,
and does not authorize publication backfill, historical APPLY, normalization,
or scoring.

## Revised completion semantics and final verdict

Task259B closes because the evidence has answered the architecture question,
not because Common Crawl coverage became complete:

```text
WAYBACK_SOURCE_AUDIT_COMPLETE=true
WAYBACK_COMPLETE=104/104
WAYBACK_EXACT_VERSION=22
WAYBACK_NO_CAPTURE=79
WAYBACK_VERSION_MISMATCH=3
WAYBACK_UNRESOLVED=0

MISMATCH_SEMANTIC_AUDIT_COMPLETE=true
CONTAINER_ONLY_DIFFERENCES=0
METADATA_ONLY_DIFFERENCES=0
STRUCTURAL_SCHEMA_DIFFERENCES=0
RAW_DATA_DIFFERENCES=3
UNKNOWN_DIFFERENCES=0

FROZEN_TASK258B_BINDING=PROVEN
HISTORICAL_REVISION_RISK_PROVEN=true
CURRENT_CBR_HISTORICAL_ARTIFACTS_POINT_IN_TIME_FAITHFUL=DISPROVEN

STATUS=PASS
LIVE_ARCHIVAL_AUDIT=PARTIAL_COMPLETE_BY_DESIGN
OVERALL_ARCHIVAL_STATUS=ARCHIVAL_EVIDENCE_PARTIAL
HISTORICAL_AVAILABILITY_PROVEN_BY_ARCHIVE=PARTIAL
UNRESOLVED_COMMON_CRAWL_ARTIFACTS=94
DEFERRED_TO_HISTORICAL_VERSION_RECONSTRUCTION=true

PRODUCTION_RAW_LAYER_CONTAINS_LATER_REVISIONS=PROVEN_FOR_3_IDENTIFIED_ARTIFACTS
PUBLICATION_TIME_PROVEN=false
PIT_READY=false
```

At least three current CBR historical artifacts are proven to differ from
historically archived versions. Therefore the full current historical corpus
cannot be assumed point-in-time faithful without version-level evidence.

```text
DATABASE_ACCESSED=false
DATABASE_MUTATION_EXECUTED=false
DATABASE_PERSISTENCE=false
FILESYSTEM_WRITE=true
FILESYSTEM_WRITE_SCOPE=LOCAL_OPERATIONAL_CHECKPOINT_ONLY
ARCHIVE_WRITE_EXECUTED=false
PUBLICATION_BACKFILL_EXECUTED=false
NORMALIZATION=false
SCORING=false
PUBLICATION_AT=null
PIT_READY=false
PRODUCTION_ACTIONS=NONE
```

The audit does not modify Task259A, CBR clients, source contracts, evidence
store, ingestion runners, models, migrations, Docker/CI, or frontend code.

## Decision and next safe step

```text
TASK259B_COMPLETE=true
FINAL_LIVE_VERDICT=ARCHIVAL_EVIDENCE_PARTIAL
TASK259B_COMMIT_ALLOWED=true
NEXT_RECOMMENDED_TASK=260A_HISTORICAL_ARTIFACT_VERSIONING_AND_PIT_SELECTION_CONTRACT
```

Task260A is the only recommended next step because version-aware identity and
selection semantics must be designed before any schema or database work. It is
not started or authorized by Task259B. No PIT evidence persistence, publication
backfill, historical financial backfill, or downstream execution is unlocked
automatically.
