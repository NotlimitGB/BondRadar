# Task237 — 50k Paper Pilot Agent & Data Readiness Audit

## 1. Executive verdict

**Finding — CODE_PROVEN:** the repository contains a sizeable data, ML, risk,
portfolio-allocation, and virtual-paper infrastructure, but it does not contain
an economically executable bond purchase ledger. Positions are monetary target
allocations; they have no quantity, lot size, clean/dirty execution price, or
accrued coupon amount. Candidate admission also has no hard placeholder-issuer
or market-age gate.

**Impact — P0:** a 50,000 RUB / 90-day result could describe model-allocation
returns without demonstrating that those positions could have been purchased
or exited as bonds at the recorded time.

Task237 implementation status and paper-pilot readiness are separate:

- `TASK237=PASS` means the audit is complete, deterministic, and read-only.
- `PAPER_PILOT_READINESS=NO_GO` means the audited investment experiment is not
  yet defensible.

## 2. Starting HEAD

- Branch: `main`
- Starting commit: `73fe992e2fcea1dba205833b7df21a6bd35e22bc`
- Commit subject: `Add Unified Bond Product Read Model`
- Starting tracked tree: clean
- Evidence quality: `CODE_PROVEN`

## 3. Audit scope

The audit inspected only the investment/data chain: issuer identity, bond
terms, market snapshots, cashflows, financial reports and controlled values,
credit/risk, ML candidate selection, portfolio construction, paper accounting,
backtests, robustness, live scheduling, and the pilot quality gate.

It did not run Task233–Task236, refresh MOEX, calculate scores or risk, execute a
paper cycle, access VDS/production, or change production code. The accompanying
CLI performs aggregate database reads only. Evidence quality: `TEST_PROVEN` by
`test_agent_data_readiness_audit.py`.

## 4. Decision-chain architecture

| Layer | Current implementation | Primary files | Inputs | Output | Downstream consumer | Missing-data behavior | Freshness-aware | Point-in-time concern | Pilot readiness | Evidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Universe | ML prediction rows define candidates | `portfolio_construction_service.py::_load_candidates` | completed model run, date | raw candidates | portfolio construction | bonds without prediction disappear | date-scoped predictions | no historical universe membership | fail | CODE_PROVEN |
| Issuer identity | Bond FK joins Company | `Bond.company_id`; `_load_candidates` | Company row/name | candidate company | risk/allocation | missing join disappears; placeholder name accepted | no | economic obligor may be unresolved | fail | CODE_PROVEN |
| Bond terms | Static Bond columns | `models/bond.py` | nominal, coupon, maturity, offer, flags | metadata/risk warnings | risk/features | nullable terms often warn only | no | static values can be used in historical features | partial | CODE_PROVEN |
| Market data | dated snapshots plus static Bond fallback | `feature_snapshot_service.py`; `bond_risk_assessment_service.py` | snapshot/Bond fields | feature/risk values | ML/risk | missing values can remain null | selects `<= as_of`, no max age | static fallback is not time-versioned | fail | CODE_PROVEN |
| Cashflows | typed coupon/amortization/redemption events | `bond_cashflow_service.py`; `total_return_label_service.py` | dated events | aggregate future return components | labels/backtest | optional unless configured | event dated | completeness not universal | partial | CODE_PROVEN |
| Financial statements | legacy normalized FinancialReport | `models/financial_report.py` | issuer report fields | ratios/health | credit/risk | missing fields reduce quality/status | publication preferred | legacy fallbacks can omit publication-time proof | fail | CODE_PROVEN |
| Financial normalization | controlled exact-value table | `controlled_financial_statement_value.py` | reviewed values | controlled rows | no investment consumer | string entity ID has no Company FK | source periods only | not joined to decision timeline | fail | CODE_PROVEN |
| Credit health | heuristic financial ratio score | `CompanyCreditHealthService` | report, optional company score | persisted health snapshot | bond risk/features | high missing count becomes insufficient | as-of snapshot | legacy-report fallback risk | partial | CODE_PROVEN |
| Bond risk | credit/liquidity/duration/structure/high-yield gates | `BondRiskAssessmentService` | health, score, market, Bond | persisted assessment | portfolio filters | some missing fields warn; insufficient can be configured out | latest `<= as_of` | no market-age gate | partial | CODE_PROVEN |
| Bond score | weighted static Bond fields | `BondScoreService` | Bond YTM/duration/liquidity, company score | persisted score | risk/features | missing factors reduce factor count | no | uses current Bond row | fail | CODE_PROVEN |
| Company score | financial heuristic snapshot | `company_scoring.py` | FinancialReport | persisted score | credit/bond score | insufficient signal possible | creation timestamp | report publication depends on upstream | partial | CODE_PROVEN |
| Relative value | required premium stored but not enforced | risk + portfolio services | YTM, premium, spread | diagnostics | no selection comparison | missing YTM accepted | no | alternatives not compared on value | fail | CODE_PROVEN |
| BUY | probability filter/rank then weight increase | portfolio + paper services | prediction, optional filters | allocation increase | virtual portfolio | price/YTM not required | no market-age gate | selection can use stale inputs | fail | CODE_PROVEN |
| HOLD | no explicit decision | `PaperTradingService.rebalance` | unchanged target weight | no transaction | next rebalance | no re-underwriting reason | schedule only | absence of action is not policy | fail | CODE_PROVEN |
| SELL | allocation decrease/removal | `PaperTradingService.rebalance` | changed target set | allocation decrease/removal | virtual portfolio | no bond-specific exit triggers | schedule only | no event-time sell rationale | fail | CODE_PROVEN |
| Position sizing | fractional target weights | `PortfolioConstructionService.construct` | capital and caps | RUB allocation amount | paper positions | unallocated cash allowed | no | no units/lots | fail | CODE_PROVEN |
| Portfolio constraints | position, issuer, high-risk caps | portfolio schemas/service | configured decimal caps | constrained weights | paper rebalance | risk filters configurable | as-of candidate date | no sector/duration/cash-floor policy | partial | CODE_PROVEN |
| Execution | target monetary allocations | `PaperTradingService.rebalance` | current portfolio value | position amount | accounting | no executable price required | no | no bid/ask or trade timestamp | fail | CODE_PROVEN |
| Accounting | cash + allocation amounts + aggregate returns | paper models/service/report | fees and return labels | NAV/drawdown | monitoring | missing labels can block or be allowed | dated periods | not a bond-unit ledger | fail | CODE_PROVEN |
| Backtest | weighted realized-label simulation | `StrategyBacktestService` | predictions and labels | period returns | experiment/robustness | missing labels excluded | risk `<= as_of` | feature/static fallback and universe bias | partial | CODE_PROVEN |
| Walk-forward | train/predict folds | `MLWalkForwardService` | feature/label ranges | ML fold metrics | validation | failed folds reported | split dates | validates prediction, not full execution | partial | CODE_PROVEN |
| Robustness | parameter/subperiod comparison | `StrategyRobustnessService` | strategy experiments | flags/metrics | promotion/readiness | warnings configurable | subperiod based | inherits simulator limitations | partial | CODE_PROVEN |
| Benchmarking | equal-weight, top-YTM, top-liquidity baselines | `StrategyBacktestService._baseline_results` | evaluable candidates | baseline returns | experiment | no cash/OFZ benchmark | period scoped | same biased candidate pool | partial | CODE_PROVEN |
| Live scheduling | persisted due schedules and cycle runs | `paper_trading_live_schedule_service.py`; `paper_trading_live_cycle_service.py` | schedule | paper cycle | monitoring | readiness can block | schedule timestamps | scheduling does not cure stale source data | partial | CODE_PROVEN |
| Pilot quality gate | aggregates existing readiness/dry-run gates | `PreDeployPaperPilotQualityGateService` | readiness, robustness, bootstrap | readiness flags | operator | manual warnings do not all block 50k flag | recent-data aggregate | does not prove execution economics | fail | CODE_PROVEN |

## 5. Universe findings

**Finding — CODE_PROVEN:** candidates are predictions for one completed
`MLModelRun` and one `as_of_date`; they are not all bonds, but there is no
separate investable-universe eligibility record. `_filter_candidate` checks
probability, optional liquidity, risk status, and configured risk levels.

- Unsupported instrument gate: `MISSING`.
- Perpetual/subordinated: warnings or blocks only through the risk structure
  gate, conditional on weak credit.
- Amortizing/offer bonds: warnings, not dedicated economic handling.
- Floating coupons: stored but not separately gated in candidate selection.
- Missing terms: not a universal block.
- Liquidity: optional portfolio threshold; low values can block at risk level.

## 6. Issuer / credit-perimeter findings

**Finding — CODE_PROVEN:** candidate construction joins `Company` by numeric FK
but never checks the canonical placeholder prefix. `Company.inn` exists, but is
not used in portfolio admission. No guarantor, surety, SPV obligor, or
corporate-group credit-perimeter join participates in the BUY path.

An existing Company named `Unknown issuer for ...` is therefore structurally a
valid candidate issuer. The Task236 sample proves at least one such production
bond exists; it does not prove the universe-wide count.

## 7. Market-data findings

**Finding — CODE_PROVEN:** risk and feature construction prefer a latest dated
`BondMarketSnapshot`, while Task236 product reads use `trade_date DESC`, MOEX
priority, then `id DESC`. Risk selection has an `as_of_date` ceiling, but no
maximum age. Snapshot fields fall back independently to static Bond fields.

- Months-old data can participate in selection.
- Price is not read by portfolio construction or paper rebalance.
- Missing YTM is not a candidate exclusion.
- Missing volume/liquidity warns at risk level unless another configured filter
  blocks it.
- Clean price, dirty price, and NKD exist in snapshots but are not paper BUY
  inputs.

The Task237 CLI measures the latest-row field and age coverage without defining
a trading freshness threshold. Evidence: `DB_MEASURABLE`.

## 8. Bond-economics findings

**Finding — CODE_PROVEN:** `BondMarketSnapshot` models clean price, dirty price,
NKD, YTM, duration and spread; `Bond` models nominal, coupon, maturity, offer and
structure flags; `BondCashflowEvent` models coupon, amortization, redemption and
offer redemption. These facts do not form an executable purchase ledger.

The simulated buyer pays a target RUB allocation plus a portfolio-level
turnover fee, not quantity × dirty price plus NKD. Coupons, amortizations and
redemptions can enter `total_return` labels, but paper accounting applies only
their aggregate `future_return`; it does not credit distinct cash ledger events.

## 9. Financial-statement findings

### Field-level matrix

| Field | MODEL_EXISTS | DATA_SOURCE_EXISTS | NORMALIZATION_EXISTS | USED_IN_DECISION | POINT_IN_TIME_SAFE | PRODUCTION_COVERAGE_MEASURABLE |
| --- | --- | --- | --- | --- | --- | --- |
| revenue | yes | FinancialReport/source document | legacy + exact controlled keys | credit health | partial | yes |
| EBITDA/operating profit | EBITDA legacy; operating profit controlled | yes | yes | EBITDA affects credit | partial | yes |
| net profit | yes | yes | yes | credit health | partial | yes |
| cash | yes | yes | yes | credit health | partial | yes |
| short-term debt | yes | yes | exact key measurable | credit health | partial | yes |
| long-term debt | no legacy field | controlled exact key only | exact key measurable | no | no | yes, controlled entity only |
| total debt | yes | yes | exact key measurable | credit health | partial | yes |
| net debt | yes | yes | not safely inferred by audit | credit health | partial | legacy only |
| interest expense | yes | yes | exact key measurable | credit health | partial | yes |
| operating cash flow | yes | yes | exact keys measurable | credit health | partial | yes |
| capex | no | not proven | not reliably measurable | no | no | no |
| free cash flow | no | not proven | not reliably measurable | no | no | no |
| equity | yes | yes | exact keys measurable | credit health | partial | yes |
| assets | no legacy field | controlled total-assets key | exact key measurable | no | no | controlled entity only |
| credit rating | Company field | manual/current row | not financial-value normalization | limited heuristic paths | no history | yes |
| debt maturity profile | no structured profile | not proven | no | no | no | no |

**Finding — CODE_PROVEN:** `CompanyCreditHealthService` uses legacy
`FinancialReport` values and ratios. `ControlledFinancialStatementValue` is not
consumed by credit, risk, portfolio, or paper services and has a string
`company_id` without a `Company` FK.

Publication time is tracked, but `FeatureSnapshotService._latest_financial_report`
has a final legacy period fallback without a `created_at <= cutoff` predicate.
That path is not point-in-time defensible.

## 10. Credit-policy findings

`CompanyCreditHealthService` starts from a heuristic score and adjusts for
company score, leverage, interest coverage, cash coverage, operating cash flow,
margin, equity and missingness. Critical red flags include negative equity,
interest coverage below one, debt/EBITDA above five, and company score below 30.

`BondRiskAssessmentService` produces `eligible_for_analysis`, `watchlist`,
`blocked_by_risk`, or `insufficient_data`. Distressed credit, poor liquidity,
extreme duration, and weak-credit special structures can block. Missing YTM,
duration or liquidity may only warn. Stale market age and placeholder identity
do not block. Classification: `partially grounded`.

## 11. Relative-value findings

**Finding — CODE_PROVEN:** risk calculates `required_risk_premium`, and feature
snapshots can contain `spread_to_ofz`, but portfolio filtering and ranking do not
compare YTM with either value. Ranking is primarily prediction probability,
then liquidity/risk score. Alternatives are not duration-adjusted on relative
value.

`RELATIVE_VALUE_POLICY_EXISTS = false`

## 12. BUY findings

```text
MLPrediction for model/date
→ PortfolioConstructionService._load_candidates
→ optional probability/liquidity/risk filters
→ probability-first ranking
→ fractional target weight
→ PaperTradingService allocation_increase
```

This is a partial candidate/allocation policy, not a bond execution policy.
`NO_ACTION` occurs only through no selected positions or no weight change; costs
are not compared with expected benefit bond by bond.

## 13. HOLD findings

```text
unchanged target weight → no transaction emitted
```

There is no explicit HOLD decision, reason, or bond-level periodic
re-underwriting contract. `HOLD_POLICY_EXISTS = false`.

## 14. SELL findings

```text
candidate omitted or weight reduced on rebalance
→ allocation_decrease or allocation_removed
```

There is no bond-specific SELL policy for credit deterioration, rating change,
liquidity, spread compression, superior opportunity, maturity, offer, stale
data, or missing data. `SELL_POLICY_EXISTS = false`.

## 15. Position-sizing findings

```text
capital × constrained fractional allocation_weight = allocation_amount
```

Maximum position, issuer and high-risk weights exist. Sector and duration
concentration, minimum executable position, lot size, nominal, dirty price and
NKD cash requirements do not. Fractional monetary allocations can be impossible
to execute with 50,000 RUB.

## 16. Execution findings

| Element | Result | Evidence |
| --- | --- | --- |
| commission | PARTIAL | flat `turnover × transaction_cost_rate` |
| slippage | MISSING | no execution-price adjustment |
| bid/ask | MISSING | no quote-side choice |
| lot size | MISSING | no quantity/lot field |
| dirty price | MISSING | not read by rebalance |
| NKD | MISSING | not debited from cash |
| coupon | PARTIAL | may be embedded in total-return label |
| amortization | PARTIAL | may be embedded in total-return label |
| redemption/offer | PARTIAL | cashflow labels support types; no execution ledger |

## 17. Accounting findings

Cash, positions, fees, portfolio value, cumulative return and drawdown are
persisted and reportable. Realized performance is produced by multiplying
allocation amounts by `BondReturnLabel.future_return`; there is no unit inventory,
accrued coupon balance, separate coupon income, realized sale price, or bond-event
cash ledger. Internal virtual-allocation arithmetic exists, but realistic bond
accounting is not proven.

## 18. Backtest-integrity findings

| Check | Result | Evidence |
| --- | --- | --- |
| dated predictions/labels | PASS | model/date joins |
| risk selected at or before date | PASS | `_latest_risk_by_bond` |
| market point-in-time | PARTIAL | dated snapshot plus static Bond fallback |
| financial publication time | FAIL | legacy period fallback lacks creation cutoff |
| transaction costs | PARTIAL | flat turnover rate |
| coupons/amortization/redemption | PARTIAL | total-return labels only |
| NKD/dirty price/lots | FAIL | absent from simulator |
| survivorship bias | FAIL | no historical universe-membership contract |
| special bond events | PARTIAL | cashflow labels, not full execution semantics |

`LOOKAHEAD_RISK = material` because current/static values and legacy financial
fallbacks can enter historical feature construction.

## 19. Walk-forward / robustness findings

`MLWalkForwardService` performs dated training/prediction folds and
`MLValidationSuiteService` validates prediction quality. Strategy experiment,
robustness and promotion services compare simulated variants/subperiods. These
validate ML predictions and allocation-return simulations, not executable bond
portfolio economics. Their success cannot clear Task237 P0 blockers.

## 20. Benchmark findings

Backtest baselines include equal-weight evaluable candidates, top YTM and top
liquidity. They do not establish explicit cash, OFZ, or independent corporate
buy-and-hold benchmarks, and inherit the same candidate pool and simulator.

`BENCHMARK_FRAMEWORK = partial`

## 21. Current paper-pilot infrastructure findings

The repository has bootstrap, readiness, schedule, cycle, monitoring and report
services. `PreDeployPaperPilotQualityGateService` checks corporate/live data,
model availability, robustness, paper readiness, external regime and dry runs.
Its `ready_for_50k` calculation does not prove lots, dirty price, NKD, explicit
SELL, or point-in-time financial safety. Operational completeness is therefore
not investment-experiment readiness.

## 22. Data coverage capabilities

`scripts/agent_data_readiness_audit.py` measures current production coverage
read-only after separate VDS execution. It reports issuer placeholders, exact
Task236 latest-market semantics, field completeness, diagnostic ages, terms,
cashflows, latest risk, scores, financial reports, source documents, controlled
metrics, and multi-layer intersections.

Controlled financial entity coverage is measurable, but its bond/company
intersection is `NOT_MEASURABLE`; the tool does not guess across the missing FK.

## 23. Known production evidence

Evidence quality: `PRIOR_PRODUCTION_EVIDENCE`, verified on 2026-08-10 after
Task236, not refreshed by Task237:

- bond ID 34 was bound to `Unknown issuer for RU000A0JUAN6`;
- latest market date was 2026-05-19 from MOEX;
- product price and YTM were null;
- latest risk was present.

Historical inventory counts from earlier audits remain approximate and are not
presented as current. The Task237 CLI is the mechanism for a later read-only VDS
measurement.

## 24. Blocker register

| BLOCKER_ID | SEVERITY | LAYER | FINDING | EVIDENCE | WHY_IT_MATTERS_FOR_50K_PILOT | RECOMMENDED_FUTURE_TASK |
| --- | --- | --- | --- | --- | --- | --- |
| T237-P0-01 | P0 | Universe/identity/market | Placeholder issuer, stale market and null execution fields are not universal hard gates | portfolio filters; Task236 sample | capital can be allocated without resolved obligor or usable quote | investable universe, issuer resolution, market freshness |
| T237-P0-02 | P0 | Execution/accounting | No units, lots, dirty price or NKD purchase ledger | paper position model and rebalance | allocations may be impossible and PnL economically wrong | execution and accounting realism |
| T237-P0-03 | P0 | Point-in-time | Static Bond fallbacks and legacy financial period fallback are not fully time-safe | feature snapshot selection | historical results can use later-known information | backtest integrity and publication-time gate |
| T237-P0-04 | P0 | Decision policy | HOLD is implicit and bond-specific SELL policy is absent | paper rebalance transactions | a 90-day risk experiment lacks defined exit/re-underwriting semantics | HOLD/SELL policy |
| T237-P1-01 | P1 | Financials | Controlled reviewed values do not feed Company credit/risk and financials can be configured out | controlled model; health/risk/portfolio services | credit underwriting is not consistently source-backed | financial sufficiency and credit-policy integration |
| T237-P1-02 | P1 | Relative value | Required premium/spread do not drive candidate selection | risk and portfolio services | high-quality but unattractive bonds can be selected | relative-value and BUY policy |
| T237-P1-03 | P1 | Evaluation | Benchmark and survivorship controls are incomplete | backtest baselines/universe | performance attribution cannot distinguish strategy value robustly | benchmark and universe-history framework |
| T237-P2-01 | P2 | Data linkage | Controlled string entity ID lacks Company FK | controlled-value model | cross-layer controlled coverage cannot be measured safely | deterministic controlled-entity binding |

Counts: `P0=4`, `P1=3`, `P2=1`.

## 25. Recommended dependency order

1. Establish deterministic corporate investable universe and economic-obligor
   identity gates.
2. Establish market freshness and executable quote/lot/dirty-price/NKD inputs.
3. Bind controlled financial evidence to Company and require a defined
   financial sufficiency policy.
4. Make historical market and financial feature selection point-in-time strict.
5. Define relative-value and explicit BUY/HOLD/SELL policies.
6. Implement unit-based bond execution, event cash accounting and reconciliation.
7. Rebuild backtests with historical universe, execution economics and independent
   benchmarks.
8. Re-run readiness audit before authorizing any pilot.

## 26. PAPER_PILOT_READINESS verdict

```text
CAN_AGENT_BUY_WITH_UNKNOWN_ISSUER = true
CAN_AGENT_BUY_WITH_STALE_MARKET = true
CAN_AGENT_BUY_WITHOUT_PRICE = true
CAN_AGENT_BUY_WITHOUT_YTM = true
CAN_AGENT_BUY_WITHOUT_FINANCIAL_STATEMENTS = true

FINANCIALS_ACTUALLY_AFFECT_CREDIT_DECISION = partial

RELATIVE_VALUE_POLICY_EXISTS = false
BUY_POLICY_EXISTS = partial
HOLD_POLICY_EXISTS = false
SELL_POLICY_EXISTS = false

LOT_SIZE_REALISM = fail
DIRTY_PRICE_REALISM = fail
NKD_REALISM = fail
COUPON_ACCOUNTING = partial
AMORTIZATION_ACCOUNTING = partial
REDEMPTION_ACCOUNTING = partial
COMMISSION_REALISM = partial
SLIPPAGE_REALISM = fail

POINT_IN_TIME_MARKET = partial
POINT_IN_TIME_FINANCIALS = fail
LOOKAHEAD_RISK = material
SURVIVORSHIP_BIAS_CONTROL = fail

BENCHMARK_FRAMEWORK = partial
50K_PORTFOLIO_EXECUTABILITY = fail
```

PAPER_PILOT_READINESS=NO_GO
