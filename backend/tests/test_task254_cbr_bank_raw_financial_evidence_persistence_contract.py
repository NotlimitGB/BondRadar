from __future__ import annotations

import re
from pathlib import Path


DOCUMENT = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "audits"
    / "TASK254_CBR_BANK_RAW_FINANCIAL_EVIDENCE_PERSISTENCE_CONTRACT.md"
)


def _document() -> str:
    return DOCUMENT.read_text(encoding="utf-8")


def _sections(text: str) -> dict[int, str]:
    matches = list(re.finditer(r"^## ([1-9][0-9]*)\. (.+)$", text, re.MULTILINE))
    return {
        int(match.group(1)): text[
            match.start() : matches[index + 1].start() if index + 1 < len(matches) else len(text)
        ]
        for index, match in enumerate(matches)
    }


def test_task254_has_exact_ordered_sections_baseline_and_scope() -> None:
    text = _document()
    headings = re.findall(r"^## ([1-9][0-9]*)\. (.+)$", text, re.MULTILINE)
    assert [int(number) for number, _title in headings] == list(range(1, 37))
    assert len({number for number, _title in headings}) == 36

    for required in (
        "STARTING_SHA=2bd302d5d5106e5e5bb40f8942993cf35b030443",
        "CURRENT_ALEMBIC_HEAD=202608280002",
        "CONTRACT_VERSION=cbr-bank-raw-financial-evidence-v1",
        "CONTRACT_ONLY=true",
        "MIGRATION_REQUIRED_IN_TASK254=false",
        "MIGRATION=NONE",
        "MODEL_CHANGES=false",
        "DATABASE_ACCESS=false",
        "PRODUCTION_ACTIONS=NONE",
        "LEGACY_FINANCIAL_REPORT_OWNER=Company",
        "LEGACY_FINANCIAL_REPORT_SAFE_FOR_CBR_RAW_DATA=false",
        "CBR_RAW_REPORTING_SUBJECT_MODEL_EXISTS=false",
        "CBR_RAW_ARTIFACT_MODEL_EXISTS=false",
        "CBR_RAW_OBSERVATION_MODEL_EXISTS=false",
        "REPORTING_ENTITY_SEPARATE_FROM_LEGALISSUER=true",
    ):
        assert required in text

    expected_files = (
        "EXPECTED_CHANGED_FILES="
        "docs/audits/TASK254_CBR_BANK_RAW_FINANCIAL_EVIDENCE_PERSISTENCE_CONTRACT.md,"
        "backend/tests/test_task254_cbr_bank_raw_financial_evidence_persistence_contract.py"
    )
    assert expected_files in text


def test_legacy_boundary_source_identity_and_prior_coverage_are_fail_closed() -> None:
    text = _document()
    sections = _sections(text)
    legacy = sections[7]
    for required in (
        "LEGACY_FINANCIAL_REPORT_REUSED=false",
        "company_id + period_year + period_quarter",
        "normalized economic",
        "rebuild_existing",
        "missing currency is defaulted to RUB",
        "credit health",
        "feature snapshots",
    ):
        assert required in legacy

    identity = sections[9] + sections[10]
    for required in (
        "PRIMARY_SOURCE_IDENTITY=REGN",
        "REGN_PRIMARY_SOURCE_IDENTITY=true",
        "SUBJECT_TYPE=CREDIT_ORGANIZATION_REGN",
        "LEGALISSUER_LINK=OPTIONAL",
        "TITLE_IDENTITY=false",
        "FUZZY_MATCHING=false",
        "VERIFIED",
        "NOT_FOUND",
        "AMBIGUOUS",
        "NOT_VERIFIED",
        "SOURCE_IDENTITY_BLOCKED",
        "NOT_EVALUATED",
        "REGN -> OGRN -> INN -> LegalIssuer.issuer_inn",
    ):
        assert required in identity

    for required in (
        "TASK251_FINANCIAL_REGNS=353",
        "SOURCE_RESOLVED_REGNS=353",
        "REGN_LEGALISSUER_VERIFIED=26",
        "REGN_LEGALISSUER_NOT_FOUND=327",
        "LEGAL_ISSUER_IDENTITY_QUALITY_BLOCKERS=0",
        "RAW_SOURCE_EXISTENCE!=BONDRADAR_ISSUER_EXISTENCE",
        "unmatched REGNs",
    ):
        assert required in text


def test_six_table_schema_is_concrete_append_only_and_non_destructive() -> None:
    text = _document()
    schema = _sections(text)[20]
    tables = (
        "cbr_bank_reporting_subjects",
        "cbr_bank_source_artifacts",
        "cbr_bank_report_snapshots",
        "cbr_bank_raw_observations",
        "cbr_bank_subject_legal_issuer_evidence",
        "cbr_bank_subject_legal_issuer_profiles",
    )
    for table in tables:
        assert schema.count(f"### `{table}`") == 1

    for required in (
        "primary key",
        "canonical positive decimal string",
        "(source, subject_type, subject_regn)",
        "content_bytes",
        "compressed `BYTEA`",
        "content_sha256",
        "(source, content_sha256)",
        "artifact_id",
        "artifact FK `RESTRICT`",
        "member_schema_inventory",
        "observation_set_sha256",
        "snapshot_fingerprint",
        "snapshot_id`, `reporting_subject_id",
        "source_row_fingerprint",
        "source_dimensions",
        "raw_value_text",
        "parsed_decimal_value",
        "unscaled arbitrary-precision `NUMERIC`",
        "observation_fingerprint",
        "bridge_contract_version",
        "legal_issuer_id",
        "FK `SET NULL`",
        "evidence_fingerprint",
        "current_evidence_id",
        "last_observed_at",
        "last_resolved_at",
    ):
        assert required in schema

    constraints = _sections(text)[21]
    for required in (
        "octet_length(content_bytes)=compressed_size",
        "publication_status=KNOWN",
        "subjects(subject_regn)",
        "artifacts(content_sha256)",
        "snapshots(form,report_date)",
        "observations(report_date,subject_regn)",
        "observations(form,source_code)",
        "link_evidence(legal_issuer_id)",
        "No unique constraint is allowed on `REGN+form+report_date+source_code`",
    ):
        assert required in constraints

    retention = _sections(text)[22]
    for required in (
        "APPEND_ONLY=cbr_bank_source_artifacts,cbr_bank_report_snapshots,cbr_bank_raw_observations,cbr_bank_subject_legal_issuer_evidence",
        "RESOLVED_CURRENT_VIEW=cbr_bank_reporting_subjects,cbr_bank_subject_legal_issuer_profiles",
        "DELETE_BY_NORMAL_APP_FLOW=false",
        "UPDATE_SOURCE_FACTS=false",
        "Artifact/snapshot/subject FKs use `RESTRICT`",
        "LegalIssuer FKs use `SET NULL`",
        "No LegalIssuer deletion",
    ):
        assert required in retention


def test_values_pit_fingerprints_history_and_safety_are_explicit() -> None:
    text = _document()
    for required in (
        "ARTIFACT_BYTES_PRESERVED=true",
        "ARTIFACT_SHA256_LINEAGE=true",
        "exact decoded raw_value_text",
        "raw_value_text` is decoded from",
        "exact source field bytes",
        "blank!=zero",
        "invalid!=zero",
        "missing!=zero",
        "FLOAT_ALLOWED=false",
        "PUBLIC_VALUE_BLANK",
        "Decimal(str(float))` is forbidden",
        "RAW_VALUE_SCALING=false",
        "0409123=RUB_THOUSANDS,RUB,multiplier_1000",
        "0409135=PERCENT,currency_NULL,multiplier_NULL",
        "report_date!=publication_at",
        "PIT_PUBLICATION_STATUS=UNKNOWN",
        "HISTORICAL_BACKCAST_ALLOWED=false",
        "RESTATEMENT_POLICY=APPEND_ONLY",
        "REOBSERVATION_A_B_A_SUPPORTED=true",
        "A(T1) -> B(T2) -> A(T3)",
        "Artifact fingerprint",
        "Snapshot fingerprint",
        "Observation fingerprint",
        "Identity-link fingerprint",
        "Mutable database IDs are never sole inputs",
        "Observation-set SHA",
        "RawObservation -> ReportSnapshot -> SourceArtifact",
    ):
        assert required in text

    for required in (
        "SCHEMA_HISTORICAL_CAPABLE=true",
        "CURRENT_SNAPSHOT_PERSISTENCE_SUPPORTED_BY_DESIGN=true",
        "GENERAL_MONTHLY_INGESTION_PROVEN=false",
        "ARBITRARY_MONTH_INGESTION_PROVEN=false",
        "HISTORICAL_BACKFILL_PROVEN=false",
        "TASK251_CHANGED=false",
        "TASK252_CHANGED=false",
        "TASK253_CHANGED=false",
        "NETWORK_USED=false",
        "VDS_ACCESS=false",
        "NORMALIZATION=false",
        "SCORING=false",
        "TASK255_STARTED=false",
        "LOCAL_BROAD_REGRESSION=SKIPPED_BY_DESIGN",
        "CI=NOT_WAITED_BY_DESIGN",
        "CBR_RAW_FINANCIAL_EVIDENCE_PERSISTENCE_CONTRACT=READY",
        "NEXT_TASK=Task255 — CBR Bank Raw Financial Evidence Store v1",
    ):
        assert required in text
