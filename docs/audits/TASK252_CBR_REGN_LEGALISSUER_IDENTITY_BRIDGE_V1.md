# Task252 — CBR REGN → LegalIssuer Identity Bridge v1

## 1. Execution profile

```text
TASK=Task252
IMPLEMENTATION=READ_ONLY_CURRENT_IDENTITY_BRIDGE
STARTING_SHA=d4ada964d717ff19f54b4241831b3d92f48ae997
ALEMBIC_HEAD=202608280002
```

## 2. Context

Task251 supplies exact CBR regulatory-reporting subjects as REGN strings, while the existing LegalIssuer Master is rooted in MOEX issuer identity and exposes a nullable exact INN. Task252 connects those existing boundaries without changing either one.

## 3. Repository preflight

```text
LEGAL_ISSUER_HAS_INN=true
LEGAL_ISSUER_HAS_OGRN=false
LEGAL_ISSUER_HAS_CBR_REGN=false
EXISTING_CBR_IDENTITY_BRIDGE=false
EXISTING_SOAP_CLIENT=false
EXISTING_XML_CLIENT=false
TASK251_REGN_AVAILABLE=true
TASK251_DATABASE_PERSISTENCE=false
MIGRATION_REQUIRED=false
```

## 4. Goal

The only identity chain is:

```text
REGN -> OGRN -> INN -> LegalIssuer
```

Every arrow is exact. Names are diagnostic fields and never identity keys.

## 5. Why the bridge exists

A Task251 record keyed by REGN is not attributable to a BondRadar issuer until the official identifier chain has been resolved. Ticker, title similarity, transliteration, addresses, LLM output and manual aliases are prohibited.

## 6. Authoritative identity chain

FullCoList contributes current REGN → OGRN. FinOrg contributes current OGRN → INN. BondRadar contributes exact INN → LegalIssuer. No source-internal FinOrg ID becomes a BondRadar identifier.

## 7. FullCoList authority

Official source: [CBR FullCoList](https://www.cbr.ru/banking_sector/credit/FullCoList/). The page is an official current registry surface and exposes REGN, OGRN, organization name, organization kind, legal form, registration date, licence status and location.

## 8. FinOrg authority

Official source contracts: [SearchByOGRNs](https://www.cbr.ru/FO_ZoomWS/FinOrg.asmx?op=SearchByOGRNs) and [GetLastUpdate](https://www.cbr.ru/FO_ZoomWS/FinOrg.asmx?op=GetLastUpdate). Task252 uses SOAP 1.1 and the action `http://web.cbr.ru/SearchByOGRNs`.

## 9. LegalIssuer boundary

LegalIssuer remains MOEX-rooted. Task252 reads only `id`, `issuer_inn`, `resolution_state`, `source_issuer_id` and `issuer_title`. It adds no CBR fields and performs no writes.

## 10. Resolution states

The bridge records VERIFIED and separate fail-closed states for missing/ambiguous REGN, missing/conflicting OGRN, FinOrg not-found/source-error/mismatch/missing-invalid-conflicting INN, LegalIssuer not evaluated/not found/ambiguous/not verified.

## 11. Current-only boundary

```text
CURRENT_IDENTITY_BRIDGE=true
HISTORICAL_IDENTITY_BRIDGE=false
PIT_STATUS=CURRENT_ONLY
HISTORICAL_BACKCAST_ALLOWED=false
```

Task251 report date, FullCoList registry date, FinOrg last update and bridge retrieval time remain distinct.

## 12. Source clients

The source boundary uses `httpx`, GET only for FullCoList and POST only for the two documented SOAP operations. It sets a fixed User-Agent and never accepts credentials, cookies or arbitrary hosts.

## 13. FullCoList parser

The parser locates semantic Russian headers rather than trusting column position, requires a page-level “по состоянию на DD.MM.YYYY” date, preserves nullable descriptive metadata and rejects a missing identity table or malformed identifiers.

## 14. FinOrg SOAP client

The request is deterministic and contains validated OGRNs only. The response must contain `SearchByOGRNsResult`, `DS`, `IsSucess=true`, and only requested OGRNs. `Error`, `ErrorText`, Id, OGRN, INN, Name and Status are handled as distinct fields.

## 15. Batching and resource safety

```text
MAX_OGRNS_PER_REQUEST=100
MAX_TOTAL_OGRNS_PER_RUN=1000
FULLCOLIST_RESPONSE_LIMIT=4_MIB
FINORG_RESPONSE_LIMIT=2_MIB
MAX_REDIRECTS=3
MAX_TRANSIENT_ATTEMPTS=3
```

The live validation budget is one FullCoList logical call, one GetLastUpdate logical call and at most four SearchByOGRNs logical calls.

## 16. Identifier normalization

REGN is a positive digits-only string canonicalized with non-semantic leading zeroes removed. OGRN is exactly 13 digits. INN is exactly 10 digits. Identifiers never pass through float, are never padded and missing values never become zero.

## 17. Conflict semantics

Identical records deduplicate. Same REGN with multiple OGRNs is `CBR_REGN_AMBIGUOUS`; one OGRN attached to multiple REGNs is `CBR_OGRN_CONFLICT`; multiple INNs for one OGRN is `FINORG_INN_CONFLICT`; multiple LegalIssuer rows for one INN is `LEGAL_ISSUER_INN_AMBIGUOUS`.

## 18. LegalIssuer resolver

The resolver accepts an injected SQLAlchemy Session and issues bounded SELECT statements under `no_autoflush`. Exact `issuer_inn` is the sole join key. One non-verified result is blocked; duplicates are ambiguous. Title mismatch is warning-only.

## 19. Bridge snapshot

The immutable snapshot retains ordered source records and results, source dates, state counts and deterministic hashes for requested REGNs, source-resolved REGNs and LegalIssuer-verified REGNs. It contains no financial values.

## 20. Task251 integration

REGNs are the union of Task251 value-bearing `subjects` across 0409101/0409102/0409123/0409135. Support and nomenclature rows do not enter the bridge. Task251 artifact hashes, schemas, records, Decimal values and subject semantics are unchanged.

## 21. Probe CLI

The CLI exposes only `--source-only --task251-fixture-report-date YYYY-MM-DD`, emits compact JSON schema `bondradar.cbr_legal_issuer_bridge_probe.v1`, has no DB mode, DB URL, session factory or output-file option, and never claims LegalIssuer coverage.

## 22. Live source validation

Bounded source-only validation is performed once against current CBR identity sources using the existing Task251 immutable RAR fixtures. No RAR is downloaded. Measured results are recorded below after the mandatory run.

```text
LIVE_SOURCE_VALIDATION=PASS
TASK251_UNION_REGNS=353
FULLCOLIST_ROWS=1894
REGN_FOUND=353
REGN_MISSING=0
UNIQUE_OGRNS=353
FINORG_FOUND=353
FINORG_MISSING=0
INN_PRESENT=353
INN_MISSING=0
REGN_TO_INN_RESOLVED=353
REGN_TO_INN_UNRESOLVED=0
REGISTRY_CONFLICTS=0
FINORG_CONFLICTS=0
SOURCE_ERRORS=0
FULLCOLIST_LOGICAL_CALLS=1
GET_LAST_UPDATE_LOGICAL_CALLS=1
SEARCH_BY_OGRNS_LOGICAL_CALLS=4
REGISTRY_AS_OF=2026-08-30
FINORG_LAST_UPDATE=2026-08-30T00:00:00+00:00
LEGAL_ISSUER_COVERAGE_MEASURED=false
```

## 23. Allowed scope

Only the new bridge package, synthetic bridge fixtures, one focused test, source-only probe and this audit are added.

## 24. Forbidden scope

No migration, model change, persistence, DB mutation, production DB/VDS access, deployment, title/fuzzy/LLM matching, financial normalization, credit metrics, scoring, strategy, broker or trading work is authorized.

## 25. Focused verification

Focused tests cover HTML, SOAP/XML, HTTP containment, all identity states, SELECT-only LegalIssuer resolution, Task251 integration, deterministic hashes, CLI sanitization and PIT flags.

## 26. Local test policy

Only focused Task252, direct Task251 integration, changed-Python compileall, one bounded live source-only run and diff/scope validation are local gates. Broad regressions are delegated to CI.

## 27. Acceptance projection

Task252 passes only if both official source contracts support exact identifiers, live validation stays within budget, focused tests pass and no mutation or scope expansion occurs.

## 28. Scope validation

```text
MIGRATION=NONE
DATABASE_PERSISTENCE=false
DATABASE_MUTATION_EXECUTED=false
PRODUCTION_DATABASE_ACCESSED=false
VDS_ACCESSED=false
FUZZY_MATCHING=false
TITLE_MATCHING_USED_FOR_IDENTITY=false
NORMALIZATION=false
SCORING=false
PRODUCTION_ACTIONS=NONE
```

## 29. Delivery

After all gates pass, the single authorized commit is `Add CBR LegalIssuer Identity Bridge`, followed by a normal fast-forward push. No force/rebase/deploy is permitted.

## 30. Reporting status

GitHub CI is not polled: `CI=NOT_WAITED_BY_DESIGN`. Local source-only validation does not measure production LegalIssuer coverage.

## 31. Recommended Task253

`Task253 — CBR REGN → LegalIssuer Production Read-Only Coverage Probe` may later run exact SELECT-only coverage against production under separate authorization. It is not started or unlocked automatically.

## 32. Hard stop and residual risks

The bridge is current-only; historical identity is not proven; LegalIssuer production coverage is not measured; two previously observed LegalIssuer rows lack INN; CBR source structures may change; inactive/reorganized institutions may appear; no bridge evidence is persisted; and raw financial evidence remains unpersisted, unnormalized and unscored.
