# Task301 — Portfolio Strategy v1

## 1. Role and scope

Investment Model (Task299) measures batch-relative attractiveness and supplies ranking.
Risk Engine (Task300) decides admissibility. Strategy constructs target allocation.
Future Shadow Execution converts targets to executable positions and transactions.
This implementation adds six files and changes no upstream contracts, models,
migrations, APIs, frontend, or legacy behavior.

## 2. Frozen contracts

Strict frozen Pydantic models forbid extra fields. Contracts are
`portfolio-strategy-policy-v1` and `portfolio-strategy-v1`; all PIT declarations
remain false. Request capital must be a positive finite Decimal. Nested full
Investment and Risk batches, selected source evaluations and risk candidates are
preserved. Invalid contracts raise ValueError without partial output.

## 3. Strategy policy

The immutable vector is target invested weight 1.00, maximum 10 positions,
initial seed weight 0.05, and allocation increment 0.01. The seed and target are
exact multiples of the increment. No public override or runtime tuning exists.
Risk policy remains the exact independently frozen Task300 policy.

## 4. Input validation and eligibility

The builder accepts InvestmentEvaluationBatchView, its exact corresponding
RiskCandidateBatchView, and PortfolioStrategyRequest. Existing Task300 validation
helpers check versions, PIT, identities, counts, ranks, numeric source evidence,
and risk candidate envelopes. Full Investment source equality is required.
Allocation requires Investment READY and Risk ELIGIBLE. Missing verified issuer
and all other candidate limitations remain explicit non-selection diagnostics.

## 5. Sole ordering authority

Candidate scans and selected output follow Task299 `ranked_bond_ids` exactly.
Score, yield, issuer, liquidity, duration, capacity and Bond ID do not reorder
candidates. Non-selected rows follow rank where present, then Bond ID for rows
without rank. No score or rank is modified.

## 6. Seed scan

Starting from empty accepted weights, each eligible unselected candidate in rank
order attempts exactly 0.05. Skip seeds when ten positions are selected or less
than 0.05 target room remains. Every actual proposal contains the complete
portfolio and passes through PortfolioRiskEvaluator. PASS accepts the transition;
BLOCKED retains the previous state and records exact risk reason codes.

## 7. Top-up scan

After each seed scan, visit selected positions in rank order and attempt +0.01.
Skip if target room is insufficient or the proposed weight exceeds the public
Task300 position policy cap. This cap is read from Task300, never hard-coded.
All other constraints are decided by the Risk Engine oracle.

## 8. Rounds and convergence

A round is one seed scan plus one top-up scan. Rejected seeds and top-ups are
eligible for retry in later rounds because duration composition can change.
Stop at exactly 1.00 invested or after a full round with zero accepted changes.
Every accepted change increases total weight by at least 0.01, so at most 100
accepted changes and one terminal no-progress round can occur. There is no
economic timeout or arbitrary attempt limit.

## 9. Maximum-position behavior

At ten selected bonds, no further seed is permitted. Existing positions may
continue growing. There is no replacement search, backtracking, or combinatorial
optimization. If selected positions cannot absorb more weight, cash remains.

## 10. Final verification and cash

The final accepted portfolio receives an additional PortfolioRiskEvaluator call,
including the empty case. Non-PASS is an implementation/contract error.
Final weights must agree with accepted trace replay, be at least 0.05, lie on
the 0.01 grid, and respect ten positions and total <=1.00.
No hidden normalization, truncation, rounding, replacement or post-risk weight
change occurs. Outcomes are FULLY_INVESTED, PARTIALLY_INVESTED, or EMPTY.

## 11. Trace and exclusions

Every actual oracle allocation attempt records sequence 1..N, round, phase,
Bond ID/rank, prior and attempted position/total weights, acceptance, risk status,
and exact reasons. Final verification is separate and is not an allocation attempt.
Every upstream evaluation appears as selected or non-selected exactly once.
Reason precedence: investment unavailable, risk candidate blocked, target reached,
maximum positions reached, rejected minimum seed, then not selected after
convergence. Latest rejected seed reasons remain visible even when a final
structural selection limit is the primary non-selection reason.

## 12. Evidence, summary and arithmetic

Selected rows preserve exact score/rank, identities, seed weight, top-up count,
capacity, duration and sensitivity. Position amount and DV01 come from final
Task300 position results. Summary cash, amounts, duration, concentration and
DV01 metrics are copied from final Risk Engine metrics, never independently
recomputed. Allocation arithmetic uses a fresh Context(prec=28,
rounding=ROUND_HALF_EVEN); no float, quantize or caller-context dependence.

## 13. Read-only orchestration

PortfolioStrategySnapshotService(db).build(bond_ids, as_of_date, capital_rub, *,
market_source="moex", max_market_age_days=7, max_curve_age_days=7,
liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5) validates
requests before loading. IDs are explicit, unique, positive exact ints and sorted;
dates exclude datetime, ages exclude bool, window underflow is rejected.
Under shared no_autoflush it calls InvestmentModelSnapshotService once,
RiskCandidateReducer once, and PortfolioStrategyBuilder once. Returned request
context must match. There are no own SQL queries, mutations, transaction calls,
or lower-level loads. Unexpected dependency exceptions propagate.

## 14. Provenance and capabilities

Provenance records Investment batch/model policy, Risk policy, Strategy policy,
`RANK_ORDERED_PROGRESSIVE_ALLOCATION_V1`, source context/capital and candidate,
ranked, eligible and selected IDs. No clocks, timestamps or UUIDs.
Portfolio strategy/construction, deterministic selection, risk-constrained
allocation and target weights capabilities are true. Executable quantities,
lot sizing, rebalance delta, transaction costs, slippage, market impact, broker,
Shadow, realized performance, benchmark comparison and PIT remain false.

## 15. Verification

Focused synthetic builder tests exercise policy, rank authority, seed/top-up
acceptance and rejection, deferred seed, later successful top-up retry, ten-position
limit, full/partial/empty allocation, exact .73/.27 cash, capital dependence,
issuer and DV01 rejection, final oracle call, trace replay, malformed inputs,
serialization, immutability, Decimal isolation and AST safety.
Snapshot tests prove one call per stage, forwarding, SELECT-only integration,
caller-owned pending state, validation-before-load and exception propagation.
Run the two Task301 modules, then the five requested Task299/300 regression
modules, compile the three new application modules, and check working/staged diff.
Full suite is deferred to exact-commit CI.

## 16. Limits and handoff

Portfolio Strategy v1 is not a mathematically optimal portfolio. It is a
transparent deterministic rank-first allocation policy. Target weights are not
executable orders or direct investment recommendations. No broker access,
persistence, production/VDS, deployment, live sources, legacy portfolios, ML,
backtest, lot sizing or Shadow transactions are authorized here. One commit and
one ordinary push deliver the foundation; CI is reported as a snapshot without
waiting. Task302 — Shadow Execution v1 requires independent review and a separate
request. No downstream implementation starts here.
