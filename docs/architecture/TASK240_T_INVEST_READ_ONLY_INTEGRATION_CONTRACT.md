# Task240 — T-Invest Read-Only Integration Contract & Security Architecture

Status: architecture contract only

Target phase: `AUTONOMY_LEVEL=0 / OBSERVE_ONLY`

Trading state: `TRADING_DISABLED`

Write surface: `PHASE1_BROKER_WRITE_SURFACE=false`

## Evidence labels

Material statements use one of these labels:

- **OFFICIAL_DOCUMENTED_FACT** — stated by current official T-Bank documentation.
- **BONDRADAR_DESIGN_CHOICE** — a fail-closed architectural decision for BondRadar.
- **OPEN_QUESTION** — requires later implementation or live-validation evidence and must not be guessed.

Task240 does not connect to T-Invest, obtain or use a token, select an account,
persist broker data, change the database, or enable trading. It defines the
minimum safe boundary for later separately authorized tasks.

## 1. Executive decision

**BONDRADAR_DESIGN_CHOICE:** Phase 1 is a read-only ingestion and reconciliation
boundary for one explicitly selected T-Invest account. The intended account is
human-labelled `BR Strategy Account`, but that mutable name is never an account
selector or identity.

The architecture is:

```text
T-Invest read-only source
        ↓
BrokerReadClient (no write methods)
        ↓
append-only normalized BrokerSnapshot
        ↓
Instrument Identity Bridge
        ↓
Canonical PortfolioState
        ↓
analytics and reconciliation only
```

Phase 1 does not contain an execution client behind a disabled flag. It contains
no execution surface at all. A later trading component, if ever authorized,
must be a separate interface, dependency, service, permission set and task.

Architecture decisions:

```text
REAL_PORTFOLIO_REUSES_PAPER_PORTFOLIO=false
BROKER_API_RESPONSE_IS_DOMAIN_MODEL=false
PHASE1_HAS_ORDER_WRITE_METHODS=false
PHASE1_CAN_CANCEL_ORDERS=false
PHASE1_CAN_PLACE_ORDERS=false
PHASE1_REQUIRES_BROAD_UNIVERSE_READY=false
BROKER_PRICE_IS_SOLE_MARKET_TRUTH=false
TICKER_ONLY_MAPPING_ALLOWED=false
UNKNOWN_CURRENCY_DEFAULTS_TO_RUB=false
UNKNOWN_OPERATION_DROPPED=false
DEPOSIT_COUNTS_AS_RETURN=false
```

The current bond screener remains available as the **Candidate Discovery
Layer**. It is not the future primary portfolio home, and it is not removed in
this task.

## 2. Official T-Invest capability map

The source contracts below are current official T-Bank documentation. API
contracts may evolve, so Task242 must pin and test the exact protocol version it
uses.

| Need | Official capability | Required use in Phase 1 | Important limits or caveats |
|---|---|---|---|
| Account discovery | `UsersService.GetAccounts` | List accessible accounts and validate an explicitly selected account | An account-specific token may make only its account visible; a mutable account name is not identity |
| Dynamic limits | `UsersService.GetUserTariff` | Discover actual unary and stream limits | Actual returned limits and rate-limit metadata override documentation examples |
| Portfolio valuation | `OperationsService.GetPortfolio` | Broker totals, positions, quantities, average/current prices, NKD and broker P/L | This is broker valuation truth, not independent market truth |
| Exact positions | `OperationsService.GetPositions` | Cash, blocked cash, security balance, blocked quantity and identifiers | `limits_loading_in_progress` makes a snapshot incomplete |
| Operations | `OperationsService.GetOperationsByCursor` | Paginated operations and trades | Continue while `has_next`; default limit 100, maximum 1000; source operation IDs may change |
| Legacy operations | deprecated `GetOperations` | Do not use as the primary history path | Limited to the most recent 1,000 operations and has known limitations |
| Active/recent orders | `OrdersService.GetOrders` | Read active orders and supported same-day filtered states | Not a deep historical order archive; official FAQ documents same-day limitations |
| Known order status | `OrdersService.GetOrderState` | Read status and fills for a known order ID | May not return orders older than about one day; cannot establish full history alone |
| Instrument lookup | `InstrumentBy`, `BondBy`, `FindInstrument` | Resolve and preserve official identifiers | Search results are candidates; BondRadar mapping still requires exact conflict checks |
| Bond reference data | `Bonds` / `BondBy` | Lot, nominal, currency, identifiers and bond terms when needed | Bulk `Bonds` has a lower individual limit than ordinary instrument methods |
| Broker market evidence | `MarketDataService.GetLastPrices` | Optional additional broker-market observation | Does not replace broker portfolio valuation or independent MOEX analytics |
| Read streams | portfolio, positions, operations and order-state streams | Deferred optimization, not required for the first snapshot implementation | Initial implementation uses bounded unary reads and periodic reconciliation |
| Sandbox | sandbox endpoint and sandbox token | Fake transport is used before any sandbox work; sandbox is not production account truth | Sandbox accounts and state are isolated from real accounts |

**OFFICIAL_DOCUMENTED_FACT:** `GetPortfolio` exposes portfolio totals and
positions. A portfolio position may include quantity, average position price,
current price, current NKD, expected yield, blocked lots, `position_uid`,
`instrument_uid`, ticker and class code. `GetPositions` separately exposes cash,
blocked cash and security balances.

**OFFICIAL_DOCUMENTED_FACT:** `GetOperationsByCursor` provides cursor pagination
with `has_next` and `next_cursor`. Official guidance prefers it over deprecated
`GetOperations`, warns that operation IDs may change, and recommends a limit
greater than two.

**OFFICIAL_DOCUMENTED_FACT:** `GetOrders` and `GetOrderState` are read methods
inside a service that also exposes write methods. BondRadar must not import or
wrap the service wholesale; the Phase 1 adapter exposes only the selected read
calls.

**OFFICIAL_DOCUMENTED_FACT:** T-Invest supports gRPC and documented REST
endpoints. The transport choice is hidden behind the future read-only adapter.

**OFFICIAL_DOCUMENTED_FACT:** Production and sandbox use separate endpoints and
token types; a sandbox token is rejected by ordinary production methods. The
sandbox models isolated test accounts and is not evidence about the selected
real account. Task240 uses neither endpoint.

Official sources:

- [T-Invest API overview](https://developer.tbank.ru/invest/intro/intro)
- [Token types and access](https://developer.tbank.ru/invest/intro/intro/token)
- [Accounts service](https://developer.tbank.ru/invest/services/accounts/head-account)
- [Accounts gRPC contracts](https://developer.tbank.ru/invest/services/accounts/users)
- [Operations service](https://developer.tbank.ru/invest/services/operations/head-operations)
- [Operations gRPC contracts](https://developer.tbank.ru/invest/services/operations/methods)
- [Operations method caveats](https://developer.tbank.ru/invest/services/operations/operations_problems)
- [Orders gRPC contracts](https://developer.tbank.ru/invest/services/orders/methods)
- [Orders FAQ](https://developer.tbank.ru/invest/services/orders/faq_orders/)
- [Instrument identification](https://developer.tbank.ru/invest/intro/intro/faq_identification)
- [Instrument contracts](https://developer.tbank.ru/invest/services/instruments/methods)
- [Market data service](https://developer.tbank.ru/invest/services/quotes/head-marketdata)
- [API limits](https://developer.tbank.ru/invest/intro/intro/limits)
- [Recommended request deadlines](https://developer.tbank.ru/invest/intro/developer/deadlines)
- [HTTP error semantics](https://developer.tbank.ru/invest/intro/developer/error-codes/http_errors)
- [Sandbox semantics](https://developer.tbank.ru/invest/intro/developer/sandbox)

## 3. Security and token model

**OFFICIAL_DOCUMENTED_FACT:** T-Invest documents read-only, full-access,
transfer-access, sandbox and account-specific tokens. A read-only token cannot
place trading orders. An account-specific token can be issued with read-only
rights.

**OFFICIAL_DOCUMENTED_FACT:** Each `GetAccounts` account row includes an
`access_level` determined by the token, with explicit `READ_ONLY`, `FULL_ACCESS`,
`NO_ACCESS` and unspecified states.

```text
OFFICIAL_READ_ONLY_TOKEN_SCOPE=yes
```

**BONDRADAR_DESIGN_CHOICE:** Phase 1 requires an account-specific read-only
token. A full-access or transfer-access token fails client preflight. A token
with access to every account is not accepted when an account-specific token can
be issued for the selected account. Future startup must require exactly
`ACCOUNT_ACCESS_LEVEL_READ_ONLY`; full, none or unspecified access fails before
portfolio collection.

Security invariants:

- The token is supplied only by an environment-specific secret provider and is
  held only in process memory for the lifetime of the transport.
- The token value is never committed, stored in application persistence,
  returned by an API, emitted in logs, metrics, traces, exceptions, support
  bundles, snapshots, fixtures, documentation or LLM context.
- Separate development, sandbox and real-account credentials are mandatory.
- A future test suite uses synthetics and fake transports, never a copied real
  token.
- Transport exceptions are converted to fixed error categories. Authorization
  metadata and source exception text are not passed upward.
- Account IDs are sensitive identifiers. The exact ID is retained in restricted
  persistence for reconciliation, while logs and normal API responses use an
  internal portfolio ID or one-way redacted fingerprint.
- Secret values are never command-line arguments because process listings and
  shell history may retain them.
- Secret rotation invalidates live client instances and requires a fresh
  preflight; no token is cached in database rows.

Defence in depth is required even though the official token is read-only:

1. account-specific read-only broker permission;
2. read-only client interface;
3. explicit transport method allowlist;
4. no generic RPC invocation method;
5. dependency and import checks excluding write request types;
6. runtime `AUTONOMY_LEVEL=0` and `TRADING_DISABLED` assertions;
7. audit tests proving no write method is reachable.

## 4. NO WRITE CAPABILITY contract

The future interface is conceptual in Task240 and must be implemented only in
Task241/Task242:

```text
BrokerReadClient
  list_accounts()
  get_portfolio(account_ref)
  get_positions(account_ref)
  get_operations_page(account_ref, cursor, window)
  get_orders(account_ref, window/status filters)
  get_order_state(account_ref, known_order_ref)
  get_instrument(instrument_ref)
  get_bond(instrument_ref)
  get_last_prices(instrument_refs)      # optional read evidence
  get_user_tariff()                     # read limit discovery
```

The interface and its transport module must not expose, accept or dynamically
dispatch:

```text
post_order
post_order_async
replace_order
cancel_order
post_stop_order
cancel_stop_order
withdraw
pay_in
currency_transfer
margin mutation
generic_rpc(method_name, payload)
```

Reading active orders does not imply authority to cancel them. Reading a
withdrawal operation does not imply authority to initiate a withdrawal.

The future kill-switch state space is reserved as:

```text
TRADING_DISABLED
TRADING_MANUAL_ONLY
TRADING_GUARDED
TRADING_ENABLED
```

Phase 1 is always `TRADING_DISABLED`; its stronger property is that write code
is absent. No broker component may automatically increase autonomy or trading
state.

## 5. Broker identifiers

**OFFICIAL_DOCUMENTED_FACT:** T-Invest identifies assets, positions and trading
instruments separately. Official documentation names `asset_uid`,
`position_uid`, `instrument_uid`, FIGI, ticker and class code, and identifies UID
as the primary trading-instrument identifier.

BondRadar classification:

| Identifier | Role | Persistence and trust |
|---|---|---|
| `instrument_uid` | Primary broker trading-instrument identity | Required when supplied; exact, case-preserved source value; mapping key within broker namespace |
| `position_uid` | Primary broker position identity across trading modes when supplied | Preserved separately from instrument UID; used for holdings and position reconciliation |
| `asset_uid` | Broker asset grouping | Preserved as reference evidence; not substituted for instrument or position identity |
| FIGI | Legacy/global-market bridge identifier with documented coverage caveats | Preserved; exact-match candidate only; not the sole identity if UID conflicts |
| ISIN | Cross-source bond-security bridge | Preferred exact internal Bond candidate when present and unique; conflict blocks mapping |
| ticker | Human/venue shorthand | Never globally unique and never accepted alone |
| class code | Trading venue/mode discriminator | May be paired with ticker for lookup evidence; not a cross-source identity by itself |
| internal `Bond.id` | BondRadar domain identity | Never inferred from ticker alone; linked through a versioned bridge record |

```text
PRIMARY_BROKER_IDENTIFIER=instrument_uid
PRIMARY_BROKER_POSITION_IDENTIFIER=position_uid_when_supplied
CROSS_SOURCE_IDENTIFIERS=ISIN,FIGI,instrument_uid-backed confirmed bridge
```

All source identifiers are preserved. They are not collapsed into ISIN and are
not overwritten when a later source omits one.

## 6. BrokerSnapshot design

`BrokerSnapshot` is an append-only normalized source boundary, not a final SQL
schema. One snapshot represents one bounded collection attempt for one selected
account.

Required envelope:

- internal snapshot ID and deterministic normalized checksum;
- broker namespace and source contract/protocol version;
- exact restricted account ID plus public redacted fingerprint;
- account type, status and access level as observed;
- collection start, collection end, `observed_at`, `ingestion_at`, latest
  successful sync and per-call observation timestamps;
- component status for accounts, portfolio, positions, operations, orders and
  instruments;
- pagination start/end cursor, page count, `has_next` completion and requested
  operation window;
- data-quality state and warnings using fixed codes;
- no token, authorization metadata, arbitrary raw payload or exception text.

Normalized cash rows preserve currency, available quantity, blocked quantity
and source method. Unknown currency stays unknown; there is no RUB default.

Normalized position rows preserve:

- every supplied broker identifier;
- instrument type, ticker and class code as source strings;
- quantity in instruments and any separately reported blocked quantity;
- reported lot quantity only when explicitly supplied and not deprecated, or a
  derived lot count only when an exact current lot contract is available and
  the derivation is labelled;
- average position price, FIFO price if supplied, current price, NKD, expected
  yield/P&L and daily yield with original currency and numeric precision;
- source method and observation time for each value group;
- explicit unknowns instead of numeric or boolean defaults.

The snapshot also contains normalized open/recent order rows, fill rows and
recent operation rows. Each has source timestamps, type/status strings,
identifiers and an immutable normalized payload hash.

Broker responses remain transient by default. Persistence stores the normalized
source facts required for reconciliation, source identifiers, cursors,
timestamps, quality states and integrity hashes. A correction produces another
snapshot or operation version; it does not rewrite the historical snapshot.

Because the source calls are not one atomic broker transaction, each component
has its own observation timestamp. A cross-call change may yield
`INCOMPLETE_DATA` or `MISMATCH`, never a fabricated internally consistent state.

## 7. Genesis Snapshot

`GENESIS_SNAPSHOT` is the first explicitly accepted, complete and reconciled
snapshot of the dedicated account.

Expected experimental state:

```text
broker=T-Invest
human_label=BR Strategy Account
expected_starting_capital≈50000 RUB
expected_bond_positions=0
expected_strategy_positions=0
expected_unresolved_bond_positions=0
AUTONOMY_LEVEL=0
TRADING_DISABLED
```

The approximate RUB 50,000 is experiment configuration, not a software
invariant. Actual broker-reported cash and NAV are source truth. Genesis records:

- explicitly confirmed account identity and fingerprint;
- first complete observation window and acceptance timestamp;
- cash and blocked cash by currency;
- broker-reported NAV and its currency/basis;
- positions, unresolved positions and open orders as actually reported;
- the operation cursor/window establishing history continuity;
- data-quality and reconciliation status;
- an immutable genesis checksum.

Genesis acceptance is blocked by account ambiguity, incomplete pagination,
unresolved snapshot components, unexpected positions/open orders or a mismatch
requiring operator review. Those values are never forced to zero. If actual
cash differs from the expectation, the difference is reported and the operator
decides whether this is still the intended genesis account.

## 8. External cash-flow semantics

Portfolio performance must separate investment return from external flows.
The operation ledger preserves at least:

- deposit/input;
- withdrawal/output;
- security transfer in/out;
- internal account transfer;
- buy/sell and fills;
- coupon;
- full redemption and amortizing redemption;
- broker commission and service/other fees;
- taxes and tax corrections;
- broker corrections;
- unknown/new operation type.

A deposit of RUB 50,000 increases cash and contributed capital but contributes
zero investment return. A withdrawal reduces capital but is not a portfolio
loss. Fees and taxes are performance costs only according to a separately
versioned performance policy; source operation classification is retained
unchanged.

Future time-weighted or money-weighted performance uses the external-flow
ledger and exact source timestamps. Task240 chooses neither calculation method.
Unknown operations remain `UNKNOWN_OPERATION_TYPE`, are persisted, and mark
reconciliation incomplete until classified. They are never silently discarded.

## 9. Instrument identity bridge

Bridge records are versioned and retain source evidence and review state:

```text
broker namespace
account-independent instrument_uid
position_uid when supplied
FIGI / ISIN / ticker / class_code
candidate internal Bond.id
match method
match state
evidence checksum
confirmed_at / superseded_at
```

States:

- `MATCHED` — exactly one internally consistent mapping is accepted.
- `UNRESOLVED` — insufficient exact evidence or no internal Bond exists.
- `CONFLICT` — exact identifiers point to different internal instruments, a
  previously accepted UID changed incompatibly, or multiple Bonds match.

Resolution order:

1. reuse an active, previously confirmed broker UID bridge if its identifiers
   remain consistent;
2. evaluate exact position UID/instrument UID evidence;
3. evaluate a unique exact ISIN Bond candidate;
4. evaluate FIGI and ticker+class-code only as corroborating evidence;
5. block when identifiers disagree or uniqueness is not proven.

No fuzzy name matching and no ticker-only automatic mapping are allowed.
Unresolved instruments remain visible in broker-source views with their broker
identifiers and values. Their BondRadar enrichment is incomplete, and strategy
action is blocked.

## 10. Portfolio reconciliation

Reconciliation compares one complete BrokerSnapshot with a derived canonical
PortfolioState and independent source evidence.

Statuses:

- `RECONCILED` — exact identity, quantity, blocked quantity and cash checks pass;
  valuation components agree under exact source-compatible precision.
- `RECONCILED_WITH_TOLERANCE` — exact identity/quantity/cash checks pass and only
  documented valuation differences fall within an explicit versioned tolerance.
- `MISMATCH` — known comparable values disagree outside the accepted contract.
- `INCOMPLETE_DATA` — required component, page, mapping, enum interpretation or
  source value is missing/unknown.
- `STALE` — last complete snapshot exceeds the configured freshness contract.

Dimensions:

- selected account identity and access level;
- complete instrument/position set;
- quantity and blocked quantity;
- cash and blocked cash by currency;
- broker-reported portfolio total and position valuations;
- independently calculated value when price basis, currency and timestamps make
  comparison valid;
- NKD where supplied;
- complete operations window/cursor continuity;
- active/recent order and partial-fill state;
- unresolved mappings and source quality.

Identifiers, integer quantities and explicit currency codes are compared
exactly. Task240 defines no arbitrary monetary or freshness thresholds. Those
are versioned configuration established in Task247 using protocol precision and
live-validation evidence. A mismatch blocks future strategy/execution flow but
does not hide the broker snapshot.

## 11. Broker truth versus BondRadar analytics

| Category | Examples | Authority |
|---|---|---|
| Broker source truth | account, positions, quantities, cash, operations, order state, broker identifiers, broker-reported valuation/current price/NKD/P&L | BrokerSnapshot; immutable source-labelled values |
| BondRadar calculation | weights, portfolio YTM/duration, issuer and sector concentration, strategy score, risk decisions, benchmark excess return | Derived PortfolioState/analytics with method version |
| MOEX/external enrichment | independent market snapshot, terms, cashflows, issuer identity, financial evidence, ratings | Existing source-specific stores with provenance |

Calculated values must never be written into fields labelled as broker facts.
Broker data must not overwrite MOEX snapshots, instrument terms, issuer identity
or financial evidence.

## 12. Data-quality states

Every nullable or source-sensitive field can carry:

- `KNOWN` — explicitly supplied and contract-valid;
- `UNKNOWN` — absent, new enum or not supplied;
- `STALE` — last valid observation exceeds its configured freshness;
- `UNRELIABLE` — source reports a value but caveats or incomplete collection
  prevent normal use;
- `CONFLICT` — two expected-to-agree source bindings disagree.

Rules:

- unknown numeric values do not become zero;
- unknown booleans do not become false;
- unknown currency does not become RUB;
- deprecated fields are preserved only as labelled source evidence and do not
  silently override current fields;
- new enum values are retained as source strings and mapped to an explicit
  unknown state;
- stale values may be displayed with warnings but cannot satisfy a freshness
  gate for an actionable future decision.

This carries forward Task239's fail-closed normalization philosophy.

## 13. Operations history

Phase 1 uses `GetOperationsByCursor` with a bounded UTC interval and page size
greater than two and no more than the documented maximum. It follows
`next_cursor` until `has_next=false`. A page error makes the window incomplete;
earlier pages are not published as a complete history.

Persist for each normalized operation:

- account and internal ingestion identity;
- source ID and parent ID as mutable attributes, not sole primary keys;
- source cursor, operation/trade IDs, source type/status and raw enum string;
- instrument UID and other identifiers when supplied;
- event, execution and ingestion timestamps;
- payment, price, quantity, quantity remainder, commission/tax components and
  currencies without float conversion;
- child operations and fills when supplied;
- normalized source hash and version relationship to later corrections.

Deduplication must not depend on source operation ID alone. The future loader
uses overlapping windows and compares normalized operation/trade fingerprints.
Conflicting versions are retained and marked for reconciliation rather than one
being silently deleted.

Official documentation notes that corporate-action history may be incomplete
and suggests broker reports for exact information. Broker-report ingestion is a
deferred, separately designed read-only reconciliation enhancement, not part of
the minimum Phase 1 implementation.

## 14. Orders and order history

Phase 1 reads orders to detect manual trading and reconcile fills. It cannot
submit, replace or cancel an order.

- `GetOrders` supplies active orders and supported same-day filtered states.
- `GetOrderState` supplies the status and fills of a known order ID.
- Operations and trade rows provide longer-lived execution evidence.
- Partial fills preserve requested, executed, remaining and cancelled
  quantities plus every supplied fill ID/time/price.
- Cancellation is recorded as observed state only.
- Manual orders not yet reflected in operations remain part of the open-order
  reconciliation dimension.

Official documentation does not promise deep historical order retrieval from
these methods. BondRadar must not label the resulting order collection
complete beyond its explicit observation window. Missing or stale order state
produces `INCOMPLETE_DATA` or `STALE`.

## 15. Pricing source policy

```text
broker-reported valuation = reconciliation truth for what the broker displays
independent MOEX data      = analytics and market-source truth
```

Both are retained with observation time, currency, units and price basis.
Bond prices may be expressed as settlement-currency values or price points in
different T-Invest contracts; no conversion is allowed without an exact nominal,
currency and price-type contract.

`GetPortfolio.current_price` may be used to explain broker NAV. Optional
`GetLastPrices` data is another broker-origin market observation. Neither
silently overwrites the latest MOEX snapshot or becomes the sole market truth.
Small source/time differences are expected but are not automatically accepted
without the future reconciliation tolerance contract.

## 16. Rate limits, deadlines and retries

**OFFICIAL_DOCUMENTED_FACT:** T-Invest uses dynamic per-user/service limits.
Official documentation currently lists, among others, 100 account-service
requests/minute, 200 operations-service requests/minute, 200 ordinary
instrument-service requests/minute, 15/minute for bulk instrument-list methods,
and service/method-specific order limits. The official recommendation is no
more than 50 aggregate requests/second from one address. `GetUserTariff` and
rate-limit response metadata provide the current effective limits.

**BONDRADAR_DESIGN_CHOICE:** Actual `GetUserTariff` and response metadata win
over copied documentation numbers. The future client uses a centralized
per-service limiter and bounded concurrency.

Official minimum deadline guidance includes 300 ms for `GetAccounts` and
instrument lookup, 1,500 ms for `GetPortfolio`, 1,000 ms for `GetPositions`, and
500 ms for `GetOrders`. Future configured deadlines must not be below official
recommendations and may be higher for network conditions.

Retry policy for unary reads:

- at most three total attempts;
- exponential backoff with bounded jitter and a maximum 30-second delay;
- respect server reset/retry metadata when present;
- retry only 408, 429, 500, 503, 504 and corresponding transient gRPC states
  such as `DEADLINE_EXCEEDED`, `RESOURCE_EXHAUSTED`, `INTERNAL` and
  `UNAVAILABLE`;
- do not retry invalid argument, authentication, permission, not found,
  unsupported media/protocol, unimplemented or other deterministic failures;
- never retry indefinitely and never advance a cursor after a failed page;
- on exhaustion, return a sanitized fixed failure category and mark the
  component incomplete.

Although reads are logically idempotent, repeated pages can overlap or source
state can change. The snapshot boundary and operation deduplication rules remain
mandatory.

## 17. Timestamp semantics

Do not replace broker event time with ingestion time. Preserve:

- broker operation/order/fill timestamp;
- effective timestamp supplied by the source;
- component request start and response observation time;
- overall snapshot collection start/end;
- `observed_at` for the normalized snapshot;
- `ingestion_at` for local persistence;
- latest complete successful sync time;
- reconciliation evaluation time;
- mapping confirmation/supersession time;
- genesis observation and acceptance time.

All source timestamps retain their documented timezone semantics and are stored
as timezone-aware instants. Date-only fields remain date-only. A missing source
event time is `UNKNOWN`, not replaced by `ingestion_at`.

## 18. Existing repository reuse map

Repository evidence inspected for Task240 includes the Bond model, paper
portfolio models, `BondProductReadService`, MOEX universe/cashflow services,
portfolio construction, strategy backtest/experiments, pilot-universe gate and
current screener/live-paper routes.

| Existing component | New architecture role | Decision | Reason |
|---|---|---|---|
| Bond model | Internal bond/security anchor behind the identity bridge | ADAPT | Retain current market/security fields; broker identifiers belong in a separate bridge rather than being collapsed into Bond |
| Company / issuer identity | Canonical issuer enrichment | KEEP | Broker positions need existing issuer analytics after a safe Bond match |
| Financial evidence | Issuer analysis input | KEEP | Remains independent primary-source evidence, never broker truth |
| MOEX market snapshots | Independent analytics/market truth | KEEP | Supports comparison with broker valuation without overwrite |
| MOEX cashflows | Independent contractual cashflow evidence | KEEP | Supports analytics and reconciliation; not inferred from broker position defaults |
| Risk assessments | Analytics/risk-gate input | KEEP | Derived state remains separate from broker facts |
| Screener / BondDashboard | Candidate Discovery Layer | ADAPT | Retained, but no longer the future personal-portfolio home |
| BondProductReadService | Bond/issuer/market/risk read composition | KEEP | Useful enrichment boundary after exact instrument mapping; not a broker adapter |
| PortfolioConstructionService | Future source-neutral portfolio construction | ADAPT | Currently tied to ML runs/predictions; later consumes qualified candidates and PortfolioState |
| Strategy backtest | Research qualification | ADAPT | Preserve backtests and capital sweep; evolve toward versioned StrategyConfig and point-in-time inputs |
| Strategy experiment | Parameter/variant research | ADAPT | Retain as research track, independent of broker source truth |
| ML prediction stack | Optional candidate/alpha input | FREEZE | Must not directly drive real execution; retain existing behavior pending future alpha architecture |
| PaperPortfolio | Shadow/paper source only | FREEZE | Coupled to initial capital, ML runs, allocation weights and synthetic rebalance lifecycle; not real broker storage |
| Paper live cycle | Existing shadow execution simulation | FREEZE | No reuse as broker ingestion or reconciliation |
| Paper schedules | Existing synthetic cadence | FREEZE | Broker sync cadence requires a separate read-only scheduler and safety contract |
| Live-paper UI | Explicit shadow-portfolio presentation | ADAPT | Keep current UI; later distinguish it from a separate My Portfolio view |
| Data-quality/readiness services | Fail-closed eligibility and quality vocabulary | ADAPT | Reuse epistemic discipline and blockers with broker-specific states |
| Deployment / CI | Future secret/no-write enforcement | ADAPT | Later add isolated secret handling, method allowlist tests and import guards; no Task240 config changes |
| Obsolete duplicate presentation after replacement | None | REMOVE_LATER | Removal requires a later explicit task after replacement; Task240 deletes nothing |

## 19. PaperPortfolio decision

```text
REAL_PORTFOLIO_REUSES_PAPER_PORTFOLIO=false
```

Repository evidence shows that `PaperPortfolio` requires `initial_capital`,
tracks a selected ML model run and rebalance timestamps. Its positions store
allocation weights/amounts, predictions and model-run provenance. Its
transactions encode synthetic allocation changes, period returns and rebalance
fees. Its snapshots store simulated cumulative/period returns.

Those are simulation semantics, not broker-source semantics. Reusing the model
would mislabel allocations as holdings, synthetic transactions as broker
operations and calculated values as source truth.

Future relationship:

```text
BrokerPortfolioSource ──→ PortfolioState-compatible read view
Shadow/PaperPortfolio ──→ PortfolioState-compatible read view
```

The sources do not share persistence or mutation lifecycle. Shared analytics
consume the common read view with explicit source type.

## 20. Interaction with the Research Universe

Two tracks remain independent:

```text
Track A — Personal Portfolio
T-Invest read-only → BrokerSnapshot → identity bridge → PortfolioState

Track B — Research Foundation
broad historical universe → security master → identity → point-in-time market,
cashflow and financial history → research dataset
```

The broad universe is required for candidate discovery, relative value,
backtests and survivorship-aware research. It must not be narrowed to current
holdings. Conversely, correct display and reconciliation of the selected real
account must not wait for every market security to be repaired. An unresolved
held instrument is shown as broker truth with incomplete analytics.

## 21. Interaction with future Strategy and Risk

The future decision flow remains:

```text
Risk Gate
   ↓
Alpha / Relative Value
   ↓
Portfolio Construction
   ↓
Pre-trade Risk
   ↓
BUY / HOLD / REDUCE / EXIT / NO_ACTION
```

No single score may execute a trade. The Risk Engine may hard-block unsupported,
stale, unresolved or conflicting instruments. Alpha operates only among
acceptable candidates. Task240 provides broker truth and reconciliation state
only; it implements none of these engines or decisions.

Autonomy roadmap is reserved as:

```text
0 OBSERVE_ONLY
1 ADVISORY
2 MANUAL_APPROVAL
3 GUARDED_AUTOMATION
4 LIMITED_AUTOPILOT
5 FULL_STRATEGY_AUTOMATION
```

No component may raise the autonomy level automatically. Every level change
requires a separate explicit gate and authorization.

## 22. Interaction with the 90-day Shadow Test

Qualification remains:

```text
historical research universe
    ↓
StrategyConfig v1
    ↓
valid point-in-time backtest
    ↓
OOS / walk-forward qualification
    ↓
90-day Shadow Test
    ↓
real BR Strategy Account
```

The shadow test remains mandatory. Its intended initial NAV is RUB 50,000 to
match the real experiment scale, while research/backtests must support capital
sweeps such as 50k, 100k, 250k, 500k, 1m and 5m without hardcoding them in
Task240.

Phase 1 contributes reliable genesis, holdings, cash flows, manual-order and
broker valuation observations. It does not qualify a strategy or start the
shadow test.

## 23. Threat model and failure modes

| Threat or failure | Required safe behavior |
|---|---|
| Token leak | Revoke/rotate token, stop sync, sanitize all outputs, treat incident as security failure; token is never persisted |
| Token logged in exception | Fixed error categories only; exception/metadata redaction before logging; fail tests on token-like output |
| Wrong account selected | No automatic selection; require explicit ID confirmation and account-specific token; block genesis |
| Account ID hardcoded | Account identity comes from confirmed restricted configuration/persistence, never source code |
| User creates another account | `GetAccounts` may show it; selected ID remains unchanged; ambiguity or disappearance blocks sync |
| Stale snapshot | Display stale warning; actionable future decisions requiring freshness are blocked |
| Pagination truncation | Snapshot component `INCOMPLETE_DATA`; no partial history labelled complete |
| Duplicated operation ingestion | Overlap-aware fingerprints and versions; do not double-count cash flows |
| Missing operations | Continuity gap makes reconciliation incomplete; do not infer zero activity |
| Unknown operation type | Preserve source enum/data as `UNKNOWN_OPERATION_TYPE`; block complete accounting |
| Instrument mapped to wrong Bond | Versioned exact bridge, conflict checks and review; conflict blocks analytics/action |
| Ticker collision | Ticker alone never maps; require stronger exact identifiers |
| ISIN unavailable | Retain broker UID position and show unresolved; do not fuzzy-map |
| FIGI/UID mismatch | Mark `CONFLICT`, preserve both, stop mapping-dependent analytics |
| Partial API outage | Publish no complete snapshot; retain last successful snapshot as stale with component error codes |
| Rate limiting | Respect dynamic limits/reset metadata and bounded backoff; exhaustion yields incomplete sync |
| Broker value differs from independent calculation | Preserve both; reconcile with explicit basis/timestamps; mismatch blocks downstream action |
| RUB assumed for unknown currency | Forbidden; currency stays unknown and currency-dependent totals remain incomplete |
| Deposit counted as profit | External-flow ledger excludes deposits from investment return |
| Withdrawal counted as loss | External-flow ledger excludes withdrawals from investment return |
| Broker correction | Preserve correction/version and recompute reconciliation; do not rewrite source history silently |
| Manual trade missed by BondRadar | Operations/orders continuity mismatch; snapshot/reconciliation incomplete until recovered |
| Stale open order state | Mark order component stale and block action-sensitive state |
| Partial fill | Preserve requested/executed/remaining quantities and fills; do not treat as fully executed |
| New broker enum value | Preserve source string, classify unknown, require mapping update; do not use enum default |
| Source response changes during multi-call snapshot | Preserve per-call times and return mismatch/incomplete state, not fabricated atomicity |
| Raw payload contains unexpected sensitive data | Raw response remains transient and is never dumped to logs, API or general-purpose JSON storage |
| Full-access token supplied accidentally | Capability/preflight rejection before any broker call; no fallback to full access |

Fail-closed summary:

- Unknown mapping: display broker truth, mark analytics incomplete, block strategy.
- Stale snapshot: display with warning, block freshness-dependent action.
- Unknown operation: preserve it, mark reconciliation incomplete.
- Unknown currency: no RUB fallback.
- Account ambiguity: no automatic selection.
- Portfolio mismatch: block future strategy/execution pipeline.

## 24. Open questions

The following questions are deliberately deferred and must not be guessed:

1. **OPEN_QUESTION:** Which official Python transport is selected in Task242 —
   official SDK or direct generated gRPC — after checking dependency, maintenance
   and method-allowlist properties?
2. **OPEN_QUESTION:** What snapshot freshness thresholds are appropriate for
   display, analysis and future actionable decisions?
3. **OPEN_QUESTION:** What monetary tolerances are justified for broker versus
   MOEX valuation after observing price basis, timestamp skew and precision?
4. **OPEN_QUESTION:** What historical start/window is available for the newly
   created account, and when is broker-report reconciliation necessary?
5. **OPEN_QUESTION:** Which stable normalized operation fingerprint best handles
   official source ID changes without merging distinct corrections?
6. **OPEN_QUESTION:** Can read-only order methods return every required state for
   an account-specific token in the chosen transport version? Validate with
   synthetic contract tests first and a separately authorized non-mutating smoke
   only later.
7. **OPEN_QUESTION:** What encrypted-at-rest mechanism stores the exact selected
   broker account ID in the deployed environment?
8. **OPEN_QUESTION:** How should multi-currency NAV be presented until an
   independently sourced FX contract exists?

None of these questions requires a token or live call in Task240, and none
weakens the no-write boundary.

## 25. Recommended implementation tasks

Implement only through separately authorized tasks, in this order:

1. **Task241 — Read-Only Broker Domain & Fake Transport**

   Define strict source-neutral types, quality states, the narrow read interface,
   fake transport and static/import guards proving no write surface.
2. **Task242 — T-Invest Read Client**

   Implement the allowlisted read adapter, secret boundary, sanitized errors,
   dynamic limits, deadlines, bounded retries and fully synthetic tests.
3. **Task243 — Account Discovery and Explicit Selection**

   List accessible accounts, require operator confirmation, bind the selected
   account securely and reject ambiguity or mutable-name selection.
4. **Task244 — Genesis Broker Snapshot**

   Collect and atomically persist the first complete append-only snapshot and
   external-flow baseline; do not assume RUB 50,000 or zero positions.
5. **Task245 — Instrument Identity Bridge**

   Preserve all broker identifiers and implement exact `MATCHED`, `UNRESOLVED`
   and `CONFLICT` mapping with no fuzzy/ticker-only acceptance.
6. **Task246 — Canonical Portfolio State**

   Assemble a source-labelled portfolio read model shared by real and shadow
   analytics without sharing source persistence.
7. **Task247 — Reconciliation Gate**

   Validate account, positions, cash, valuation, NKD, operations and orders;
   establish evidence-backed freshness/tolerance configuration.
8. **Task248 — Minimal My Portfolio API**

   Expose sanitized broker-backed state and reconciliation only; never expose
   token or broker write controls.
9. **Task249 — Minimal My Portfolio UI**

   Present holdings, cash, quality and reconciliation separately from the
   shadow/paper UI; include no trade controls.

Every task retains `AUTONOMY_LEVEL=0`, `TRADING_DISABLED`, and
`PHASE1_BROKER_WRITE_SURFACE=false` unless a later explicit architecture and
authorization task supersedes the phase. Task240 does not authorize Task241 or
any downstream implementation.

---

## Task240 completion boundary

```text
OFFICIAL_T_INVEST_DOCS_REVIEWED=true
ACCOUNT_API_IDENTIFIED=true
PORTFOLIO_API_IDENTIFIED=true
POSITIONS_API_IDENTIFIED=true
OPERATIONS_API_IDENTIFIED=true
ORDERS_READ_API_IDENTIFIED=true
INSTRUMENT_API_IDENTIFIED=true
TOKEN_SECURITY_CONTRACT_DEFINED=true
OFFICIAL_READ_ONLY_TOKEN_SCOPE=yes
PHASE1_BROKER_WRITE_SURFACE=false
BROKER_SNAPSHOT_CONTRACT=true
GENESIS_SNAPSHOT_CONTRACT=true
EXTERNAL_CASHFLOW_ACCOUNTING=true
INSTRUMENT_BRIDGE_CONTRACT=true
RECONCILIATION_CONTRACT=true
DATA_QUALITY_CONTRACT=true
RESEARCH_UNIVERSE_TRACK_PRESERVED=true
SHADOW_90_DAY_TEST_PRESERVED=true
REPOSITORY_COMPONENT_MATRIX_COMPLETE=true
LIVE_BROKER_CALL_PERFORMED=false
BROKER_TOKEN_USED=false
```

Task240 PASS means only that the architecture is concrete enough to begin a
separately approved Task241. It does not mean T-Invest is connected, an account
or token exists, genesis is captured, the portfolio is ingested, a strategy is
qualified, or any execution is possible.
