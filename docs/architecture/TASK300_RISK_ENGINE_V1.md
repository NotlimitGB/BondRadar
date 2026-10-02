# Task300 — Risk Engine v1

## 1. Purpose

Risk Engine v1 does not determine whether a bond is attractive. It determines whether a supplied candidate or portfolio proposal satisfies the frozen v1 risk policy. PASS is not a recommendation to trade. No aggregate risk score, artificial risk ranking or credit-quality label is introduced.

## 2. Baseline and scope

Baseline is `c8a06649f2d109b525f7b008cf81d63751e56f8c`. Eight new files provide the schema, candidate reducer, portfolio evaluator, snapshot service, three focused tests and this document. Task298/299, existing models, migrations, API, frontend and legacy services remain unchanged. Temporary directories are preserved. No production/VDS, live sources, deployment, broker access or persistence is authorized.

## 3. Separation of responsibilities

Task298 supplies CORE evidence, Task299 evaluates relative attractiveness, Task300 checks admissibility, and future Task301 chooses allocations. Task300 never selects replacements, changes Investment Score or rank, clamps/normalizes weights, invests cash automatically or generates quantities/lots. It does not import Company-based legacy concentration, old assessment/ML scores or paper-trading constraints.

## 4. Frozen policy

`risk-engine-policy-v1` is immutable and rejects drift. Public APIs have no policy override.

| Policy field | Exact Decimal |
|---|---:|
| max_position_weight | 0.20 |
| max_issuer_weight | 0.25 |
| min_liquidity_score | 25 |
| max_position_to_median_daily_turnover | 0.05 |
| max_candidate_modified_duration_years | 5.0 |
| max_invested_weighted_modified_duration_years | 3.5 |
| max_portfolio_relative_dv01_per_1bp | 0.00040 |
| max_portfolio_dv01_per_100k_rub | 40 |

## 5. Contracts and APIs

All Task300 models use strict types, `frozen=True`, `extra="forbid"`; top-level source/result/policy contracts declare `pit_ready=False`. Versions are `risk-candidate-v1`, `risk-candidate-batch-v1`, `proposed-risk-portfolio-v1` and `portfolio-risk-evaluation-v1`.

`RiskCandidateReducer.build(investment_batch)` returns one envelope per Task299 evaluation. `PortfolioRiskEvaluator.build(risk_batch, proposed_portfolio)` evaluates a supplied proposal. `RiskEngineSnapshotService(db).build(bond_ids, as_of_date, proposed_portfolio, *, market_source="moex", max_market_age_days=7, max_curve_age_days=7, liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5)` delegates one upstream load and one call to each stage.

## 6. Source validation

Only the modern Task299 → attached Task298 → attached M3 chain is used. Type/version/PIT drift, malformed counts/partitions/ranks, wrong identities, risk-input drift, nonfinite or out-of-range evidence produce `ValueError`, never an ordinary policy BLOCKED result. Existing pure Task299 validators check supplied candidate/peer envelopes without loading data or recalculating Investment Score/percentile statistics. Task300 checks READY score/rank presence and bounded score values; it does not threshold score or rank.

Risk-batch validation uses the same pure envelope derivation as the reducer to detect altered status, capacity, issuer and metrics. It does not invoke the reducer class a second time or duplicate gate implementations.

## 7. Candidate gates

Candidate eligibility requires READY Investment Evaluation, verified positive exact-int Task269 LegalIssuer identity, liquidity score at least 25, positive finite median turnover, modified duration between zero and 5 inclusive, finite nonnegative supplied relative sensitivity and per-bond DV01. Verified issuer provenance must retain its verified Security Reference mapping and exact source issuer identity. No Company, name or INN fallback exists.

Reasons include `INVESTMENT_MODEL_NOT_READY`, `ISSUER_IDENTITY_UNAVAILABLE`, `LIQUIDITY_SCORE_BELOW_MINIMUM`, `LIQUIDITY_CAPACITY_UNAVAILABLE`, `CANDIDATE_DURATION_LIMIT_EXCEEDED`. All apply independently; primary status precedence is INVESTMENT_MODEL_NOT_READY, BLOCKED, ELIGIBLE. Correctly missing issuer or zero turnover is a valid policy limitation. Contradictory verified identity or negative/nonfinite numeric evidence is invalid input.

## 8. Liquidity capacity proxy

```text
capacity_amount_rub = median_daily_turnover_value × 0.05
capacity_weight = capacity_amount_rub / capital_rub
capacity_bound = min(0.20, capacity_weight)
```

Candidate-only envelopes report the amount and policy cap; unknown capital means null capacity weight and final admissible weight. With capital, eligible positions expose `capacity_bound` as their maximum admissible weight. Noneligible positions expose final maximum zero, while retaining the calculated bound and capacity diagnostics.

This 5% turnover constraint is a conservative proxy. It is not a guaranteed executable amount, empirically calibrated market-impact model, bid/ask allowance or transaction-cost estimate.

## 9. Proposal and position gates

Capital is a positive finite Decimal. Position IDs are unique positive exact integers; each weight is a finite Decimal in `(0,1]`, and total weight must not exceed one. Unallocated cash is valid. Unknown source candidate IDs fail validation.

The output contains a new canonical proposal ordered by Bond ID, retaining every weight/capital value. The caller's proposal is unchanged. Positions are independently checked for candidate eligibility, 20% weight cap and `capital × weight <= capacity_amount`. All position violations remain visible. A breached weight is never reduced or renormalized.

## 10. Exact issuer concentration

Grouping key is solely verified `legal_issuer_id`. Known groups report sorted Bond IDs, total weight, 25% limit, signed headroom and PASS/BLOCKED. Negative headroom is preserved, not clamped. Ultimate-parent/economic-group concentration is not yet modeled; sector, Company, INN, brand and SPV relationships are not substitutes.

Missing issuer positions are blocked and recorded as `ungrouped_bond_ids`; known groups remain visible. `issuer_concentration_complete=false` and `ISSUER_CONCENTRATION_INCOMPLETE` prevent interpreting the known-group maximum/count as a complete issuer view. Those metrics describe known groups only.

## 11. Duration aggregation

```text
invested_weight = Σ weight
capital_weighted_modified_duration = Σ(weight × modified_duration)
invested_weighted_modified_duration = capital_weighted_modified_duration / invested_weight
```

The 3.5-year gate applies to the invested bond sleeve. Cash does not dilute it. Capital-weighted duration is also reported separately. All proposed positions contribute, including policy-blocked positions with valid numeric evidence. Candidate duration above 5 is separately blocked using the same frozen individual gate.

## 12. DV01 aggregation

```text
position_amount_rub = capital_rub × weight
position_dv01_rub_per_1bp = position_amount_rub × supplied_relative_price_sensitivity_per_1bp
portfolio_dv01_rub_per_1bp = Σ position_dv01_rub_per_1bp
portfolio_relative_dv01_per_1bp = portfolio_dv01_rub_per_1bp / capital_rub
portfolio_dv01_per_100k_rub = portfolio_relative_dv01_per_1bp × 100000
```

The relative limit is 0.00040 and its verified policy equivalent is 40 RUB/100k/bp. Per-100k reporting derives from the same relative result. All proposed positions contribute. Per-bond DV01 and relative sensitivity are copied, never reconstructed from price, nominal, duration or quantity. No convexity or shock extrapolation is added. Passing the DV01 gate alone does not imply overall PASS.

## 13. Decision, metrics and empty state

Overall PASS requires all position, issuer, invested-duration and DV01 gates to pass. Reasons are sorted unique; simultaneous breaches are not hidden by the first failure. Metrics include invested/cash weights and amounts, known issuer and position counts/maxima, completeness diagnostics, both duration representations and three DV01 representations.

Empty proposal is risk-PASS with `EMPTY_PORTFOLIO`, all invested amounts/DV01/counts zero, cash weight one and undefined invested-sleeve duration null. It is not Strategy-ready. Empty candidate source batches are valid only with proposals referencing no candidates.

## 14. Arithmetic, determinism and provenance

Calculations use fresh `Context(prec=28, rounding=ROUND_HALF_EVEN)` without float conversion or additional quantize. Candidates/positions sum in Bond ID order, issuer records sort by LegalIssuer ID, reasons/flags sort lexicographically. Different proposal ordering produces identical serialized semantic output.

Full evaluations, risk batch and canonical proposal are attached. Provenance records source batch/model-policy/risk-policy versions, frozen vector, candidate/READY/proposed IDs, represented LegalIssuer IDs, as-of/source and versions `risk-position-capacity-v1`, `risk-issuer-concentration-v1`, `risk-duration-aggregation-v1`, `risk-portfolio-dv01-v1`. Market snapshot identities remain traceable through attached evaluations. No timestamp or generated run ID is introduced.

## 15. Read-only orchestration and verification

The snapshot service validates proposal/request before acquisition, then runs exactly one Task299 snapshot call, one candidate reducer call and one portfolio evaluator call under common `no_autoflush`. It owns no transaction and performs no SQL or Session mutation itself. Dependency exceptions propagate without partial output. Caller pending new/dirty/deleted state remains unchanged.

Run the three Task300 focused modules; then Task299 math/reducer/snapshot and Task298 reducer/snapshot regressions; compile four Task300 application modules and review working/staged diffs plus exact eight-file scope. Tests use synthetic evidence and disposable SQLite, covering all policy boundaries, simultaneous blockers, exact grouping, missing identity, capacities, cash/sleeve distinction, DV01, canonical order, tampering, Decimal isolation, frozen serialization and direct call counts. Full local suite is deferred to exact-commit CI. No live sources are used.

## 16. Capabilities, unsupported dimensions and delivery

```ini
CANDIDATE_RISK_ENVELOPE_READY=true
POSITION_WEIGHT_GATE_READY=true
ISSUER_CONCENTRATION_GATE_READY=true
LIQUIDITY_SCORE_GATE_READY=true
LIQUIDITY_CAPACITY_PROXY_READY=true
CANDIDATE_DURATION_GATE_READY=true
PORTFOLIO_DURATION_GATE_READY=true
PORTFOLIO_DV01_READY=true
RISK_ENGINE_READY=true
PORTFOLIO_CONSTRUCTION_READY=false
PORTFOLIO_OPTIMIZATION_READY=false
RECOMMENDATION_READY=false
SHADOW_EXECUTION_READY=false
LOT_SIZING_READY=false
TRANSACTION_COST_MODEL_READY=false
SLIPPAGE_MODEL_READY=false
MARKET_IMPACT_MODEL_READY=false
KEY_RATE_DURATION_READY=false
CONVEXITY_READY=false
VAR_READY=false
EXPECTED_SHORTFALL_READY=false
DEFAULT_PROBABILITY_READY=false
ECONOMIC_GROUP_CONCENTRATION_READY=false
SECTOR_CONCENTRATION_READY=false
PIT_READY=false
```

No rating hierarchy, PD/LGD/default stress, historical volatility/covariance, expected loss, spread stress, backtest or execution model exists in v1. Delivery is one `Add Risk Engine v1 foundation` commit and one normal push after exact baseline/scope/concurrency checks. Report one exact-commit CI snapshot without waiting. Stop after delivery. Task301 Portfolio Strategy v1 requires independent review and a separate request; no production operation is authorized.
