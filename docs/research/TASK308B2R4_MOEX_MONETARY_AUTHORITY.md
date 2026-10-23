# Task308B2R4 — MOEX Bondization Monetary Field Authority Audit

## 1. Decision and scope

**Research date:** 2026-10-23 (Europe/Moscow).
**Base implementation:** `4ce4cb953e8a5cc2c88d1316bc9d55ef0e613c93`.
**Decision:** `DOCUMENTATION_GAPS_REMAIN`.
**Source authority:** `PARTIAL`, with narrower documented claims identified below.

Official calendar assets establish table-specific labels, nominal-currency
presentation, and the displayed RUB-equivalent rate-date rule. The inspected
materials do **not explicitly establish the denominator of `coupons.value` or
`amortizations.value` as one bond rather than one lot or an issue-level amount**.
That unresolved claim prevents an affirmative scheduled-cashflow design gate,
even under the agreed RUB-only scope. A natural interpretation or numerical
agreement is insufficient under this task's proof standard.

RUB-only READY would have been permissible if the applicable units were proved;
missing FX methodology alone would not have vetoed that restricted design.
Here the RUB amount denominator itself remains unproved. This is a documentation
gap, not a finding that MOEX values are wrong or that suitable documentation
cannot exist. No conflicting applicable authority was established.

Only this document is added. B2/R1/R2/R3 runtime contracts, monetary-authority
flags, serializers, tests, historical source fingerprints, and published
evidence remain unchanged. No cashflow engine is implemented or authorized.

## 2. Method and provenance boundaries

Research read official public HTML, documentation HTML, and static JS/JSON
assets. Scripts were inspected as text, never executed. No browser application
was bootstrapped. No URL assembled by those scripts was requested. In particular,
there were zero requests to live Bondization, security, history, calculator,
or CCI data endpoints. `/iss/reference/` and numbered reference pages below are
documentation, not instrument data acquisition.

An official UI label documents its displayed meaning, not every backend
formula, payment entitlement, denominator, revision policy, or publication time.
An exact static endpoint/field/label chain is stronger than a similarly named
field at another endpoint. UI formatting and client-side pagination fallbacks
are not adopted as economic or completeness rules.

Authority vocabulary:

- **DOCUMENTED:** exact applicable authority for the explicitly bounded claim.
- **PARTIAL:** some applicable facts established, with a required interpretation missing.
- **UNPROVEN:** no applicable definition established by the inspected sources.
- **CONFLICTING:** incompatible applicable authorities; none established here.

No direct production payload was inspected. Consequently every matrix
`RAW_TYPE` is **UNKNOWN**. JavaScript numeric-column settings and supplied
numeric examples are not proof of an actual JSON int/Decimal/string token.
Expected UI types, source labels, and operator observations are kept separate.

Sources below are mutable public resources, not a newly archived evidence
package. SHA-256 values identify retrieved response bytes; no raw source copy
is committed. Retrieval date is not publication date. Version query suffixes
and HTTP cache metadata do not establish historical decision availability.

## 3. Official source register

All accessible sources were checked on 2026-10-23. Asset publication dates were
not independently established. Source identifiers are used throughout the matrix
to avoid repeating excerpts or disguising paraphrase as a quotation.

| ID | Exact official URL | What was inspected / limits |
|---|---|---|
| H-RU | [RU calendar](https://www.moex.com/ru/bondization/calendar) | HTML scripts, table templates, `ng-init`, `bondizationApp.config`; no dynamic data loaded |
| H-EN | [EN calendar](https://www.moex.com/en/bondization/calendar) | Same application with EN locale and the ISS base URL |
| U | [URL service](https://www.moex.com/js/bondization/services/bondizationUrlService.js?v=20260730162834337) | Lines 9–19 and 52–59 bind optional exact SECID, table selection and route |
| D | [Calendar data service](https://www.moex.com/js/bondization/services/bondizationCalendarDataService.js?v=20260730162834337) | Lines 23–30, 83–94, 106–117: response table mapping and UI issue-size scaling |
| C-C | [Coupon controller](https://www.moex.com/js/bondization/controllers/couponControler.js?v=20260730162834337) | Lines 11–25 bind coupon locale titles to exact fields |
| C-A | [Amortization controller](https://www.moex.com/js/bondization/controllers/amortizationControler.js?v=20260730162834337) | Lines 12–26 bind amortization locale titles to exact fields |
| L | [Locale service](https://www.moex.com/js/bondization/services/bondizationLocaleService.js?v=20260730162834337) | Lines 9–16 bind locale IDs to static locale JSON |
| RU | [RU locale](https://www.moex.com/js/bondization/locale/ru.json) | `titles.amort`, `titles.coupon`, `amort-caption`, `value_rub-remark` |
| EN | [EN locale](https://www.moex.com/js/bondization/locale/en.json) | Same table-qualified keys; EN makes the amount currency explicit |
| M | [Models](https://www.moex.com/js/bondization/factories/bondizationModels.js?v=20260730162834337) | Raw property assignment and CSV mapping; not an economic specification |
| F | [Display filters](https://www.moex.com/js/bondization/filters/bondizationFilters.js?v=20260730162834337) | Lines 21–43: missing-value display and nominal-currency spelling filter |
| R | [ISS reference index](https://iss.moex.com/iss/reference/) | Direct HTML inspected; no `bondization`, `amortizations`, or `data_source` match in that retrieval |
| R917 | [CCI coupon documentation](https://iss.moex.com/iss/reference/917) | A different service; documents an update-time filter, not Bondization publication authority |
| R915 | [CCI coupon-action documentation](https://iss.moex.com/iss/reference/915) | Different corporate-action reporting scope; no field-equivalence proof |
| N | [FACEUNIT announcement](https://www.moex.com/n18266) | Published 2018-01-10; change effective 2018-01-18, explicitly scoped to history responses |
| G | [Amortization glossary](https://bondguide.moex.com/terms/9) | Full page unavailable: browser fetch timeout and direct connection failure. Official indexed description is conceptual only |
| K | [MOEX calculator page](https://www.moex.com/ru/bondization/calc) | Informational-tool disclaimer and reference to issuer issuance documents; no calculator data requested |

Documentation R917/R915 was accessible by direct static HTTP even when the web
reader could not render it. R's coupon references were CCI references, not
Bondization documentation. The absence of a route in one index is not proof that
no specification exists elsewhere. Official-domain searches for field names,
per-bond wording and `data_source=maturity` found no applicable missing contract;
the search is not exhaustive. Irrelevant presentations, other-issue terms and
third-party code were not used to establish field authority. A late repeat-fetch
check stopped at a TLS connection timeout before rereading locale bytes. The
digests below identify the earlier successful responses; no second hash-check
PASS or source-change conclusion is claimed from that failed connection.

Retrieved static response digests:

| ID | SHA-256 |
|---|---|
| U | `35dfbc40c5012124aa1f9f08b411821bd62742f21b3b0eb1e6208f93e5951426` |
| D | `05aea1f70042db8bf2193fb70458e84ed6c38ffa70f854f78142e7ff37d69d5c` |
| C-C | `f02a236a7a8124180a51c50de21c343464d608c3f699b61e088813fa5c4ddc0c` |
| C-A | `34917edd0f7d146464efcc1f51dac89d05f72a964136f2c693ce5708d6fedc24` |
| L | `4b533276a603cef91b3b1398d84d46e07d8e2d8042c70a63b97e8a4667da7fba` |
| RU | `77432b7dbd388e780d9cecec68d3dc10cee5f011cf51bcfcc320dd1ab20e3468` |
| EN | `6410ac70eed9906041f8560840a012fbdf61965f8a767cd663a185b192a37d99` |
| M | `06428891a44beaeaccba17021df8acedd4af8e15b5ba3714d59bae8d8be77717` |
| F | `da895cfeeba608cbacc376635faeb7c8f89cdd9e88cd68bee916e28675073cd1` |
| R | `f471454422ae45aac40f3c9eb4ab7218adea995da4384d7c97a13b01c4fe1f99` |
| R917 | `2dcfb55a437914076af14bf842bcde0e22e74e325e7f4c60e886bfc9dc57a646` |
| R915 | `f7429e1a439acde1b567f00634e98c09aacd2458d428dc8557dc7f713534f95a` |

## 4. Exact endpoint and UI mapping

The static chain is H-RU/H-EN → U → D → C-C/C-A → RU/EN; L resolves the locale.

1. Calendar config supplies `https://iss.moex.com/iss`.
2. U assembles `/statistics/engines/stock/markets/bonds/bondization{optional /SECID}.json`.
3. U selects the requested table and its cursor, extended JSON, locale, date range,
   pagination and `is_traded=1`. These are inspected strings, not executed requests.
4. D reads the selected response table, assigns each item to the corresponding
   model, changes the date's display format and scales **only `issuevalue`** for
   the UI. It does not multiply/divide `value`, `valueprc`, or `value_rub`.
5. Controllers bind the exact source field to the table-specific locale label.
   M's generic property assignment preserves the item's field names.

This supports applicability to the same Bondization route/table family as B2.
It does not prove all query modes, inactive instruments, formats, versions or
SECID-specific responses are semantically identical. B2 used different meta/JSON
settings and did not inherit the calendar's traded-only filter. No new probe
was performed to bridge any remaining query-specific difference.

The calendar's short-page/error fallbacks and numeric display formatting are
explicitly outside the authority adopted here. They cannot weaken B2/R1/R2
completeness, Decimal precision, or unknown-value gates.

## 5. Short source excerpts

HTML line breaks are removed only for readability. Each excerpt is quoted once;
matrix entries reference these IDs rather than repeating it.

| Excerpt | Source/key | Relevant text |
|---|---|---|
| E1 | EN `titles.coupon.valueprc` | “Coupon rate, per cent per annum” |
| E2 | EN `titles.amort.valueprc` | “Payment (% of face value)” |
| E3 | EN `titles.coupon.value`, `titles.amort.value` | “Payment amount, nominal currency” |
| E4 | EN both tables' `value_rub` | “Payment amount, RUB*” |
| E5 | EN both tables' `facevalue` | “Outstanding face value” |
| E6 | EN `amort-caption` | “Redemption and amortisation” |
| E7 | RU both tables' `faceunit` | “Валюта номинала” |
| E8 | RU both tables' `initialfacevalue` | “Номинал” |
| E9 | RU both tables' `issuevalue` | “Объем эмиссии (млн)” |
| E10 | RU `value_rub-remark`, future-payment clause | “для будущих выплат значение рассчитывается по курсу на текущую дату” |
| E11 | RU same key, current/past-date clause | “по курсу на эту дату” |
| E12 | N, history-specific announcement | “FACEUNIT - валюта номинала” |

E1/E2 document different displayed meanings. They do not prescribe a conversion
from annual rate to period cash, day count, rounding, or the particular nominal
reference for amortization percentages. E10/E11 specify display rate-date
selection, not rate provider, fixing time, legal settlement currency or payment.

## 6. Field authority matrix

Every row below has actual `RAW_TYPE=UNKNOWN` for the reason in §2. “Numeric UI”
is only an expected display type in the controllers. `C` maps through U/D/C-C/L;
`A` through U/D/C-A/L. Source IDs link to the exact official URLs in §3; excerpt
IDs link to §5. The status is bounded by the unresolved questions in the last
column, not permission to perform an economic conversion.

| ID | FIELD | SOURCE_TABLE | RAW_TYPE | SOURCE_DESCRIPTION | ECONOMIC_MEANING | MONETARY_UNIT | DENOMINATOR | CURRENCY | OFFICIAL_SOURCE_URL | RELEVANT_SOURCE_EXCERPT | EXPLICIT_ENDPOINT_MAPPING | AUTHORITY_STATUS | UNRESOLVED_QUESTIONS |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | value | coupons | UNKNOWN | E3; numeric UI | Supplied scheduled cash candidate | Nominal-currency amount shown | **Per bond/lot/issue UNPROVEN** | Nominal currency per E3 | EN, RU, U, D, C-C | E3 | C: source item.value → coupon label | PARTIAL | Q1: exact denominator, payment currency exceptions |
| A1 | value | amortizations | UNKNOWN | E3; numeric UI | Supplied principal cash candidate | Same bounded E3 scope | **Per bond/lot/issue UNPROVEN** | Nominal currency per E3 | EN, RU, U, D, C-A | E3 | A: source item.value → amort label | PARTIAL | Q1/Q6: denominator, event-kind rules |
| C2 | valueprc | coupons | UNKNOWN | E1; numeric UI | Annual-rate label, not period cash | Percent per annum | Nominal basis/day-count formula not specified | Not itself currency | EN, RU, U, C-C | E1 | C: item.valueprc → coupon-specific title | DOCUMENTED for annual-rate label only | Q2: which principal base, dates, formula, rounding |
| A2 | valueprc | amortizations | UNKNOWN | E2; numeric UI | Nominal-percentage label, not annual rate | Percent of nominal | Initial/current/event-relative nominal not resolved | Not itself currency | EN, RU, U, C-A | E2 | A: item.valueprc → amort-specific title | DOCUMENTED for percentage label only | Q2/Q7: exact nominal reference and post-event rule |
| C3 | value_rub | coupons | UNKNOWN | E4, E10/E11 | Display RUB-equivalent candidate | RUB display | Inherits unresolved amount denominator | RUB | EN, RU, U, D, C-C | E4, E10/E11 | C: item.value_rub → coupon RUB title and shared remark | PARTIAL; rate-date display rule documented | Q3/Q4: fixing provider/time, method, revisions, legal settlement |
| A3 | value_rub | amortizations | UNKNOWN | E4, E10/E11 | Display RUB-equivalent candidate | RUB display | Inherits unresolved amount denominator | RUB | EN, RU, U, D, C-A | E4, E10/E11 | A: item.value_rub → amort RUB title and shared remark | PARTIAL; rate-date display rule documented | Q3/Q4: same unresolved fixing/denominator questions |
| C4 | faceunit | coupons | UNKNOWN | E7; nonnumeric UI | Nominal currency field | Currency code, not cash amount | Not applicable | Its supplied nominal code | RU, EN, U, C-C; N contextual only | E7; E12 limited to history | C: item.faceunit → nominal-currency title | DOCUMENTED for nominal-currency label only | Does not independently establish coupon payment currency or amount denominator |
| A4 | faceunit | amortizations | UNKNOWN | E7; nonnumeric UI | Nominal currency field | Currency code, not cash amount | Not applicable | Its supplied nominal code | RU, EN, U, C-A; N contextual only | E7; E12 limited to history | A: item.faceunit → nominal-currency title | DOCUMENTED for nominal-currency label only | Same separation from monetary-unit/payment authority |
| C5 | facevalue | coupons | UNKNOWN | E5; numeric UI | Outstanding-nominal context | Nominal amount context | Per-security quantity semantics not formally specified here | Nominal context; not an independent currency field | EN, RU, U, C-C | E5 | C: item.facevalue → outstanding-nominal title | PARTIAL | Q7: effective date and before/after-event semantics |
| A5 | facevalue | amortizations | UNKNOWN | E5; numeric UI | Outstanding-nominal context | Nominal amount context | Same unresolved quantity/timing basis | Nominal context | EN, RU, U, C-A | E5 | A: item.facevalue → outstanding-nominal title | PARTIAL | Q7: whether remaining nominal is pre/post this amortization |
| C6 | initialfacevalue | coupons | UNKNOWN | E8; numeric UI | Nominal context; “original” meaning not established merely by key | Nominal amount context | Exact original amount/quantity basis unproved | Nominal context | RU, EN, U, C-C | E8 | C: item.initialfacevalue → nominal title | PARTIAL | Initial at issuance, current series, redenomination or another baseline? |
| A6 | initialfacevalue | amortizations | UNKNOWN | E8; numeric UI | Same bounded E8 context | Nominal amount context | Same unresolved baseline | Nominal context | RU, EN, U, C-A | E8 | A: item.initialfacevalue → nominal title | PARTIAL | Needed baseline for A2 and principal accounting |
| C7 | issuevalue | coupons | UNKNOWN | E9; numeric UI | Issue-level display context | UI millions; raw unit not proved | Issue-level context, not a proved per-bond payment | Not specified by E9 | RU, EN, U, D, C-C | E9 | C: item.issuevalue scaled by 1,000,000 only for display | PARTIAL | Raw unit: currency amount or other size; timing and issuance base |
| A7 | issuevalue | amortizations | UNKNOWN | E9; numeric UI | Same bounded E9 context | UI millions; raw unit not proved | Same issue-level limit | Not specified by E9 | RU, EN, U, D, C-A | E9 | A: same display scaling | PARTIAL | Same raw-unit/aggregate scope gap |
| C8 | data_source | coupons | UNKNOWN | No applicable calendar label/enum found | No extra classification allowed | Not established | Not applicable | Not applicable | U/D/C-C inspected; R search boundary | No applicable excerpt located | No explicit coupon field-semantic mapping found | UNPROVEN | Presence and allowed meanings require source contract |
| A8 | data_source | amortizations | UNKNOWN | Operator reports exact maturity marker; no official enum found | Existing R3 source-only policy, not documentary monetary proof | Not established | Not applicable | Not applicable | U/D/C-A inspected; R; G conceptual only | E6 proves combined table scope, not enum value | No exact marker-to-event-type contract located | UNPROVEN official enum; R3 reviewed policy remains separate | Q5/Q6: maturity vs partial marker definitions and revision rules |

No matrix row authorizes use of `value_rub` as a historical conversion series.
No DOCUMENTED label claim resolves an adjacent UNPROVEN denominator or formula.

## 7. Answers to the ten specific questions

1. **Does `value` mean payable amount per bond?** C1/A1 establish an amount role
   and nominal-currency presentation through a real route/field binding. They
   do not explicitly say one bond. Per-bond use remains blocked pending that
   exact applicability/denominator statement; financial convention is not the proof.
2. **What does `valueprc` mean?** C2 and A2 document different table labels.
   They must not share one universal percentage-to-cash formula. The unresolved
   principal base and calculation rules remain in the matrix.
3. **Is `value_rub` fixed cash or indicative conversion?** C3/A3 and E10/E11
   support a date-dependent displayed equivalent, not an assertion of received
   cash or an immutable fixing for future events. Provider/method remain open.
4. **What rate date applies to future FX payments?** E10 explicitly uses the
   current date, while E11's surrounding source clause applies the table date
   for current/past entries. Timezone, intraday fixing, holidays and rounding
   were not specified in the reviewed note. Future FX amounts cannot become
   historical decision inputs or guaranteed settlement amounts.
5. **Is `data_source=maturity` enough?** It remains enough for the already
   approved R3 scheduled source-only classification, with payment NOT_CONFIRMED.
   A8 lacks an official field/enum specification, so R4 does not upgrade it to
   documented contractual fulfillment or monetary authority.
6. **Can principal be in amortizations without redemptions?** The official
   E6/D/C-A chain explicitly presents the combined principal table. The two
   operator cases are consistent with that design. A missing separate block
   must not trigger a second fabricated principal event. This does not prove
   universal backend table availability or the exact maturity enum for all issues.
7. **What proves nominal remaining after a partial event?** C5/A5 identify the
   displayed outstanding field, but not its event-relative effective instant.
   C6/A6 do not close that gap. A versioned definition or dated contractual
   principal schedule is required; do not automatically subtract A1 from A5.
8. **How are unknown future coupons represented?** The supplied observations
   contain missing monetary values. F displays absent/null/blank values with a
   placeholder; this documents display handling only. No complete source-level
   taxonomy of unknown/unset/floating coupons was found. Preserve missing values
   and diagnostics; no zero, interpolation or rate-based reconstruction.
9. **What proves publication/historical availability?** None of the established
   Bondization fields proves this. Observation/retrieval time and scheduled event
   date are different axes. R917/R915 document an update-time filter for CCI,
   another service: an update filter is neither first publication nor an archived
   Bondization version and cannot be transferred across endpoints.
10. **What is missing before a reliable schedule model?** First close the RUB
    denominator and event/nominal timing gates G1–G3 below. A model also needs
    known/unknown coverage, binding/revisions and field authority rather than
    numeric agreement. FX and historical models additionally need G4/G5.

## 8. Existing instruments: context, not reverified data

The supplied R2 source/qualification hashes are
`7a698ad26fd3fcf8e4387285755212e98fff2edce013db48538a0012700decd0` and
`db6da43136f547a1ce7efad31fc30343a04939b8d7088877dd5f40b9548e8a47`.
The supplied R3 implementation SHA is the base SHA in §1. Operator reports R3
execution COMPLETE and row integrity PASS. R3 production report bytes and their
semantic hashes were not provided or personally replayed in this research.

| Operator case | Supplied facts | Permitted interpretation / limit |
|---|---|---|
| RU000A0JQAM6 coupons | 38 rows; 35 values; 3 missing on 2027-09-15, 2028-03-15, 2028-09-15 | Schedule-row presence and amount completeness are distinct; missing dates stay unknown |
| SU24020RMFS8 coupons | 12 rows, all with values; accepted current ISIN RU000A100QS2 | Amount presence does not establish denominator or payment |
| Both principal observations | One amortizations row each: data_source maturity; value 1000; valueprc 100; value_rub 1000; faceunit RUB | Consistency with a final scheduled principal observation, not proof of cash entitlement/unit or actual payment |

No arithmetic from those equalities is used to establish field authority.
BASELINE/TABLE_ONLY are separate observations, not additive payments. Neither
an offer date/price nor a maturity-marked row proves actual exercise or receipt.
The cause of RU's future missing coupons is not inferred merely from their dates.

## 9. Exact gaps and closure requirements

These are questions for a separately reviewed evidence task, not permission
to contact MOEX, collect more data, or change parsers now.

| Gate | Exact required document or written clarification | Why it matters |
|---|---|---|
| G1 — RUB denominator | MOEX Bondization field specification or attributable written confirmation for the route in §4, separately defining coupons.value and amortizations.value as per single bond, per lot or per issue; currency exceptions and relevant query/version scope | Primary blocker even for RUB-only design |
| G2 — principal/event semantics | Allowed data_source values; exact meaning of maturity; final vs partial event rules; whether facevalue is before/after each dated event; initialfacevalue reference and revision/redenomination treatment | Prevents wrong remaining principal and duplicate redemption |
| G3 — unknown values and contractual completeness | Source representation of unset coupons, null amount vs no event, authoritative schedule coverage/revision policy, and issuer terms if any reconstruction formula is proposed | Missing amounts cannot be filled by a generic percentage formula |
| G4 — FX method | value_rub denominator, provider/rate type, timezone/fixing date and time, nonbusiness-day treatment, rounding, revisions and relation to legal payment currency | Needed only if FX/conversion is admitted; not supplied by the date-rule note alone |
| G5 — historical publication | Field-specific first publication/effective/update timestamps, immutable versions or retained source-bound releases for each decision date | Separate historical/PIT blocker even with documented current amounts |

Issuer issuance terms can establish contractual rights for an exact issue but
do not automatically define an ISS field mapping. K explicitly distinguishes
informational calculator output from issuer-established parameters. Any future
use requires both chains, not a similarly named value from another endpoint.

If G1 and the relevant RUB scope gates close, a future design may exclude
`value_rub` and all FX conversions entirely. That future scope must explicitly
retain unknown amounts, scheduled-not-paid status and non-PIT limitations.
This document neither implements that model nor pre-approves its formulas.

## 10. Compatibility, verification and handoff

R3's schema still fixes economic_amount_authority_status, amount_unit_authority
and historical_pit_readiness to UNPROVEN; the maturity literal remains its reviewed
source-only policy. Its source-field proof outputs are not rewritten by the
narrower current-document findings above. No new runtime authority registry or
evidence adapter is created. B2/R1/R2 hashes and R3 serialization are untouched.

Focused review checks: all 16 table-qualified matrix rows and required columns;
source/excerpt references; ten question answers; raw-type UNKNOWN declarations;
bounded DOCUMENTED claims; exact endpoint chain; decision consistency; no per-bond
inference or future-FX/PIT promotion. Accessible source links were fetched as
documentation/static material; G's unavailable status is explicit. No broad
local tests or new test modules are needed for this single-document change.
Working/staged `git diff --check`, one-file scope and unchanged runtime/fingerprint
checks complete local verification; broad repository checks remain CI's task.

```ini
SOURCE_AUTHORITY_STATUS=PARTIAL
COUPON_VALUE_UNIT=NOMINAL_CURRENCY_DISPLAY_DENOMINATOR_UNPROVEN
AMORTIZATION_VALUE_UNIT=NOMINAL_CURRENCY_DISPLAY_DENOMINATOR_UNPROVEN
VALUE_RUB_AUTHORITY=PARTIAL_DISPLAY_RATE_DATE_RULE_DOCUMENTED
VALUEPRC_AUTHORITY=DOCUMENTED_TABLE_LABELS_CALCULATION_BASIS_GAPS
FINAL_MATURITY_AUTHORITY=PARTIAL_COMBINED_TABLE_AND_R3_POLICY_ENUM_UNPROVEN
HISTORICAL_PUBLICATION_AUTHORITY=UNPROVEN
CASHFLOW_ENGINE_DECISION=DOCUMENTATION_GAPS_REMAIN
LIVE_ISS_REQUESTS=0
DB_MUTATION=false
DEPLOY=false
PIT_READY=false
OFFICIAL_DAY0_STARTED=false
```

Next safe step is independent review of this document and its exact commit/CI.
Any clarification request, engine implementation, source acquisition, production
operation, backtest or official Day 0 requires a separate explicit task.
