# Task246A — Official Issue Document Locator & Reviewed Support-Party Evidence Contract

## 1. Execution Profile

Task246A is a documentation-only, fail-closed source-contract audit.

```text
TASK_ID=Task246A
MODE=DOCUMENTATION_ONLY_SOURCE_AUDIT
ISSUE_IDENTITY=READY
OFFICIAL_DOCUMENT_LOCATOR=NO_GO
AUTOMATED_EXTRACTION=NO_GO_FOR_AUTOMATED_EXTRACTION
MIGRATION=NONE
PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
DB_COVERAGE_RUN=false
PRODUCTION_ACTIONS=NONE
REVIEW_REQUIRED=true
AUTOMATIC_VERIFICATION=false
```

False support-party evidence is materially worse than missing coverage. No
candidate described by this contract is trusted investment data.

## 2. Context

Task241–Task244 established security terms, source-backed Bond-to-issuer
mapping, and canonical legal-issuer identity. Task245 established that no
investigated credential-free structured source provides a universal
parent/group/guarantor/reporting-entity graph.

Task246A narrows the next investigation to official documents for an exact
security, issue, or issue program. It does not change existing models or
production data.

## 3. Task245 Decision

Task245 classified `GUARANTOR` and `SPV_SPONSOR_ORIGINATOR` as `PARTIAL`
because authoritative issue documents may state these roles explicitly. It did
not prove a deterministic document locator.

Task246A therefore evaluates the required chain without skipping a step:

```text
exact issue identity -> official document reference -> exact bytes -> SHA-256
-> explicit support role -> stable target identifier -> REVIEW_REQUIRED
```

## 4. Goal

The goal is to specify what would be required to answer whether an official
document for one exact issue explicitly identifies a support party. The task
separately evaluates issue identity, document location, integrity, temporal
meaning, extraction limits, target identity, and review readiness.

The evidence-based result is that issue identity is available, but a public,
stable, deterministic original-document locator was not established.

## 5. Non-Goals

Task246A does not implement parent/group/reporting-entity inference, OCR, LLM
extraction, fuzzy matching, automatic verification, persistence, credit
scoring, strategy wiring, or production population.

It does not interpret a same-group relationship as support and does not infer
support from an issuer or security name.

## 6. Core Architecture Decision

`IssueDocumentLocatorResult` and `SupportPartyCandidate` are separate future
contracts. A document reference is not a relationship assertion, and parsed
text is not accepted evidence until the document bytes, exact role, scope, and
target identity are all bound and manually reviewed.

No generic source adapter or fixture-only implementation is created when the
real source behavior is unproven.

## 7. Supported Relationship Roles

The future candidate taxonomy keeps these roles distinct:

| Role | Meaning boundary |
|---|---|
| `GUARANTOR` | Explicit guarantee under the document's legal terminology |
| `SURETY_PROVIDER` | Explicit surety/`поручитель`; not silently relabeled guarantor |
| `OFFEROR` | Party making an offer; not a guarantee |
| `COLLATERAL_PROVIDER` | Provider of identified collateral/security |
| `SPONSOR` | Explicit sponsor role |
| `ORIGINATOR` | Explicit originator role; not automatically parent |
| `SERVICER` | Optional, only when explicitly stated |
| `BACKUP_SERVICER` | Optional, only when explicitly stated |

`OFFEROR != GUARANTOR` and `ORIGINATOR != PARENT`.

## 8. Scope Semantics

Every future candidate must use exactly one scope: `SECURITY`, `ISSUE`, or
`ISSUE_PROGRAM`. Guarantor, surety provider, offeror, and collateral provider
default to the exact security/issue/program established by the source.

```text
ISSUER_WIDE_PROPAGATION=false
```

A relationship observed for one Bond may not be copied to another Bond of the
same LegalIssuer.

## 9. Starting Commit and Preflight

Task246A was locked to:

```text
BRANCH=main
STARTING_COMMIT=06038cd3cf5e91e48e7871a1a2414bb1a0c5a8a3
TRACKED_WORKTREE=clean
ALEMBIC_HEAD=202608280002
```

No migration is required or allowed.

## 10. Required Repository Investigation

Repository inspection established:

- `Bond` stores ISIN and MOEX SECID but no CBR security ID, registration
  number, or issue-program identifier.
- Security Master models describe current instrument terms, not official issue
  document identity.
- Bond legal-issuer and LegalIssuer evidence preserve security/issuer identity,
  not issue-document references.
- `FinancialReportSourceDocument` is report/Company-scoped and does not provide
  an issue-document SHA-256 contract.
- no CBR source client or issue-document locator exists;
- `httpx` is available, but no supported PDF text-extraction dependency is part
  of the backend contract;
- the existing official-source assistant has task-specific hashing/download
  logic and is not a reusable issue-document authority.

No existing provenance model safely represents this relationship pipeline.

## 11. Primary Source Strategy

The source priority remains:

1. Bank of Russia official security/issue identity resources;
2. an original official issue document hosted by the Bank of Russia, issuer,
   or an authorized disclosure channel;
3. search/third-party discovery only, never evidence authority.

The implementation must stop when tier 1 identity cannot lead to a proven tier
1 or authorized tier 2 document locator.

## 12. CBR Security / Issue Identity Anchor

The Bank of Russia securities registry supports exact lookup by CBR security
ID, ISIN, registration number, CFI, and issuer identifiers. Official sources:

- <https://www.cbr.ru/registries/rcb/reestr-cb>
- <https://www.cbr.ru/vfs/registers/rcb/reestrcb.pdf>

The CBR security ID is an official unique registry identity based on the
ISIN/registration-number combination. A future resolver may return:

- ISIN, CBR security ID, registration number, optional program number;
- issuer INN, OGRN and official title;
- security category and dated registry snapshot/publication evidence.

Frozen statuses are `EXACT_ISIN`, `EXACT_REGISTRATION_NUMBER`,
`IDENTIFIER_MISSING`, `SECURITY_NOT_FOUND`, `IDENTIFIER_CONFLICT`, `AMBIGUOUS`,
`SOURCE_ERROR`, and `SCHEMA_ERROR`. SECID alone is not a CBR identity key, and
no fuzzy issuer-name resolution is allowed.

## 13. Official Issue Document Locator

The public securities registry and informational securities extract provide
issue/registration facts, including whether a prospectus was registered, but
the inspected contracts did not expose a stable original-document ID/URL and
retrievable bytes for the required document types:

- <https://www.cbr.ru/registries/rcb/ecb/>

The electronic-registration channel lists submission document types but is an
authenticated issuer submission workflow, not a public retrieval API:

- <https://www.cbr.ru/issuers_corporate/el_reg_issue/>

The Interfax disclosure gateway is authenticated/subscription access and is
not authorized in this task:

- <https://e-disclosure.ru/poluchenie-informacii/shlyuz-api>

Therefore `OFFICIAL_DOCUMENT_LOCATOR=NO_GO`. Search-engine results, guessed
URLs, undocumented scraping, and authentication bypass cannot repair this gap.

## 14. Document Identity Contract

A future `IssueDocumentReference` must contain source family, exact ISIN,
registration number, issue/program identity, document type/title, canonical
source locator, source record/document ID when supplied, publication and
registration dates when supplied, media type, retrieval status, and authority
tier.

Supported document types are `ISSUE_DECISION`, `PROSPECTUS`, `BOND_PROGRAM`,
`PLACEMENT_TERMS`, `MATERIAL_FACT`, `ISSUER_REPORT`, and
`OTHER_OFFICIAL_DISCLOSURE`. URL alone is not document identity.

## 15. Document Integrity Contract

Successful retrieval must calculate SHA-256 over exact unmodified bytes and
retain `content_sha256`, `content_length`, `media_type`, and `retrieved_at`.
The same bytes produce the same hash; changed bytes create a distinct content
observation. Parsed or normalized bytes are never substituted for the original
hash input.

## 16. Document Temporal Contract

The future contract keeps `observed_at`, `published_at`, `registered_at`,
`effective_from`, and `effective_to` separate and nullable where the source is
silent. It never infers `effective_from = published_at`.

A current locator is not evidence that the same document was publicly
available at an earlier date.

## 17. Machine-Readability Contract

Required classifications are `STRUCTURED`, `SEARCHABLE_TEXT`,
`BINARY_SEARCHABLE_PDF`, `NON_MACHINE_READABLE`, `UNSUPPORTED_FORMAT`, and
`RETRIEVAL_FAILED`.

Task246A implements neither OCR nor LLM extraction. A scanned PDF without a
usable text layer is `NON_MACHINE_READABLE`; automated extraction stops.

## 18. Support-Party Candidate Contract

A future immutable `SupportPartyCandidate` must retain role, scope, subject
LegalIssuer source identity, exact issue identifiers, target title and stable
identifiers, document type/locator/SHA-256/source record, publication and
observation times, page/section/field locator, exact source label, extraction
method, identity state, review state, and ordered blockers.

Every candidate has `review_state=REVIEW_REQUIRED`; no candidate is `VERIFIED`.

## 19. Target Identity Contract

An ingestible candidate requires at least one stable target identifier: INN,
OGRN, LEI, or an official registry ID. States are `STABLE_ID_PRESENT`,
`NAME_ONLY`, `IDENTIFIER_CONFLICT`, and `TARGET_IDENTITY_INCOMPLETE`.

Name-only candidates remain non-ingestible. No fuzzy title mapping or automatic
LegalIssuer match is permitted.

## 20. Extraction Rules

Allowed future extraction is limited to explicit structured fields,
deterministic label/value pairs, exact legal-role clauses, or documented
sections/tables with adjacent stable identifiers.

Sentiment, contextual guesses, brand inference, same-group inference, arbitrary
organization regexes, and LLM interpretation are forbidden. Original source
labels are retained. `поручитель` maps to `SURETY_PROVIDER` unless a reviewed
legal/source contract explicitly establishes another meaning.

## 21. Mandatory Review Boundary

```text
AUTOMATIC_VERIFICATION=false
REVIEW_REQUIRED=true
APPROVAL_WORKFLOW_IMPLEMENTED=false
RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false
```

The task proves a contract only. It creates no approval UI, reviewed evidence,
or downstream eligibility.

## 22. Negative Evidence Contract

Frozen outcomes are `EXPLICIT_RELATION_FOUND`,
`DOCUMENT_FOUND_NO_SUPPORTED_RELATION`, `DOCUMENT_NOT_MACHINE_READABLE`,
`DOCUMENT_NOT_FOUND`, `DOCUMENT_SCOPE_INCOMPLETE`,
`TARGET_IDENTITY_INCOMPLETE`, `SOURCE_UNSUPPORTED`, `SOURCE_ERROR`, and
`SCHEMA_ERROR`.

Missing extraction does not prove `NO_GUARANTOR_EXISTS`. Only an explicit
official statement can become strong negative evidence; otherwise the result
is `UNKNOWN`.

## 23. Source / Network Failure Contract

Timeout, HTTP error, rate limit, access denial, schema change, malformed
document, unexpected media type, and invalid content remain explicit failures.

```text
SOURCE_ERROR != DOCUMENT_FOUND_NO_SUPPORTED_RELATION
SOURCE_ERROR != NO_RELATION
```

Any future client must use finite timeouts and bounded retry only for transient
failures. CAPTCHA/authentication/anti-bot bypass is forbidden.

## 24. Read-Only Sample Probe

No CLI is implemented because a deterministic public official document locator
was not confirmed. A fixture-only adapter would fabricate source behavior.

```text
PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
DB_COVERAGE_RUN=false
NETWORK_USED=false
```

## 25. Controlled Sample

No live sample was run. The proposed anchors `RU000A1032P1`, `RU000A104511`,
and `RU000A107G55` are not assigned assumed support relationships.

A future authorized locator probe is bounded to at most ten securities and
must report identity status, document counts/types, retrieval and readability,
candidate counts, target-ID completeness, and blockers independently.

## 26. PIT Requirements

A future observation must preserve CBR snapshot/publication date, document
publication date, issue registration date, exact content hash, and retrieval
time. Evidence may not be backcast before public availability.

```text
PIT_CAPABILITY=LIMITED
CURRENT_DOCUMENT_MAY_NOT_BE_BACKCAST=true
```

Current registry archives support dated identity-state evidence, but no
complete historical original-document availability contract was proven.

## 27. Licensing and Automation Boundary

| Source | Access | Automation/licensing status |
|---|---|---|
| CBR securities registry and extract | Public official pages/downloads | `REQUIRES_REVIEW` for persistent automated reuse; no document locator proven |
| CBR electronic registration | Authenticated issuer submission channel | `RESTRICTED` for this task |
| Interfax disclosure gateway | Authenticated/subscription API | `RESTRICTED` without authorization |
| Issuer official sites | Source-specific public access | `UNKNOWN`; no universal automation contract |
| Search/third-party discovery | Provider-specific | `DISCOVERY_ONLY`, never evidence authority |

No unclear source is labeled `CLEAR_FOR_CURRENT_RESEARCH_USE`. No credentials
or secrets are introduced.

## 28. Service Architecture

No `CbrSecurityRegistryClient`, generic `IssueDocumentLocator`, or support-party
service is implemented. A future service may be added only after real endpoint,
row/document identity, pagination, retrieval, schema-error, and licensing
behavior are proven with official evidence.

Services, when authorized, must separate fetch from interpretation, remain
read-only, use fixture-backed CI, and expose no DB commit path.

## 29. CLI Architecture

No `scripts/issue_document_support_probe.py` is created. A future CLI may use a
versioned `bondradar.issue_document_support_probe.v1` schema only after the
locator contract is proven. It must expose failures and safety flags without
raw document bodies, secrets, or connection data.

## 30. Persistence Boundary

```text
MIGRATION=NONE
DATABASE_MUTATION_EXECUTED=false
RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false
LEGAL_ISSUER_MUTATED=false
FINANCIAL_REPORT_MUTATED=false
```

Task246A creates no document, candidate, relationship, review, or foreign-key
table. Persistence is not recommended until retrieval and review contracts are
proven separately.

## 31. Allowed Scope

Allowed work is limited to repository/source reconnaissance, official citations,
the fail-closed contract document, and deterministic tests of that document.
The focused test reads only the tracked Markdown file.

## 32. Forbidden Scope

Task246A performs no network request, live source probe, DB access, migration,
document download, OCR, LLM processing, relationship inference, persistence,
production/VDS access, scoring, recommendation, broker call, or trading action.

```text
ALEMBIC_EXECUTED=false
BOND_MUTATED=false
COMPANY_MUTATED=false
SECURITY_MASTER_MUTATED=false
SCORING_EXECUTED=false
BROKER_USED=false
TRADING_EXECUTED=false
```

## 33. Required Tests

The focused documentation test verifies the 38-section inventory, exact source
and status contracts, issue identifiers, document types, integrity and temporal
fields, role/scope separation, stable target identity, review-only and negative
evidence semantics, licensing findings, NO_GO decision, safety flags, and
Task246B handoff.

Behavioral source/extraction tests are deliberately not fabricated because no
source client or extractor exists.

## 34. Acceptance Criteria

- `AC1`: migration and production persistence are absent.
- `AC2`: exact issue identity precedes document interpretation.
- `AC3`: the official locator gap is explicit and not filled by search results.
- `AC4`: document bytes require immutable SHA-256.
- `AC5`: all support roles remain distinct and issue-scoped.
- `AC6`: stable target identity and manual review are mandatory.
- `AC7`: OCR, LLM and fuzzy matching are absent.
- `AC8`: source failure and missing extraction never become no relation.
- `AC9`: current evidence is not backcast.
- `AC10`: CI has no live internet dependency.
- `AC11`: no parent/group/reporting-entity work leaks into scope.
- `AC12`: the next task improves the locator before persistence.

## 35. Local Validation

Required commands are:

```text
python -m py_compile backend/tests/test_task246a_official_issue_document_support_contract.py
python -m pytest backend/tests/test_task245_issuer_relationship_source_contract.py backend/tests/test_task246a_official_issue_document_support_contract.py -q
git diff --check
```

The full backend suite is not required because application/shared code does not
change. An unexecuted suite is not reported as PASS.

## 36. Git / Diff / Commit / Push

The only allowed changed files are this document and its focused test. The
authorized commit message is `Add Official Issue Document Support Contract`.
Only a normal fast-forward push to `origin/main` is permitted; force/rebase and
CI polling are forbidden.

```text
CI=NOT_WAITED_BY_DESIGN
```

## 37. Final Report

The delivery report states status, commits, exact changed files,
`MIGRATION=NONE`, issue-identity and document-locator findings, source/document
contracts, machine readability, role/scope/target identity, review and PIT
boundaries, live-probe status, tests, scope validation, commit/push,
`CI=NOT_WAITED_BY_DESIGN`, and `PRODUCTION_ACTIONS=NONE`.

The recommended next work is:

```text
RECOMMENDED_TASK246B=Task246B — Authorized Official Disclosure Document Locator Contract
TASK246B_AUTOMATICALLY_UNLOCKED=false
```

Task246B must first prove a public or explicitly authorized document endpoint,
stable issue-to-document identity, exact byte retrieval, historical semantics,
and permissible automation. Reviewed extraction is a later separate task.

## 38. HARD STOP

Task246A hard-stops if implementation would require persistence, unauthorized
credentials, anti-bot bypass, search-engine evidence, fuzzy target matching,
issuer-wide support propagation, OCR/LLM interpretation, fabricated PIT
semantics, or unrelated changes.

```text
FINAL_DECISION=NO_GO_FOR_AUTOMATED_EXTRACTION
RECOMMENDED_NEXT_ACTION=IMPROVE_OFFICIAL_DOCUMENT_LOCATOR_FIRST
PRODUCTION_ACTIONS=NONE
```

Residual risks remain: document availability varies by issue; archives may be
incomplete; publication date is not necessarily effective date; target IDs may
be absent; support can change; legal terminology varies; scanned PDFs are not
machine-readable here; source URL/content and licensing terms may change; and a
reviewed support-party candidate still does not identify a reporting entity.
