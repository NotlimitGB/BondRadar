# Task308B2R3 — Bondization cashflow semantics and source-row integrity v1

## 1. Scope and implementation boundary

Base SHA: `09e3a0b3b7bcaa2bdee939f9076a6a05beb57b00`.
Eight new files implement strict schemas, a pure row-integrity helper, source-only
semantics, artifact verification/publication, a separate runner and two focused
test modules. B2/R1/R2 contracts, serializers, artifacts and A4 fingerprint
dependencies remain unchanged. No migration or application DB access is needed.

Development uses only synthetic fixtures and MockTransport. No production/VDS
evidence was accessed, no ISS request or operator command was executed, and no
historical return, schedule amount reconstruction or profitability was computed.

## 2. Operator-supplied real evidence

The operator reports COMPLETE R2 execution and independent replay PASS, with 14
observations, 10 structurally recoverable and 4 requested-table-missing results.
These are supplied facts, not runtime invariants or independently observed
development evidence.

For RU000A0JQAM6, coupons reportedly contain 38 rows and a complete source cursor:
35 monetary values and three missing values at indices 35/36/37. Dates range
2010-03-15 through 2028-09-15; faceunit RUB, facevalue 1000, first value 72.6 and
first valueprc 13.2500000000. A complete amortization row reports amortdate
2028-09-15, data_source maturity, faceunit RUB, facevalue/initialfacevalue/value/
value_rub 1000 and valueprc 100. Its offer observation reports 2009-10-13,
price 101.706 and null value, without completion or exercise proof.

For SU24020RMFS8, accepted current ISIN is reportedly RU000A100QS2. Twelve
coupon rows have values, ranging 2019-10-30 through 2022-07-27; first/last
values 13.19/29.03. Its maturity-marked amortization row reports 2022-07-27,
the same nominal/amount context above, and one complete row. Offers contain
zero rows without proven completion. No separate redemptions block was found
for either instrument in these observations. This does not establish that the
block can never exist for another instrument or source version.

## 3. The earlier false positional comparison

R2 stores each keyed row as an `object` typed tree, sorted by field name. Raw
source rows are positional arrays bound to source columns. Comparing the typed
object's key order with source column order, or expecting an `array` tag, tests
different representations rather than the same values. That reported false
comparison is not proof of data corruption. R2 serialization is preserved.

The independent check reconstructs `dict(zip(columns, positional_row))`, decodes
the R2 object by key, and compares every field's exact type/value. It checks all
retained rows in their original row order, never only the first row.

## 4. Typed integrity algorithm

Verify raw bytes against the receipt-bound SHA. After upstream R2 qualification,
read the exact requested block from pair-preserving JSON; never use another
economic table. Source columns and widths use the existing strict table helper.
Typed objects require unique string field names and only str/int/Decimal/bool/
NoneType scalar tags. Bool does not equal int, numeric strings do not equal
numbers, null does not equal blank. Decimal values preserve their exact tuple,
including scale, without float conversion or rounding.

Verify row count, row order and each R2 typed-row SHA. Separately verify old
B2 Event row SHA and raw page SHA; the legacy plain-row and R2 typed-row hashes
have different domains and must not be compared as the same digest.

SOURCE_ROW_INTEGRITY PASS means all *retained* R2 rows match their raw bodies.
An expected missing requested table has zero retained rows and is not corruption.
Structurally rejected observations also contribute zero retained rows and retain
their upstream diagnostics; PASS does not promote rejected evidence. Any actual
retained row mismatch produces BLOCKED and no authoritative semantic events.

## 5. Frozen lineage and public interface

Keyword-only `HistoricalCashflowSemanticsService.qualify_cashflow_semantics(...)`
accepts the complete B2/R1 input set, published R2 directory, and ten expected
semantic hashes. It uses unchanged R2 `verify_published()` plus full R2 offline
qualification/replay and compares the entire recovered package with publication.
Receipt identity, requested SECID/table, profile, timestamp, body SHA and offsets
bind each subsequent row check to its source request.

Strict frozen extra-forbid versions: `bondization-source-row-integrity-v1` and
`bondization-cashflow-semantics-v1`. The wrapper is type-stable for COMPLETE,
PARTIAL and BLOCKED. COMPLETE describes completed offline analysis, not monetary
or historical readiness. Missing observations preserve upstream PARTIAL.

Input fingerprints cover B2/R1 evidence, all explicit original input files and
the R2 published set. They are checked before/after processing and before
publication. Incomplete/tampered lineage never becomes authoritative partial
semantic output. Source roots are read without creating locks/checkpoints.

## 6. Reviewed event classification policy

`bondization-source-semantics-policy-v1` is reviewed R3 source-only policy:

| Exact evidence | Source-only class |
|---|---|
| Coupon table occurrence | SCHEDULED_COUPON |
| Amortizations with exact string data_source `maturity` | SCHEDULED_FINAL_MATURITY |
| Other or absent amortization marker | SOURCE_AMORTIZATION_UNCLASSIFIED |
| Offer table occurrence | SCHEDULED_OFFER_OBSERVATION |
| Actual supplied redemption table occurrence | SOURCE_REDEMPTION_UNCLASSIFIED |

No trim, translation, case normalization or negative inference is applied to
marker values. `Maturity`, `partial` and unknown markers do not qualify as
final maturity or partial amortization. SCHEDULED_PARTIAL_AMORTIZATION exists
as a contract class but cannot be emitted without a future separately reviewed
positive source proof/policy. R3 has no such proof input.

The maturity marker supports a source-reported scheduled classification, not
field-specific documented monetary units or evidence of received principal.
Every event has PAYMENT_STATUS NOT_CONFIRMED. No extra redemption is generated
from the maturity row or from an offer.

## 7. Field-level authority matrix

Every source-row occurrence carries the eight independent field findings, exact
raw type/value, original field spelling, proposed semantic, authority source,
scope, proof status, conflicts and limitations. Absence is MISSING; supplied
null remains NoneType. Neither is rewritten.

| Field | Proposed source interpretation | Current authority boundary |
|---|---|---|
| faceunit | Currency of nominal | PARTIAL when supplied; MOEX documentation below covers history, not Bondization amount units |
| facevalue | Nominal context | UNPROVEN field-specific unit authority |
| initialfacevalue | Original nominal context | UNPROVEN |
| value | Supplied scheduled source amount | Per-bond/per-lot/per-issue unit UNPROVEN |
| valueprc | Source percentage context | No formula or nominal-percent transformation |
| value_rub | Candidate RUB-equivalent context | Denominator and conversion authority UNPROVEN |
| issuevalue | Candidate issue-level context | Not accepted as a per-bond amount |
| data_source | Source marker | Exact maturity literal PARTIAL under reviewed source-only policy; other markers UNPROVEN |

The official [FACEUNIT announcement](https://www.moex.com/n18266), dated
2018-01-10, describes nominal currency in history responses. That scope does
not document Bondization VALUE as RUB per single bond. The MOEX
[amortization definition](https://bondguide.moex.com/terms/9) explains partial
nominal repayment conceptually, not exact endpoint field interpretation.
Neither source establishes monetary denomination for every field here.

No reviewed field-specific monetary unit contract is available in this version.
DOCUMENTED is a schema vocabulary value, not an output this implementation can
promote. Unit claims, names and numeric consistency are not payment authority.
No VALUEPRC formula, nominal default, faceunit-to-value unit inference, currency
conversion or offer-price-to-amount conversion is performed.

## 8. Canonical observation and profile safety

Keep every source occurrence with request ID, profile, row SHA, returned identity
and source observation time. These are observations, not summed schedule entries.
Canonical selection first prefers a structurally usable COMPLETE observation,
then BASELINE, TABLE_ONLY and B2_ACCEPTED. No choice is based on convenient
amount availability or later economic outcomes.

Multiple pages within a profile, or continuation pages, are not silently treated
as one complete observation; diagnostic MULTIPAGE_OBSERVATIONS_NOT_COMBINED
prevents that profile becoming a canonical schedule. Other independently usable
profiles remain visible. Missing tables have no canonical observation.

Existing R2 cross-profile disagreements and any differing source metadata are
preserved, with explicit economic-promotion blockers. Agreement is diagnostic
consistency across observation times, not historical publication proof.

## 9. Coupon amount coverage and maturity records

Coupon counts use only the chosen request ID. Cursor completeness, row count,
amount-present, amount-missing, invalid-present count and exact missing dates
are separate. Invalid-present is a subset of present, not missing. All absent,
null or blank supported amount aliases mean missing; malformed/conflicting,
negative, nonfinite or storage-unrepresentable supplied amounts remain invalid.
There is no interpolation, extrapolation or replacement with zero.

Source event records preserve SECID, returned ISIN (null if absent), exact
scheduled date, typed data_source/value/valueprc/value_rub/facevalue/
initialfacevalue/faceunit fields, row SHA, observation time and source
completeness. These are sufficient to inspect the maturity-marked occurrence
without inventing an independent principal payment. Offers remain separate
observations with no automatic exercise.

## 10. Artifacts, hashing and publication

- `task308b2r3-source-row-integrity.json`
- `task308b2r3-cashflow-semantics.json`
- `task308b2r3-cashflow-semantics-summary.md`

Existing canonical sorted-key ASCII compact serialization is reused. Integrity
binds all ten upstream hashes and logical input fingerprints; semantics binds
integrity SHA. Each report excludes its own SHA from hash material. Output paths,
runtime, connection data and generated timestamps do not enter semantic hashes.
Source observation timestamps remain evidence and are preserved.

Publication uses an exclusive output lock, private staging and immutable writes.
The semantic JSON is installed last as completion artifact. Identical reruns are
idempotent; conflicting existing artifacts are rejected. Controlled failure
removes only newly installed files; staging is cleaned. The output directory
must be disjoint from source roots and original input parents; symlinks/reparse
points are rejected. Raw source bodies are not copied into the new artifacts.

Retain R2 bounds: 256 MiB total input, 32 MiB per JSON input, 512 evidence entries,
8 MiB original body, R2's stricter authorized R1 bounds, 32 MiB combined output
and 8 GiB free reserve. There is no acquisition budget or source client in R3.

`verify_published()` checks canonical report hashes, lineage/counts, allowed
policy outputs and exact generated summary. Independent operational evidence
verification must additionally rerun full offline qualification and compare the
entire resulting package, not just trust self-contained output hashes.

## 11. Future offline operator command (not executed)

First require independent review, green exact-commit CI, a pinned reviewed R3
checkout and compatible runner image/dependencies. Use a separate read-only
code checkout, `--network none`, non-root UID/GID, read-only container,
`--cap-drop ALL`, `--security-opt no-new-privileges`, evidence/report input `:ro`
mounts and an independent writable output mount. Do not fetch dependencies.

Preserve original B2 and R1 evidence mounts `/probe/task308b2` and `/r1evidence`,
and the original absolute B2 plan/auth/evaluation paths used by the path-bound
R1 plan reconstruction. Mount the R2 published set read-only at `/r2reports`.
Set the following variables to the exact original read-only input files.

```sh
: "${B2_PLAN:?}" "${B2_AUTH:?}" "${B2_EVAL:?}"
: "${R1_FORENSICS:?}" "${R1_PLAN:?}" "${R1_AUTH:?}" "${R1_REPORT:?}"
python /code/scripts/historical_cashflow_semantics.py \
  --mode QUALIFY_CASHFLOW_SEMANTICS \
  --b2-plan "$B2_PLAN" --b2-authorization "$B2_AUTH" \
  --b2-evidence-root /probe/task308b2 --b2-evaluation "$B2_EVAL" \
  --r1-forensics "$R1_FORENSICS" --r1-plan "$R1_PLAN" \
  --r1-authorization "$R1_AUTH" --r1-evidence-root /r1evidence \
  --r1-report "$R1_REPORT" --r2-output-dir /r2reports --output /out \
  --expected-b2-plan-sha256 477c1c1cc2c154503405205aa3f811a2de583b4ca689ba832731da05d0503a52 \
  --expected-b2-authorization-sha256 737a4bec5a271de497aa4ba5877d7bab3b0fa5737cb47a9568136d69dd587d60 \
  --expected-b2-probe-sha256 fe9d14f4e4b69212cff17f7cc0a7b6e423df03f65c282697788dac2545164013 \
  --expected-b2-evaluation-sha256 73cb99d457182d44e5f65a1fcc9d7904a37b73e4081efff22b9081068110b579 \
  --expected-forensics-sha256 ebe29aec1ce34863e0c62785891bc8434cfaf7e72524cf8612dc4c3ea6651e72 \
  --expected-diagnostic-plan-sha256 7bf4090d919ebb68be79ff9719677efc2efc315334aba1cde6bc1c8378672dbc \
  --expected-diagnostic-authorization-sha256 e1ff3ceb76a5d08eb5794d9f1696b256724688699fc6487e6866a213cc8e14fc \
  --expected-diagnostic-report-sha256 2a3cea10545a6e955392a8515663da9efe5ca695ba4ad5ea94b0170a381bc0a4 \
  --expected-r2-source-contract-sha256 7a698ad26fd3fcf8e4387285755212e98fff2edce013db48538a0012700decd0 \
  --expected-r2-qualification-sha256 db6da43136f547a1ce7efad31fc30343a04939b8d7088877dd5f40b9548e8a47
```

Verify both new hashes and full source preservation; repeat the offline command
to verify deterministic replay, then inspect canonical observation references,
coupon missing dates, per-row fields and all limitations. Console output is only
a stable status/hash envelope. No source response bodies are printed.

## 12. Tests, capabilities and stop

Focused hermetic tests cover typed keyed reconstruction and every retained row,
null/Decimal/type distinctions, raw/row/legacy hash mismatches, expected missing
tables, exact maturity markers, unknown/partial markers, coupon missing values,
canonical selection, profile disagreements, amount-unit boundaries, full B2/R1/R2
replay, source preservation, immutable/interruptible publication, arbitrary-root
runner, deterministic output and static no-network/no-DB isolation.

Only focused new modules and the minimum relevant unchanged B2/R1/R2 cases are
run locally; broad regression is delegated to exact-commit CI. Compile/import,
working/staged diff checks and the unchanged A4 fingerprint are also checked.

Implemented capabilities concern audit integrity and source semantics only.
Actual payment confirmation, historical publication/decision availability,
PIT, total-return execution, historical backtest and acquisition readiness remain
false. There is no database representation, import, live request, trading,
deployment, migration or official Day 0. After review and green CI, the only next
step is separately authorized offline operational replay. Monetary-unit proof,
source revisions/publication, remaining unknown coupons and any future economic
cashflow schedule require separate reviewed evidence and decisions.
