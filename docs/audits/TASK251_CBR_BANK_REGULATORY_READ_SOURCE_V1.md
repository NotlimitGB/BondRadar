# Task251 — CBR Bank Regulatory Bundle Read-Only Source v1

## 1. Status

Task251 implements a standalone read-only source boundary for public CBR forms
0409101, 0409102, 0409123, and 0409135. It does not create a database model,
route, persistence path, normalized metric, score, or issuer mapping.

```text
CONTRACT_VERSION=bondradar.cbr_bank_regulatory_bundle_probe.v1
STARTING_SHA=071926270af44bb777f9e9e986bfa9dbe8233fa5
ALEMBIC_HEAD=202608280002
MIGRATION=NONE
DATABASE_PERSISTENCE=false
PRODUCTION_ACTIONS=NONE
```

Task252 remains locked unless source parsing, exact fixture integration, and the
mandatory Linux/Docker runtime proof all pass.

## 2. Runtime contract

```text
PYTHON=3.12
BASE_IMAGE=python:3.12-slim
RAR_METADATA_RUNTIME=rarfile==4.5
RAR_EXTRACTION_RUNTIME=libarchive_bsdtar
DBF_RUNTIME=dbfread==2.0.7
LINUX_EXECUTABLE=/usr/bin/bsdtar
WINDOWS_EXECUTABLE=%SystemRoot%/System32/tar.exe
WINDOWS_LOCAL_LIBARCHIVE=bsdtar_3.8.8_libarchive_3.8.8
```

`rarfile` is used only for RAR3/RAR5 metadata validation. Extraction is limited
to one already validated member at a time through a fixed libarchive executable.
On Windows, `tar.exe --version` must identify both bsdtar and libarchive. On
Linux/Docker, only `/usr/bin/bsdtar` is accepted. Arbitrary configured archive
programs are rejected.

`rarfile` is ISC licensed, `dbfread` is MIT licensed, and libarchive is BSD
licensed. The Docker image installs Debian `libarchive-tools`; no unrar binary
or proprietary RAR extraction runtime is required.

## 3. Source discovery

Discovery reads only the official CBR reporting page:

`https://www.cbr.ru/banking_sector/otchetnost-kreditnykh-organizaciy/`

The standard-library HTML parser accepts only actual anchor `href` values whose
basename exactly matches `(101|102|123|135)-YYYYMMDD.rar`. The original `href`
is retained. Resolution is limited to HTTPS on `cbr.ru` or `www.cbr.ru` with no
credentials and no non-default port. Archive URLs are never generated.

Absent, malformed, duplicate, conflicting, foreign-host, or over-redirected
references fail closed.

## 4. HTTP boundary

The injected `httpx.Client` boundary issues GET only with a BondRadar Task251
user agent, 5-second connect timeout, 30-second read timeout, and no automatic
redirects. At most three same-host redirects and three attempts are allowed.
Only timeout, HTTP 429, and HTTP 5xx conditions are retried with bounded
backoff. HTTP 404, invalid input, invalid content, and deterministic failures are
not retried as successful absence.

Limits are 2 MiB per archive and 8 MiB for all archives in one client run.
Response bodies are streamed and stopped at the cap.

## 5. Approved immutable fixtures

The bounded live acquisition used the implemented discovery boundary once and
downloaded exactly four current CBR artifacts. No historical artifact was
downloaded.

| Form | Artifact | Bytes | SHA-256 |
|---|---|---:|---|
| 0409101 | `101-20260801.rar` | 360046 | `7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a` |
| 0409102 | `102-20260801.rar` | 74392 | `0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21` |
| 0409123 | `123-20260801.rar` | 33042 | `6da408180123fa6748399acb89c717e3fc32380ee818679248043daa9a60baab` |
| 0409135 | `135-20260801.rar` | 33181 | `061a00791196d660bdb070de890228c820ea7e0d8af7978309b11b4226ed4776` |

```text
FIXTURE_COUNT=4
TOTAL_COMPRESSED_BYTES=500661
LIVE_NETWORK_USED=true
HISTORICAL_DOWNLOADS=0
```

Transport bytes are hashed before archive inspection. For these four artifact
identities, both byte count and SHA-256 are mandatory; any change is
`ARTIFACT_MUTATED`.

## 6. Archive containment

Before extraction, the source requires a RAR signature and parses metadata with
strict errors. It rejects encrypted, solid, multi-volume, redirected/link-like,
nested, path-bearing, absolute, drive-prefixed, traversal, duplicate-casefolded,
non-DBF, malformed, excessive, or empty inventories.

```text
MAX_MEMBERS=16
MAX_MEMBER_UNCOMPRESSED_BYTES=16777216
MAX_TOTAL_UNCOMPRESSED_BYTES=67108864
MEMBER_EXTRACTION_TIMEOUT_SECONDS=20
```

Only an individually named, metadata-approved member is sent to bsdtar stdout.
The emitted size and CRC32 must match RAR metadata. Source member names are
never used as filesystem output paths. DBF bytes use generated names inside a
private temporary directory that is removed after parsing.

## 7. DBF contract

The approved CBR files carry DBF language-driver byte `0`. Task251 binds that
specific CBR public-file convention to strict DOS CP866 decoding; any other
marker is unknown and rejected. Character decoding errors fail closed.

The custom field parser preserves `N` and textual `F` numbers as finite
`Decimal`, maps blank numeric text to `None`, and preserves zero as
`Decimal("0")`. A comma decimal separator is read exactly as its decimal value.
Binary floating-point field types, unknown/unsupported types, deleted rows,
malformed values, non-finite values, unknown encodings, and missing memo files
fail closed.

```text
DEFAULT_DBFREAD_NUMERIC_USED_FOR_FINANCIAL_VALUES=false
RAW_NUMERIC_FLOAT_COUNT=0
DECIMAL_SAFETY=PASS
```

The parser converts the original DBF bytes directly to `Decimal`; it never
round-trips a source number through Python `float`.

```text
MAX_RECORDS_PER_DBF=65536
MAX_RECORDS_PER_BUNDLE=131072
```

All source columns are retained in original field order as immutable tuples.
No raw row is printed by the CLI.

## 8. Approved member and schema fingerprints

Member fingerprints hash the ordered uppercase `(name, type, length,
decimal-count)` field projection. Form fingerprints hash the ordered exact
member-name and member-fingerprint inventory.

| Form | Approved members | Form fingerprint |
|---|---|---|
| 0409101 | `072026B1.dbf`, `072026N1.dbf`, `NAMES.dbf` | `aa5ca40686c9dbc7b9eb1e2957d14b359fcc16fbe0797e201ff19b3627de38c6` |
| 0409102 | `072026_P1.dbf`, `072026NP1.dbf`, `072026SP1.dbf`, `SPRAV1.dbf`, `SPRAV11.dbf` | `cfd92a9ec3148c4ef0c40741864930f3154bb3e961b78eab257cbdddefd3161b` |
| 0409123 | `072026_123B.dbf`, `072026_123D.dbf`, `072026_123N.dbf` | `99dd4c23639bbc181afe40777a7fd52377024c87163bf76c6af9622ac9ec94d4` |
| 0409135 | `072026_135_3.dbf`, `072026_135B.dbf` | `bab052841d8a949af2ffb6f84b363921cb020b31aaefe64063e5e8bdc68f9809` |

Only these measured 2026 layouts are approved. A 2021 or otherwise unknown
layout is `UNSUPPORTED_SCHEMA_VERSION`; it is not guessed from similar fields.

## 9. Form bindings

- 0409101 binds `REGN`, `PLAN`, `NUM_SC`, `A_P`, `VITG`, `IITG`, and `DT`.
- 0409102 binds `REGN`, `CODE`, `SIM_R`, `SIM_V`, `SIM_ITOGO`, and `DT`.
- 0409123 binds `REGN`, `C1`, and `C3`; `072026_123B.dbf` supplies the exact
  source date by REGN. Unit is `RUB_THOUSANDS`, currency `RUB`, multiplier
  `1000` because this is the proven form contract, not a generic fallback.
- 0409135 binds `REGN`, `C1_3`, `C2_3`, `C3_3`, and `C4_3` and keeps values in
  `PERCENT`. No currency or conversion is assigned. The source Cyrillic form
  prefix `Н` has the canonical official ratio-code projection `N` while the raw
  source field remains retained unchanged.

Member roles are explicit and source-specific:

| Form | Value member | Support member(s) | Nomenclature member(s) |
|---|---|---|---|
| 0409101 | `072026B1.dbf` | `072026N1.dbf` | `NAMES.dbf` |
| 0409102 | `072026_P1.dbf` | `072026NP1.dbf`, `072026SP1.dbf` | `SPRAV1.dbf`, `SPRAV11.dbf` |
| 0409123 | `072026_123D.dbf` | `072026_123B.dbf` | `072026_123N.dbf` |
| 0409135 | `072026_135_3.dbf` | `072026_135B.dbf` | none |

Only a REGN with at least one non-null accepted value in the named value
member enters `subjects_by_form`. Blank value rows remain retained as
`PUBLIC_VALUE_BLANK`; support and nomenclature rows never create financial
subject membership. Nomenclature is parsed separately using exact keys. A
missing label stays `None` and does not invalidate a valid raw numeric record.

## 10. Exact fixture result

```text
101_ROWS=25654
101_SUBJECTS=353
102_ROWS=10079
102_SUBJECTS=212
123_ROWS=1400
123_SUBJECTS=352
135_ROWS=1709
135_SUBJECTS=345
123_CODES=000,102,105,203
135_CODES=N1.0,N1.1,N1.2,N1.3,N2,N3,N4,N15,N15.1,N16,N16.1,N16.2,N27
N18_SYNTHESIZED=false
SUBJECT_SET_HASH_101=692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd
SUBJECT_SET_HASH_102=90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8
SUBJECT_SET_HASH_123=5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc
SUBJECT_SET_HASH_135=660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619
101_102_INTERSECTION=212
101_123_INTERSECTION=352
101_135_INTERSECTION=345
102_123_INTERSECTION=211
102_135_INTERSECTION=211
123_135_INTERSECTION=345
ALL_FOUR_INTERSECTION=211
ONLY_101_102=1
ONLY_101_123=7
ONLY_101_123_135=134
ONLY_101_102_123_135=211
```

The implementation computes overlap directly from exact trimmed REGN strings.
The immutable value-member audit proves `ALL_FOUR_INTERSECTION=211`, not the
prior Task250 planning projection of `170`. Task250 Section 21 and its focused
contract assertions now carry an explicit post-implementation correction. No
REGN was filtered to force either value, and the Task250 source decision and
engineering gate remain unchanged.

## 11. Snapshot semantics

A snapshot is constructed only after every requested artifact, archive,
member, DBF, schema, value, and resource check succeeds. There is no partial
snapshot. Form and overlap ordering is deterministic. Cross-form joins use only
exact REGN strings; title, OGRN, INN, Company, and LegalIssuer are not consulted.

Published timestamps are not established by these archive names. Every result
therefore retains:

```text
PIT_STATE=PIT_PARTIAL
PUBLISHED_AT_KNOWN=false
CURRENT_DISCLOSURE_IS_REGULATORILY_REDUCED=true
```

## 12. Probe CLI

The standalone command requires `--report-date YYYY-MM-DD` and one to four
unique values after `--forms`. It emits exactly one compact JSON object to
stdout. Success exits 0, sanitized source/runtime/parser failure exits 1, and
invalid arguments exit 2. It never writes an output file and never emits raw
rows, company names, DB information, credentials, URLs with secrets, or
exception text.

The report includes artifact names, hashes, schema fingerprints, row/subject
counts, deterministic subject-set hashes, source-code inventories, overlap and
exact-exclusive counts, PIT warnings, and explicit false safety flags.

## 13. Failure taxonomy

The source keeps these states distinct: artifact not found, rate limited,
timeout, source error, invalid content, artifact too large, artifact mutated,
RAR runtime unavailable, invalid archive, traversal, duplicate member, member
or total expansion limit, unsupported archive feature, invalid DBF, value parse
error, and unsupported schema version. None is converted to a zero value or a
successful no-data result.

## 14. Architectural boundary

```text
API_ROUTE_ADDED=false
MODEL_ADDED=false
MIGRATION=NONE
SQLALCHEMY_IMPORTED=false
DATABASE_ACCESSED=false
DATABASE_PERSISTENCE=false
LEGACY_COMPANY_MAPPING_REUSED=false
UNSAFE_RUB_DEFAULT_REUSED=false
MISSING_VALUE_ZERO_REUSED=false
LEGACY_PERIOD_OVERWRITE_REUSED=false
NORMALIZATION_EXECUTED=false
SCORING_EXECUTED=false
LEGALISSUER_JOIN_EXECUTED=false
DEPLOYMENT_EXECUTED=false
PRODUCTION_ACTIONS=NONE
```

## 15. Downstream boundary

The intended next dependency is:

`Task252 — CBR REGN → LegalIssuer Identity Bridge v1`

It is not implemented or automatically unlocked here. It requires a separate
authorization. Task251 performs no Task252 work.

## 16. Local verification outcome

```text
TASK250_POST_IMPLEMENTATION_CORRECTION=true
CORRECTION_SOURCE=TASK251_EXACT_IMMUTABLE_FIXTURE_VALUE_MEMBER_AUDIT
COMPILEALL=PASS
FOCUSED_TASK251=14_PASSED
TASK247_TASK250_REGRESSIONS=27_PASSED
NARROW_SOURCE_REGRESSIONS=11_PASSED
FULL_BACKEND_LOCAL=INCOMPLETE_EXTERNAL_LOCAL_CONSTRAINT_AT_54_PERCENT
FULL_BACKEND_FAILURES_OBSERVED=0
LIVE_FIXTURE_ACQUISITION_AND_PARSE=PASS
DOCKER_ENGINE=PASS
DOCKER_BUILD=PASS
DOCKER_BSDTAR=3.7.4
DOCKER_LIBARCHIVE=3.7.4
DOCKER_RARFILE=4.5
DOCKER_DBFREAD=2.0.7
DOCKER_FIXTURE_PARSE=PASS
DOCKER_DECIMAL_SAFETY=PASS
DOCKER_RAW_NUMERIC_FLOAT_COUNT=0
DOCKER_NETWORK_ENABLED=false
RAR_RUNTIME_SCOPE=CURRENT_CBR_SUPPORTED_ARTIFACTS_ONLY
TASK251_DELIVERY_GATES=PASS
TASK252_STARTED=false
```

The final compile, focused/regression, full-suite, Docker build, libarchive,
DBF and fixture-runtime results are recorded from the delivery run. No second
network download was performed; runtime proof uses only the committed immutable
fixtures with container networking disabled.
