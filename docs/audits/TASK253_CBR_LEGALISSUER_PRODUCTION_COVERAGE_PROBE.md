# Task253 — CBR REGN → LegalIssuer Production Read-Only Coverage Probe v1

## 1. Result

Task253 adds a backend-image-compatible, current-only coverage probe. Delivery of
the probe is not authorization to run it against production.

```text
IMPLEMENTATION=READ_ONLY_PROBE
PRODUCTION_EXECUTION=NOT_AUTHORIZED
PRODUCTION_EXECUTION=NOT_EXECUTED
```

## 2. Baseline

```text
STARTING_SHA=7e23d267b0ee7ea1bd58fc037c3f1a9a62581f6b
ALEMBIC_HEAD=202608280002
DATABASE_DIALECT_PRODUCTION=postgresql
```

## 3. Scope

Task253 consists only of the coverage module, its focused test, and this audit.
It changes no model, migration, configuration, Docker file, Task251 code,
Task252 identity semantics, or root script.

## 4. Entry point

The backend-image entry point is:

```text
python -m app.services.cbr_legal_issuer_bridge.coverage_probe
```

It requires a Task251 fixture report date, the name of an environment variable
holding the database URL, and an explicit read-only confirmation flag. A raw
database URL is not accepted as an argument.

## 5. Database configuration boundary

The environment-variable name is validated before lookup. Its value is read
only into process memory and is never emitted. Missing configuration and an
invalid or non-PostgreSQL URL fail before database connection.

## 6. Transaction boundary

The first two database statements are exactly:

```sql
SET TRANSACTION READ ONLY
SHOW transaction_read_only
```

Coverage SELECTs are allowed only when the verified value is exactly `on`.
Every outcome rolls back and closes the session and disposes the dedicated
engine. The session has `autoflush=False`.

## 7. Mutation boundary

The module exposes no add, flush, commit, delete, DML, DDL, migration, or
persistence operation.

```text
DATABASE_MUTATION_EXECUTED=false
DATABASE_PERSISTENCE=false
MIGRATION=NONE
MODEL_CHANGES=false
```

## 8. Task251 fixture contract

The probe reads the four existing approved Task251 RAR fixtures from the
backend test-fixture inventory. Before parsing, every fixture must match the
Task251 byte count and SHA-256 identity for the requested date. The probe uses
the existing Task251 bundle service and derives the REGN union from accepted
value-bearing subjects; the union count is not hardcoded.

For the approved `2026-08-01` fixtures, focused validation retains the existing
353-REGN union. Supporting and nomenclature rows do not create subjects.

## 9. Task252 bridge reuse

The probe invokes `CbrLegalIssuerBridgeService.bridge_regns` exactly once and
supplies the existing `LegalIssuerInnResolver`. The identity chain remains:

```text
CBR REGN -> FullCoList OGRN -> FinOrg INN -> exact LegalIssuer INN
```

No title, fuzzy, substring, transliteration, or inferred identity route exists.

## 10. LegalIssuer inventory

A bounded aggregate SELECT reports:

```text
legal_issuer_total
legal_issuer_verified
legal_issuer_with_inn
legal_issuer_without_inn
```

`LEGAL_ISSUER_NOT_FOUND` stays separate from upstream source-identity failures
and from ambiguous/non-verified identity-quality blockers.

## 11. Bond profile coverage

Only Task252 `VERIFIED` results contribute exact MOEX `source_issuer_id`
identities. Bond profile coverage requires all of:

```text
contract_version=bond-legal-issuer-mapping-v1
mapping_state=verified
mapping_source=moex_security_reference
source_issuer_id=exact Task252 verified source identity
```

Titles and INNs are not used to infer a Bond mapping. The output reports
matched profile rows and distinct Bond IDs separately.

## 12. State completeness

The JSON includes every `CbrBridgeState` key on every successful probe run,
including explicit zero counts. This keeps missing, ambiguous, invalid,
not-found, non-verified, and verified outcomes distinguishable.

## 13. Hashes

The report includes deterministic SHA-256 set hashes for requested REGNs,
source-resolved REGNs, LegalIssuer-verified REGNs, matched LegalIssuer IDs, and
matched Bond IDs. Hash inputs are sorted unique canonical identifiers; database
row order cannot change the result.

## 14. Time contract

The report keeps separate:

- Task251 report date;
- CBR registry as-of date;
- FinOrg last-update time;
- source retrieval time;
- probe generation time.

```text
PIT_STATUS=CURRENT_ONLY
HISTORICAL_BACKCAST_ALLOWED=false
```

Current identity resolution must not be projected backward into historical
research.

## 15. Output contract

The compact JSON schema is:

```text
bondradar.cbr_legal_issuer_production_coverage_probe.v1
```

It contains aggregates, state counts, hashes, timestamps, and safety flags. It
contains no identity-row dump, company names, source payloads, financial values,
SQL, filesystem paths, URLs, environment values, DSNs, credentials, or exception
text.

## 16. Failure contract

Invalid arguments exit `2`. Missing/invalid configuration and sanitized runtime
failures exit `1`. Complete coverage exits `0`. Failure JSON contains only the
schema, `status=failed`, and a fixed error code.

## 17. Test adapter

Focused tests use a disposable SQLite database behind a private PostgreSQL-guard
adapter. The adapter accepts only the two guard statements and SELECTs, allowing
the same production query and resolver code to be exercised without contacting
production or weakening the PostgreSQL-only CLI contract.

## 18. Determinism evidence

Synthetic tests cover exact verified, missing, ambiguous, and non-verified
LegalIssuer outcomes; verified and non-verified Bond profiles; repeated source
identity mappings; all state keys; stable hashes; and reordered source input.
Equivalent logical projections produce byte-equivalent report dictionaries for
an injected retrieval and generation time.

## 19. Containment evidence

Focused guards prove confirmation is required before engine creation, a
non-PostgreSQL URL is rejected before connection, read-only verification must be
`on`, and rollback/close/dispose run after a sanitized failure. Source inspection
rejects mutation methods and DML/DDL surfaces.

## 20. Safety projection

```text
TASK251_CHANGED=false
TASK252_IDENTITY_SEMANTICS_CHANGED=false
PRODUCTION_DATABASE_ACCESSED=false
VDS_ACCESSED=false
DATABASE_MUTATION_EXECUTED=false
DATABASE_PERSISTENCE=false
FUZZY_MATCHING=false
TITLE_IDENTITY=false
NORMALIZATION=false
SCORING=false
PRODUCTION_ACTIONS=NONE
```

## 21. Non-executed operational template

The following is documentation only. It was not run by Task253 delivery:

```text
docker compose -f docker-compose.prod.yml exec -T backend \
  python -m app.services.cbr_legal_issuer_bridge.coverage_probe \
  --task251-fixture-report-date 2026-08-01 \
  --database-url-env DATABASE_URL \
  --confirm-read-only
```

## 22. Next step

Task253 stops after delivery. The sole next step is:

```text
ONE_CONTROLLED_VDS_READ_ONLY_COVERAGE_RUN_AFTER_EXPLICIT_USER_AUTHORIZATION
```

That run requires a separate explicit authorization and does not authorize
persistence, remediation, normalization, scoring, or any trading action.
