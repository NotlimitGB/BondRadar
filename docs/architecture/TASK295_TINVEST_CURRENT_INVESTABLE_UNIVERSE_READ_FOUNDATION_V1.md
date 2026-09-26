# Task295 — T-Invest Current Investable Universe Read Foundation v1

## Purpose and source boundary

Task295 introduces a read-only source adapter for the current T-Invest bond and
DFA instrument lists. The current actionable candidate universe is bounded by
T-Invest availability, while the existing broad BondRadar research universe
remains intact and continues to provide independent history and context.

T-Invest is the source of broker/API availability. MOEX remains the source for
independent bond-market analytics; CBR, rating agencies, and existing accepted
credit evidence remain the credit sources; OIS and issue documentation remain
the legal issue-term sources. This task does not bridge source instruments to
BondRadar `Bond` rows.

## Read methods and request contracts

The client exposes exactly two broker read methods:

| Method | REST route | Request body | Meaning |
| --- | --- | --- | --- |
| `list_base_bonds()` | `InstrumentsService/Bonds` | `{"instrumentStatus":"INSTRUMENT_STATUS_BASE"}` | Current T-Invest base bond list |
| `list_dfas()` | `InstrumentsService/Dfas` | `{}` | Current discovered DFA list |

`INSTRUMENT_STATUS_UNSPECIFIED` and `INSTRUMENT_STATUS_ALL` are not used for
the actionable bond universe. The Dfas request has no status filter; discovery
alone does not establish DFA execution availability. The REST operations use
HTTP POST as required by the documented proxy, while remaining source-level
reads. Each client method returns a typed response envelope that records its
exact method and request contract; the reducer rejects an envelope that does
not prove `Bonds(BASE)` or the empty `Dfas` request.

## Identity and normalized source evidence

UID is the primary T-Invest identity and is required to be a nonblank string.
The normalized contracts preserve supplied UID, position UID, asset UID, FIGI,
ISIN, ticker, and class code exactly; they do not trim values, match on names,
or infer identity from ticker. FIGI is retained only as a legacy identifier.
Duplicate UIDs within either response fail the whole reduction with
`SOURCE_IDENTITY_CONFLICT`; no duplicate is silently selected or overwritten.

Normalized bond and DFA rows retain separate source-universe classifications
and independent execution flags. Bond `required_tests` distinguishes a missing
field (`NOT_SUPPLIED`) from an explicitly empty source list (`SOURCE_EMPTY`).
The Dfas contract does not supply `required_tests`; its normalized value stays
not supplied rather than becoming an empty list. Source row fields are retained
for audit and future exact bridging.

DFA nominal, yield, coupon, frequency, payment date, ACI, forecast yield, and
basic assets are preserved in their source structures. Task295 does not
convert quotation values, derive coupon rates, or calculate financial
features. In particular, forecast yield is not yield to maturity, and coupon
value is not treated as a coupon rate.

## Availability and qualification

`listed_in_source_universe`, API-trade, buy, sell, qualification, and
`required_tests` remain separate source facts. Only actual JSON booleans are
accepted when a boolean field is supplied. Omitted optional flags remain
unknown; strings, numbers, and null boolean values are invalid rather than
coerced to true or false.

The source-level classification is `API_BUY_AVAILABLE` when API-trade and buy
are true, `API_VISIBLE_NOT_BUYABLE` when API-trade is true and buy is false,
and `API_TRADE_UNAVAILABLE` when API-trade is false. If required flags are not
supplied, classification remains unknown. A qualification restriction is
orthogonal and never changes that classification. The owner’s personal
qualification is not known or inferred.

## Validation, failures, and request safety

The pure reducer requires an object response with an `instruments` array and
object rows. Missing or blank UIDs, duplicate UIDs, malformed supplied
booleans, malformed required-test lists, malformed basic-assets lists, and
wrong response shapes fail closed without returning a partial universe. Rows
are sorted by UID for deterministic output.

The client requires a bearer token passed explicitly to its constructor. It
does not read environment variables, log credentials, expose headers, or
include response bodies or underlying HTTP exceptions in errors. Sanitized
failure categories distinguish authentication, permission, rate limit,
timeout, transient source errors, invalid source responses, source identity
conflicts, and rejected requests. Retry is deferred: each method makes one
request attempt. Tests use only synthetic credentials and `httpx.MockTransport`.

## Persistence, history, and capability boundary

This is a current source observation only. It does not establish historical
listing availability, survivorship-safe history, or point-in-time readiness.
There is no database persistence, migration, API route, account or portfolio
access, production token wiring, live T-Invest request, order method, strategy
integration, M3-A change, or deployment.

```text
TASK_ID=Task295
TINVEST_CURRENT_UNIVERSE_FOUNDATION=true
TINVEST_CURRENT_UNIVERSE_READY=true
TINVEST_BONDS_BASE_REQUEST_READY=true
TINVEST_DFAS_REQUEST_READY=true
BONDS_ALL_REQUESTED=false
BOND_UNIVERSE_METHOD=Bonds
BOND_UNIVERSE_STATUS=INSTRUMENT_STATUS_BASE
DFA_UNIVERSE_METHOD=Dfas
DFA_HAS_INSTRUMENT_STATUS_FILTER=false
BOND_SOURCE_NORMALIZATION_READY=true
DFA_SOURCE_NORMALIZATION_READY=true
UID_REQUIRED=true
DUPLICATE_UID_FAIL_CLOSED=true
BOND_AVAILABILITY_FLAGS_READY=true
DFA_AVAILABILITY_FLAGS_READY=true
QUALIFICATION_DIMENSION_PRESERVED=true
PERSONAL_QUALIFICATION_RESOLVED=false
RESEARCH_UNIVERSE_PRESERVED=true
LIVE_CANDIDATE_UNIVERSE_TINVEST_BOUND=true
RESEARCH_UNIVERSE_SOURCE=BROAD_INDEPENDENT_MARKET_SOURCE_EVIDENCE
CURRENT_ACTIONABLE_UNIVERSE_SOURCE=T_INVEST
FUTURE_EXECUTION_VENUE=T_INVEST
PRIMARY_TINVEST_INSTRUMENT_ID=uid
FIGI_PRIMARY_ID=false
TICKER_ONLY_IDENTITY=false
BROKER_AVAILABILITY_TRUTH=T_INVEST
BOND_MARKET_ANALYTICS_TRUTH=MOEX
DFA_ECONOMIC_VALUES_REINTERPRETED=false
DFA_ECONOMIC_NUMERIC_NORMALIZATION=false
STATIC_RATE_LIMIT_ASSUMED=false
RETRIES_IMPLEMENTED=false
TINVEST_HISTORICAL_UNIVERSE_READY=false
TINVEST_UNIVERSE_PIT_READY=false
DATABASE_PERSISTENCE=false
DATABASE_MIGRATION=false
BROKER_WRITE_SURFACE=false
LIVE_TINVEST_REQUEST=false
REAL_TOKEN_USED=false
ACCOUNT_API_USED=false
PORTFOLIO_API_USED=false
ORDER_API_USED=false
TOKEN_LOGGING=false
GENERIC_RPC=false
MOEX_MARKET_LAYER_CHANGED=false
M3_A_CHANGED=false
STRATEGY_CHANGED=false
RISK_ENGINE_CHANGED=false
PRODUCTION_ACCESSED=false
SHADOW_STARTED=false
TASK296_STARTED=false
```

## Verification and handoff

Focused synthetic tests cover exact methods and request bodies, strict response
normalization, UID conflicts, availability and qualification separation,
source-value preservation, sanitized failures, and the no-write surface. The
handoff is independent Task295 review before any deployment or live read-only
probe. Deployment, probing, universe persistence, and Task296 require separate
authorization and are not started here.
