# Task308B1 — Historical Identity Recovery Inventory & Acquisition Plan v1

## 1. Problem and actual evidence

Base: `0e758cda3fcfea33ad00dbbb5ee35e7c303e8cf5`. Both Task308A workflows
39241031636 and 39241031735 succeeded. Operator-provided production context:
Task307 has 10,671 distinct SECIDs, 7,093 observed and 3,578 listing-only; all
10,671 original ISINs are absent. Independent operator diagnostics report 10,572
checksum-valid and 99 checksum-invalid SECIDs. These are reconciliation references,
not runtime constants or production findings independently executed by this task.

Task308A operator observations: 12 probed, 11 current exact bindings, one exact
record without ISIN, 36 attempts/pages, 24 complete source responses, zero transport
failures/conflicts, and 77,941 response bytes. No real artifacts or sources were
accessed during development; tests use synthetic artifacts and MockTransport.

## 2. Reviewed upstream contracts

Reuse Task307 strict report and Task308A schemas, `validate_report`, deterministic
sample builder, `verify_authorization`, offline `evaluate`, its receipt/page replay,
`isin_valid`, canonical serializer and shared `is_ofz_instrument`.
The existing nine-file A4 implementation fingerprint remains unchanged. No existing
runtime, model, migration, A4/307 report or accepted A probe contract is edited.
New modules never invoke authorize/probe/fetch, acquisition, sync or a DB session.

## 3. Task307/308A SHA chain

Required explicit inputs: Task307 report, A sample plan, authorization, accepted
probe directory and evaluation. Optional finalized A4 manifest is recommended.
Rebuild the complete A plan from the full Task307 population and its original
sample-size setting, compare every field, replay accepted bytes/receipts and require
the saved published probe manifest to be COMPLETE. Compare the entire replayed
evaluation, not merely its status or recovery count. An absent manifest, unresolved
intent, wrong binding, invalid checksum/hash or semantic mismatch blocks output.

Operator context hashes:

| Artifact | SHA-256 |
|---|---|
| A4 manifest | `b7a55de16a510b798e1d8da5f72ce757215257c86c9ee8b34afcf67a2783bce7` |
| A4 content | `731aa5e01d44576665aba5b961cbc9740fb931d6d7a47a029b977d81e1de16c4` |
| Task307 | `8f4cb3e25b92a02f9e12e8d07e731c1ed90e760e78194f009164c2227e3b2ffa` |
| A plan | `021633974a108e586187e42944545ba6f58f386cc8f7c8e284ac0baada8381dd` |
| A authorization | `123f16ef1960593ac59b1397b2becf2f51b76f03392bc5c2df5c3f1e0b9c5fe1` |
| A probe | `f63e1aefe3700bad7a739ea7b8ce8793212489b610fe084398d199182878c010` |
| A evaluation | `3dbcaf48088029e4fec76e6abf68a1e7f313f1216bdc4c3ddf323d538cfaf347` |

All input byte hashes are watched before/after verification and again before final
publication. Read-only mounts remain the operator's concurrency boundary; hash
checks detect observed changes, not a guarantee against an adversary changing and
restoring files between reads. No 4.7 GB A4 page scan or current-source refresh.

## 4. SECID versus ISIN

Inventory has exactly one row per distinct **raw** SECID in ascending exact order.
Original identity pairs, boards, dates, intervals and per-pair counters remain
attached. Different ISIN pairs for one SECID survive; duplicate exact pairs fail.
Syntax, source binding and historical authority are orthogonal. Confirmed current
ISIN is populated only from replayed Task308A evidence, never by assigning SECID.
Source agreement is useful consistency, not independent publishers' confirmation.

## 5. Population-wide checksum diagnosis

Format is uppercase ASCII `[A-Z]{2}[A-Z0-9]{9}[0-9]`; reuse A's expanded-letter
Luhn validator. Report format-valid/checksum-valid, format-valid/checksum-invalid,
and not-ISIN-format separately. A failing checksum never invalidates a MOEX SECID.
Expected counters are optional CLI expectations and are independently recalculated;
the supplied operator script explicitly checks 10671/10572/99 and 11/12.
Any explicitly supplied expectation mismatch blocks publication.

## 6. Exact current source bindings

Keep both identifiers, all per-source statuses/request/response hashes, retrieval
and observation timestamps, source-reported publication/effective strings and
review flags. No timestamp is regenerated. All selected-but-unprobed A units and
the rest of the Task307 population remain unproven. Full source field presence is
also inventoried from exact accepted records: field name and distinct SECID sets
with supplied values. Presence is not canonical validity or dated authority.
Raw payloads are not copied into B1 artifacts.

## 7. OFZ SECID/ISIN mismatch

Regressions preserve `SU24018RMFS2 → RU000A0JV7K7` and
`SU24020RMFS8 → RU000A100QS2`. Canonical OFZ uses only the shared identity helper.
No generated SU→RU transformation, maturity/structure inference, historical SECID
rewrite, or non-OFZ→corporate sector inference is permitted.

## 8. MVZ unresolved case

`MVZ220004019` has exact records in both complete sources but no ISIN. Preserve
`EXACT_RECORD_WITHOUT_ISIN` and `OFFICIAL_ALTERNATIVE_IDENTITY_SOURCE_REQUIRED`.
It is not source absence, a transport failure or an invalid historical instrument.
Exclude it from automatic new-probe selection because it was already probed.
An alternative archive/documentary contract needs a separate review; identical
successful REFERENCE/DESCRIPTION requests are not automatically repeated.

## 9. Checksum-invalid population

List every affected SECID with original memberships, prefix, dates, independent
trade/usable indicators, source coverage and next evidence requirements. Unknown
economic classification stays unknown. Exact current source evidence may establish
a different ISIN despite failing SECID checksum. Syntax success alone establishes
no binding. The 99-count is specific to the supplied production artifact set.

## 10. Historical universe classifications

Publish exact ID sets for all, actual-trade evidence, usable price/NKD evidence,
canonical OFZ, listing-only, inactivity proxy, current confirmed, probed unresolved,
never probed and checksum-invalid groups. Pair counts and distinct SECID counts
have separate denominators. Observation bounds are min/max over original pairs;
supplied listing intervals are retained without synthesizing continuous listing.

Task307 provides no identity-level same-row joint membership. Therefore
`JOINT_EXECUTION_COVERAGE=NOT_ESTABLISHED`. Copy its aggregate joint metrics with
their original row denominators; do not derive joint IDs from separate positive
trade/usable counters. Neither group proves an executable fill or model readiness.

## 11. Deterministic exception selection

Maximum 12 new SECIDs. Features are existing A strata plus raw two-character
prefix and syntax class. Start covered features with all previously actually
probed SECIDs. Pick up to nine unprobed checksum-invalid exceptions by greatest
new-feature count, then raw SECID ASC. Pick up to three non-exception controls
only while each adds a new feature; update coverage after every selection.
Fill unused capacity from remaining exceptions. If population is insufficient,
publish fewer rows with explicit unmet quotas. Published selection is SECID-sorted;
selection-time new features and roles are retained for deterministic replay.

Overlapping strata have exact eligible/selected memberships and overlap counts.
Previously probed SECIDs are excluded regardless of whether their result succeeded.
Proposal is not authorization and is not directly consumable by Task308A PROBE;
future B2 must introduce a reviewed scope binding rather than forge an A sample.

## 12. Acquisition priorities

1. Small new reference qualification: proposal cohort, maximum 12, separately approved.
2. Cashflow feasibility: planning pool of existing exact confirmed, review-free
   identities; choose a separately reviewed bounded cohort of at most six.
3. Historical research cohort: current exact identities with historical observations,
   shown only as a diagnostic pool. Independent trade/usable evidence may prioritize
   investigation but cannot prove joint economic coverage.
4. Wider acquisition: empty executable scope; requires later volume/failure/PIT review.

Each stage has an explicit authorization dependency. Existing reference responses
are reused. Actual fixed/floating/amortizing distinctions are not inferred; those
proof requirements remain visible instead of inventing diverse economic cohorts.

## 13. Reference field requirements

Roadmap maps exact SECID/ISIN, instrument type, title/INN, issue name, nominal/currency,
maturity, listing/status, coupon type/frequency, structural flags, lot/board and
publication/version metadata to Bond, Security Master and Task269/272/273/302 gates.
Source aliases describe implemented mappings, not guaranteed population coverage.
SOURCE_DOCUMENTED refers to reviewed reference/specification/receipt contracts;
terms remain REQUIRES_PROBE or UNKNOWN unless separately established. Field-presence
observations identify already available material that should not be downloaded again.
No verified current profile or issuer linkage is created.

## 14. Cashflow evidence requirements

Inspect existing four bondization tables: coupons, amortizations, redemptions,
offers. Require exact identity/event date, supplied finite nonnegative RUB amount,
revisions, duplicates and source completeness. Offers remain nonautomatic.
Missing amounts are not replaced by nominal. Nominal changes require specific dated
proof; no separate acquisition contract is assumed. `other`/actual-payment proof
is unavailable in the existing four-table contract, not declared universally absent
from MOEX. Scheduled events are not confirmation of actual payments or historical
publication. B1 performs no cashflow source requests.

## 15. PIT/survivorship limitations

Current reference bindings never establish historical decision-time availability.
Decision cutoff remains 00:00 UTC T; effective dates and present-day retrieval do
not backdate publication. Absence near the source frontier is a proxy, not proof
of redemption/default/maturity/delisting. No current-survivor filter removes any
historical inventory identity. Restricted later research cohorts must disclose
excluded populations and selection bias. PIT, acquisition and Day 0 stay false.

## 16. Resource estimates

Recompute sample totals from accepted receipts. Decimal28/HALF_EVEN averages are
descriptive only; no guaranteed full-population extrapolation. Recommended first
reference ceiling reuses A's 72 attempts/48 pages/64 MiB/300 seconds for <=12 SECIDs.
Six-instrument cashflow scenario has 24 initial table queries; pagination/retries
can exhaust a separately reviewed 72-attempt/64-MiB envelope. Source tails and
schedule revisions remain uncertain. Future disk reserve is at least 8 GiB;
no larger VDS tariff is presumed necessary.

B1 limits: Task307 32 MiB; each auxiliary JSON 8 MiB; probe directory 128 MiB;
total input bytes 192 MiB; all outputs 32 MiB; original identity rows <=100,000;
one serialized inventory line <=1 MiB, probe directory <=256 files (the A attempt/page
ceilings normally require at most 196). Limits may only be reduced. Row/input caps
and streaming provide memory refusals; they are not a hard RSS monitor. Suggested
operator isolation is 1 GiB RAM/1 CPU. Scale tests report traced incremental Python
allocation, excluding fixture preparation/source decoding, not production RSS.

## 17. Offline operator workflow

This script is documentation only, not executed in development. A separate pinned
checkout and existing dependencies suffice; no production deployment, DB connection
or tokens. Mount inputs/probe read-only, give only output write access, and use
network-disabled execution. Set REVIEWED_SHA to the delivered independently reviewed
B1 commit, CODE_ROOT/PYTHON/INPUTS/OUT to explicitly reviewed local paths.

```sh
#!/bin/sh
set -eu
: "${REVIEWED_SHA:?set independently reviewed Task308B1 commit}"
CODE_ROOT=${CODE_ROOT:-/code/BondRadar}
PYTHON=${PYTHON:-python3}
INPUTS=${INPUTS:-/inputs}
OUT=${OUT:-/reports/task308b1}

# A. Verify pinned checkout and already installed dependencies, without downloads.
test "$(git -C "$CODE_ROOT" rev-parse HEAD)" = "$REVIEWED_SHA"
test -z "$(git -C "$CODE_ROOT" status --porcelain --untracked-files=no)"
export PYTHONPATH="$CODE_ROOT/backend"
"$PYTHON" -c 'import pydantic; from app.services.historical_identity_recovery_artifacts import verify_inputs, verify_published'
mkdir -p "$OUT"

# B. Offline REPORT; this is not a source-acquisition authorization.
"$PYTHON" "$CODE_ROOT/scripts/historical_identity_recovery_inventory.py" --mode REPORT \
  --task307-report "$INPUTS/task307-coverage-audit.json" \
  --task306a4-manifest "$INPUTS/manifest.json" \
  --task308a-plan "$INPUTS/task308a-sample-plan.json" \
  --task308a-authorization "$INPUTS/task308a-probe-authorization.json" \
  --task308a-evidence-root "$INPUTS/task308a-probe" \
  --task308a-evaluation "$INPUTS/task308a-feasibility-report.json" \
  --expected-task307-sha256 8f4cb3e25b92a02f9e12e8d07e731c1ed90e760e78194f009164c2227e3b2ffa \
  --expected-manifest-sha256 b7a55de16a510b798e1d8da5f72ce757215257c86c9ee8b34afcf67a2783bce7 \
  --expected-task308a-evaluation-sha256 3dbcaf48088029e4fec76e6abf68a1e7f313f1216bdc4c3ddf323d538cfaf347 \
  --expect-total-secids 10671 --expect-format-valid 10671 \
  --expect-checksum-valid 10572 --expect-checksum-invalid 99 \
  --expect-probed 12 --expect-current-bindings 11 --expect-mismatched-bindings 2 \
  --output-dir "$OUT"

# C. Inspect exact population/exception results; no sampling or hidden truncation.
cat "$OUT/task308b1-summary.md"
"$PYTHON" - "$OUT" <<'PY'
import json,sys
from pathlib import Path
r=json.loads((Path(sys.argv[1])/'task308b1-identity-audit.json').read_bytes())
print('COUNTS=',r['counts'])
print('CHECKSUM_INVALID_SECIDS=',r['checksum_invalid_secids'])
print('INPUT_HASHES=',r['input_hashes'])
PY

# D. Independently read the five published artifacts and validate hashes/row order/counts.
"$PYTHON" - "$OUT" <<'PY'
import sys
from app.services.historical_identity_recovery_artifacts import verify_published
r=verify_published(output_dir=sys.argv[1])
print('OUTPUT_VERIFICATION=PASS', 'REPORT_SHA256='+r.report_sha256)
print('NETWORK_ACCESS=false DB_MUTATION=false PIT_READY=false ACQUISITION_READY=false')
PY
```

## 18. Immutable evidence and publication

Inputs, accepted raw responses, receipt timestamps and authorization are read only.
No raw response duplication into outputs. Reject symlinks/reparse points and output
overlap with probe/discovery roots. Exclusive writer lock plus private staging
protect publication. Stream canonical ASCII JSONL in SECID order; newline bytes
are part of inventory SHA. Canonical JSON uses sorted keys, compact separators,
finite values and existing Decimal/date encoding.

The audit SHA binds upstream hashes, inventory SHA, proposal/roadmap semantic SHAs
and summary byte SHA. Summary uses inventory SHA, avoiding a circular report hash.
Paths/runtime/disk metadata are excluded from semantic identities. Install audit
last, after dependencies. Controlled failure removes only files newly installed
by that attempt. An abrupt crash may leave a prefix without completion audit;
identical bytes can be resumed, differing artifacts are never overwritten. A
COMPLETE result requires all five outputs. Identical reruns verify rather than
rewrite. `verify_published` checks output integrity, not upstream source replay.

## 19. Tests and invariant checks

Two focused modules cover source-chain tampering/replay, full population, exact
pair preservation, syntax/current authority separation, two SU cases, MVZ, exception
selection/exclusion, portable read-only artifacts, output hashes, input mutation,
resource refusal, interrupted publication and static no-network/DB/authorization
calls. Synthetic 10,671 population uses a fully acquired MockTransport probe;
no fabricated COMPLETE transport result substitutes for accepted evidence.
Relevant Task307/308A/issuer/OFZ regressions remain unchanged. Compile/import and
working/staged diff checks accompany exact seven-new-file review. Broad tests belong
to exact-commit CI; no full local backend suite or production probe.

## 20. Decision gate before Task308B2

Delivery is one focused commit and one normal push; report immediate exact-commit
CI separately from local acceptance. Next: independent review and green CI, then
separately authorized operator REPORT. To authorize B2, review reconciled population,
accepted bindings, exact exceptions, per-field reuse, unknown structures, source
contracts, source/disk budgets and historical-proof limitations. A bounded cohort
needs its own scope and explicit acquisition approval. Neither B1 COMPLETE nor an
ISIN checksum authorizes source requests, mass recovery, projection, profitability,
broker actions or the official 90-day experiment.
