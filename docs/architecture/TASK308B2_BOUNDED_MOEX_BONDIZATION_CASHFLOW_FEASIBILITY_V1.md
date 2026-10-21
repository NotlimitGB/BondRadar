# Task308B2 — Bounded MOEX bondization cashflow feasibility

## 1. Reviewed source contracts

This development package implements a source-feasibility experiment, not a cashflow import. The fixed candidate route is `https://iss.moex.com/iss/statistics/engines/stock/markets/bonds/bondization/{raw_secid}.json`. The four independent tables are coupons, amortizations, redemptions and offers. Endpoint behavior, cursor availability, amount units and actual data coverage remain hypotheses until a separately authorized operator probe. Synthetic transport tests do not establish production source support.

## 2. Actual reuse

Task308B1 `verify_published` verifies its complete five-artifact publication. Its existing optional `verify_inputs` performs full Task308A offline replay when that complete optional input set is supplied to the Python API. Task308A supplies the canonical encoder, strict JSON/model reader, safe local paths, immutable file installation, writer lock and capacity checks. Existing `isin_valid`, canonical OFZ identity, currency canonicalization, Numeric storage-fit checks and the historical canonical serializer are reused. The source client's static route builder is reused, but neither its fetch/pagination/normalization code nor legacy cashflow sync is called. No second identity resolver or ISIN checksum implementation is introduced.

## 3. Upstream trust boundary

An externally pinned B1 report SHA is mandatory. All five B1 artifacts, inventory byte hash, semantic roadmap SHA, its independent file SHA and nine upstream provenance hashes are checked/frozen. Current binding also requires exact SECID and ISIN, source-confirmed complete results, request/response hashes and UTC observation times. Checksums do not establish a binding. Inventory streams one row at a time; only the previously probed confirmed pool is retained, bounded by Task308A's twelve identities. Original SU SECIDs and recovered RU ISINs remain separate. MVZ220004019, unprobed, conflicting and review-required rows are excluded.

The B1 operator context supplied with the work package describes 10,671 SECIDs, eleven confirmed bindings and nine observed confirmed identities. These are context, not runtime constants or independently verified production measurements. No production artifacts were read during development.

## 4. Cohort policy

PLAN requires an exact integer size from one to six. Restrictions must be unique eligible raw SECIDs; insufficient eligible coverage blocks the plan. An observed canonical OFZ is selected first if available within restrictions. Subsequent deterministic priorities are historical observation, actual-trade indicator, usable-price/NKD indicator, new board/stratum coverage, then raw SECID. Output sorts by SECID while retaining selection order and reasons. Indicators are independent; joint execution coverage remains NOT_ESTABLISHED. No investability or fixed/floating/amortizing classification is inferred.

## 5. Four-table query and authorization

Every proposed SECID has all four planned table partitions. AUTHORIZE is offline and requires the reviewed plan SHA, explicit proposed SECID subset, nonempty table subset, an explicit limits object, a private existing evidence directory and confirmation. Authorization binds the destination and limits but is not a digital signature. PROBE additionally requires explicit network confirmation. Unrequested tables remain NOT_REQUESTED. No request is made by PLAN, AUTHORIZE or EVALUATE.

The keyword-only Python API is `HistoricalCashflowFeasibilityService.plan(...)` / `.evaluate(...)` and `HistoricalCashflowProbeService.authorize(...)` / `.probe(...)`. Optional full replay accepts the existing Task308B1 `verify_inputs` argument mapping. The runner exposes the five optional Task307/308A paths as an all-or-nothing set, with an optional Task306A4 manifest; partial optional input is refused.

## 6. Bounded pagination

Requests use `iss.meta=on`, `start=offset`, `limit=100`, `iss.only=table,table.cursor` and `table.start=offset`. COMPLETE requires explicit valid INDEX/TOTAL/PAGESIZE metadata. Integers are exact, index equals offset, total/size stay stable, size is 1..100, and row counts equal min(size,total-index). Next offset is index+size. No short-page or empty-page terminator is used. Missing/invalid cursor, changing totals/sizes or repeated full-page content prevent COMPLETE. Empty completion is supported only by proof such as TOTAL=0. Schema-valid rows remain preserved even if completion metadata fails. Repeated-page detection ignores row ordering but never deduplicates event occurrences.

## 7. Source identity

Requested SECID, accepted current ISIN context and actually returned identifiers are independent fields. Missing returned ISIN stays null. One matching returned identifier can corroborate the prior binding without inventing its counterpart. No returned identifiers means REQUEST_CONTEXT_ONLY and no source identity assertion. Contradictions and conflicting simultaneous aliases invalidate the affected occurrence; no substitution or normalization resolves them.

## 8. Amount, currency and units

Amounts use exact Decimal and explicit per-table aliases. Simultaneously supplied nonblank aliases must agree. Boolean, malformed, negative and nonfinite amounts are invalid. Finite amounts outside Numeric(18,6) remain exact but are EVENT_INVALID/STORAGE_UNREPRESENTABLE. No rounding, percentage conversion or nominal/currency default is allowed. Supplied currency uses the existing currency parser. Monetary amount, currency and unit authority are independent. Bare VALUE and a source string such as `per bond` do not establish reviewed unit authority. VALUEPRC and offer PRICE remain separate source context and are never monetary amounts by inference.

## 9. Events, duplicates and revisions

Each row occurrence retains table, offset, row index, canonical row SHA and byte-exact page SHA. Repeated identical rows are grouped diagnostically, never removed. Same SECID/table/date with different amount/currency/unit forms a conflicting event group. Source corrections are not authoritative replacements. Per-partition counts distinguish attempts, pages, raw/valid/invalid, dated, amount, currency, unit, duplicate and conflicting occurrences. Multiple source revisions remain observable.

## 10. Scheduled versus actual

Valid rows are SCHEDULED_OBSERVATION. No row becomes ACTUAL_PAYMENT_CONFIRMED. Dates are parsed only from supplied explicit date aliases; record/publication/effective metadata remain separate from retrieval/observation timestamps. Offers are observations, not automatically exercised redemptions. Economic structure and actual payment proof require independent review.

## 11. PIT and readiness

Source feasibility can be CASHFLOW_SOURCE_FEASIBLE_WITH_LIMITATIONS; economic qualification stays UNPROVEN or CONFLICTING. Source unit and publication authority remain unproven. Historical publication, historical decision availability, PIT, backtest, total-return execution and executable-market readiness stay false. Canonical OFZ classification establishes only identifier family. No coupon scheduling inference, pricing, return, duration or profitability calculations are performed.

## 12. Limits and journal

Frozen ceilings: six SECIDs, four tables, twenty-four initial partitions, seventy-two attempts, forty-eight accepted pages, 8 MiB per response, 64 MiB total response bytes, 300 seconds including downtime, 128 MiB evidence and 8 GiB free-space reserve. Requests are sequential with at least 0.5 seconds between starts. Up to three attempts per page retry only known transient HTTP/transport failures; waits use 0.25/0.50 seconds, rate limiting and bounded numeric Retry-After up to sixty seconds. No redirects, credentials, arbitrary URLs, environment trust or disabled TLS verification are supported.

An immutable intent precedes every request. Immutable receipts bind intent, predecessor, request, status and response SHA. Schema-valid bodies are content-addressed. Malformed/unsafe responses retain a sanitized failure and byte hash only; their body is not published. State records the receipt chain, pending attempt and fixed UTC deadline. Unknown pending/unreconciled outcomes block automatic retry; operator reconciliation is required. Accepted pages are never fetched again. Global budget stop is durable. Offline replay checks hashes, cursor facts, request scope, time/rate evidence and chain state before evaluation. HTTP exception text, headers and cookies are never persisted. The decoded-body guard conservatively refuses a chunk that could exceed budget; it does not measure TCP buffering.

## 13. Operator steps A–F (templates only)

Use a reviewed pinned checkout and existing reviewed image/dependencies. Offline steps run with network disabled. Future containers should be read-only, non-root, `--cap-drop ALL --security-opt no-new-privileges`, 1 GiB RAM / 1 CPU, read-only upstream mount and separate private report/evidence mounts. Do not expose an application DB or token. These commands were not executed against real evidence.

A. Separately authorized offline PLAN:

```text
python scripts/historical_cashflow_feasibility.py --mode PLAN --b1-root /input/b1 --expected-b1-report-sha256 <pinned-b1-sha> --pilot-size 3 --output /reports/plan
```

B. Inspect the complete JSON/summary, exact cohort/order, SU→RU mappings, lineage, exclusions, all four partitions and frozen budgets. Independently verify hashes using the existing canonical serializer; do not equate a printed SHA with operator approval.

C. Separately reviewed offline AUTHORIZE (limits.json contains the explicit ProbeLimits values):

```text
python scripts/historical_cashflow_feasibility.py --mode AUTHORIZE --plan /reports/plan/task308b2-plan.json --reviewed-plan-sha256 <reviewed-plan-sha> --secids <approved-raw-secids> --tables coupons amortizations redemptions offers --limits /review/limits.json --evidence-root /evidence/private --output /review/b2-authorization.json --confirm
```

D. Live PROBE requires a new independent operator decision and network permission. Do not chain it to A–C:

```text
python scripts/historical_cashflow_feasibility.py --mode PROBE --plan /reports/plan/task308b2-plan.json --authorization /review/b2-authorization.json --evidence-root /evidence/private --confirm-network
```

E. Offline EVALUATE with evidence mounted read-only and a separate output directory:

```text
python scripts/historical_cashflow_feasibility.py --mode EVALUATE --plan /reports/plan/task308b2-plan.json --authorization /review/b2-authorization.json --evidence-root /evidence/private --output /reports/evaluation
```

F. Inspect replayed partition completeness, preserved identifiers, unit/amount diagnostics, duplicate/conflicting groups and all false downstream flags. Verify canonical report SHA and summary. Identical publication is idempotent; conflicting output is refused. JSON is the completion artifact installed last under an exclusive lock/private staging. Combined JSON/summary is limited to 32 MiB without truncation. Controlled publication failure removes only newly installed files.

## 14. Verification

Two focused modules use synthetic B1 evidence and HTTPX MockTransport. They cover pinned lineage, both identity forms/SU24020, restrictions, exact amounts, aliases, empty/no-cursor behavior, cursor pagination/failures, duplicates, retries, immutable receipts, unknown intents, budgets, downtime, offline replay, output conflicts and static no-DB/no-sync boundaries. A generated 10,671-SECID inventory yielded a planning tracemalloc peak of 33,666,025 bytes (about 32.1 MiB), including the reused verifier's bounded read buffer; the regression guard is 64 MiB. This measures Python allocations during that synthetic planning phase, not process RSS or production memory. Relevant Task308A/B1, historical source and cashflow parsing tests provide regressions. Full backend regression is deferred to exact-commit CI.

## 15. Acceptance boundaries

Acceptance means the bounded mechanisms and four source-contract paths are implemented and synthetically verified. Real endpoint feasibility, unit semantics and historical evidence remain unmeasured. No application model, migration, existing source path, fingerprint dependency, deployment or production data changes are authorized. Output is diagnostic, never an import or authorization for downstream execution.

## 16. Handoff

Independent code/SHA review and green CI precede a separately authorized offline PLAN. Live pilot and any acquisition expansion require separate operator decisions. No ACQUIRE/APPLY, production access, backtest, profitability assessment or official Day 0 is unlocked by this package.
