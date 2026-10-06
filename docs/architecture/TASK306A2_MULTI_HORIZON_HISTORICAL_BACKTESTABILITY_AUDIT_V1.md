# Task306A2 — Multi-Horizon Historical Backtestability Audit v1

## 1. Objective and superseding boundary
The v2 task request supersedes the earlier proposed 90-day-only Task306A2.
One implementation audits 90/180/365 calendar-day endpoints; 365 is primary.
It answers evidence measurability, not economic effectiveness. Forward Task303/304
keep daily accounting and 90-day policy unchanged.

## 2. Baseline and scope
Start SHA: 491a8213066c85706c10c17b9c6e91b0f6d6d7cb; Alembic 202610040001.
Exactly seven new files: schema, pure endpoint helper, read-only audit service,
REPORT runner, two focused tests, this document. No existing runtime edits,
models, migrations, source ingestion, or temporary-directory changes.

## 3. Provided production context
The request supplies market range 2026-02-19..2026-09-30 and 89 candidate dates;
Task306A reported zero PIT_SAFE/DIAGNOSTIC_ONLY and 89 UNUSABLE dates.
Task306A1 supplied 237321 credit-size-prerequisite observations, independent
credit maximum 2892, decision-only maximum 32, eight canonical OFZ with history,
176 usable yield/duration observations and 22 Task268 READY dates. It also
reported 62 dates with full 90-day global range but zero continuous coverage.
These are context only, not runtime constants, test expectations or new findings.

## 4. API and read-only snapshot
MultiHorizonHistoricalBacktestabilityAuditService(db).build() has no methodology
overrides. Caller transaction must already be consistent and read-only:
PostgreSQL repeatable-read/serializable read-only; SQLite query_only plus physical
transaction. Otherwise stable BLOCKED. Task306A1 executes once in the same
snapshot; BLOCKED stops supporting reads. Its actual canonical SHA is retained.
Supporting evidence uses the unchanged scalar/aggregate reader. Reconstructed
Task306A source SHA and C1 decision counts must match. Caller new/dirty/deleted
remain unchanged under no_autoflush. Separate Task268 identity maps use the same
Connection with rollback_only; pending caller values cannot alter curves.

## 5. Frozen policy and monthly selection
Horizons ordered (90,180,365), max quote age seven. Monthly research entry is the
first persisted MOEX trade date of each calendar month, independent of C1
decision counts and all readiness gates. All raw
trade dates are retained. Raw recovery/outcomes never alter the entry grid.
This is a lower-frequency diagnostic grid, not statistically independent samples.
Actual Task277 readiness remains NOT_EVALUATED.

## 6. Entry and terminal authority
Entry selects latest MOEX snapshot strictly before T; terminal selects latest
MOEX snapshot no later than T+H. Same-day maximum ID wins. Entry age 1..7;
terminal 0..7. No quote after target, neighboring Bond, interpolation or next
snapshot fallback. Valid positive clean_price has priority, then positive price.
NKD is finite nonnegative; current verified RUB/positive nominal are diagnostic.
Identity, snapshot/date/age and quote basis are preserved; no dirty-value formula.

## 7. Independent endpoint horizons
Each horizon reports canonical and after-safe-raw-recovery funnels in the request
order. Global range is independent, not cumulative. Entry/terminal values can
support a 365-day endpoint with no intermediate snapshots. This neither relaxes
forward daily-chain accounting nor proves exhaustive historical evidence.
Counts on all observed Bonds and decision-only intersections stay separate.

## 8. Cashflows, redemption and coupon observability
Only persisted MOEX events in (T,T+H] apply. Coupon/amortization/redemption require
finite nonnegative RUB amounts. Duplicates, unsupported events and automatic
post-redemption events block readiness; prior redemption blocks entry. A valid
redemption closes terminal quote requirement, even without full global tail.
Maturity cannot substitute for redemption. Offers remain diagnostic only.
Coupon baseline observable means at least one coupon and valid coupon evidence,
including duplicate/post-redemption validation. Principal is not coupon income.
Zero persisted events never prove zero contractual cashflows. Contractual
completeness remains UNPROVEN even when diagnostic endpoint readiness is true.

## 9. Raw price and NKD recovery
Only missing canonical fields are candidates. Nonnull invalid fields are not
overwritten. Raw must be the same snapshot's MOEX mapping, with matching trimmed
case-insensitive SECID and exact parsed TRADEDATE. Direct price mapping uses
LEGALCLOSEPRICE and CLOSE/WAPRICE/PRICE priority; history canonical mapping uses
legal_close_price and close_price/market_price/weighted_average_price/last_price.
Existing ingestion Decimal parsers are reused. Raw/history disagreement or an
invalid supplied representation fails closed. NKD reuses Task294 parser and
ACCINT/ACCRUEDINT agreement semantics, with history accrued_interest compatibility.
No apply, flush, repair or network request. Recoverability states are
ALREADY_CANONICAL / RAW_RECOVERABLE / RAW_CONFLICT / RAW_INVALID / RAW_MISSING.

## 10. Coverage and portfolio prerequisites
Market coverage counts unique selected snapshot IDs separately for entry and
90/180/365 endpoints; economic price/NKD usability is independent of freshness
and current terms, which remain endpoint gates. Missing endpoints appear in
funnel blockers, not invented snapshot counts. Hypothetical coverage counts
additional economic readiness from safe raw evidence. Portfolio 3/5/10 counts
intersect exact decision-only IDs with diagnostic outcome-ready IDs, separately
canonical/recoverable, on monthly dates. They do not establish risk admissibility
or the ability to select a duration-matched portfolio.

## 11. Primary 365-day classification
RESEARCH_WINDOWS_AVAILABLE means at least one monthly date with three ready
Bonds; PARTIAL means some ready Bonds but none reaches three; NO_365D_WINDOWS
means none. Canonical/recoverable classifications are independent. 12/24/36
windows are planning scenarios, never profitability acceptance thresholds.

## 12. OFZ selection and modified-duration gap
C1's Task268-at-T status is preserved. Separate Task268(T-1, moex, age=7) defines
pre-decision components once per date. Component endpoints use the same horizon
rules. Frequency is not a Task268 curve gate. Current-term/frequency/yield
prerequisites are reported separately from curve readiness; actual Task272
READY and modified-duration matching are NOT_EVALUATED. No duration formula.
All canonical OFZ have frequency diagnostics: already verified, evidence
recoverable, evidence conflict, missing. Source-backed MOEX frequency evidence
observed by cutoff can support recoverability; spacing cannot. Missing evidence
has exact Bond IDs and TARGETED_OFZ_SECURITY_MASTER_REFRESH reason; no refresh runs.

## 13. Historical range scenarios
Anchor only on persisted max date D, no clock. Latest planning monthly slot is
month-start <=D-365. For N windows first slot is N-1 months earlier; history starts
30 calendar days before it for liquidity, ends latest slot+365. N=1/12/24/36.
3Y/5Y are 36/60 entry slots, not merely three/five years of market data. Calendar
slots are hypothetical, never asserted MOEX trade dates. missing span counts
required extensions outside current range. Leap years use timedelta(days=365).
Range coverage does not imply price/NKD/cashflow/terms/OFZ/universe coverage.

## 14. Remediation and PIT boundaries
Daily discontinuity alone never triggers broad backfill. Missing first annual
window range can support broad-history planning; missing endpoints inside an
adequate range supports targeted planning. Safe raw NKD/price pathways, cashflow
and OFZ evidence are reported independently. "SUFFICIENT" in the NKD category
refers to its market-field evidence only, not all research blockers. Current
Security Master/issuer mappings and historical universe completeness remain
explicit limitations. No broad credit remediation based on legacy zero counts.
No PIT_SAFE_BACKTEST or model/live readiness follows from diagnostic endpoints.

## 15. Runner and hashing
REPORT only. Own read-only transaction, rollback always, no commit. stdout JSON
by default, explicit --output only, arbitrary repository mount, sanitized errors.
NETWORK_ACCESS=false means no source requests; selected DB reads are necessary.
Canonical sorted JSON uses ASCII, compact separators and allow_nan=False;
includes policy, source SHA, evidence summaries/scenarios/remediation. Own SHA,
clock, connection credentials and output paths excluded. Percentages use fresh
Decimal precision 28/HALF_EVEN. Immutable contracts include stable BLOCKED paths.

## 16. Verification and safety handoff
Focused synthetic endpoint/runner tests, relevant Task268/272/273/294/306A/A1
regressions, compile, unchanged Alembic head and seven-file diff review. Full
local suite deferred to exact-commit CI. One commit and one normal push, one CI
snapshot without polling. Independent review and green CI precede a separately
authorized production read-only audit. No production/VDS, live source, mutation,
profitability, deployment, backfill, official Day0, Task306B/C or Live execution.

Local acceptance: 59 focused tests passed. Regression selection contained 553
unique scenarios; 552 passed in the backend cwd, and a cwd-sensitive existing
MOEX test failed to locate backend/requirements.txt. The unchanged MOEX module
was rerun from repository root: all 61 tests passed. No test was skipped or
modified to handle this. Compile and Alembic head checks passed.


## 17. Task306A2F — Model-independent monthly schedule
Task306A2F supersedes only the original model-dependent monthly selection.
Frozen literal is FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH with no old alias.
Select min raw_market_dates per year/month before per-date calculations. Months
with 0/1/2 decision candidates remain scheduled and remain in monthly denominators;
later readiness cannot shift their entry date. Such months are research findings,
not omitted observations. Portfolio 3/5/10 and primary >=3 thresholds are unchanged,
but operate on this fixed schedule. Missing scheduled source RCA date returns
BLOCKED / MONTHLY_ENTRY_DATE_NOT_IN_SOURCE_RCA_GRID with source SHA, without
substitution or partial results. Endpoint/cashflow/raw/OFZ/credit/range logic is
unchanged. No source mutation, profitability, production or deployment.

Task306A2F local acceptance: 64 Task306A2 audit/runner focused tests and 95
unchanged Task306A/A1 regressions passed. Compile and Alembic head checks passed;
exact scope is four existing schema/service/audit-test/document files.
