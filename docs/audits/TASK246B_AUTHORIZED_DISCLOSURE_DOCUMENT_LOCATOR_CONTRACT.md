# Task246B — Authorized Official Disclosure Document Locator Contract

## 1. Execution Profile

Task246B is a documentation-only, fail-closed source-contract audit.

```text
TASK_ID=Task246B
MODE=DOCUMENTATION_ONLY_SOURCE_AUDIT
LOCATOR_DECISION=TARGETED_MANUAL_ONLY
ECONOMIC_GATE=TARGETED_MANUAL
IMPLEMENTATION=DOCUMENTATION_ONLY
LIVE_PROBE_RUN=false
NETWORK_USED_FOR_PROBE=false
PROTECTED_ENDPOINT_CALLED=false
MIGRATION=NONE
PRODUCTION_ACTIONS=NONE
```

False source certainty is worse than missing document coverage. Task246B does
not force an implementation when exact issue-to-document binding is unproven.

## 2. Context

Task241–Task244 established security and legal-issuer identity. Task245
classified issue-document support evidence as partial. Task246A proved that CBR
issue identity is available but did not establish a public deterministic
original-document locator.

Task246B evaluates one bounded set of official/accredited source hypotheses. It
ends at document location and exact-byte retrieval; support-party extraction is
outside this task.

## 3. Starting State

The prior production counts are accepted only as supplied context:

```text
BONDS=2995
VERIFIED_BOND_LEGAL_ISSUER_PROFILES=2995
LEGAL_ISSUERS=498
LEGAL_ISSUER_VERIFIED=498
LEGAL_ISSUER_CONFLICTS=0
PRODUCTION_COUNTS_REMEASURED=false
```

No production or database access occurred in Task246B.

## 4. Why This Task Exists

For SPVs, SFOs, securitizations, mortgage agents and supported issues, legal
issuer identity may not describe the full credit-support structure. Before any
relationship extraction, BondRadar needs a reliable chain from exact issue
identity to an official disclosure record, stable document reference and exact
document bytes.

## 5. Goal

The goal is to determine whether a source can provide deterministic, authorized
and economically justified issue-document retrieval with explicit failures,
versioning and PIT semantics. The task does not equate the existence of a
document API with readiness to build a universal pipeline.

## 6. Decision Outcomes

The frozen alternatives are `PUBLIC_LOCATOR_READY`,
`AUTHORIZED_CREDENTIALED_LOCATOR_READY`, `TARGETED_MANUAL_ONLY`, and `NO_GO`.

Task246B selects:

```text
LOCATOR_DECISION=TARGETED_MANUAL_ONLY
```

CBR does not expose a proven public original-document locator. The documented
Interfax gateway supplies event/file capabilities but does not publicly prove
universal exact ISIN/registration-number-to-document binding, and authorized
access is not configured.

## 7. Non-Goals

Task246B does not implement guarantor/support-party extraction, parent/group or
reporting-entity mapping, persistence, OCR, LLM analysis, fuzzy matching,
credit scoring, financial ingestion, a mass crawl or a browser scraper.

## 8. Starting Commit and Preflight

```text
BRANCH=main
STARTING_COMMIT=0573c5e20cabfa9029bb6728d5aa81a2cfa4dec9
TRACKED_WORKTREE_AT_START=clean
ALEMBIC_HEAD=202608280002
```

No pull, merge, rebase or migration was performed.

## 9. Required Repository Investigation

Repository inspection established:

```text
CURRENT_ISSUE_IDENTIFIERS=Bond.isin,Bond.secid
CURRENT_DOCUMENT_LOCATOR=NONE
CURRENT_DOCUMENT_RETRIEVAL=TASK_SPECIFIC_ONLY_NOT_REUSABLE
CURRENT_HASH_UTILITY=hashlib.sha256_LOCAL_CALL_SITES
CURRENT_DISCLOSURE_CREDENTIAL_CONFIG=NONE
```

`Bond` does not store a CBR security ID, registration number or program ID.
`FinancialReportSourceDocument` is Company/report-scoped and has no suitable
issue-document integrity contract. No CBR/Interfax locator client exists.

## 10. Task246A Invariants

```text
AUTOMATIC_VERIFICATION=false
REVIEW_REQUIRED=true
ISSUER_WIDE_PROPAGATION=false
OCR_ALLOWED=false
LLM_EXTRACTION_ALLOWED=false
FUZZY_TARGET_MATCHING=false
MIGRATION=NONE
PRODUCTION_ACTIONS=NONE
```

Task246B changes only the source-access assessment.

## 11. Source Investigation Scope

The investigation is limited to:

1. Bank of Russia securities/issue resources;
2. the documented Interfax/e-disclosure gateway;
3. the public e-disclosure website as a separate surface;
4. issuer official pages as targeted fallback;
5. no arbitrary commercial or third-party database.

Official documentation research was performed; no protected API, original
document download, live probe or production source call was made.

## 12. Source Authority Model

Every future source is classified by authority, access and automation:

- authority: `OFFICIAL_REGULATOR`, `ACCREDITED_DISCLOSURE`, `ISSUER_PRIMARY`,
  or `DISCOVERY_ONLY`;
- access: `PUBLIC`, `PUBLIC_WITH_RATE_LIMIT`,
  `AUTHORIZED_CREDENTIALS_REQUIRED`, `PAID_SUBSCRIPTION_REQUIRED`, `RESTRICTED`,
  or `UNKNOWN`;
- automation: `AUTOMATION_ALLOWED_FOR_CURRENT_RESEARCH`,
  `AUTOMATION_REQUIRES_REVIEW`, `AUTOMATION_RESTRICTED`, or
  `AUTOMATION_UNKNOWN`.

Unclear permission is never upgraded to allowed automation.

## 13. Access / Authorization States

The contract distinguishes `PUBLIC_ACCESS`, `AUTHORIZED_ACCESS_AVAILABLE`,
`AUTHORIZED_ACCESS_NOT_CONFIGURED`, `SUBSCRIPTION_REQUIRED`, `ACCESS_DENIED`,
`RATE_LIMITED`, `AUTHENTICATION_ERROR`, `TERMS_REVIEW_REQUIRED`, and
`SOURCE_UNAVAILABLE`.

Lack of authorization is never `DOCUMENT_NOT_FOUND`.

## 14. Issue Identity Contract

The CBR registry supports exact identity using CBR security ID, ISIN and state
registration number. MOEX SECID alone is not a CBR issue identifier.

A future identity projection preserves optional Bond ID/SECID, ISIN, CBR
security ID, registration/program numbers, issuer INN/OGRN/title, official
source locator and source snapshot date. No title-based recovery is allowed.

Official source: <https://www.cbr.ru/registries/rcb/reestr-cb>.

## 15. Issue → Document Locator Contract

A valid locator accepts exact issue identity and produces zero or more immutable
`OfficialDisclosureDocumentReference` objects. Frozen outcomes are
`DOCUMENT_REFERENCE_FOUND`, `NO_DOCUMENT_REFERENCE_FOUND`, `ISSUE_NOT_FOUND`,
`ISSUE_IDENTITY_INCOMPLETE`, `DOCUMENT_SCOPE_AMBIGUOUS`, `AUTH_REQUIRED`,
`SUBSCRIPTION_REQUIRED`, `ACCESS_DENIED`, `RATE_LIMITED`, `SOURCE_ERROR`,
`SCHEMA_ERROR`, and `UNSUPPORTED_SOURCE`.

`NO_DOCUMENT_REFERENCE_FOUND` is valid only after a successful source response.

## 16. Document Reference Contract

A future reference contains source family/authority/access, exact subject issue
identifiers, issuer identifiers, source event/document IDs, document type/title,
canonical and download locators, published/registered/updated timestamps, media
type, authorization state, PIT capability, automation state and blockers.

When the source supplies a stable file UID, URL alone is insufficient identity.

## 17. Exact-Byte Retrieval Contract

A retrieval-ready source must deterministically return raw bytes, status, final
canonical URL, HTTP status, media type, content length and retrieval time. No
preprocessing, text normalization, HTML-to-PDF conversion or screenshot
substitution is allowed before hashing.

## 18. Integrity Contract

Exact bytes are bound with `SHA256(raw_bytes)` and the fields
`content_sha256`, `content_length`, `media_type`, and `retrieved_at`.

```text
same bytes => same SHA256
different bytes => different SHA256
```

A redirect does not replace the stable source document UID.

## 19. Temporal / PIT Contract

`observed_at`, `source_published_at`, `source_registered_at`,
`source_updated_at`, `retrieved_at`, `effective_from`, and `effective_to` remain
separate. `published_at == effective_from` is never assumed.

Source PIT states are `PIT_READY`, `PIT_PARTIAL`, `CURRENT_ONLY`, and
`PIT_UNKNOWN`.

## 20. Document Versioning Contract

One URL is not assumed immutable. Future observation identity retains source
document ID, content SHA-256, observation time and any source revision ID.
Different bytes under one locator set `MUTABLE_LOCATOR=true` and create a new
content observation.

## 21. Retrieval Failure Semantics

Retrieval statuses are `RETRIEVED`, `NOT_FOUND`, `AUTH_REQUIRED`,
`SUBSCRIPTION_REQUIRED`, `ACCESS_DENIED`, `RATE_LIMITED`, `TIMEOUT`,
`INVALID_CONTENT`, `UNSUPPORTED_MEDIA_TYPE`, and `SOURCE_ERROR`.

No failure is converted to `NO_DOCUMENT_EXISTS`; a failed fetch remains unknown.

## 22. Public Locator Path

### CBR

The registry exposes exact security identity and dated registry archives, while
the emission-securities extract exposes registration facts. The inspected
public contracts do not provide a deterministic original-document UID/URL and
byte-download endpoint.

The CBR electronic-registration page confirms that issue decisions,
prospectuses and bond programs are submitted through an authenticated personal
cabinet and returned to the applicant in a signed package. It is a submission
workflow, not a public retrieval API:
<https://www.cbr.ru/issuers_corporate/el_reg_issue/>.

```text
ISSUE_IDENTITY=READY
DOCUMENT_METADATA=PARTIAL
DOCUMENT_LOCATOR=NO_GO
BYTE_RETRIEVAL=NO_GO
PIT=PIT_PARTIAL
```

## 23. Credentialed Locator Path

The accredited Interfax gateway documents token authorization, disclosure-event
queries, file-type dictionaries and exact file-content retrieval through
`GET /api/v1/disclosure/download/files/{uid}`:
<https://gateway.e-disclosure.ru/swagger/ui/index.html>.

The service is subscription-based. Officially published pricing for file/report
events is RUB 16,180 per month excluding VAT with a three-month minimum;
extended archive access has an additional charge:
<https://e-disclosure.ru/poluchenie-informacii/shlyuz-api>.

```text
API_DOCUMENTED=true
AUTH_REQUIRED=true
SUBSCRIPTION_REQUIRED=true
EVENT_METADATA=true
DOCUMENT_ID=true
BYTE_DOWNLOAD=true
HISTORICAL_QUERY=true_WITH_CONTRACT_LIMITS
EXACT_ISSUE_BINDING=NOT_PROVEN
CURRENT_ACCESS_AVAILABLE=false
PRICE_FILES_REPORTS_RUB_MONTH_EX_VAT=16180
MINIMUM_SUBSCRIPTION_MONTHS=3
```

The documented event/file chain is real, but the public documentation reviewed
does not prove a universal exact ISIN/registration-number-to-file binding.
No credentials or credential configuration are present or requested.

## 24. Manual / Targeted Fallback Path

The public e-disclosure website and issuer official pages may allow a reviewer
to locate documents for selected complex issues. Public attachment URLs do not
by themselves establish a documented issue-to-document API.

```text
PUBLIC_WEB_PATH=MANUAL_ONLY
ISSUER_SITE_PATH=TARGETED_MANUAL_ONLY
PUBLIC_SITE_SCRAPER_IMPLEMENTED=false
ISSUER_SITE_CRAWL_IMPLEMENTED=false
```

A future reviewer may locate an official document and bind exact bytes/checksum
in a separately authorized workflow. Reviewing all 2995 Bonds is not proposed.

## 25. Licensing / Terms Contract

| Source | Authority | Access | Automation | PIT | Finding |
|---|---|---|---|---|---|
| CBR registry/extract | `OFFICIAL_REGULATOR` | `PUBLIC` | `AUTOMATION_REQUIRES_REVIEW` | `PIT_PARTIAL` | identity ready; original-document locator absent |
| CBR personal cabinet | `OFFICIAL_REGULATOR` | `RESTRICTED` | `AUTOMATION_RESTRICTED` | `PIT_UNKNOWN` | authenticated submission channel |
| Interfax gateway | `ACCREDITED_DISCLOSURE` | `PAID_SUBSCRIPTION_REQUIRED` | `AUTOMATION_REQUIRES_REVIEW` | `PIT_PARTIAL` | event/file API proven; exact issue binding not proven |
| Public e-disclosure | `ACCREDITED_DISCLOSURE` | `PUBLIC_WITH_RATE_LIMIT` | `AUTOMATION_REQUIRES_REVIEW` | `PIT_PARTIAL` | manual discovery; no stable issue API contract |
| Issuer sites | `ISSUER_PRIMARY` | source-specific | `AUTOMATION_UNKNOWN` | `PIT_UNKNOWN` | targeted heterogeneous fallback |

Allowed terms findings are `CLEAR_FOR_CURRENT_RESEARCH_USE`, `REQUIRES_REVIEW`,
`RESTRICTED`, and `UNKNOWN`. No legal permission is inferred.

## 26. Economic / Engineering Gate

The possible gates are `BUILD_NOW`, `DEFER_UNTIL_NEEDED`, `TARGETED_MANUAL`, and
`NO_GO`. Task246B selects:

```text
ECONOMIC_GATE=TARGETED_MANUAL
```

The API has setup, subscription and maintenance cost; exact issue binding is
unproven; support-document analysis is most useful for a subset of complex/SPV
issues; and M2 Credit Data has broader pre-Shadow-Test value. API existence
alone does not justify a universal pipeline.

## 27. Controlled Live Probe

No live probe was run because the public locator failed the contract and the
strongest API requires unconfigured paid authorization.

```text
LIVE_PROBE_RUN=false
NETWORK_USED_FOR_PROBE=false
AUTHORIZED_ENDPOINT_USED=false
PROTECTED_ENDPOINT_CALLED=false
DATABASE_MUTATION_EXECUTED=false
```

Official public documentation was researched separately from a source probe.

## 28. Sample Design

No sample securities were called. A future explicitly authorized probe remains
bounded to ten diverse securities and tests only identity, metadata, locator,
bytes, hashes and retrieval stability—never support-party facts.

## 29. Coverage Semantics

Future sample reporting keeps separate counts for `ISSUE_IDENTITY_RESOLVED`,
`DOCUMENT_METADATA_FOUND`, `DOWNLOAD_REFERENCE_FOUND`, `BYTES_RETRIEVED`,
`HASHED`, and `PIT_TIMESTAMP_AVAILABLE`.

No <=10 sample may be extrapolated to the 2995-Bond universe.

## 30. Service Architecture

No disclosure client or generic locator service is implemented. CBR lacks the
required public byte locator, while Interfax access and exact issue binding are
not ready. A fixture-only client would invent source behavior.

## 31. CLI Architecture

No `scripts/disclosure_document_locator_probe.py` is created. A future CLI is
allowed only for a proven real source and must omit raw bytes, credentials and
connection data from output.

## 32. Persistence Boundary

```text
MIGRATION=NONE
DOCUMENT_PERSISTENCE_IMPLEMENTED=false
RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false
REVIEW_PERSISTENCE_IMPLEMENTED=false
DATABASE_MUTATION_EXECUTED=false
```

Persistence remains separately authorized future work.

## 33. Security / Secrets Boundary

No token, login, password, cookie, API key or credential placeholder is added.
No environment values are printed. Task246B reports only that no disclosure
credential configuration variable exists.

Authentication, CAPTCHA, anti-bot and payment controls are never bypassed.

## 34. Allowed Scope

Allowed changes are official documentation research, source/access/economic
comparison, this tracked contract and its deterministic documentation test.

## 35. Forbidden Scope

Task246B performs no migration, DB write, document/relationship persistence,
LegalIssuer/Bond mutation, extraction, OCR, LLM processing, browser automation,
subscription purchase, protected call, mass crawl, scoring, strategy, backtest,
Shadow, T-Invest or VDS deployment.

## 36. Required Tests

Because Task246B remains documentation-only, tests validate the 42-section
inventory, source matrices, all frozen state/status enums, CBR and Interfax
projections, byte/PIT/failure contracts, economic decision, safety invariants
and exact downstream recommendation. Behavioral source fixtures are not
fabricated.

## 37. Acceptance Criteria

- `AC1`: exact baseline, no migration or production action.
- `AC2`: CBR and Interfax paths are evaluated separately from public HTML.
- `AC3`: exact issue identity is mandatory.
- `AC4`: stable document UID is preferred over URL.
- `AC5`: retrieval requires exact bytes and SHA-256.
- `AC6`: source/auth failures remain distinct from absence.
- `AC7`: PIT and mutable-locator risks are explicit.
- `AC8`: no scraper, bypass, extraction, persistence or mass crawl exists.
- `AC9`: access, subscription, pricing and terms uncertainty are explicit.
- `AC10`: primary/economic decisions reflect opportunity cost.
- `AC11`: no fake source client or CLI is created.
- `AC12`: Task246C is not automatically started.

## 38. Local Validation

Required commands:

```text
python -m py_compile backend/tests/test_task246b_authorized_disclosure_document_locator_contract.py
python -m pytest backend/tests/test_task245_issuer_relationship_source_contract.py backend/tests/test_task246a_official_issue_document_support_contract.py backend/tests/test_task246b_authorized_disclosure_document_locator_contract.py -q
git diff --check
```

The full backend suite is not run because application/shared code is unchanged.

## 39. Git / Diff / Scope Validation

```text
STARTING_SHA=0573c5e20cabfa9029bb6728d5aa81a2cfa4dec9
BRANCH=main
TRACKED_WORKTREE_AT_START=clean
MIGRATION=NONE
APPLICATION_CODE_CHANGED=false
NETWORK_USED=OFFICIAL_DOCUMENTATION_RESEARCH_ONLY
LIVE_PROBE_RUN=false
PROTECTED_ENDPOINT_CALLED=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

The only allowed changed files are this document and its focused test.

## 40. Commit / Push Rules

After mandatory checks, the authorized commit message is
`Add Authorized Disclosure Document Locator Contract`. Only a normal
fast-forward push to `origin/main` is allowed. Force/rebase, deployment and CI
polling are prohibited.

```text
CI=NOT_WAITED_BY_DESIGN
```

## 41. Final Report

The report must state source-by-source access and capability, exact CBR and
Interfax projections, public-web status, primary locator and economic decisions,
byte/PIT/licensing contracts, probe/implementation status, tests, scope,
commit/push, CI and production actions.

```text
RECOMMENDED_NEXT_STEP=DEFER_DOCUMENT_AUTOMATION_AND_MOVE_TO_M2_CREDIT_DATA
TASK246C_STARTED=false
```

This selects the request's Option 3. A targeted manual support-document workflow
may be authorized later for selected complex securities, but it is not the next
implementation task.

## 42. HARD STOP

Task246B hard-stops before any locator code if it would require credentials,
subscription purchase, guessed URLs, undocumented endpoints, scraping/browser
automation, issue-identity weakening, transformed bytes, conflated failures,
secret retention, extraction, persistence, a sample above ten or unrelated
changes.

```text
LOCATOR_DECISION=TARGETED_MANUAL_ONLY
ECONOMIC_GATE=TARGETED_MANUAL
IMPLEMENTATION=DOCUMENTATION_ONLY
RECOMMENDED_NEXT_STEP=DEFER_DOCUMENT_AUTOMATION_AND_MOVE_TO_M2_CREDIT_DATA
PRODUCTION_ACTIONS=NONE
```

Residual risks remain: access and subscription terms may change; document IDs
and URLs may be mutable; archives may be incomplete; publication may differ
from legal effectiveness; bytes may change under a locator; scanned documents
and foreign regimes vary; manual review remains necessary; and document
automation competes with higher-value financial-data work.
