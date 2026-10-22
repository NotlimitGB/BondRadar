# Task308B2R2 — Strict bondization source repair v2

## 1. Purpose and boundaries

Separate offline source-contract qualification of immutable B2/R1 evidence.
This is not a repair of a published v1 report, network acquisition, database
import, payment schedule, or total-return calculation. No production evidence
was accessed during development. All implementation checks use synthetic
responses, MockTransport and local private evidence directories.

Base: `1b96501ce50463779a4753b83a7967ef41f60012`. B2/R1 runtime contracts,
published artifacts, and Task306A4 fingerprint dependencies are unchanged.

## 2. Operator-supplied facts

The operator supplied a SHA-verified COMPLETE R1 diagnostic result: 10 attempts,
4 accepted responses, 6 quarantined responses, 58,953 received bytes and 12
observations (including reused B2 offers). These are provided context, not
development-time observations or runtime acceptance counts.

For both RU000A0JQAM6 and SU24020RMFS8, coupon BASELINE and redemption
BASELINE/TABLE_ONLY responses reportedly contain exactly two identical root
`coupons.cursor` members. TABLE_ONLY coupons reportedly have 38/12 rows without
proven completion. Offers reportedly contain 1/0 rows without proven completion.
All monetary unit authority remains unproven. Earlier contradictory narrative
counts do not override independently replayed journals.

## 3. Separate v2 contracts

Strict, frozen, extra-forbid models describe expected hashes, decoder views,
source observations, profile comparisons, structural reports, economic
qualification and the operation wrapper. Versions are
`bondization-candidate-decoder-v2`, `bondization-source-contract-report-v2`,
and `bondization-offline-qualification-v2`.

COMPLETE means the requested offline analysis executed; PARTIAL means missing
observations or a partial upstream diagnostic run. BLOCKED means integrity or
lineage verification failed: no authoritative observations/coverage are emitted.
Economic limitations do not masquerade as integrity failures or successful
economic qualification.

## 4. Pair-preserving JSON policy

Decode every object into ordered pairs, numbers into exact int/Decimal values,
and reject nonfinite constants. Before ordinary dictionaries exist, inspect all
members and duplicate locations. Strict equality distinguishes bool/int/Decimal,
null/string and array order; object key order alone is immaterial. No float,
first-key-wins or last-key-wins interpretation is used for qualification.

The only permitted reduction is exactly two root `coupons.cursor` occurrences,
strictly equal in their complete typed structure, with a valid cursor block.
The policy is `root-coupons-cursor-exception-v2`. Nested duplicates, other keys,
three occurrences, different values or malformed duplicated cursors reject the
whole structural interpretation. Duplicate findings retain bounded sanitized key
display, full key hash, occurrence count, location and value digests.

Resource bounds: 8 MiB per original body (R1 replay independently enforces its
2 MiB authorization), 100,000 JSON nodes, depth 64, 512 duplicate findings,
1,024 characters per numeric token, 1,024 columns and 100 rows per page.
No rewritten source body is saved.

## 5. Requested table and cursor isolation

Require the exact requested economic table, unique case-insensitive columns,
primitive cells and equal row widths. Case-variant duplicate table names are
ambiguous. Requested SECID/current ISIN context and returned identifiers remain
separate. Missing returned ISIN is null; contradictory supplied identities
reject the affected response. SU24020RMFS8's accepted context is obtained from
the verified lineage (operator fixture: RU000A100QS2), never manufactured from
the SECID.

Only `<requested table>.cursor` can prove that table's pagination. A redundant
coupon cursor in a redemption response is not a redemption cursor. Exact-int
INDEX/TOTAL/PAGESIZE must match offset, bounds and row count. COMPLETE requires
reaching TOTAL; otherwise PAGINATION_REQUIRED. Missing cursor means
COMPLETENESS_UNPROVEN, malformed target cursor means CURSOR_INVALID. Valid rows
remain diagnostic observations when a nonduplicated target cursor is invalid.
An empty/short page and HTTP success prove nothing independently.

R1 requests remain offset zero. Existing B2 amortization pages may retain their
original nonnegative offsets; no additional pages are fetched by R2.

## 6. Raw and interpreted identities

Each view records raw byte SHA-256, a separate repaired-view SHA-256, explicit
exception and policy, table schema/metadata, source rows and row hashes. The
view hash covers a typed canonical tree, preventing JSON strings from colliding
with Decimal values. It identifies an interpretation, not an original response.
Raw bytes remain content-addressed in their original accepted/quarantine root.

## 7. Verification order and lineage

`HistoricalCashflowQualificationService.qualify_existing_evidence(...)` requires
all B2/R1 paths and eight explicit semantic hashes. It snapshots the full input
file set, verifies hashes, reconstructs the B2 forensic report and diagnostic
plan using existing helpers, compares the entire objects, then replays R1 and
compares its entire published report. Old duplicate rejection stays unchanged.

Only after those checks does the v2 decoder inspect SHA-bound retained bodies.
Unavailable rejected B2 bytes cannot be reconstructed from receipt hashes.
Input hashes are checked again after analysis and twice during publication.
Any missing input, path-binding mismatch, mutation, tampering or reconciliation
failure blocks authoritative output. Source roots are read without locks or
checkpoints. Symlinks/reparse points are rejected by existing safe-path helpers.

## 8. Cohort and distinct observations

Only RU000A0JQAM6 and SU24020RMFS8 are eligible. Interpret R1 BASELINE and
TABLE_ONLY coupons/redemptions/offers, reused B2 offers, and existing accepted
B2 amortizations for those identities. Missing observations remain explicit.
No broader original B2 instrument is admitted into qualification.

Every occurrence preserves request/receipt identity, profile, observation time,
raw SHA, row index and source-row SHA. Cross-profile comparison uses exact
column schemas and row multisets, including multiplicities. Results are
CROSS_PROFILE_MATCH, CROSS_PROFILE_DIFFERENCE or COMPARISON_UNPROVEN. Different
times remain different observations; matching values do not establish a causal
query-parameter effect, publication date or additional coupon payment.

## 9. Economic and unit semantics

Reuse B2's pure event mapper and aliases without changing v1. Exact Decimal
amounts survive; Numeric(18,6) overflow/scale failure remains diagnostic invalid
evidence, without rounding. Typed source rows preserve fields, raw values,
currency, metadata, nominal, percentages and scheduled dates.

VALUEPRC and offer PRICE are context, not reconstructed monetary amounts.
Missing nominal/currency/unit/date/amount is not defaulted. Explicit unit claims
may support PARTIAL or CONFLICTING diagnostics, never DOCUMENTED authority.
There is no reviewed field-specific unit-proof input in this version; DOCUMENTED
cannot be produced. General ISS system-parameter documentation is insufficient.

Offers are not automatically exercised. Scheduled coupons/redemptions do not
prove actual payment. Structural coverage is separate from economic readiness.
An available complete observation with no structural/profile conflict may be
STRUCTURALLY_COMPLETE, but unit, payment and publication blockers remain.
Per-observation counts are not a deduplicated economic cashflow schedule.

## 10. Artifacts and publication

Four new artifacts, separate from every source root:

- `task308b2r2-source-contract-report.json`
- `task308b2r2-source-contract-summary.md`
- `task308b2r2-qualification-report.json`
- `task308b2r2-qualification-summary.md`

Existing sorted-key, ASCII, compact canonical serialization is reused. Semantic
hashes exclude their own hash fields; qualification binds the structural report
hash. Logical input labels and content hashes enter provenance; physical paths
and output metadata do not. Observation timestamps are source evidence.

Exclusive writer lock, private staging, shared 32 MiB output cap and 8 GiB free
reserve. Source JSON and qualification JSON follow summaries; qualification JSON
is the completion artifact. Identical reruns are idempotent; conflicting files
are not overwritten. Controlled publication failure removes only files created
by that attempt. Staging is cleaned up. `verify_published()` revalidates hashes,
qualification derivation, comparisons and both summaries. Independent source
verification reruns offline qualification against the original inputs and
compares its entire package with the published package.

## 11. Offline operator A–L procedure (not executed)

A. Pin the independently reviewed R2 commit and confirm its CI is green.
B. Prepare a separate checkout of that exact commit.
C. Verify imports against the reviewed runner image; no dependency installation
   from the network during the report run.
D–F. Mount original B2 evidence read-only at `/probe/task308b2`, R1 diagnostic
   evidence read-only at `/r1evidence`, and all plans/reports/authorizations
   read-only at explicit paths. Original absolute destination bindings must hold.
G. Use `--network none`, non-root UID/GID, `--read-only`, `--cap-drop ALL`,
   `--security-opt no-new-privileges`, code `:ro`, and an independent output mount.
H. Run only the following entrypoint. These are operator instructions, not
   authorization to run them as part of development.

```sh
# Set these to the exact read-only input file paths, not evidence copies.
: "${B2_PLAN:?}" "${B2_AUTH:?}" "${B2_EVAL:?}"
: "${R1_FORENSICS:?}" "${R1_PLAN:?}" "${R1_AUTH:?}" "${R1_REPORT:?}"
python /code/scripts/historical_cashflow_qualification.py \
  --mode QUALIFY_EXISTING_EVIDENCE \
  --b2-plan "$B2_PLAN" --b2-authorization "$B2_AUTH" \
  --b2-evidence-root /probe/task308b2 --b2-evaluation "$B2_EVAL" \
  --r1-forensics "$R1_FORENSICS" --r1-plan "$R1_PLAN" \
  --r1-authorization "$R1_AUTH" --r1-evidence-root /r1evidence \
  --r1-report "$R1_REPORT" --output /out \
  --expected-b2-plan-sha256 477c1c1cc2c154503405205aa3f811a2de583b4ca689ba832731da05d0503a52 \
  --expected-b2-authorization-sha256 737a4bec5a271de497aa4ba5877d7bab3b0fa5737cb47a9568136d69dd587d60 \
  --expected-b2-probe-sha256 fe9d14f4e4b69212cff17f7cc0a7b6e423df03f65c282697788dac2545164013 \
  --expected-b2-evaluation-sha256 73cb99d457182d44e5f65a1fcc9d7904a37b73e4081efff22b9081068110b579 \
  --expected-forensics-sha256 ebe29aec1ce34863e0c62785891bc8434cfaf7e72524cf8612dc4c3ea6651e72 \
  --expected-diagnostic-plan-sha256 7bf4090d919ebb68be79ff9719677efc2efc315334aba1cde6bc1c8378672dbc \
  --expected-diagnostic-authorization-sha256 e1ff3ceb76a5d08eb5794d9f1696b256724688699fc6487e6866a213cc8e14fc \
  --expected-diagnostic-report-sha256 2a3cea10545a6e955392a8515663da9efe5ca695ba4ad5ea94b0170a381bc0a4
```

I. Independently verify source hashes, both report hashes and input byte
   preservation; rerun the same offline command and compare the entire package.
   For library verification:

```python
from app.services.historical_cashflow_qualification_artifacts import verify_published
package = verify_published(output_dir="/out")
assert package.status != "BLOCKED"
assert package.qualification.source_contract_report_sha256 == package.source_contract.report_sha256
```

J. Inspect per-SECID/table findings in the generated summaries. Console stdout
   is a type-stable operation envelope; stderr contains only the two result hashes.
   Complete raw responses are never printed.
K. Preserve both old evidence trees and all old reports unchanged.
L. Stop for independent review; do not issue further source requests.

## 12. Synthetic verification and resource limits

Focused decoder and qualification tests cover the duplicate matrix, table/cursor
isolation, exact identities, Decimal preservation, unit safety, old replay,
quarantine integrity, cross-profile multiplicities, timestamps, publication,
interruption, input drift, runner paths and fingerprint preservation. Relevant
existing B2/R1 cases are run unchanged; broad regression remains CI's job.

Input inspection is bounded to 512 evidence entries, 256 MiB total input,
32 MiB per JSON input, and one bounded body per decoder invocation. No full
raw response is added to reports. This task makes no production RSS or real
cashflow-completeness claim.

## 13. Readiness and next gates

Always false: actual payment confirmation, historical publication and decision
availability proof, PIT readiness, historical backtest readiness, total-return
execution readiness, acquisition readiness, network access, DB mutation and
financial event creation. No migration, resolver, import, live pilot, broker,
repair of stored data, total-return backtest or official Day 0 is performed.

Implementation acceptance is synthetic-only. Operational acceptance requires
separate authorization for the offline operator replay and review of its exact
findings. Missing pagination evidence and field-specific unit documentation
require a separately reviewed task; neither is silently cleared by v2 recovery.
