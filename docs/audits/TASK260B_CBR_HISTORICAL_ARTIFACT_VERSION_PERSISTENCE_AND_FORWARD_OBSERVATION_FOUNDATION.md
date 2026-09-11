# Task260B — CBR Historical Artifact Version Persistence and Forward Observation Foundation

## Existing architecture reuse

`cbr_bank_source_artifacts` remains the immutable artifact-version store. Task260B adds only append-only availability observations. It does not change Task259 evidence, Task260A selection semantics, runners, raw values, fingerprints, or publication fields.

## Why SourceArtifact already represents a version

The minimum Task260A identity `(form, report_date, content_sha256)` is already present on `CbrBankSourceArtifact`, together with exact compressed bytes. A second artifact-version table would duplicate identity and byte provenance.

The existing stronger uniqueness `(source, content_sha256)` is retained. Identical bytes that might legitimately serve multiple form/report-date identities remain documented technical debt; Task260B does not broaden artifact identity.

## Availability evidence schema

`cbr_bank_artifact_availability_evidence` records:

- exact artifact FK with `ON DELETE RESTRICT`;
- contract `cbr-bank-artifact-availability-evidence-v1`;
- source `CBR_DIRECT`, `WAYBACK`, `COMMON_CRAWL`, or `OTHER_ARCHIVE`;
- exact observation timestamp, payload-binding flag, immutable source reference, and creation audit time.

Semantic uniqueness across artifact, source, observation time, payload binding, and reference deduplicates exact retries while retaining later observations. The schema can represent unbound evidence, but Task260B direct CBR ingestion always records exact-bound evidence.

## Existing artifact bootstrap

Migration `202609110001` creates one `CBR_DIRECT` exact-bound observation per existing source artifact:

```text
observed_at=first_retrieved_at
source_reference=source_url
```

It never uses report date, discovery time, ingestion time, or an inferred publication time. Existing raw/snapshot/artifact rows are not updated or deleted.

## Forward re-observation semantics

Every successful Task255 store call records the incoming Task251 artifact retrieval time, including when artifact bytes are reused. An exact replay reuses its evidence row; a later retrieval of the same SHA appends a new observation.

## Revision preservation

A new SHA for the same form/report date creates a separate `CbrBankSourceArtifact` and its own evidence. The earlier version and all its observations remain intact.

## DB to PIT domain mapping

`historical_versioning_persistence.py` converts persisted artifacts and observations into Task260A `ArtifactVersion` objects. `select_persisted_artifact_version_as_of()` delegates exclusively to Task260A `select_artifact_version_as_of()`; no database-specific precedence or fallback exists.

## Raw observation lineage

```text
RawObservation.snapshot_id
→ ReportSnapshot.artifact_id
→ SourceArtifact.content_sha256
```

This already identifies the exact artifact version that produced each raw row.

```text
RAW_TO_ARTIFACT_VERSION_LINEAGE=PROVEN
RAW_SCHEMA_REWRITE_REQUIRED=false
```

## Look-ahead protection

An artifact retrieved in 2026 is not eligible for a 2024 AS-OF query merely because its report date is in 2024. Only exact-payload-bound evidence at or before the requested timestamp establishes `safe_known_from`. No current/restated fallback is permitted.

Availability evidence proves that exact bytes were known no later than an observation. It does not prove first publication and never populates `publication_at`.

## Migration safety

Upgrade creates the new table, constraints/index, and bootstrap rows only. Downgrade drops only the new table. Production migration execution, backfill, archive import, reparsing, normalization, and scoring are outside Task260B.

## Production deployment status

```text
PRODUCTION_MIGRATION_APPLIED=false
PRODUCTION_DB_ACCESSED=false
PRODUCTION_ACTIONS=NONE
```

## Known limitations

```text
PUBLICATION_TIME_PROVEN=false
PIT_READY=false
```

Production has not received the migration; archived historical versions and Task259 Wayback evidence are not imported; publication times remain unknown; many historical queries must continue returning `NO_KNOWN_VERSION_AS_OF`.

The existing controlled monthly and historical runner revision guards still target the Task255 Alembic head. They are intentionally unchanged by the locked Task260B scope and must be reconciled under a separate authorization before either runner is used after this migration.

## Next step

The only next operation is `CONTROLLED_PRODUCTION_BACKUP_AND_TASK260B_MIGRATION_AFTER_EXPLICIT_AUTHORIZATION`. It is not executed or authorized by Task260B.
