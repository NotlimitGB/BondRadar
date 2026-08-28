# Task245 — Issuer Relationship Source Contract & Coverage Audit

## 1. Execution Profile

Task245 is a documentation-only, fail-closed source-contract audit.

```text
TASK_ID=Task245
MODE=DOCUMENTATION_ONLY_SOURCE_AUDIT
MIGRATION=NONE
PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
DB_COVERAGE_RUN=false
PRODUCTION_ACTIONS=NONE
RELATIONSHIP_PERSISTENCE_IMPLEMENTED=false
```

False relationship inference is materially worse than missing coverage. This
task does not create a corporate graph, relationship evidence, source adapters,
or a database population path.

## 2. Context

Task241 established an evidence-aware bond security master. Task242 established
the MOEX security-to-legal-issuer source contract. Task243 separated a Bond's
source-backed legal issuer mapping from legacy `Company`, and Task244 introduced
the canonical `LegalIssuer` identity keyed by source namespace and source issuer
ID.

Task244 intentionally stopped at legal-issuer identity. A legal issuer is not
automatically its parent, group, guarantor, sponsor, originator, or reporting
entity.

## 3. Current Production Baseline

The following values are prior Task244 production evidence supplied to Task245;
Task245 did not reconnect to or recount production:

```text
LegalIssuer=498
LegalIssuerEvidence=2995
LegalIssuer verified=498
BondLegalIssuerProfile verified=2995
LegalIssuer missing INN=2
Company=2996
CompanyIdentityProfile=3
FinancialReport=1
```

```text
PRODUCTION_BASELINE_KIND=PRIOR_PRODUCTION_EVIDENCE
CURRENT_RELATIONSHIP_COVERAGE=NOT_MEASURED_DURING_IMPLEMENTATION
```

No production, VDS, or live-source action was performed to validate these
counts in Task245.

## 4. Problem Statement

Current identity answers which legal entity issued a security. It does not
prove economic control, consolidated reporting scope, guarantee support, or an
SPV's sponsor/originator.

The following implications are invalid without separate evidence:

- legal issuer → parent;
- participant/shareholder → controlling parent;
- parent → guarantor;
- group membership → reporting entity;
- guarantor for one issue → guarantor for every issue;
- an issuer name containing a group brand → membership or support.

Task245 therefore rejects a generic `issuer -> related_company` abstraction.

## 5. Goal

The goal is to freeze a source-backed relationship taxonomy and determine what
official source families can actually support before any persistence schema is
designed.

The audit records source meaning, scope, stable identities, temporal support,
automation feasibility, licensing uncertainty, and fail-closed gaps. It does
not make any relationship current, verified, or eligible for downstream use.

## 6. Critical Domain Distinction

Every future relation must declare its scope independently of its type:

- `LEGAL_ISSUER` — an issuer-level relationship;
- `SECURITY` — one identified security;
- `ISSUE` — one registered issue;
- `ISSUE_PROGRAM` — one identified issuance program;
- `REPORTING_SCOPE` — an analytical financial-report mapping.

`GUARANTOR`, surety, offer, and collateral facts default to `SECURITY` or
`ISSUE`. They may not be promoted to `LEGAL_ISSUER` without explicit source
proof.

## 7. Relationship Taxonomy

The required relationship concepts remain separate:

| Relationship | Meaning | Default scope |
|---|---|---|
| `LEGAL_ISSUER_CLASSIFICATION` | Official entity/security classification, not ownership | `LEGAL_ISSUER` |
| `IMMEDIATE_PARENT` | Explicit direct controlling parent | `LEGAL_ISSUER` |
| `ULTIMATE_PARENT` | Explicit ultimate controlling entity | `LEGAL_ISSUER` |
| `GROUP_MEMBERSHIP` | Explicit economic/corporate group membership | `LEGAL_ISSUER` |
| `GUARANTOR` | Entity legally guaranteeing obligations | `SECURITY` or `ISSUE` |
| `SPV_SPONSOR_ORIGINATOR` | Explicit sponsor or originator role; roles remain distinguishable | `ISSUE` or `ISSUE_PROGRAM` |
| `REPORTING_ENTITY` | Entity whose statements are appropriate for a defined analysis | `REPORTING_SCOPE` |

Future contracts must additionally distinguish guarantor, surety provider,
offeror, collateral provider, sponsor, originator, servicer, parent, and group.

## 8. Evidence Quality Model

| Tier | Contract |
|---|---|
| `AUTHORITATIVE_STRUCTURED` | Explicit machine-readable official/primary field with stable subject and target identities |
| `AUTHORITATIVE_DOCUMENT` | Explicit fact in an official disclosure, prospectus, issue decision, or report, bound to document provenance |
| `PRIMARY_SEMI_STRUCTURED` | Explicit primary-source fact with weak schema or incomplete identifiers |
| `DISCOVERY_ONLY` | Candidate-generating evidence that cannot authorize persistence |
| `UNSUITABLE` | Ambiguous semantics, unstable/undocumented access, identity failure, or incompatible terms |

No weaker tier is upgraded for convenience. LLM output, name similarity, and
search snippets are never relationship evidence.

## 9. Starting Commit and Preflight

Task245 was locked to:

```text
BRANCH=main
STARTING_COMMIT=ae71e2a81024ca91452e1f37904da96145053358
TRACKED_WORKTREE=clean
ALEMBIC_HEAD=202608280002
```

The implementation adds no migration and changes no pre-existing source,
model, service, script, or configuration file.

## 10. Repository Investigation

Repository inspection found no authoritative relationship domain:

- `LegalIssuer` and `LegalIssuerEvidence` preserve Task243 legal-issuer
  identity and attributes only.
- `BondLegalIssuerProfile` and `BondLegalIssuerEvidence` map a Bond to its
  observed legal issuer, not to related entities.
- `BondSecurityMasterProfile` describes security terms and structure, not
  issuer ownership or support.
- `CompanyIdentityProfile.issuer_group_name` and `issuer_group_inn` are legacy
  application fields. They are not Task243/Task244 evidence and are not
  authoritative relationship facts.
- `FinancialReport.company_id` remains legacy Company ownership and cannot be
  treated as a reporting-entity proof.
- MOEX cashflow offer rows prove event structure, not offeror or guarantor
  identity.
- No current field preserves a structured guarantor, surety provider,
  originator, sponsor, or issue-document identity suitable for this contract.

Subject identity is available as
`LegalIssuer(identity_source, source_issuer_id)` with optional INN, OKPO, and
title. Bond-level source evidence additionally carries SECID and ISIN. No
existing model safely represents the target side of a relationship.

## 11. Source Families Investigated

### 11.1 MOEX ISS

The documented ISS contract supports named response blocks, projections, and
pagination. The current `/iss/securities.json` contract provides security and
issuer identity, while bond description and cashflow endpoints provide terms
and schedules. No documented field was established for parent, ultimate
parent, group, guarantor, sponsor/originator, or reporting entity.

- Official documentation: <https://www.moex.com/files/4be999zbzp80bx2bgmwayrtyx0>
- Result: structured issuer identity, `SOURCE_UNSUPPORTED_FOR_RELATION` for the
  Task245 relationship classes.

### 11.2 Bank of Russia

The securities registry exposes issue/security and issuer identifiers,
including ISIN, registration number, issuer INN/OGRN, and issuer name. It does
not expose the required relationships as structured fields.

- Securities registry: <https://www.cbr.ru/registries/rcb/reestr-cb>
- Financial-market participant directory: <https://cbr.ru/finorg/>
- Bond disclosure context: <https://www.cbr.ru/explan/obraschenie-obligaciy/>

The financial-organization directory can support subset classification such as
regulated financial-market participant. It does not provide universal issuer
classification or ownership/support relations.

### 11.3 FNS / EGRUL / Transparent Business

EGRUL integration is official structured XML with full snapshots and daily
changes, but access uses separately supplied subscriber credentials. Registry
records can expose stable INN/OGRN identity, participants/founders, management,
and managing organizations.

- Integration model: <https://www.nalog.gov.ru/rn77/service/egrip2/egrip_vzayim/>
- Integration access: <https://www.nalog.gov.ru/rn77/service/egrip2/>
- Transparent Business entry point: <https://www.nalog.gov.ru/donline/>

A participant, founder, director, or managing organization is not by itself a
controlling immediate parent. These fields are authoritative for their stated
registry roles but `DISCOVERY_ONLY` for parent/group classification until a
separate control rule and sufficient ownership/control evidence are approved.

### 11.4 Official disclosures and disclosure agencies

Mandatory issuer disclosures and accredited disclosure channels can locate
prospectuses, issue decisions, issuer reports, affiliation lists, and material
facts. The original disclosed document—not an aggregator's interpretation—must
remain the evidence object.

Interfax documents an authenticated REST gateway returning structured
disclosure-event metadata:
<https://e-disclosure.ru/poluchenie-informacii/shlyuz-api>.
Authentication/subscription and document heterogeneity prevent a Task245
credential-free source adapter.

### 11.5 Issue and prospectus documents

Issue decisions, prospectuses, programs, and issuer reports can explicitly
identify a guarantor, surety provider, offeror, collateral provider, sponsor,
or originator. This is `AUTHORITATIVE_DOCUMENT` only when the evidence retains:

- issue/security/program identifier;
- document locator and immutable checksum;
- document type and publication date;
- exact relationship label and scope;
- stable target identifier such as INN, OGRN, LEI, or official registry ID;
- page/section or structured field provenance;
- review state.

Document text alone, a target name alone, or a document search hit is not
automatically ingestible.

### 11.6 Issuer official sites

An issuer site can provide primary document evidence when the fact is explicit,
the locator is stable, the document is integrity-bound, and target identity is
anchored. It is a targeted fallback, not a universal 498-site scraper.

### 11.7 Commercial and third-party aggregators

Commercial databases, finance portals, search engines, Wikipedia, snippets,
and LLM output may discover a primary document. They remain `DISCOVERY_ONLY` or
`UNSUITABLE` and cannot become the persisted authority.

## 12. Source Eligibility Rules

A future automated source is eligible only when all are true:

1. relationship meaning is explicit;
2. subject maps exactly to a `LegalIssuer` source identity;
3. target has a stable legal identifier;
4. scope is explicit;
5. provenance and source locator are retainable;
6. observation and any source temporal fields are distinguishable;
7. absence is distinguishable from source/schema/network failure;
8. contradictory records can coexist and fail closed;
9. access is documented and operationally bounded;
10. automation/licensing conditions are reviewed;
11. no title-only or fuzzy primary matching is required.

No investigated source satisfied all of these conditions for an automated,
credential-free Task245 relationship adapter.

## 13. Source Capability Matrix

Legend: `READY`, `PARTIAL`, `DISCOVERY_ONLY`, `NO_GO`, and `CURRENT_ONLY` are
Task245 architecture findings, not data acceptance decisions.

| Source family | Classification | Immediate/ultimate parent | Group | Guarantor | Sponsor/originator | Reporting entity | Subject ID | Target ID | PIT | Structured API | Document provenance | Automation | Licensing | Tier / blocking limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MOEX ISS | `PARTIAL` metadata hints only | `NO_GO` | `NO_GO` | `NO_GO` | `NO_GO` | `NO_GO` | emitent ID, INN; SECID/ISIN | none for relation | `CURRENT_ONLY` | documented JSON | no relationship document contract | safe for existing identity calls, unsupported for relations | `REQUIRES_REVIEW` | `AUTHORITATIVE_STRUCTURED` identity only; relationship fields absent |
| CBR securities registry | `PARTIAL` security/issuer categories | `NO_GO` | `NO_GO` | `PARTIAL` only via separately located issue documents | `PARTIAL` documents | `DISCOVERY_ONLY` documents | INN/OGRN, security registry ID, issue number, ISIN | document-dependent | registry current/history varies; document dates available | public structured/table views, no proven relation API | authoritative registry/document locators | relationship automation not proven | `REQUIRES_REVIEW` | registry identifies issuer/issue, not related entity |
| CBR financial-organization directory | `PARTIAL` regulated subset | `NO_GO` | `NO_GO` | `NO_GO` | `NO_GO` | `NO_GO` | INN/OGRN/registry number | not applicable | `CURRENT_ONLY` unless dated register used | public search/register files; no frozen universal API | registry provenance | subset-only | `REQUIRES_REVIEW` | `AUTHORITATIVE_STRUCTURED` classification for covered regulated types only |
| FNS/EGRUL integration | `PARTIAL` legal form/status | `DISCOVERY_ONLY` participant/manager | `DISCOVERY_ONLY` | `NO_GO` | `DISCOVERY_ONLY` | `NO_GO` | INN/OGRN | INN/OGRN when supplied | full snapshot plus daily changes; not a ready as-of resolver | credentialed bulk XML | registry file/version/date | requires access setup and dedicated parser | `REQUIRES_REVIEW` | authoritative registry roles do not prove control |
| Accredited disclosure channels | `DISCOVERY_ONLY` | `PARTIAL` documents | `PARTIAL` documents | `PARTIAL` | `PARTIAL` | `DISCOVERY_ONLY` | issuer code/INN and document metadata vary | document-dependent | publication timestamp and historical documents | authenticated/subscription APIs may exist | strong when original document is retained | contract and document pipeline required | `REQUIRES_REVIEW` | `AUTHORITATIVE_DOCUMENT`; heterogeneous identity and wording |
| Issue/prospectus documents | `PARTIAL` | `PARTIAL` only if explicit | `PARTIAL` only if explicit | `PARTIAL` strongest available path | `PARTIAL` strongest available path | `DISCOVERY_ONLY` | issue/program/ISIN/registration number | INN/OGRN/LEI/registry ID when present | publication/effective fields are document-specific | generally PDF/text, not universal API | required checksum/page/section | review-gated extraction only | inherits locator terms; `REQUIRES_REVIEW` | `AUTHORITATIVE_DOCUMENT`; missing target IDs block automation |
| Issuer official sites | `PARTIAL` targeted | `PARTIAL` targeted | `PARTIAL` targeted | `PARTIAL` targeted | `PARTIAL` targeted | `DISCOVERY_ONLY` | issuer INN/official domain/document | document-dependent | source-specific | heterogeneous | possible for stable documents | targeted manual discovery | `UNKNOWN` per site | `PRIMARY_SEMI_STRUCTURED` or `AUTHORITATIVE_DOCUMENT` after validation |
| Third-party aggregators/search | `DISCOVERY_ONLY` | `DISCOVERY_ONLY` | `DISCOVERY_ONLY` | `DISCOVERY_ONLY` | `DISCOVERY_ONLY` | `DISCOVERY_ONLY` | inconsistent | inconsistent/name-only common | unknown | provider-specific | must link to primary source | not eligible as authority | `UNKNOWN` or `RESTRICTED` | `DISCOVERY_ONLY`; never automatic evidence |

## 14. Identity Anchoring Contract

The relationship subject is:

```text
identity_source + source_issuer_id
```

Optional subject attributes are INN, OKPO, and title. They do not replace the
natural source identity.

An automatically ingestible target requires at least one stable legal anchor:
INN, OGRN, LEI, official registry ID, or a reviewed source-specific entity ID.
Target title remains an attribute. Title-only, normalized-name similarity,
brand matching, shared substrings, and same-INN inference are forbidden as the
primary mapping mechanism.

A source relation with explicit text but no stable target identity must become
`TARGET_IDENTITY_INCOMPLETE` and remain unresolved.

## 15. Temporal / PIT Contract

Future relationship evidence must keep separate nullable fields for:

- `observed_at` — when BondRadar observed the source;
- `published_at` — when the source published the record/document;
- `effective_from` and `effective_to` — only when explicitly supplied;
- document/report period where applicable.

```text
CURRENT_RELATIONSHIP != PIT_RELATIONSHIP
CURRENT_ONLY_MAY_NOT_BE_BACKCAST=true
```

MOEX security reference and public directory searches are treated as
`CURRENT_ONLY` unless a dated source contract proves otherwise. EGRUL bulk
snapshots and changes contain source dates but require a separate as-of
resolver. Disclosure documents preserve publication history, while the legal
effective interval must not be invented from publication time.

## 16. Relationship Scope Contract

Every future candidate must contain one of:

```text
LEGAL_ISSUER
SECURITY
ISSUE
ISSUE_PROGRAM
REPORTING_SCOPE
```

The candidate must also carry the exact issue/security/program identity when
the scope is not `LEGAL_ISSUER`. Missing scope is a blocker. No service may
silently broaden a relation's scope.

## 17. Parent / Group Semantics

The following roles are not interchangeable:

- founder;
- participant/shareholder;
- director;
- managing organization;
- immediate controlling parent;
- ultimate parent;
- group member.

EGRUL registry roles may create discovery candidates under their exact source
labels. Without explicit control semantics, ownership share, and stable target
identity they may not become `IMMEDIATE_PARENT`, `ULTIMATE_PARENT`, or
`GROUP_MEMBERSHIP`. Transitive ultimate-parent inference is prohibited until
every intermediate edge is independently evidenced and temporally compatible.

## 18. Guarantor Semantics

```text
GUARANTOR_DEFAULT_SCOPE=SECURITY_OR_ISSUE
ISSUER_WIDE_GUARANTEE_INFERRED=false
```

A guarantor, surety provider, offeror, and collateral provider remain different
legal roles. One issuer may have two securities with different guarantors or
with support on only one security. A future document adapter must bind the
relation to the exact ISIN/registration number/issue program and document.

## 19. SPV / Sponsor / Originator Semantics

SPV, sponsor, originator, servicer, parent, guarantor, and reporting entity may
all be different entities. No role is inferred from a name containing `СФО`,
`ипотечный агент`, a group brand, or another lexical marker. Such a marker can
only be a non-authoritative `DIAGNOSTIC_HINT`.

Only an explicit source role with stable identities and issue/program scope can
support future `SPV_SPONSOR_ORIGINATOR` evidence.

## 20. Reporting Entity Semantics

`REPORTING_ENTITY` is an analytical mapping answering which entity's financial
statements are suitable for a defined credit analysis. It is not automatically
the legal issuer, parent, ultimate parent, guarantor, or group.

Issuer reports, consolidated statements, issue documents, or rating analysis
may generate candidates, but a separate methodology and review contract is
required before attaching any `FinancialReport`. Task245 changes neither report
ownership nor credit/scoring logic.

## 21. Legal Issuer Classification

Task245 status:

| Relationship | Status | Reason |
|---|---|---|
| `LEGAL_ISSUER_CLASSIFICATION` | `PARTIAL` | CBR/FNS can prove selected registry classes, not a universal taxonomy |
| `IMMEDIATE_PARENT` | `DISCOVERY_ONLY` | EGRUL participant/manager roles do not prove control |
| `ULTIMATE_PARENT` | `NO_GO` | no deterministic universal source contract established |
| `GROUP_MEMBERSHIP` | `DISCOVERY_ONLY` | group labels and participation are insufficient |
| `GUARANTOR` | `PARTIAL` | authoritative issue documents can prove issue-scoped support |
| `SPV_SPONSOR_ORIGINATOR` | `PARTIAL` | heterogeneous authoritative documents can state exact roles |
| `REPORTING_ENTITY` | `DISCOVERY_ONLY` | requires separate analytical methodology and review |

Name regexes are never authoritative classification. CBR registry membership
may classify a covered regulated entity; absence from a subset registry cannot
classify it as non-financial.

## 22. Read-Only Coverage Probe

No Task245 probe is implemented because no investigated contract provides a
documented, credential-free, stable combination of relationship meaning,
scope, subject identity, and target identity.

```text
PROBE_IMPLEMENTED=false
LIVE_PROBE_RUN=false
DB_COVERAGE_RUN=false
CURRENT_RELATIONSHIP_COVERAGE=NOT_MEASURED_DURING_IMPLEMENTATION
```

Creating a generic adapter backed only by fixtures would invent source
behavior. That is explicitly rejected. The absence of a probe is a fail-closed
result, not evidence that no relationships exist.

## 23. Future Probe Architecture

A later source-specific probe may expose explicit-sample and database-coverage
modes only after its access and identity contracts are proven. Its per-subject
statuses are frozen as:

```text
SOURCE_SUCCESS
SOURCE_SUCCESS_NO_RELATION
SUBJECT_NOT_FOUND
TARGET_IDENTITY_INCOMPLETE
RELATION_AMBIGUOUS
SOURCE_UNSUPPORTED_FOR_RELATION
SOURCE_ERROR
```

`SOURCE_ERROR` must never become `SOURCE_SUCCESS_NO_RELATION`.

Every candidate relation must contain subject source issuer ID, optional
subject INN, relationship type, scope, target title, target stable identifiers,
source family, exact locator, source record/document ID, observation time,
available publication/effective times, evidence quality, ingestion eligibility,
ordered blockers, and exact raw field names used. Full documents and raw
payloads remain excluded.

Contract cases that a future source adapter must prove include: explicit stable
target success; correct no-relation response; schema/network failure; name-only
target rejection; participant ambiguity; issue-scoped guarantor; different
guarantors for securities of the same issuer; current-only labeling; missing
subject INN; foreign identity; deterministic normalization; and licensing
fail-closed behavior.

## 24. Network Safety and Source Etiquette

Any future live probe must use finite connect/read timeouts, bounded retry only
for transient failures, an explicit user agent, request pacing, bounded samples,
and per-source error reporting. CI must use fixtures.

The following remain forbidden: concurrency spikes, CAPTCHA bypass,
authentication circumvention, anti-bot bypass, undocumented deprecated core
endpoints, uncontrolled scraping, response caching containing raw documents,
and secret retention.

Task245 itself performs no network calls in its implementation or tests.

## 25. Licensing / Terms Audit

| Source | Public access | Automation/terms finding | Status |
|---|---|---|---|
| MOEX ISS | public endpoints and documentation | relationship fields absent; non-display/data-product terms require source-specific review | `REQUIRES_REVIEW` |
| CBR registries/pages | public official pages | use/redistribution and per-resource machine access must be reviewed before persistent automation | `REQUIRES_REVIEW` |
| FNS open data | public datasets may permit automated reuse with attribution | EGRUL integration uses subscriber access attributes and a separate delivery contract | `REQUIRES_REVIEW` |
| Interfax/e-disclosure | public pages; automated gateway is authenticated | subscription/API agreement required | `REQUIRES_REVIEW` |
| Other accredited agencies | public disclosure varies | API, redistribution, and automation terms not established here | `REQUIRES_REVIEW` |
| Issuer official sites | source-specific | no universal automation license | `UNKNOWN` |
| Commercial/third-party sources | provider-specific | discovery only unless separately contracted | `UNKNOWN` or `RESTRICTED` |

No source with unclear terms is labeled
`CLEAR_FOR_CURRENT_RESEARCH_USE`. This audit makes no legal conclusion beyond
the inspected source documentation.

## 26. Deliverables

Task245 creates exactly:

1. this audit document;
2. one focused documentation contract test.

It deliberately creates no model, migration, service, CLI, fixture adapter,
configuration, scheduled job, or report artifact.

## 27. Allowed Scope

Allowed work is limited to source reconnaissance, repository inspection,
official citations, documentation of contracts and gaps, and deterministic
tests of this document's required sections and safety statements.

The focused test may read this tracked document. It does not import application
models, connect to a database, or call an external source.

## 28. Forbidden Scope

Task245 does not create or mutate:

- relationship tables or foreign keys;
- `LegalIssuer` or `LegalIssuerEvidence`;
- Bond, Bond legal-issuer mapping, Security Master, Company, or Company identity;
- FinancialReport attachment or values;
- strategy, scoring, risk, backtest, paper/shadow, broker, or trading state;
- production/VDS data;
- source credentials, schedulers, or network caches.

```text
DATABASE_MUTATION_EXECUTED=false
ALEMBIC_EXECUTED=false
LEGAL_ISSUER_MUTATED=false
BOND_MUTATED=false
COMPANY_MUTATED=false
FINANCIAL_REPORT_MUTATED=false
SCORING_EXECUTED=false
BROKER_USED=false
TRADING_EXECUTED=false
```

## 29. Tests

The focused documentation test verifies:

- all 34 numbered sections exist once and in order;
- the seven relationship statuses match the Task245 decision;
- all source families, official locators, evidence tiers, temporal and scope
  contracts are present;
- future probe statuses and the source-error/no-relation distinction are frozen;
- licensing uncertainty cannot be promoted to clear use;
- the Task246 split and NO_GO areas are explicit;
- all documentation-only and safety invariants remain present.

Probe behavior tests are not fabricated because no probe implementation exists.

## 30. Acceptance Criteria

- `AC1`: no relationship persistence schema exists.
- `AC2`: legal issuer, parent, group, guarantor, sponsor/originator, and reporting
  entity remain separate.
- `AC3`: guarantor scope defaults to security/issue.
- `AC4`: automated target identity requires stable identifiers.
- `AC5`: title/fuzzy matching is non-authoritative.
- `AC6`: source errors cannot become no relation.
- `AC7`: current-only evidence cannot be backcast.
- `AC8`: each investigated source has an evidence-quality finding.
- `AC9`: each source has an automation/licensing status.
- `AC10`: MOEX relationship fields are not assumed from issuer identity.
- `AC11`: CBR subset classification is not generalized.
- `AC12`: EGRUL participants/managers are not converted to parents.
- `AC13`: disclosure metadata and original documents remain separate.
- `AC14`: operating, sovereign, SPV/securitization, foreign, and missing-INN
  archetypes remain representable without fabricated relationships.
- `AC15`: every relationship type is marked `PARTIAL`, `DISCOVERY_ONLY`, or
  `NO_GO`.
- `AC16`: Task246 recommendations are source- and scope-specific.
- `AC17`: no production/VDS action occurs.

## 31. Local Validation

Required local validation is limited to:

```text
python -m py_compile backend/tests/test_task245_issuer_relationship_source_contract.py
python -m pytest backend/tests/test_task245_issuer_relationship_source_contract.py -q
git diff --check
```

Task242–Task244 regressions and the full backend suite are not required because
no application or shared source code changes. An incomplete or unexecuted full
suite is not reported as PASS.

## 32. Git / Diff / Commit / Push

The allowed changed-file inventory is exactly:

```text
docs/audits/TASK245_ISSUER_RELATIONSHIP_SOURCE_CONTRACT.md
backend/tests/test_task245_issuer_relationship_source_contract.py
```

After focused verification, the authorized commit message is:

```text
Add Issuer Relationship Source Contract
```

Only a normal fast-forward push to `origin/main` is authorized. Force, rebase,
and CI polling are prohibited.

```text
CI=NOT_WAITED_BY_DESIGN
```

## 33. Final Report Contract

The delivery report must state status, starting/ending commits, exact changed
files, `MIGRATION=NONE`, source families and exact locators, relationship
capability matrix, identity anchoring, temporal/PIT findings, guarantor scope,
coverage status, licensing, probe status, tests, scope validation, commit/push,
`CI=NOT_WAITED_BY_DESIGN`, and `PRODUCTION_ACTIONS=NONE`.

Coverage must remain `NOT_MEASURED_DURING_IMPLEMENTATION`; prior Task244 counts
must not be represented as newly measured relationship coverage.

## 34. Hard Stop and Recommended Task246

Task245 hard-stops if implementation would require relationship persistence,
LegalIssuer/Company mutation, fuzzy authoritative matching, issuer-wide
guarantor flattening, authentication/CAPTCHA bypass, an undocumented core
endpoint, fabricated PIT semantics, or unisolated changes.

The recommended next work is deliberately split:

1. `Task246A — Issue Document Locator and Guarantor Evidence Contract`:
   establish an authorized official locator, exact issue/program identifiers,
   document checksum and publication time, explicit guarantor/surety/offeror
   extraction, stable target identity, and mandatory manual review.
2. A separately authorized FNS/EGRUL participant/managing-organization
   discovery contract that preserves source roles and never converts them to
   parent/control automatically.
3. A separate CBR/FNS registry-membership classification contract, isolated
   from ownership and support graphs.

```text
TASK246_AUTOMATICALLY_UNLOCKED=false
TASK246_EXECUTED=false
```

Residual risks remain: incomplete source coverage; changing corporate control;
current-state leakage into historical research; group/parent/guarantor
conflation; security-specific guarantees; legal issuer/reporting-entity
divergence; incomplete foreign identities; document-extraction error; and
changing licensing or automation conditions.
