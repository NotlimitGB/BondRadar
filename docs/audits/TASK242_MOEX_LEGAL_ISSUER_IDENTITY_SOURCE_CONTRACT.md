# Task242 — MOEX Legal Issuer Identity Source Contract

## 1. Status and boundary

Task242 adds a deterministic source contract and a read-only coverage probe for
official MOEX security-reference data. It does not create or verify a legal
issuer identity, alter `Bond.company_id`, update `Company` or
`CompanyIdentityProfile`, merge companies, or change Pilot Universe eligibility.

`COVERAGE_STATUS=NOT_MEASURED_DURING_IMPLEMENTATION`

No live MOEX request and no database coverage run was made while implementing
this contract. Source behavior is covered by the official ISS named-table
contract and synthetic fixtures.

## 2. Official source contract

The source is the official MOEX ISS discovery route:

`GET /iss/securities.json`

The request uses:

- `q=<normalized exact identifier query>`;
- `iss.meta=off`;
- `iss.only=securities`;
- `securities.columns=secid,isin,shortname,name,primary_boardid,emitent_id,emitent_title,emitent_inn,emitent_okpo`;
- bounded `start`/`limit` pagination.

The [official MOEX ISS developer manual](https://www.moex.com/files/4be999zbzp80bx2bgmwayrtyx0)
documents named response blocks, `iss.only`, block column projections and ISS
pagination. Task242 therefore parses only the named `securities` block. A missing
block, missing requested column, malformed row, invalid JSON, or exhausted page
bound is a source-contract failure. Zero valid rows is a valid empty result.

The normalized in-memory candidate contains:

- `secid`, `isin`, `short_name`, `full_name`, `primary_board`;
- `issuer_id`, `issuer_title`, `issuer_inn`, `issuer_okpo`.

Security identifiers and board codes are trimmed and uppercased. INN and OKPO
are trimmed strings so leading zeroes are not lost. Missing values remain
`None`. The full MOEX payload is neither returned by the typed contract nor
persisted.

## 3. Deterministic security matching

Array position is never authoritative. Matching proceeds as follows:

1. Exact SECID candidates are considered first.
2. A non-null expected ISIN corroborates the exact SECID when equal.
3. A conflicting non-null ISIN produces `SECURITY_IDENTIFIER_CONFLICT`.
4. If no exact SECID candidate exists, an exact expected ISIN may produce
   `EXACT_ISIN_RECOVERED`.
5. Unrelated rows are ignored.
6. Duplicate exact candidates are merged only when every non-null security and
   issuer identity field is compatible. Missing plus present data is compatible.
7. Conflicting duplicates produce `SECURITY_AMBIGUOUS`; no candidate is chosen.

The complete security status vocabulary is:

- `EXACT_SECID`;
- `EXACT_SECID_ISIN_CORROBORATED`;
- `EXACT_ISIN_RECOVERED`;
- `SECURITY_IDENTIFIER_MISSING`;
- `SECURITY_NOT_FOUND`;
- `SECURITY_AMBIGUOUS`;
- `SECURITY_IDENTIFIER_CONFLICT`;
- `SOURCE_ERROR`.

There is no fuzzy, display-name, substring or ticker-only authority.

## 4. Issuer metadata semantics

Issuer metadata is classified independently of security matching:

- `ISSUER_COMPLETE`: issuer ID, title, INN and OKPO are all present;
- `ISSUER_PARTIAL`: at least one is present;
- `ISSUER_MISSING`: all are absent.

These are observed official-source facts, not verified legal identity. A title
alone is not verified identity. A foreign issuer without a Russian INN is
partial rather than corrupt. Task242 never invents identifiers, converts missing
strings into placeholders, or maps the candidate automatically to a Company.

## 5. Current repository architecture

The current MOEX universe sync resolves a Company using issuer metadata when it
is present. When it is absent for a new security, the existing sync may create a
Company named `Unknown issuer for <SECID>`. Consequently `Bond.company_id`
expresses current application linkage; it is not proof that the linked Company
is the security's verified legal issuer.

Existing issuer identity, duplicate-company resolution, Security Master and
Pilot Universe services remain unchanged. In particular, Task242 does not turn
MOEX issuer fields into an accepted `CompanyIdentityProfile` and does not relax
the verified-and-accepted identity requirement used by Pilot Universe.

## 6. Explicit-security probe

The DB-independent mode accepts one or more repeated `--secid` values. SECIDs
are trimmed, uppercased, validated against
`[A-Z0-9][A-Z0-9._-]{0,31}`, deduplicated in first-seen order and capped at 100.
Source calls preserve that order; result rows are sorted by requested SECID.

The output schema is:

`bondradar.moex_issuer_identity_source_probe.v1`

It reports fixed security and issuer classifications and normalized narrow
fields. It imports the database session factory only if DB coverage mode is
selected.

## 7. Database coverage probe

`--db-coverage` loads Bond, Company and CompanyIdentityProfile linkage in one
bounded outer-joined SELECT. Per Bond, it queries the official reference source
using SECID and uses an exact ISIN fallback/corroboration when available. It
reports:

- Bond identifier coverage and exact match classifications;
- issuer-field and unique issuer-ID/INN coverage;
- nominal-currency and ISIN-prefix diagnostics;
- placeholder Company and identity-profile presence;
- MOEX issuer-INN agreement with populated Company/profile INNs;
- deterministic bounded samples for not-found, ambiguous, conflicting,
  incomplete-issuer, INN-mismatch and placeholder-recovery cases.

On PostgreSQL the CLI executes `SET TRANSACTION READ ONLY`, verifies
`SHOW transaction_read_only`, and always rolls back and closes the session.
It never accepts a database URL through the CLI and never calls `commit()`.

## 8. Output privacy and failure behavior

Samples are bounded by `--sample-limit` in the range 1–100. Output excludes
database connection information, credentials, full MOEX payloads and exception
text. Invocation and terminal failures emit only a fixed sanitized error
contract. Per-security source failures are retained as the fixed `SOURCE_ERROR`
classification so other securities can still be measured.

## 9. Evidence and point-in-time limitations

The probe observes the current MOEX response. It is not a point-in-time issuer
identity history, freshness certificate, legal-register verification, or proof
that an issuer field was effective at the Bond's issue date. MOEX search results
may be incomplete or ambiguous, and foreign issuers may legitimately lack a
Russian INN. A future evidence-aware Bond-to-Legal-Issuer contract must retain
source time and review state and must not reinterpret this current snapshot as
historical truth.

## 10. Safety invariants

- `DATABASE_MUTATION_EXECUTED=false`
- `IDENTITY_VERIFIED=false`
- `IDENTITY_APPLIED=false`
- `COMPANY_MERGE_EXECUTED=false`
- `LIVE_MOEX_RUN_DURING_IMPLEMENTATION=false`
- `PRODUCTION_ACCESSED=false`
- `VDS_ACCESSED=false`
- `TASK243_STARTED=false`

## 11. Controlled handoff commands

These commands are examples for a separately authorized operator handoff. They
were not run during Task242.

Small controlled sample:

```text
python scripts/moex_issuer_identity_source_probe.py --secid RU000A104511 --secid RU000A107G55 --format json --output artifacts/task242_sample.json
```

Full read-only BondRadar coverage:

```text
python scripts/moex_issuer_identity_source_probe.py --db-coverage --sample-limit 20 --format json --output artifacts/task242_db_coverage.json
```

Neither command applies identity. Results must be reviewed before any Task243
design or implementation is authorized.
