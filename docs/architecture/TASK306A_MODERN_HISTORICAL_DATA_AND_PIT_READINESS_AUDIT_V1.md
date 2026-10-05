# Task306A — Modern Historical Data & PIT Readiness Audit v1

## 1. Purpose and development boundary

The modern Task298 → 299 → 300 → 301 → 302 methodology requires historical
qualification before official forward Day 0. This package inventories evidence;
it does not run that methodology, calculate returns, alter weights or authorize
Live. Starting SHA: `a051b90a05014cc89b9debfc89339ab8ab3f7392`.
Task305 CI `37310399175` succeeded. Alembic remains `202610040001`.

## 2. Architecture and fixed API

The scalar evidence reader and set-based SQL decision-window query feed
`ModernHistoricalReplayReadinessAuditService(db).build`. Frozen configuration is
exact `moex`, 90-day horizon, 30-calendar-day liquidity window, five observations
and seven-day quote age. Incorrect direct configuration raises ValueError before
SQL. No modern service loaders or legacy backtest methods run. The only reused
source mechanics are pure Task267 liquidity/duration parsers and canonical OFZ
identity. No Security Master resolver runs.

## 3. Contracts and failure paths

Strict frozen, extra-forbid models expose inventories, family matrix, Bond/date
coverage, date readiness, monthly windows, remediation and canonical SHA.
Version: `modern-historical-replay-readiness-audit-v1`; `pit_ready=false`.
COMPLETE means the audit ran, not that data is safe. Audit readiness is
PIT_READY_FOR_REPLAY / DIAGNOSTIC_REPLAY_ONLY / INSUFFICIENT_FOR_REPLAY;
dates are PIT_SAFE / DIAGNOSTIC_ONLY / UNUSABLE. Missing required schema or invalid
DB evidence produces a type-stable, sanitized BLOCKED report. Empty tables are
a successful insufficient-data audit.

## 4. Agreed decision-time cutoff

Decision time is 00:00 UTC of entry date T. Market observations must be strictly
before T, with age at most seven days. Liquidity uses [T−30,T−1], rather than
Task270's ordinary [T−29,T]. This agreed audit convention does not change Task270.
DATE-only publication D is available at D+1 00:00 UTC; timestamps are compared
exactly. UNKNOWN stays unknown. SQLite's timezone-naive persisted datetimes are
interpreted as UTC, matching the project's persisted timestamp convention.
Entry-day quotes belong to outcome evidence, never the decision input.

## 5. Historical universe and survivorship

Current Bonds and old prices prove an observed subset only. Inventories include
all Bonds, first/last MOEX dates, no-history Bonds, current listing states,
OFZ identity and cashflow counts. Per-date coverage identifies instruments first
observed later. No current architecture contract proves complete historical
listing/admission membership. HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN and
SURVIVORSHIP_BIAS_RISK prevent PIT_SAFE; no expected production count is fixed.

## 6. Market and liquidity inventory

Source `moex` is exact. Field coverage reports nonnull counts/percentages,
finite-value usability, Bond/date counts, economic dates, calendar gaps and
per-date Bond distributions. Calendar gaps include weekends and are not
automatically missing trading sessions. Source duration is read with the existing
raw-days parser, not blindly trusted stored duration. Liquidity coverage counts
distinct prior observation dates; it does not compute a liquidity score.
Raw turnover/trade-count component presence and positive-turnover capacity are
reported independently. Task270's ten-member benchmark minimum is also reported;
the agreed three-Bond diagnostic threshold measures data coverage, not full
Task270 READY or modern-chain execution readiness. Insufficient benchmark size
produces explicit diagnostics, never a PIT claim.
Mutable snapshot values and created_at do not prove complete historical versions
or historical availability.

## 7. Security Master and issuer identity

Current profile coverage is separate from assertions grouped by field, with
effective/observed/ingestion ranges and null counts. Current verified RUB, nominal,
fixed/dated structure, coupon frequency, lot and board can support diagnostics.
They cannot prove a complete historical resolved profile. Future observed_at is
never backdated by an earlier effective_at. Issuer mapping uses only verified
exact Security Reference bindings; Company/name/INN fallback does not run.
Current issuer linkage remains a look-ahead boundary.

## 8. Ratings and publication

Preserve exact target family, agency, provider, nullable scale and source-native
value. Filter future action dates/publications, retain UNKNOWN only for diagnostic
coverage. Multiple latest events cannot supply a cohort. Rating cohorts require
three distinct jointly covered Bonds under one exact selector/key; no ranking,
preferred agency, rating ordinal or cross-agency normalization exists.
Publication counts with resolved current targets do not prove historical issuer
mapping or historical universe completeness. Default events are inventoried,
never converted into investment judgments.

## 9. Financial/CBR evidence

Report dates, publication, observation, retrieval and ingestion are separate.
CBR forms 0409101/0409102/0409123/0409135 and exact-payload-bound archival
availability remain visible. UNKNOWN publication is not inferred from report
dates or system timestamps. Controlled statements contain no publication
authority. These families are NOT_CURRENT_TASK299_INPUT and do not introduce a
new model gate. Existing network-capable CBR audit modules are not called.

## 10. Duration, DV01 and OFZ

Inventory the source components needed by Task272/273; no pricing, modified
duration or DV01 formula runs. Dirty-value coverage requires verified current
RUB/positive nominal, valid clean quote (or Task273 price fallback) and nonnegative
NKD; dirty_price alone is not promoted. OFZ identity uses the shared helper.
PK/IN/AD exclusions, current fixed/bullet/dated maturity eligibility and positive
distinct source-duration nodes remain diagnostic gates. At least two nodes are
required. This does not prove modified-duration matching of a future strategy.
Per-date OFZ inventory separates canonical identity, current structural eligibility
and usable yield/duration observations. Dirty-price inputs, nominal, lot and board
are separate valuation/execution requirements, not added OFZ curve gates.

## 11. Cashflows and daily observed outcomes

Inventory source/type/currency, missing/invalid amounts and event ranges.
Coverage checks every date T through T+90 through merged quote-availability
intervals, not economic valuation. MOEX coupon/amortization/redemption require
finite nonnegative RUB amounts. Redemption closes later quote requirements;
maturity alone cannot substitute a redemption. Offers are diagnostics; unsupported
types, duplicate automatic events and automatic events after redemption block
observed coverage. Event order is coupon → amortization → redemption.
No missing coupon amount is derived from current coupon rates.

## 12. Completeness is distinct from observed coverage

FULL/PARTIAL/NONE describes OBSERVED_PERSISTED_EVIDENCE coverage only. Even FULL
does not prove contractual event completeness. Missing event rows never prove
zero payments. CASHFLOW_HISTORY_COMPLETENESS_UNPROVEN and exhaustive outcome
limitations remain explicit and prohibit strong PIT/total-return authority.
Future quote/event evidence cannot increase decision market/liquidity/credit
readiness counts, though it may improve outcome coverage.

## 13. Date classifications and monthly grid

The all-date grid is sorted distinct persisted MOEX trade dates. Monthly grid
selects the first non-UNUSABLE date per calendar month based only on coverage.
UNUSABLE: fewer than three jointly covered Bonds with input/terms/liquidity,
current verified issuer, source-native peer cohort and FULL observed outcomes,
or fewer than two OFZ nodes. DIAGNOSTIC_ONLY: these prerequisites hold but one
required historical proof is absent. PIT_SAFE: all prerequisites and universe,
market, terms, identity, publication, OFZ and exhaustive outcome proofs hold.
The production reader cannot currently prove historical universe completeness;
the all-proof branch is tested in the pure classifier, not forced into the reader.
Independent family counts never replace the actual intersection.

## 14. Legacy label isolation

BondReturnLabel is LEGACY_OUTCOME_EVIDENCE. Counts, horizon/method/date breakdowns
and nonnull coverage are inventoried, but return numbers are neither loaded as
economic authority nor emitted. Legacy pool/features/execution limitations and
absence of the modern OFZ benchmark prevent promotion. Labels cannot improve
date readiness. No strategy return, profit, alpha, Sharpe or win-rate output exists.

## 15. Read-only REPORT runner

`python scripts/modern_historical_replay_readiness_audit.py --mode REPORT`
prints canonical JSON to stdout. Only explicit `--output` writes a report file;
no DB persistence exists. Own PostgreSQL REPEATABLE READ/read-only transaction or
SQLite query-only explicit transaction; rollback on exit, no commit. Exit 0 for
completed diagnostic/insufficient audit; nonzero for technical failure. Connection
errors and explicit report-file failures are sanitized. DB_MUTATION=false and
NETWORK_ACCESS=false refer to no
mutation and no external source requests; read-only database connectivity is
required. No production execution is authorized by this work package.

## 16. Deterministic hashes and performance

SORTED_CANONICAL_JSON_SHA256_V1 covers configuration, inventories, matrix,
per-date facts, blockers and remediation; excludes its own hash, wall clock,
connection secrets and output path. Decimal percentage context is precision 28,
ROUND_HALF_EVEN. Source timestamps remain evidence. SQL groups date/Bond windows
instead of reloading each Bond/date; quote intervals avoid a 91-day valuation
loop. Only bounded semantic projections are read; no binary artifact bytes.

## 17. Qualification and remediation

A bad diagnostic replay may falsify a model; a good one cannot prove alpha or
Live readiness. Task306B/306C must independently establish positive total return,
coupon-only, duration-matched OFZ and naive baselines after evidence remediation.
Requirements include historical listing completeness, versioned terms/issuer
mapping, artifact-bound publications, market versions and cashflow completeness.
No source data is repaired in Task306A.

## 18. Verification and capabilities

Hermetic focused tests plus Task267/268/269/270/272/273/275/282/283 and Task298/299
snapshot regressions; compile, unchanged Alembic head and exact seven-file scope.
Local acceptance: 54 focused tests passed; 928 specified upstream regression
tests passed. Four Python module compile checks and the single Alembic head check
passed. Tests used only isolated SQLite fixtures and synthetic persisted evidence.
Full suite is deferred to exact-commit CI. Implemented audit capabilities are true;
replay, profitability, alpha, model effectiveness, Live and official Day 0 remain
false. No current dataset safety is inferred from capability declarations.

## 19. Delivery and safe handoff

One commit `Add modern historical replay readiness audit`, one normal push and
one exact-commit CI snapshot without polling. No migration, deploy, VDS, production
DB, live source, Shadow creation or official Day 0. Next: independent code/SHA/CI
review, then separately authorized production read-only audit. Task306B and Task307
require separate decisions after factual production coverage is reviewed.
