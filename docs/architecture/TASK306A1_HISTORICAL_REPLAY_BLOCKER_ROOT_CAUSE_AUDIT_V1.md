# Task306A1 — Historical Replay Blocker Root-Cause Audit v1

## 1. Purpose and baseline

Explain outcome, OFZ, credit-peer and joint-chain blockers before choosing any
remediation. Starting SHA `da6697e07c408fcfe26203e153d8454aa7a36bf6`;
Task306A CI `37351346497` succeeded; Alembic remains `202610040001`.
Task306B is paused. No profitability, replay, repair or production operation runs.

## 2. Provided production context

The operator supplied a COMPLETE production Task306A report from
`/home/bondradar/.local/share/bondradar/evidence/task306a-production-readonly-audit-20261006T044654Z.json`.
Its provided raw SHA is `1bf05d72de38a2d66ad5f8082d9cf29fe5cf0e4273151e526f437c92c1a2ba5a`;
semantic SHA is `c7703dafcc13b146b41fdbfabe276a30ba590727613be4f7262528c9c3117f39`.
Provided observations: 89 dates, all UNUSABLE, zero PIT/diagnostic dates, zero
two-node OFZ dates, zero FULL outcome dates and zero credit-peer/joint maxima.
239503 market rows, 3132 historical Bonds, 13924 cashflows, 9829 ratings,
1187 Bond-rated Bonds, 290520 legacy labels; range 2026-02-19 through 2026-09-30.
These are context supplied by the operator, not independently fetched here,
runtime invariants, fixture expectations or readiness overrides.

## 3. API and contracts

`HistoricalReplayBlockerRootCauseAuditService(db).build()` returns frozen strict
extra-forbid `historical-replay-blocker-root-cause-audit-v1`, COMPLETE or BLOCKED.
Source SHA/classification/counts are nullable only when source acquisition never
completed. Blocked summaries keep the same types. Capabilities describe implemented
audits, never dataset safety. `pit_ready`, DB_MUTATION and NETWORK_ACCESS are false.

## 4. One consistent read-only DB snapshot

Direct service calls require an existing transaction: PostgreSQL REPEATABLE READ
or SERIALIZABLE plus read-only; SQLite query_only and an active physical
transaction. Unsupported contexts are BLOCKED. No caller transaction is committed,
rolled back or upgraded by the service. Under no_autoflush, Task306A runs exactly
once, followed by scalar supporting reads on the same connection/snapshot.
Source BLOCKED prevents additional reads and curve calls.

## 5. Source binding and unchanged Task306A

Reuse its existing reader and pure helpers without edits/refactor. Reconstruct
its report from the second persisted read and require identical semantic SHA.
Per-date FULL counts, diagnostic OFZ nodes, original joint IDs and original
outcome-gated peer IDs must also match. Mismatch is stable BLOCKED. New independent
credit/decision counts are explicitly different diagnostic questions, not a
replacement for the source report or its classifications.

## 6. Counting and funnel types

Stages follow the canonical request orders. CUMULATIVE_GATE filters the current
set; INDEPENDENT_DIAGNOSTIC uses the original universe and never filters subsequent
stages. Each exposes input/pass/fail counts, percentage and independent pass
count. Bond IDs are unique within each set; several selectors never multiply
Bond counts. Aggregate funnels count Bond/date observations, with affected dates
and distinct Bonds separate. Node aggregation explicitly uses COMPONENTS_TO_NODES.

## 7. Outcome funnel

OBSERVED_BEFORE_ENTRY → CURRENT_PROFILE_PRESENT → DIRTY_VALUE_TERMS_READY →
ENTRY_OR_PRE_ENTRY_QUOTE_AVAILABLE → VALID_QUOTE_INTERVAL_EXISTS_AT_ENTRY →
FUTURE_HORIZON_IN_DATABASE_RANGE → SOME_FUTURE_MARKET_COVERAGE →
FULL_QUOTE_COVERAGE_TO_REQUIRED_END → CASHFLOW_EVENTS_SYNTACTICALLY_VALID →
MATURITY_REDEMPTION_EVIDENCE_READY → FULL_OBSERVED_OUTCOME.
The calendar-range and some-future-market stages are independent diagnostics.
Verified current RUB/positive nominal support diagnostic dirty-input coverage;
no dirty-value formula or current-data PIT promotion exists.

## 8. Horizon and quote gaps

T+90 beyond the latest global MOEX date is a range fact, not itself proof that a
redeemed Bond lacks observed outcome coverage. Valid redemption may shorten the
required quote end. Reuse seven-day quote intervals without valuing securities.
Gap distributions are computed only where the full calendar horizon is inside
the global market range; first-gap offsets, largest uncovered interval and
continuous-day counts remain factual counts. Per-date maximum continuous days
also remains visible for tail dates. No trading-calendar completeness is assumed.

## 9. Cashflows

Persisted MOEX events only, ordered coupon → amortization → redemption per date.
Offers are diagnostics; unsupported events, duplicates, invalid RUB/amount pairs,
post-redemption events, pre-entry redemption and missing maturity redemption remain
separate reasons. Zero persisted events never proves zero contractual cashflows.
No current coupon-rate/nominal fallback generates payments. FULL remains observed
evidence coverage, never exhaustive contractual total-return proof.

## 10. OFZ funnel

CANONICAL_OFZ_IDENTITY → SECURITY_MASTER_PROFILE_PRESENT → RUB_VERIFIED →
FIXED_COUPON → DATED_NON_PERPETUAL → COUPON_FREQUENCY_VERIFIED →
BULLET_AMORTIZATION → MATURITY_VERIFIED → NOT_MATURED_AT_DATE →
NOT_EXCLUDED_OFZ_PK_IN_AD → FRESH_HISTORICAL_MOEX_OBSERVATION → DURATION_READY →
YTM_READY → TASK306A_CURRENT_STRUCTURE_ELIGIBLE → TASK268_CURVE_ELIGIBLE →
DISTINCT_DURATION_NODE. The final two are independent authority diagnostics.
Current state/value distributions, canonical histories and yield/duration coverage
before structural filtering prevent confusing structural and market failures.
Representative IDs/ISINs are sorted and capped at ten per reason.

## 11. Actual Task268 cross-check

One `build_curve(T, market_source="moex", max_curve_age_days=7)` per source date.
Separate ORM Session identity map uses the same connection with rollback_only
join mode, so caller pending Bond/profile changes cannot contaminate the result.
No connection or transaction is replaced. Retain status, common trade date, node
count and exact diagnostics. Actual Task268 does not require coupon frequency,
permits entry-day quotes and keeps only its latest common curve date. Task306A
requires frequency and uses pre-entry observations. Node-count disagreement is
TASK306A_OFZ_DIAGNOSTIC_GATE_MISMATCH; neither implementation is changed here.
Task268's allowed median-yield work is not an OFZ total-return calculation.

## 12. Credit funnel and identity

OBSERVED_BOND → HAS_ANY_RATING_TARGET → EVENT_DATE_BEFORE_DECISION →
RESOLUTION_STATE_RESOLVED → TARGET_IDENTITY_MATCHES → PUBLICATION_NOT_FUTURE →
PUBLICATION_PROVEN → UNIQUE_LATEST_EVENT_PER_SELECTOR → ARTIFACT_CONTRACT_VALID →
SOURCE_PROVIDER_PRESENT → RATING_VALUE_PRESENT → SOURCE_NATIVE_KEY_CREATED →
COHORT_SIZE_GE_2_TOTAL → COHORT_SIZE_GE_3_TOTAL → TASK299_MIN_2_PEERS_SIZE_PREREQUISITE.
BOND identity is exact Bond ID/ISIN; issuer identity is exact current verified
mapping/INN. Unresolved source ISIN associations are diagnostic attribution only,
never resolved targets. CURRENT_ISSUER_MAPPING_DIAGNOSTIC is not historical proof.

## 13. Publication and cohorts

DATE D is available D+1 at 00:00 UTC; timestamps are compared exactly to T cutoff.
Diagnostic branch keeps UNKNOWN and rejects FUTURE; PUBLICATION_PROVEN is its
independent side stage. The proven branch excludes UNKNOWN before latest-event
selection. Preserve exact target/agency/provider/scale/value, including null versus
blank scale. Cohort members count distinct Bonds; three including target means
two potential peers. Top twenty historical maxima use safe categorical fields.
Actual Task277 READY is NOT_EVALUATED: no READY member, spread or percentile is
invented, and no Task299 scoring or ranking runs. This is an agreed refinement
of the original READY-named stage, not a weakened Task277 gate.

## 14. Decision-only and outcome intersection

OBSERVED → DECISION_MARKET → LIQUIDITY_OBSERVATIONS → LIQUIDITY_COMPONENTS →
LIQUIDITY_CAPACITY → CURRENT_TERMS → CREDIT_EVENT → CURRENT_ISSUER → CREDIT_PEER →
OUTCOME. Credit peer sizing uses decision prerequisites before outcome. Original
Task306A peers are reconstructed separately using its outcome-gated pool.
OFZ status and Task270's ten-member benchmark minimum remain independent facts.
Task306A checks turnover/trades; RCA additionally reports volume component
presence for the actual Task270 component prerequisite without computing scores.
Neither intersection is an executed or READY modern investment model.

## 15. Primary and secondary causes

At a zero endpoint, choose the earliest cumulative zero-pass gate with nonzero
input. A positive independent pass count identifies COMBINATION_AT_<stage>
instead of claiming one gate is globally absent. Empty initial input is explicit.
Independent failures remain secondary; aggregate frequencies sort count descending,
reason ascending. Complete private ID sets determine distinct affected counts;
bounded representative examples never substitute for denominators.

## 16. Remediation classification

Only categories from the request, linked to factual reasons. Proven gate mismatch
or outcome-gated peer masking maps to AUDIT_LOGIC_FIX. Other mappings separate
market, cashflows, canonical terms, issuer/credit evidence and historical universe.
Expected family exclusions/future publication can be NO_REMEDIATION_REQUIRED.
Unexplained composite authority exclusions are not arbitrary backfill instructions.
No remediation is executed, and no profitability-impact estimate is returned.

## 17. REPORT runner and deterministic hash

`python scripts/historical_replay_blocker_root_cause_audit.py --mode REPORT` emits
stdout JSON. Only explicit --output writes a file. Runner owns a PostgreSQL
repeatable-read/read-only or SQLite query-only transaction, always rollback,
never commit, and restores previous pooled SQLite query_only mode. Paths derive
from the script location, including `/work/scripts/...`, not working directory.
Connection/output errors are sanitized. Canonical SHA includes source SHA and
RCA facts; excludes itself, wall clock, connection details and output path.
Decimal count percentages use fresh precision-28 HALF_EVEN, no float.

## 18. Verification and scope

Two focused modules, unchanged Task306A audit/runner regressions, Task268/275/277
and Task299 credit-context regression slice, compile, unchanged Alembic head,
working/staged checks and exact six-file review. Tests use isolated SQLite and
synthetic evidence only. Full local suite is deferred to exact-commit CI.
Local acceptance: 41 focused tests, 54 unchanged Task306A tests and 455 upstream
regression tests passed. Compile and Alembic checks passed (202610040001).
No existing contract, model, migration, methodology, API or frontend edit.

## 19. Delivery and safety handoff

One commit `Add historical replay blocker root cause audit`, one ordinary push,
one exact-commit CI snapshot without polling. Current data stays unchanged.
No production/VDS, deploy, source requests, backfill, replay, Live or Day 0.
Next: independent review and green CI, then separately authorized production
read-only Task306A1 RCA. Concrete remediation requires those production findings;
Task306B does not start automatically.
