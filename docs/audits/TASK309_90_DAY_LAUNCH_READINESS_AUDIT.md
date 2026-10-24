# Task309 — 90-Day Launch Readiness & Pre-Test Alpha Validation Audit

## 1. Executive decision

Audit date: **2026-10-24**. Inspected baseline: **b1ce2023f60fe4691023236e3860c47a3c094bfe**. This is a repository/evidence audit, not a production preflight or an investment recommendation.

The modern calculation and controlled-persistence foundations exist. They do not establish a currently purchasable portfolio, verified economic cashflows, or a complete operational daily cycle. **OFFICIAL_DAY0_READY=NO**. A trustworthy PIT historical walk-forward test cannot currently be asserted; historical diagnostic coverage must remain separate from forward-test launch requirements.

The shortest defensible path is to establish current bound input coverage, qualify the actually needed cashflow amounts, and complete the grouped daily observation/recovery boundary. Rewriting Investment Model or relaxing Risk Engine is not justified by this audit. Six proposed work packages are specified in the [roadmap](../roadmaps/TASK309_MINIMAL_PATH_TO_90_DAY_TEST.md); only its first package is recommended as Task310, subject to a separate request.

Confirmed personal capital is **50,000 RUB**. The frozen seven-case grid is 50,000 / 100,000 / 500,000 / 1,000,000 / 3,000,000 / 5,000,000 / 10,000,000 RUB [E14]. These are correlated capital treatments of one market experiment, not seven independent trials. 25,000,000 RUB is neither confirmed capital nor part of that policy. Qualified-investor status is **UNKNOWN**. The scope is private research, without SaaS, licensing or commercial distribution.

## 2. Method, CI and evidence strength

HEAD, branch `main`, local `origin/main` and remote `main` were checked against the baseline; tracked tree was clean before this task. The exact baseline [CI run 39998655294](https://github.com/NotlimitGB/BondRadar/actions/runs/39998655294) was independently queried and was `completed / success`. Its configured workflow includes backend compile/tests, frontend build and production compose validation [E00]. That confirms CI at this SHA, not deployed migrations, production rows, source availability or successful trading.

This audit reads implementation, schemas, migration declarations and test source. No application service, source collector, database or investment calculation was executed. Test references below describe coverage present in source; no new local pytest run is claimed. No live official documentation research was needed: R4 is cited as a prior bounded research result, not refreshed source authority.

Evidence labels used throughout:

| Label | What it establishes | What it does not establish |
|---|---|---|
| IMPLEMENTATION | Inspected code/contract at the pinned baseline | Correct deployed state or data completeness |
| CI | Successful exact-baseline workflow | Production verification or profitability |
| OPERATOR_CONTEXT | Facts supplied with Task309 or recorded in prior research | Independent replay of those artifacts here |
| REVIEWED_RESEARCH | Repository research with stated source scope/limitations | Broader authority than its conclusion |
| UNKNOWN | No sufficiently bound operational evidence available to this audit | Proof that production data does not exist |

No latest hash-bound admission report, contemporary M3 coverage, current 50k Shadow plan, grouped activation receipt or accepted daily history was supplied and independently validated in this audit. Tracked examples/test fixtures are not substitutes. Existing temporary/evidence directories were not scanned as an implicit production evidence source. The most recent verified operational admission state is therefore **UNKNOWN**, including whether an earlier blocked batch has since become READY.

### Inspected contract versions

These literals were checked in schema source, without importing application modules or constructing runtime outputs:

| Boundary | Version / source |
|---|---|
| Candidate | `unified-candidate-v1`, `unified-candidate-batch-v1` — [schema](../../backend/app/schemas/unified_candidate.py#L99) |
| Investment | `investment-model-policy-v1`, `investment-evaluation-v1`, `investment-evaluation-batch-v1` — [schema](../../backend/app/schemas/investment_model.py#L21) |
| Risk | `risk-engine-policy-v1`, `risk-candidate-batch-v1`, `portfolio-risk-evaluation-v1` — [schema](../../backend/app/schemas/risk_engine.py#L16) |
| Strategy | `portfolio-strategy-policy-v1`, `portfolio-strategy-v1` — [schema](../../backend/app/schemas/portfolio_strategy.py#L18) |
| Shadow terms/plan | `shadow-execution-policy-v1`, `shadow-execution-terms-v1`, `shadow-execution-terms-batch-v1`, `shadow-execution-plan-v1` — [schema](../../backend/app/schemas/shadow_execution.py#L19) |
| Ledger plans | `shadow-genesis-plan-v1`, `shadow-daily-plan-v1` — [schema](../../backend/app/schemas/shadow_ledger.py#L130) |
| Experiment | `shadow-experiment-policy-v1`, modified-duration basis `MODIFIED_DURATION_TASK272` — [schema](../../backend/app/schemas/shadow_experiment.py#L26) |
| Scale | `shadow-scale-experiment-policy-v1`, `shadow-scale-activation-plan-v1` — [scale policy](../../backend/app/schemas/shadow_scale_experiment.py#L19), [activation schema](../../backend/app/schemas/shadow_scale_activation.py#L75) |

### Operator-supplied historical context

Task306A4 reportedly completed 2,224/2,224 partitions, zero failed, 41,499 pages, 3,987,965 market observations and 2,641,648 usable price/NKD observations for 2020-09-01 through 2026-09-30. These are not acceptance constants and were not replayed here. Task307 contains a verifier/reconciliation mechanism [E18]; its existence does not prove a particular operator report passed.

Supplied R2 semantic SHA values are `7a698ad26fd3fcf8e4387285755212e98fff2edce013db48538a0012700decd0` (source contract) and `db6da43136f547a1ce7efad31fc30343a04939b8d7088877dd5f40b9548e8a47` (qualification). Supplied real R3 result is COMPLETE / source-row PASS: RU000A0JQAM6 has 38 coupon rows, 35 amounts and three missing amounts on 2027-09-15, 2028-03-15 and 2028-09-15; SU24020RMFS8 has 12/12 source amounts. Both reportedly have an amortization row with exact `data_source="maturity"`. These observations are context, not an independently inspected real R3 output or proof of payments [E20–E22].

## 3. Implementation inventory and operational readiness matrix

All `CI=SUCCESS` entries below refer to the same exact-baseline run above. They are not claims that each subsystem has separate operational certification. `BLOCKED_BY_EVIDENCE` is used where the implemented mechanism's economic use specifically remains blocked; the implementation and CI columns remain separate.

| Component | IMPLEMENTATION_STATUS | CI_STATUS | OPERATIONAL_EVIDENCE_STATUS | INPUT_COVERAGE_STATUS | ECONOMIC_AUTHORITY_STATUS | PIT_STATUS | DAY0_RELEVANCE | BLOCKERS | EVIDENCE_REFERENCES | NEXT_ACTION |
|---|---|---|---|---|---|---|---|---|---|---|
| Task268 OFZ curve | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Current eligible nodes/date unknown | Canonical fixed/bullet/RUB gates implemented | Current profiles are not historical proof | Required by Task304 | P0-01 | E01 | Report current nodes and exclusion reasons |
| Task269 issuer identity / credit | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Verified LegalIssuer coverage unknown | Exact source linkage; Company is not a substitute | Unknown publications remain diagnostic | Required by risk | P0-01, P1-01 | E02 | Bind selected Bonds to verified issuer evidence |
| Task272 modified duration | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Frequency/fixed/dated inputs unknown | Finite duration and verified frequency required | Current structural profile | Required by candidates/benchmark | P0-01 | E03 | Report failures, do not infer frequency |
| Task273 dirty valuation / DV01 | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Quote/NKD/nominal freshness unknown | Explicit finite RUB/nominal and price gates | Not PIT-certified | Required | P0-01 | E04 | Establish exact snapshot/profile lineage |
| Task295 universe | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Current UIDs/buy/qualification unknown | Source facts, not account eligibility | Current read foundation | Required input boundary | P0-01 | E05 | Bind current source facts to runtime IDs |
| Task296 identity/admission/preflight | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Latest candidate/READY subset unknown | Exact matches, independent issuer/board/description | Frozen observation, not historical listing proof | Required if imports/terms are needed | P0-01 | E06 | Review latest frozen input before any separate operation |
| Task297 import | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Import receipt/canonical rows unknown | Authorized exact READY subset only | Not historical authority | Conditional, not mandatory new import | P0-01 | E07 | Prefer existing rows; no import authorized here |
| Task298 Unified Candidate | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Actual CORE intersection unknown | Preserves native M3 evidence | pit_ready=false | Required | P0-01 | E08 | Obtain exclusions and joint CORE IDs |
| Task299 Investment Model | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Actual ready peer contexts unknown | Source-native relative scoring, not expected return | pit_ready=false | Required | P0-01 | E09 | Measure ready contexts, do not weaken cohorts |
| Task300 Risk Engine | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Eligible issuer/liquidity subset unknown | Frozen constraints, not complete risk model | pit_ready=false | Required | P0-01 | E10 | Report all blockers at 50k and each scenario |
| Task301 Strategy | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Feasible nonempty 50k target unknown | Risk-oracle accepted weights | pit_ready=false | Required | P0-01 | E11 | Preserve partial cash; report no-op/empty outcomes |
| Task302 Shadow execution | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Verified terms and rounded feasibility unknown | Dirty-value proxy, not fill evidence | pit_ready=false | Required | P0-01, P1-03 | E12 | Check every selected lot and final risk PASS |
| Task302B terms bridge | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Existing supporting lineage unknown | Frozen lot plus independent board evidence | Observation is not historical proof | Conditional evidence repair | P0-01 | E13 | Diagnose first; APPLY needs separate authority |
| Task303 ledger/daily | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Persisted flows and marks unknown | Accounting trusts supplied canonical event amounts | pit_ready=false | Required | P0-02, P0-03 | E15 | Qualify economics and exercise recovery hermetically |
| Task304 benchmark/comparison | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Representative duration/snapshot alignment unknown | Fractional reference; flow authority external | pit_ready=false | Required frozen comparison | P0-01, P0-02 | E16 | Preserve strict matching and common date |
| Task304A seven-case matrix | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | All seven cases jointly ready unknown | Common investment evidence, separate capital builds | pit_ready=false | Required full grid | P0-01, P0-03 | E14 | Do not drop failed capital cases silently |
| Task305 persistence/activation | IMPLEMENTED_CI_VERIFIED | SUCCESS | UNKNOWN | Deployment/schema/receipts unknown | Atomic genesis; daily orchestration not supplied | pit_ready=false | Required full experiment | P0-03 | E17 | Review grouped daily boundary before activation |
| Task306A/A1/A2 | IMPLEMENTED_CI_VERIFIED | SUCCESS | OPERATOR_CONTEXT only | Actual audited DB coverage unknown | Diagnostic inventory/endpoints, no returns | Explicit historical limitations | Research, not automatic Day0 gate | P1-01 | E19 | Review artifacts before proposing replay |
| Task306A3/A4 archive/discovery | IMPLEMENTED_CI_VERIFIED | SUCCESS | OPERATOR_CONTEXT only | Raw historical range reported | Discovery is not canonical projection or PIT | Completeness distinctions retained | Research only | P1-01 | E18 | No new acquisition justified solely by range size |
| Task307 source coverage | IMPLEMENTED_CI_VERIFIED | SUCCESS | Actual report not replayed here | Descriptive population/coverage | Not backtest/execution readiness | All historical/PIT readiness false | Research only | P1-01 | E18 | Verify supplied finalized evidence offline if authorized |
| Task308A/B1 identity | IMPLEMENTED_CI_VERIFIED | SUCCESS | OPERATOR_CONTEXT only | Full historical bindings not established here | Current exact binding differs from historical identity | Unproven | Research only | P1-01 | E20 | Keep unresolved/extinct population visible |
| Task308B2/R1/R2/R3 | BLOCKED_BY_EVIDENCE | SUCCESS | OPERATOR_CONTEXT only | Small pilot; unknown amounts/missing families | Row integrity != monetary authority/payment | Unproven | Used flow authority must close separately | P0-02 | E21 | Retain conflicts/nulls; no profile summation |
| Task308B2R4 research | BLOCKED_BY_EVIDENCE | SUCCESS | REVIEWED_RESEARCH | Bounded documentation search | DOCUMENTATION_GAPS_REMAIN | Publication unproven | Relevant to Bondization-derived amounts | P0-02 | E22 | Field-specific applicable proof or alternative primary evidence |
| Passive OFZ / corporate baseline | NOT_IMPLEMENTED as separate modern frozen benchmark policies | NOT_APPLICABLE | UNKNOWN | UNKNOWN | No independently frozen selection/accounting baseline found | Unproven | P1 comparison extension | P1-02 | E16, E23 | Specify fair baseline before testing |
| Grouped daily persisted comparison | NOT_IMPLEMENTED in inspected modern chain | NOT_APPLICABLE | UNKNOWN | No group/date accepted receipt contract found | Single-run accounting exists | Not applicable | Required operational completion | P0-03 | E15, E17, E23 | Reuse existing services, add bounded group/day boundary |
| CFA research track | NOT_IMPLEMENTED beyond DFA universe DTO/read | NOT_APPLICABLE for track | UNKNOWN | Concrete eligible product/source unknown | No comparable payout/access authority | Unproven | Isolated research, not bond allocation gate | P1-04 | E05 | Record eligibility unknown; no synthetic transaction |

`OPERATIONALLY_VERIFIED` is not assigned to any subsystem: no qualifying contemporary operational artifacts were independently verified here. Conversely, UNKNOWN is not a claim of failed production execution.

## 4. Answers Q1–Q13 and the 50k path

| Question | Finding and evidence |
|---|---|
| Q1 — Currently investable instruments? | **UNKNOWN operationally.** Task295/296 preserve buy availability, qualification and exact identities, but Task298/302 accept explicit IDs rather than a fresh broker-universe authorization [E05, E06, E08, E12]. M3 membership is not buy eligibility. |
| Q2 — All scoring inputs? | Mechanism exists; current joint coverage **UNKNOWN**. CORE requires consistency, market freshness, OFZ relative value, rating evidence, comparable cohort, liquidity, modified duration, DV01 and liquidity-relative-value readiness. Credit peer distributions then need two exact-cohort peers; separate family counts do not prove the intersection [E08, E09]. |
| Q3 — Meaningful risk-admitted portfolio? | **UNKNOWN** for current evidence. Risk needs verified LegalIssuer, score >=25, positive median turnover and candidate modified duration <=5; portfolio limits remain binding [E10]. Empty PASS is not a meaningful investable result. |
| Q4 — Feasible allocation at 50k? | Builder supports positive capital, 5% seeds, 1% increments and at most ten positions; each accepted change and final state must PASS risk. Actual nonempty target is **UNKNOWN**; no allocation was calculated [E11]. |
| Q5 — Integer lots/residual cash? | Implemented, actual result **UNKNOWN**. Exact floor uses verified lot and supplied dirty value; no overshoot/redistribution. Terms-unavailable and below-one-lot positions remain visible; post-rounding risk can BLOCK [E12]. |
| Q6 — Ledger income/valuation/return? | Implemented for qualified persisted source amounts and Task273 marks. It books scheduled-source events, not bank/broker payment confirmations; actual income proof and current flow authority remain unproven. No observed return is reported [E15, E21, E22]. |
| Q7 — Same-date duration benchmark? | Implemented conditionally. Requires current curve, READY modified duration for all representatives, exact weight invariants and common genesis trade date. Daily current/previous valuation supports calendar dates subject to age <=7 [E16]. Operational alignment **UNKNOWN**. |
| Q8 — Seven launches/updates? | One-commit grouped genesis is implemented; all seven case inputs must pass. Per-run daily accounting and pure scale aggregation exist, but no grouped daily persistence/recovery service was found [E14, E17, E23]. |
| Q9 — Automatic observation/recovery? | Missing modern scheduler/monitoring and reviewed group/day acceptance boundary. Single-run idempotency, locks, rollback and ambiguous-commit receipts already exist and should be reused [E15, E17, E23]. |
| Q10 — Trustworthy pre-Day0 historical walk-forward? | **NO on established evidence.** Current profiles, historical listing completeness, publication availability, cashflow completeness/revisions and costs are not demonstrated; no modern walk-forward execution package established by these audits [E18–E22]. Diagnostics must be labeled NON_PIT_DIAGNOSTIC. |
| Q11 — Corporate buy-and-hold? | Not implemented as a separate frozen modern comparator. Design must precede testing, preserve defaulted/disappeared instruments and use decision-date eligibility [E16, E19, E23]. |
| Q12 — Operational Day0 blockers? | P0-01 current linked readiness evidence; P0-02 economic qualification of used cashflows; P0-03 complete, recoverable grouped observation and launch review. No deployment/activation authorization is created by this audit [E06–E17, E22]. |
| Q13 — Minimal CFA research? | Conceptually possible as isolated source/eligibility observations, but **CFA_RESEARCH_TRACK_NOT_IMPLEMENTED**. DFA listing is not platform access, transferability, liquidity or payout proof [E05]. |

### Required current evidence chain

For the same decision context, a future report must bind exact UID/ISIN/SECID/Bond IDs to current API buy availability, non-qualified access or explicit restrictions/tests, active/traded evidence, primary board and frozen admission status. It must distinguish unknown activity from explicit inactivity according to existing preflight gates [E06]. Missing Company title/INN can block creation; verified LegalIssuer linkage is a separate risk input, not automatically established by a Company INN [E02, E07, E10].

Task298 requires M3 integrity; Task299 operates on supplied candidate cohorts rather than all exchange bonds. It uses fixed 0.40/0.30/0.20/0.10 OFZ/credit/liquidity/duration components and batch-relative normalization, not a forecast of total return [E09]. Current issuer financial/CBR metrics must not be invented as mandatory direct score factors: the selected Task299 components are the authority, while missing publication authority matters for any historical use [E02, E09, E19].

Risk limits are position 0.20, verified LegalIssuer 0.25, capacity 0.05 of median daily turnover, candidate duration 5.0, invested-sleeve duration 3.5 and relative DV01/bp 0.00040 (40 per 100k). Cash does not dilute sleeve duration [E10]. At 50k a 5% seed corresponds arithmetically to 2,500 RUB, but this is a policy illustration, not a computed allocation. One verified lot exceeding a selected target can yield zero lots; asymmetrical floor rounding can also breach sleeve duration. Genesis is stricter than a diagnostic Shadow plan: READY, no quality flags, all positions positive-lot and TQCB, post-rounding PASS [E11, E12, E15].

Task302 itself permits any verified nonblank board; Task303 genesis requires TQCB. This distinction must be reported, not erased by changing Task302 policy [E12, E15]. Neither verified board/lot nor API buy facts prove a real fill, bid/ask cost or legal access for this investor.

## 5. Daily cycle: implemented versus missing

| Concern | Existing behavior | Gap / acceptance requirement |
|---|---|---|
| Genesis | Task305 atomically writes group, seven cases, child runs, benchmark genesis/components; pre/post audit; exact authorization/state hashes | Actual deployed schema, seven READY inputs and operational receipt UNKNOWN; no activation here [E17] |
| Daily APPLY | Task303 fresh session, reviewed plan rebuild, fixed locks, one commit, audit; historical date can become fresh authorized no-op | Scope is one run, not all capital cases plus benchmark/comparison [E15] |
| Calendar/weekends | Next accepted day +1 calendar day; marks age 0..7; benchmark evaluates calendar day and previous calendar day | No weekend skipping: missing/stale evidence blocks. Long closures can exceed freshness; do not weaken it [E15, E16] |
| Missed day | Sequence gap blocks; historical input mismatch returns HISTORICAL_SOURCE_DRIFT | Recover in date order from available dated evidence; missing historical evidence cannot be filled using future data [E15] |
| Source events | Coupon then amortization then redemption, quantity preserved until redemption; offer diagnostic; malformed/unsupported event blocks | Event row existence is not exhaustive schedule or payment proof. No amount/nominal/zero reconstruction [E15, E21, E22] |
| Frozen daily evidence | Ledger/snapshot/positions contain fingerprints, valuations and source IDs | Benchmark daily and comparison outputs are computed read-only; no modern group/day durable acceptance artifact found [E15–E17, E23] |
| Failure recovery | Pre-commit rollback, COMMIT_OUTCOME_UNKNOWN and POST_COMMIT_AUDIT_FAILED distinguished; no-op does not commit | Do not retry an ambiguous outcome as an unverified new mutation; group-level reconciliation and receipt policy still needed [E15, E17] |
| Group synchronization | Seven-case genesis and pure comparison validation exist | Seven successful independent daily commits would not prove atomic group/day acceptance; specify restart/reconciliation boundary [E14, E17, E23] |
| Pause/abort/horizon | Run/group status fields include ABORTED; per-run persistence sets OBSERVATION_COMPLETE at horizon | Enum presence is not an authorized pause/abort operation. No modern group lifecycle/scheduler path found; horizon/abort propagation needs explicit review [E15, E17, E23] |
| Monitoring/reporting | Typed blockers and deterministic outputs available in services | No inspected modern caller wires daily services into scheduled operator reporting. Legacy/paper paths cannot silently supply this capability [E23] |

Daily return copies Task303 accepted accounting; comparison does not recreate a second strategy-return formula. Before Day90 verdict remains IN_PROGRESS; Day90 requires positive strategy return and positive OFZ excess, otherwise FAIL, with incomplete evidence INDETERMINATE [E16]. This binary frozen rule is not a statistical alpha test.

Task305 `audit_group` is specifically a genesis audit: it requires one child snapshot and the original ledger/position counts. It must not be advertised as an existing general daily-history group audit after Task303 has appended days. A future daily boundary needs its own reviewed reconciliation using the existing single-run accounting authority [E17].

## 6. Source/evidence gap matrix and historical feasibility

| Family | Available mechanism/context | Missing proof / consequence |
|---|---|---|
| Historical population | Independent archive/discovery and exact coverage verifier | Observed/listed identities do not prove full historically investable population; canonical linkage/import not established [E18] |
| Identity / issuer | Current source binding, inventory exceptions and verified LegalIssuer mechanisms | Current mapping is not dated historical linkage; retain extinct, unresolved, delisted/defaulted population [E02, E20] |
| Prices / NKD | History mapping, strict accrued-interest parsing, endpoint/recovery audit | Range/nonnull counts do not prove economically usable entry/exit, storage projection or historical versions [E04, E18, E19] |
| Terms / OFZ | Verified current profiles; Task268 eligibility; Task304 representatives | Current structural/frequency state not historical proof. A2 endpoint prerequisites do not evaluate Task272 matching [E01, E03, E16, E19] |
| Ratings / publication | Exact BOND/LEGAL_ISSUER targets and source-native agencies/providers/scales | Task269 permits UNKNOWN publication diagnostics; timestamp filtering is date-based, not a strict 00:00 cutoff selector. A historical adapter/gate is needed, not silent reuse as PIT [E02, E19] |
| Financial / CBR | Credit features can preserve controlled financial context | Current/revised values lack automatic decision-date authority; not every financial family is a direct Task299 component [E02, E09, E19] |
| Contractual flows | Persisted event table plus strict pilot/replay/semantics | Missing amount is not zero; absent rows not complete absence; schedules/revisions/payment/publication distinct [E15, E21, E22] |
| Execution / costs | Verified terms and dirty-value lot proxy | Historical access, depth, bid/ask, commissions, slippage, tax and recovery assumptions not established [E12, E16] |

The historical audit cutoff is **00:00 UTC T**: decision quotes strictly before T; DATE-only publication D available from D+1; timestamps compared exactly. Modern forward services do not automatically implement that historical boundary. Task306A liquidity uses [T−30,T−1], unlike ordinary Task270 [T−29,T]. Do not alter modern methodology or allow entry-day observations to become prior decision evidence [E19].

| Pre-test method | Feasibility on established evidence | Explicit requirements before execution |
|---|---|---|
| Historical holdout | Not trustworthy PIT now; NON_PIT_DIAGNOSTIC only if separately qualified | Freeze train/development versus untouched holdout dates before viewing outcomes; dated eligible universe, source versions and qualified economics |
| Walk-forward | Not implemented/qualified as a trustworthy PIT test | Reuse modern evaluators with a separately reviewed dated-input boundary; exact publication/issuer/universe proof, no future reconstruction |
| Rolling 90 days | Descriptive endpoint mechanisms exist; no result calculated | Qualify canonical inputs and events; freeze non-PIT/PIT label and execution/accounting conventions |
| Rolling 180 days | Same boundary, horizon independent | Check each horizon's own tail/redemption/cashflow evidence; do not infer from 90-day coverage |
| Rolling 365 days | Primary historical endpoint research horizon, not proven alpha | Fixed first-MOEX-date-per-month schedule, complete eligible decision intersection and outcomes; no moving entry to a model-ready later date |
| Stress periods | Potential research design, coverage UNKNOWN | Predeclare periods and selection criteria; retain defaults/maturities/delisted identities and loss/recovery uncertainty |

Daily continuity is not required by A2 endpoint coverage; forward Task303 still requires a continuous accepted calendar-day chain. A4 discovery completion, safe raw recovery and reference feasibility are not a walk-forward engine. No actual historical replay, return, Sharpe ratio or excess performance was calculated. A future diagnostic result using current terms/issuer links must explicitly say **NON_PIT_DIAGNOSTIC**, even if arithmetic passes [E18–E22].

## 7. Benchmark feasibility and fair comparisons

| Comparator | Selection and current support | Capital/execution/accounting | Limitation / needed gate |
|---|---|---|---|
| A — Passive OFZ buy-and-hold | No separate frozen simple investable baseline in inspected modern chain | Same decision date/horizon; prospective transparent selection, lot/cash policy required | Cannot retrospectively choose best OFZ. Distinct from Task304 duration reference [E16, E23] |
| B — Task304 duration-matched OFZ | Existing exact representative selection, Task272 modified-duration ordering and strict bracket weights | Fractional fully invested return-factor reference; original-unit cash accumulates; no rebalance/reinvestment; no costs | Not an executable 50k comparator; an additional lot/cash/cost-aware baseline is needed for actual purchasable comparisons, without changing this frozen reference [E16] |
| C — Corporate buy-and-hold | Separate selection policy NOT_IMPLEMENTED; proposed design only | Freeze eligible liquid non-government universe, issuer cap and duration/maturity criteria, purchase/exit and cash treatment before test | Same available information and default/recovery treatment; do not make comparator artificially weak, exclude later defaults or select ex-post winners [E10, E19, E23] |
| D — BondRadar | Task298–303 code exists; current feasible portfolio UNKNOWN | Frozen scoring/risk/strategy, integer lots, residual cash, no rebalance/reinvestment | Dirty reference value is not fill/cost evidence; do not reimplement algorithm in a backtest [E08–E16] |

For a later corporate/passive policy, an eligible-liquid subset, explicit issuer cap and fixed duration/maturity band are reasonable specification dimensions, **not a policy frozen by Task309**. Thresholds must be reviewed against opportunity set/risk parity before seeing test results.

All four comparisons require a common ledger convention and an explicit assumption register:

| Dimension | Frozen/known boundary | Still required for fair executable comparison |
|---|---|---|
| Capital and cash | 50k personal case; seven correlated treatments; BondRadar residual cash versus fully invested reference | Report capital utilization/cash drag separately; do not renormalize away cash |
| Dates / horizon / rebalance | Same genesis/evaluation dates, 90 calendar days; Task304 NONE rebalance/reinvestment | No cherry-picked exit; define corporate/default/offer treatment prospectively |
| Price / NKD | Task273 dirty value and exact snapshot/nominal authority | Disclose reference timing and executable bid/ask convention; not a guaranteed transaction |
| Coupons / principal / redemption | Separate event types; no double-counting maturity and invented redemption | Applicable per-bond units, event entitlement, revisions and completeness; scheduled versus received distinction |
| Commissions / spread / slippage / impact | Excluded by frozen v1 | Separate disclosed sensitivity/after-cost methodology before claiming real-capital superiority; no hidden policy patch |
| Tax | No implemented modern tax model | Explicit gross versus net boundary; investor tax facts UNKNOWN; no tax advice implied |
| Liquidity / concentration / duration / credit | Task300 caps and source-native credit context | Comparable risk exposure; economic-group and loss/recovery differences not resolved by yield ranking |
| Drawdown / risk-adjusted performance | No modern comparison statistic established here | Comparable reliable daily path and frozen statistic definition; endpoints alone cannot establish drawdown |
| Absolute / excess return | Task304 positive absolute AND positive OFZ excess at Day90 | Compute only from accepted economic evidence; corporate excess needs separate frozen comparator |

## 8. False-alpha controls and isolated CFA research

| Risk | Required control / existing limit |
|---|---|
| LOOKAHEAD_BIAS | Historical cutoff/publication gates and source versions; modern date-based/current-profile loaders alone insufficient [E02, E19] |
| SURVIVORSHIP_BIAS | Retain independently discovered unresolved/extinct securities and historical listing limitations [E18, E20] |
| SELECTION_BIAS | Fixed monthly entries, prospective portfolio/benchmark inclusion and exclusions; do not move dates after seeing readiness [E19] |
| DATA_SNOOPING | Freeze policies and holdout before outcomes; record every diagnostic variant, not just successful windows |
| OVERFITTING | Untouched holdout or later independent periods; shared capital cases not additional independent observations |
| UNREALISTIC_EXECUTION | Keep lot/cash/cost/access distinction and post-rounding risk; proxy prices not fills [E12] |
| MISSING_DEFAULTS | Preserve failed/disappeared identities and explicit default/recovery evidence; absence of later quotes is not default proof [E18] |
| MISSING_CASHFLOWS | No zero/nominal reconstruction or assumed monetary authority; block required invalid/missing evidence [E15, E21, E22] |
| HISTORICAL_SOURCE_REVISIONS | Freeze payload/version and publication lineage; current replay agreement does not establish old availability [E18–E22] |
| UNSUPPORTED_ISSUER_BACKFILL | No current INN/name mapping promoted to historical LegalIssuer proof [E02, E20] |

Data-snooping/overfitting controls above are proposed research gates, not already implemented experiment-registry capabilities. One positive 90-day result or frozen Day90 PASS cannot establish guaranteed outperformance, risk-adjusted superiority or statistical alpha.

CFA minimum research design: a separate observation register for one or more explicitly identified sources/products, platform, access category, transferability, maturity/payout terms, valuation authority, liquidity/exit restrictions and dated evidence. Record `ELIGIBILITY_UNKNOWN`/`NO_ELIGIBLE_PRODUCT` without simulated transactions. Do not assume qualified status, map CFA onto Bond by name, aggregate it into OFZ excess, or fabricate daily market values. Task295's Dfas empty-request reader and raw DTO are the only directly identified modern CFA foundation [E05]. Research can coexist with the 90-day calendar but absence of an eligible CFA must not invent an allocation or silently invalidate a separate bond-only experiment. No live requests or orders are authorized.

## 9. Prioritized blockers

Counts: **P0=3; P1=4; P2=2**. These are Task309 findings, not copied historical P0 totals. Unknown operational evidence is an evidence gap, not an assertion of a production defect. Work-package IDs refer to the roadmap.

| BLOCKER_ID / SEVERITY | BLOCKER_DESCRIPTION / AFFECTED_COMPONENTS | DEPENDENCIES / SOURCE_EVIDENCE | PROPOSED_REMEDIATION / SAFE_ACCEPTANCE_GATE | REQUIRED_OPERATOR_ACTION |
|---|---|---|---|---|
| P0-01 | Current exact investable → CORE → risk → 50k/all-seven execution readiness unestablished; Task295–305 | Latest frozen admission, profile/issuer/market/cohort inputs and deployed schema unknown; E05–E17 | WP1: linked read-only report; independently reviewed complete inputs, nonempty READY execution, no bypass, seven case status preserved, benchmark alignment and explicit launch checklist | Supply pinned artifacts; separately authorize any contemporary production read-only preflight and later deployment/activation; no new import implied |
| P0-02 | Economic cashflow authority/completeness for actually used strategy/benchmark holdings unestablished; Task303/304 | R4 denominator gaps, small pilot nulls/revisions, canonical event trust boundary; E15, E16, E21, E22 | WP2: applicable primary per-bond amount/currency/event proof, completeness/revision policy and no maturity double-count; hermetic reconciliation. Alternative official instrument evidence may close selected scope without universal Bondization proof | Choose/approve source-backed bounded scope; any new source access/import separate; ASSUMED values remain excluded |
| P0-03 | Recoverable seven-case daily operation and durable benchmark/comparison acceptance missing in modern chain; Task303–305 | Single-run atomicity != group/day acceptance; E14–E17, E23 | WP3: deterministic group/day receipt, evidence persistence, retries/reconciliation, sequential missed-day recovery, lifecycle and monitoring; synthetic all-case failure tests then independent operational review | Approve daily execution/authorization model separately; official Day0 only after review, green CI and explicit activation authority |
| P1-01 | Trustworthy historical holdout/walk-forward blocked by PIT/universe/version/outcome gaps | Research only; E18–E22 | WP5: quantified available evidence, separate NON_PIT_DIAGNOSTIC versus PIT gate; never label current-profile replay proven alpha | Choose whether diagnostic research is useful; separately authorize any replay/acquisition; not an automatic forward-Day0 prerequisite |
| P1-02 | Simple passive OFZ and corporate benchmark policies not frozen/implemented | Comparable opportunity set and selection authority; E16, E19, E23 | WP4: prospectively reviewed fair selection/risk/cash/exit rules and untouched evaluation evidence | Review policy before test; no retrospective winners |
| P1-03 | Executable after-cost/tax and broader risk claims unsupported | Dirty proxy, no frozen costs/tax; E10, E12, E16 | WP4: explicitly gross reference labels; separate lot-aware comparator and reviewed cost scenarios before real-money superiority claims | Provide actual broker/tax/access assumptions if desired; no orders or recommendation authorization |
| P1-04 | CFA research workflow and concrete access evidence absent | DFA reader only; E05 | WP6: isolated evidence register, missing eligibility states and non-comparable outcome labels | Choose bounded source/product only under separate authority; no CFA transaction required |
| P2-01 | Statistical long-term/alpha inference unavailable from one shared 90-day period | Shared market treatments; E14, E16 | Defer longer independently frozen period/holdout design; no significance claim from seven cases | Decide later research budget; not Day0 gate |
| P2-02 | Economic-group/sector/default-stress/convexity/KRD/VaR risk not implemented by Task300 | Policy defines limited issuer/liquidity/duration/DV01 gates; E10 | Explicit scope disclosure; separate future risk task if required, without changing frozen score/risk here | Decide separate research scope; not silently added to launch |

P0-02 concerns source-backed simulated economic cash entitlement, not proof of actual cash receipt on a future date. A forward Shadow simulation may use properly qualified scheduled flows with explicit labels; actual personal payments require separate broker/bank evidence. Full historical PIT reconstruction and CFA product availability are not assigned P0.

## 10. Separate readiness and mandatory decisions

| Readiness layer | Decision | Boundary |
|---|---|---|
| Offline repository/evidence research | YES | This audit and existing offline validators; not an acquisition approval |
| Experimental financial simulation | CONDITIONAL | Qualified bounded economics, explicit assumptions and scope; no numeric simulation executed here |
| Forward Shadow observation | CONDITIONAL | P0-01/02/03, reviewed evidence and separate execution authorization |
| Official 90-day Day0 | NO | Required evidence and operational acceptance not established; activation must be explicit |
| Real-money discretionary investing | UNKNOWN | Broker access, costs, tax, liquidity and investor decisions not verified; no trade recommendation |

| Decision | Value | Minimum explicit closure requirements |
|---|---|---|
| HISTORICAL_WALK_FORWARD_FEASIBLE | NO | Reviewed dated input adapter/replay design plus historical universe, publication, terms, identity and economic/outcome proof. Otherwise only separately authorized NON_PIT_DIAGNOSTIC work |
| OFZ_COMPARISON_FEASIBLE | CONDITIONAL | Existing Task304 inputs must satisfy representative/modified-duration/common-date/strict Decimal gates; qualified cashflows and daily evidence. Executable passive comparison requires WP4 separately |
| CORPORATE_BUY_HOLD_COMPARISON_FEASIBLE | NO | WP4 frozen fair corporate selection/accounting/exit policy and qualified decision-date inputs before test |
| RUB_CASHFLOW_SIMULATION_FEASIBLE | CONDITIONAL | Close applicable per-bond denomination/currency/event/completeness gates for used holdings, no assumed amount or actual-payment claim; WP2 |
| CURRENT_STRATEGY_EXECUTION_FEASIBLE | UNKNOWN | WP1 contemporary exact investable-to-execution evidence, positive lots, no quality flags, verified issuers/terms and final risk PASS |
| MULTI_CAPITAL_FORWARD_TEST_FEASIBLE | CONDITIONAL | All seven cases ready under common evidence plus WP2/3 daily persistence/recovery and explicit activation |
| CFA_RESEARCH_TRACK_FEASIBLE | CONDITIONAL | WP6 isolated source/access observation contract; eligibility may remain unknown, no trading or invented payout |
| DAILY_CYCLE_READY | NO | WP3 grouped/day evidence and durable acceptance, missed-day/ambiguous-outcome recovery, lifecycle/operator monitoring; reuse single-run accounting |
| PRODUCTION_GENESIS_READY | UNKNOWN | Actual deployed schema and pinned current evidence/receipts not inspected; independent read-only preflight and explicit later deployment/activation gates |
| OFFICIAL_DAY0_READY | NO | P0-01/02/03 closed, independent review/green delivery CI and explicit separately authorized genesis; audit completion cannot start clock |

Recommended next task: **Task310 — Current Forward Experiment Input & Execution Readiness REPORT Foundation**, repository-only development first. It must reuse modern services and report blockers, not repair/import/activate. No roadmap work is automatically authorized.

## 11. Evidence register (baseline-pinned implementation citations)

Line anchors refer to the inspected baseline, not an executed production result. Test modules are source evidence of intended coverage; baseline CI is the separate execution evidence.

- **E00:** [CI workflow](../../.github/workflows/ci.yml#L13), `backend-tests`, `frontend-build`, `compose-config`; [exact successful baseline run](https://github.com/NotlimitGB/BondRadar/actions/runs/39998655294).
- **E01:** [Task268 OfzReferenceCurveService.build_curve](../../backend/app/services/ofz_reference_curve_service.py#L65), `_eligible` at line 44: canonical identity, structural filters, dated curve snapshots.
- **E02:** [Task269 BondCreditFeatureService.build_for_bond](../../backend/app/services/bond_credit_feature_service.py#L36), `_issuer` line 120, `_ratings` line 136, `_metrics` line 190; [Task300 issuer validation](../../backend/app/services/risk_candidate_reducer.py#L79).
- **E03:** [Task272 BondModifiedDurationService.build_for_bond](../../backend/app/services/bond_modified_duration_service.py#L29): fixed/dated, verified frequency, finite duration/denominator gates.
- **E04:** [Task273 BondDv01Service.build_for_bond](../../backend/app/services/bond_dv01_service.py#L34): exact snapshot alignment, verified RUB/nominal, canonical clean-price/fallback, NKD and partial dirty-value availability.
- **E05:** [Task295 reducer](../../backend/app/services/tinvest_instrument_universe_service.py#L26), `_normalize_dfa` line 162, `_classify_availability` line 301; [source client](../../backend/app/services/tinvest_instrument_universe_client.py#L63), `list_dfas`.
- **E06:** [Task296B build](../../backend/app/services/tinvest_bond_admission_manifest_service.py#L720), `_add_source_reasons` line 1379; [Task296C/C1 preflight](../../backend/app/services/tinvest_bond_import_preflight_service.py#L1); [independent MOEX evidence adapter](../../backend/app/services/moex_bond_import_evidence_service.py#L1); [admission tests](../../backend/tests/test_tinvest_bond_admission_manifest.py#L1), [preflight tests](../../backend/tests/test_tinvest_bond_import_preflight.py#L1).
- **E07:** [Task297 ControlledBondImportService.plan](../../backend/app/services/controlled_bond_import_service.py#L241), `apply` line 328: frozen validation, Company INN grouping, exact storage/collision gates; [focused tests](../../backend/tests/test_controlled_bond_import_service.py#L1).
- **E08:** [Task298 CORE_REASONS and reducer](../../backend/app/services/unified_candidate_reducer.py#L14), `build` line 239; [snapshot service explicit ID/context boundary](../../backend/app/services/unified_candidate_snapshot_service.py#L17); [reducer tests](../../backend/tests/test_unified_candidate_reducer.py#L1).
- **E09:** [Task299 build_peer_contexts](../../backend/app/services/investment_model_snapshot_service.py#L16), [reducer](../../backend/app/services/investment_model_reducer.py#L1), [frozen policy schema](../../backend/app/schemas/investment_model.py#L1); [cohort and scoring tests](../../backend/tests/test_investment_model_reducer.py#L52).
- **E10:** [Task300 fixed policy](../../backend/app/schemas/risk_engine.py#L16), [candidate gates](../../backend/app/services/risk_candidate_reducer.py#L97), [PortfolioRiskEvaluator.build](../../backend/app/services/portfolio_risk_evaluator.py#L29); [portfolio tests](../../backend/tests/test_portfolio_risk_evaluator.py#L1).
- **E11:** [Task301 PortfolioStrategyBuilder.build](../../backend/app/services/portfolio_strategy_builder.py#L18); [seed/top-up/empty/capital tests](../../backend/tests/test_portfolio_strategy_builder.py#L69).
- **E12:** [Task302 terms SELECT](../../backend/app/services/shadow_execution_terms_loader.py#L16), [narrow terms validation](../../backend/app/services/shadow_execution_planner.py#L140), [exact_lot_floor](../../backend/app/services/shadow_execution_planner.py#L201), `build` line 209; [snapshot explicit IDs](../../backend/app/services/shadow_execution_snapshot_service.py#L21); [rounding risk test](../../backend/tests/test_shadow_execution_planner.py#L69).
- **E13:** [Task302B ExecutionTermsEvidenceBridgeService](../../backend/app/services/execution_terms_evidence_bridge_service.py#L216), `_row` line 126, `plan` line 242, `apply` line 266; [tests including lot conflicts and timestamps](../../backend/tests/test_execution_terms_evidence_bridge.py#L94).
- **E14:** [Task304A capital grid/policy](../../backend/app/schemas/shadow_scale_experiment.py#L15), [pure matrix/genesis/comparison assembly](../../backend/app/services/shadow_scale_experiment_evaluator.py#L1), [genesis orchestrator](../../backend/app/services/shadow_scale_experiment_genesis_service.py#L6); [matrix tests](../../backend/tests/test_shadow_scale_experiment_genesis.py#L1).
- **E15:** [Task303 validate_request](../../backend/app/services/shadow_ledger_genesis_service.py#L17), [daily validate_cashflows/validate_mark/_plan](../../backend/app/services/shadow_daily_cycle_service.py#L17), [repository transaction/audit/persistence](../../backend/app/services/shadow_ledger_repository.py#L1), [run model](../../backend/app/models/shadow_test_run.py#L1), [cashflow model](../../backend/app/models/bond_cashflow_event.py#L33), [migration](../../backend/alembic/versions/202610030002_shadow_ledger_and_daily_marking.py#L1); [daily ordering/gaps/drift/horizon tests](../../backend/tests/test_shadow_daily_cycle.py#L63).
- **E16:** [Task304 policy and limitations](../../backend/app/schemas/shadow_experiment.py#L26), [curve_representatives](../../backend/app/services/ofz_total_return_benchmark_service.py#L47), `duration_weights` line 104, `build_genesis` line 127, `build_daily` line 185; [accepted-history comparison](../../backend/app/services/shadow_experiment_genesis_service.py#L101), [pure verdict evaluator](../../backend/app/services/shadow_experiment_evaluator.py#L1); [benchmark tests](../../backend/tests/test_ofz_total_return_benchmark.py#L52).
- **E17:** [Task305 activation PLAN/APPLY](../../backend/app/services/shadow_scale_activation_service.py#L134), [repository read/audit/reconstruction](../../backend/app/services/shadow_scale_persistence_repository.py#L73), [benchmark genesis model](../../backend/app/models/shadow_experiment_benchmark.py#L8), [group model](../../backend/app/models/shadow_experiment_group.py#L8), [migration](../../backend/alembic/versions/202610040001_shadow_scale_experiment_persistence.py#L1); [one-commit seven-case and failure tests](../../backend/tests/test_shadow_scale_activation.py#L64).
- **E18:** [Task306A3 archive/projection authorization](../../backend/app/services/historical_evidence_execution_service.py#L25), [A4 checkpoint/finalize](../../backend/app/services/historical_discovery_checkpoint.py#L459), [Task307 source reader](../../backend/app/services/historical_discovery_coverage_reader.py#L1), [coverage audit](../../backend/app/services/historical_discovery_coverage_audit.py#L206); [source integrity tests](../../backend/tests/test_historical_discovery_coverage_audit.py#L1).
- **E19:** [Task306A temporal/classification helpers](../../backend/app/services/modern_historical_replay_readiness_audit_service.py#L67), [A1 transaction/linkage and limitations](../../backend/app/services/historical_replay_blocker_root_cause_audit_service.py#L53), [A2 monthly schedule](../../backend/app/services/multi_horizon_historical_backtestability_audit_service.py#L216), `ofz_readiness` line 226, [endpoint helper](../../backend/app/services/historical_endpoint_evidence.py#L1), [memory-safe reader](../../backend/app/services/historical_backtestability_evidence_reader.py#L1); [A2 tests](../../backend/tests/test_multi_horizon_historical_backtestability_audit.py#L1).
- **E20:** [Task308A source identity evaluation](../../backend/app/services/historical_identity_feasibility_service.py#L1), [B1 offline inventory/selection](../../backend/app/services/historical_identity_recovery_inventory_service.py#L26), `build` line 180; [B1 architecture/lineage contract](../architecture/TASK308B1_HISTORICAL_IDENTITY_RECOVERY_INVENTORY_AND_ACQUISITION_PLAN_V1.md#L1).
- **E21:** [B2 plan/evaluation](../../backend/app/services/historical_cashflow_feasibility_service.py#L1), [R1 forensics](../../backend/app/services/historical_cashflow_source_forensics_service.py#L1), [R2 qualification](../../backend/app/services/historical_cashflow_qualification_service.py#L64), [R3 row integrity](../../backend/app/services/historical_cashflow_row_integrity.py#L1), [R3 semantics](../../backend/app/services/historical_cashflow_semantics_service.py#L37); [row tests](../../backend/tests/test_historical_cashflow_row_integrity.py#L1), [semantics tests](../../backend/tests/test_historical_cashflow_semantics.py#L1).
- **E22:** [R4 decision and authority boundary](../research/TASK308B2R4_MOEX_MONETARY_AUTHORITY.md#L3), field matrix line 163, operator context line 238, closure requirements line 258. No new official-source requests in Task309.
- **E23:** Repository search at baseline for `ShadowDailyCycleService`, `ShadowExperimentComparisonService`, `ShadowScaleActivationService` under `backend/app` and `scripts` found their definitions, not an operational modern caller. Inspection of Task304A services, Task305 repository and the four scale models found genesis persistence/pure comparisons, not group/day benchmark/comparison persistence or scheduling. Absence claims are scoped to this modern chain, not every possible external operator system. See [single-run service](../../backend/app/services/shadow_daily_cycle_service.py#L52), [comparison service](../../backend/app/services/shadow_experiment_genesis_service.py#L101), [scale repository](../../backend/app/services/shadow_scale_persistence_repository.py#L1), [scale capabilities](../../backend/app/schemas/shadow_scale_experiment.py#L50).

## 12. Audit acceptance and safety

Local acceptance consists of citation/path/symbol/version checks, table/decision/blocker/roadmap consistency, two-document scope and working/staged whitespace checks. No local broad suite is required because runtime is unchanged; baseline successful CI is separately identified above. Delivery CI for the new docs-only commit is reported outside this baseline-pinned document after push, without rewriting this document to claim an unseen result.

```ini
TASK_ID=309
IMPLEMENTATION_CHANGE=DOCUMENTATION_ONLY
P0_BLOCKER_COUNT=3
P1_BLOCKER_COUNT=4
P2_BLOCKER_COUNT=2
RECOMMENDED_WORK_PACKAGE_COUNT=6
RECOMMENDED_NEXT_TASK=Task310_CURRENT_FORWARD_EXPERIMENT_READINESS_REPORT_FOUNDATION
LIVE_SOURCE_REQUESTS=0
PRODUCTION_DB_ACCESS=false
DB_MUTATION=false
DEPLOY=false
EXPERIMENT_ACTIVATION=false
HISTORICAL_BACKTEST_EXECUTED=false
PIT_READY=false
OFFICIAL_DAY0_STARTED=false
```
