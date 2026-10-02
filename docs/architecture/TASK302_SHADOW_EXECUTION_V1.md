# Task302 — Shadow Execution v1

## 1. Role and scope

Portfolio Strategy supplies desired target weights. Shadow Execution represents
those targets using integer lots and dirty-value cash. A future Shadow Ledger
owns persisted positions, cashflows and history. This package adds eight files;
upstream Tasks298–301, ingestion, models, migrations, APIs and frontend are unchanged.

## 2. Contracts

Strict frozen Pydantic contracts forbid extra fields and declare PIT false.
Versions are `shadow-execution-policy-v1`, `shadow-execution-terms-v1`,
`shadow-execution-terms-batch-v1`, and `shadow-execution-plan-v1`. Position, summary,
provenance and capability contracts are immutable. The full Strategy, terms batch,
selected source positions and post-rounding Risk Engine result remain attached.
No timestamps, random IDs, persistence IDs or order identifiers exist.

## 3. Narrow terms loader

ShadowExecutionTermsLoader(db).build(strategy) validates Strategy before one
SELECT of Security Master id, bond_id, contract_version, currency state/code,
nominal state/value, lot state/size and board state/value. Its predicate contains
only selected Bond IDs, with deterministic ORDER BY. Empty Strategy needs no SELECT.
Shared no_autoflush preserves caller-owned new/dirty/deleted objects and pending
values. No legacy Bond fields or live sources are consulted.

## 4. Expected profile identity

Expected profile ID comes from each selected position's
source_evaluation.candidate.provenance.security_master_profile_id. Current profile
ID and contract `bond-security-master-v2` must agree. Profile replacement/version
drift makes terms unavailable, not a permission to substitute a new dirty value.
The loader never changes or refreshes source evidence.

## 5. Terms gates and drift

READY requires verified exact RUB matching candidate currency, verified positive
finite Decimal nominal matching candidate nominal, verified positive exact-int
lot size and verified nonblank string trading board. Any verified board is
accepted; Task302 adds no TQCB-only gate. Strings are preserved exactly.
Unknown/conflict states have null values under existing Security Master constraints.
Missing profiles and valid state unavailability return UNAVAILABLE. ID/version/
currency/nominal mismatch adds its specific blocker and SECURITY_MASTER_PROFILE_DRIFT.
Blockers are sorted and unique. Contradictory state/value pairs, invalid types or
nonfinite values raise ValueError. Broad execution_terms_blockers is not used;
outstanding nominal and other unrelated fields are not requirements.

## 6. Shared validation

Pure validation and narrow terms classification live in shadow_execution_planner.
Loader and planner use the same functions. Supplied Strategy is checked against
its exact modern Investment/Risk sources, frozen policy, identities, ranks,
selected/non-selected partitions, accepted trace, summary, provenance and final
PASS Risk envelope. Task300 pure source validators are reused. No allocation
algorithm or upstream risk calculation is re-run. Supplied terms must equal their
canonical classification, including partitions, order, counts and blockers.
Malformed or internally contradictory contracts raise ValueError before planning.

## 7. Dirty-value authority

The only per-bond unit cash source is the selected candidate's attached
m3.dv01.dirty_value_currency from Task273 READY evidence. It must be a positive
finite Decimal and carry matching Bond/context/snapshot/profile identity.
Price basis, quote, nominal, NKD, clean value, dirty value and snapshot diagnostics
are copied. Clean+NKD pricing is never rebuilt in Task302.

Task302 uses the latest validated Task273 market dirty value as a Shadow
reference cash value. It is not a bid/ask fill model and does not prove that the
same price was executable in the real market.

## 8. Frozen planning policy

FLOOR_TO_TARGET is mandatory. Target overshoot, residual cash redistribution,
transaction costs, slippage and market impact are false. Verified lot size and
verified trading board are required. No public overrides exist. Missing verified
lot size is a blocker. Task302 never assumes one bond per lot.
Security Master supports lot size structurally; current source coverage must be
audited separately. T-Invest lot fields are not used here.

## 9. Exact integer floor and monetary context

lot_dirty_value_rub = unit dirty value × verified lot size.
For positive Decimal target and lot value, floor uses their exact integer ratios:
if target = tn/td and lot value = ln/ld, lots = (tn×ld)//(td×ln).
No rounded quotient, float, nearest rounding or ceiling occurs.
Bond quantity is integer lots × integer lot size. Monetary/weight arithmetic uses
a fresh Context(prec=28, rounding=ROUND_HALF_EVEN), without quantize or caller
context dependence. planned_cash_cost_rub = lots × lot_dirty_value_rub.
Shortfall = target amount − cash cost. Overshoot and negative gaps are rejected.

## 10. Zero-lot and partial semantics

Every selected Strategy position remains visible, in original rank order.
Unavailable terms produce TERMS_UNAVAILABLE with exact blockers, zero lots,
quantity, cost and realized weight. READY terms with a target below one lot
produce TARGET_BELOW_ONE_LOT with zero quantities. Other positions are PLANNED.
The target shortfall for a zero-lot row equals its full target amount.
Non-selected Strategy bonds cannot re-enter the execution universe.

## 11. Cash and tracking

Planned invested cash is the sum of position costs in Strategy rank order.
Execution tracking gap is Strategy target invested RUB minus planned invested cash.
Shadow cash equals Strategy cash plus that gap and must also equal capital minus
planned invested cash exactly. Sum of position shortfalls must equal the gap.
Realized position weight is planned cash/capital; zero-lot weight is zero.
Realized invested weight is their sum, cash weight is 1 minus that sum and the
weight tracking gap is Strategy invested weight minus realized invested weight.
Cash remains cash: no synthetic cash Bond, renormalization or extra-lot redistribution.

## 12. Mandatory post-rounding risk

The planner constructs one ProposedRiskPortfolio from only PLANNED positions,
using their realized weights and Strategy's exact source_risk_batch. The pure
PortfolioRiskEvaluator is called exactly once. Its canonical proposal must equal
the computed proposal. Empty rounded portfolios require PASS. BLOCKED with planned
lots yields RISK_BLOCKED, preserving all quantities and reasons. No removal,
lot adjustment, optimization or Strategy rerun repairs a blocked result.
Duration composition can worsen when rounding removes low-duration allocations.
Risk formulas and thresholds remain exclusively Task300 authority.

## 13. Plan status

After final Risk PASS: no selected Strategy rows means EMPTY; selected rows with
zero total planned lots mean UNEXECUTABLE; some PLANNED rows plus zero-lot rows
mean PARTIAL; all selected rows PLANNED mean READY. READY describes execution
coverage, not 100% capital investment or a market fill. RISK_BLOCKED takes precedence
when planned lots exist and final Risk fails. No persistence readiness is claimed.

## 14. Summary and provenance

Summary reports Strategy capital/targets, ready/unavailable terms counts,
planned/zero-lot counts, total integer lots/quantity, invested cash, residual cash,
realized weights, RUB/weight tracking gaps and final risk status. Incompatible
per-bond price bases are not averaged. Provenance retains upstream contract and
policy versions, Task273 formula versions, selected/ready/planned/zero-lot IDs,
source snapshot IDs, used profile IDs, context and capital. Arrays follow Strategy
order; profile IDs used for planning contain only PLANNED rows.

## 15. Read-only snapshot orchestration

ShadowExecutionSnapshotService(db).build(bond_ids, as_of_date, capital_rub, *,
market_source="moex", max_market_age_days=7, max_curve_age_days=7,
liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5) validates
requests before loading. IDs must be a deterministic nonempty sequence of unique
positive exact ints; dates exclude datetime, ages exclude bool, and window
underflow is rejected. Under shared no_autoflush, call Strategy snapshot once,
terms loader once and planner once. Returned source context must match. No own
additional SQL, mutations or transaction completion; dependency exceptions propagate.

## 16. Capabilities and safety

Execution planning, verified terms gates, integer lots, dirty-value cash basis,
residual cash, realized weights and post-rounding risk capabilities are true.
Persistence, ledger, rebalance, broker/sandbox/real orders, bid/ask, costs, slippage,
market impact, realized performance, benchmark and PIT remain false.
Shadow Execution Plan v1 is not a real, sandbox, or broker order.
No VDS/production, source network, live tokens, data mutation, deploy or legacy
paper-trading/portfolio/ML/backtest dependency is authorized.

## 17. Verification and delivery

Hermetic tests cover verified/missing/conflicting/drifted terms, scalar type
contradictions, strict serialization and PIT, exact single/multi-unit lot math,
floor boundaries, partial/unexecutable/empty paths, asymmetric duration blocking,
cash identities, no redistribution, deterministic immutable sources and Decimal
isolation. SQLite tests prove one SELECT or none, narrow projection, caller-owned
pending state and three exact orchestration calls. AST checks guard sources,
legacy/mutations and duplicated pricing/risk formulas.
Run three focused modules, four specified Strategy/Risk regressions, DV01/Security
Master regressions, compile four application modules and working/staged diff checks.
Full suite is CI responsibility. Delivery is one commit and one ordinary push,
followed by one exact-commit CI snapshot without waiting.

## 18. Handoff

Stop after delivery. The next step is a separately authorized read-only Shadow
Execution Terms Coverage Audit. No production coverage probe occurs here. If lot
coverage is insufficient, an independently reviewed Task302B may bridge exact
source evidence; otherwise Task303 Ledger/Daily Cycle requires a separate request.
No unknown lots are filled, no Shadow positions are persisted and no forward
90-day test, performance pipeline or broker action begins automatically.
