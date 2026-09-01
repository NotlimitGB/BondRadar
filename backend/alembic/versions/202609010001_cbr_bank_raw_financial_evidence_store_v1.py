"""Add CBR bank raw financial evidence store v1.

Revision ID: 202609010001
Revises: 202608280002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202609010001"
down_revision = "202608280002"
branch_labels = None
depends_on = None

SUBJECTS = "cbr_bank_reporting_subjects"
ARTIFACTS = "cbr_bank_source_artifacts"
SNAPSHOTS = "cbr_bank_report_snapshots"
OBSERVATIONS = "cbr_bank_raw_observations"
IDENTITY_EVIDENCE = "cbr_bank_subject_legal_issuer_evidence"
IDENTITY_PROFILES = "cbr_bank_subject_legal_issuer_profiles"
TABLES = {
    SUBJECTS,
    ARTIFACTS,
    SNAPSHOTS,
    OBSERVATIONS,
    IDENTITY_EVIDENCE,
    IDENTITY_PROFILES,
}

JSON_DOCUMENT = postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        existing = set(sa.inspect(bind).get_table_names())
        if TABLES.issubset(existing):
            return
        if TABLES.intersection(existing):
            raise RuntimeError("Partial Task255 schema already exists")

    op.create_table(
        SUBJECTS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default="cbr-bank-raw-financial-evidence-v1",
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="CBR"),
        sa.Column(
            "subject_type",
            sa.String(48),
            nullable=False,
            server_default="CREDIT_ORGANIZATION_REGN",
        ),
        sa.Column("subject_regn", sa.String(16), nullable=False),
        sa.Column("first_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "source", "subject_type", "subject_regn",
            name="uq_cbr_bank_reporting_subjects_identity",
        ),
        sa.CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_reporting_subjects_contract_valid",
        ),
        sa.CheckConstraint(
            "source = 'CBR' and subject_type = 'CREDIT_ORGANIZATION_REGN'",
            name="cbr_bank_reporting_subjects_identity_source_valid",
        ),
        sa.CheckConstraint(
            "cast(cast(subject_regn as bigint) as varchar) = subject_regn "
            "and cast(subject_regn as bigint) > 0",
            name="cbr_bank_reporting_subjects_regn_canonical",
        ),
        sa.CheckConstraint(
            "last_observed_at >= first_observed_at",
            name="cbr_bank_reporting_subjects_observation_order_valid",
        ),
    )
    op.create_index("ix_cbr_bank_reporting_subjects_regn", SUBJECTS, ["subject_regn"])

    op.create_table(
        ARTIFACTS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contract_version", sa.String(64), nullable=False,
            server_default="cbr-bank-raw-financial-evidence-v1",
        ),
        sa.Column(
            "source", sa.String(32), nullable=False, server_default="CBR_BANK_REPORTING"
        ),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("artifact_filename", sa.String(256), nullable=False),
        sa.Column("form", sa.String(8), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("compressed_size", sa.BigInteger(), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("parser_contract_version", sa.String(96), nullable=False),
        sa.Column("archive_runtime_contract", sa.String(128), nullable=False),
        sa.Column("artifact_fingerprint", sa.String(64), nullable=False),
        sa.UniqueConstraint(
            "source", "content_sha256", name="uq_cbr_bank_source_artifacts_source_content"
        ),
        sa.UniqueConstraint(
            "artifact_fingerprint", name="uq_cbr_bank_source_artifacts_fingerprint"
        ),
        sa.CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1' "
            "and source = 'CBR_BANK_REPORTING'",
            name="cbr_bank_source_artifacts_contract_valid",
        ),
        sa.CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_source_artifacts_form_valid",
        ),
        sa.CheckConstraint(
            "compressed_size > 0 and length(content_bytes) = compressed_size",
            name="cbr_bank_source_artifacts_size_valid",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64 and content_sha256 = lower(content_sha256) "
            "and length(artifact_fingerprint) = 64 "
            "and artifact_fingerprint = lower(artifact_fingerprint)",
            name="cbr_bank_source_artifacts_hashes_valid",
        ),
    )
    op.create_index(
        "ix_cbr_bank_source_artifacts_content_sha256", ARTIFACTS, ["content_sha256"]
    )
    op.create_index(
        "ix_cbr_bank_source_artifacts_form_report_date", ARTIFACTS, ["form", "report_date"]
    )

    op.create_table(
        SNAPSHOTS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column(
            "contract_version", sa.String(64), nullable=False,
            server_default="cbr-bank-raw-financial-evidence-v1",
        ),
        sa.Column("form", sa.String(8), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("value_member_name", sa.String(256), nullable=False),
        sa.Column("member_schema_inventory", JSON_DOCUMENT, nullable=False),
        sa.Column("form_schema_fingerprint", sa.String(64), nullable=False),
        sa.Column("parser_contract_version", sa.String(96), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publication_status", sa.String(16), nullable=False),
        sa.Column("publication_at", sa.DateTime(timezone=True)),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("subject_count", sa.Integer(), nullable=False),
        sa.Column("subject_set_sha256", sa.String(64), nullable=False),
        sa.Column("observation_set_sha256", sa.String(64), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["artifact_id"], [f"{ARTIFACTS}.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "snapshot_fingerprint", name="uq_cbr_bank_report_snapshots_fingerprint"
        ),
        sa.CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_report_snapshots_contract_valid",
        ),
        sa.CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_report_snapshots_form_valid",
        ),
        sa.CheckConstraint(
            "publication_status in ('KNOWN', 'UNKNOWN') and "
            "((publication_status = 'KNOWN' and publication_at is not null) or "
            "(publication_status = 'UNKNOWN' and publication_at is null))",
            name="cbr_bank_report_snapshots_publication_valid",
        ),
        sa.CheckConstraint(
            "record_count >= 0 and subject_count >= 0",
            name="cbr_bank_report_snapshots_counts_valid",
        ),
        sa.CheckConstraint(
            "length(form_schema_fingerprint) = 64 and length(subject_set_sha256) = 64 "
            "and length(observation_set_sha256) = 64 "
            "and length(snapshot_fingerprint) = 64",
            name="cbr_bank_report_snapshots_hashes_valid",
        ),
    )
    op.create_index(
        "ix_cbr_bank_report_snapshots_form_report_date", SNAPSHOTS, ["form", "report_date"]
    )

    op.create_table(
        OBSERVATIONS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("reporting_subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "contract_version", sa.String(64), nullable=False,
            server_default="cbr-bank-raw-financial-evidence-v1",
        ),
        sa.Column("form", sa.String(8), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("subject_regn", sa.String(16), nullable=False),
        sa.Column("archive_member_name", sa.String(256), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_row_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_value_field", sa.String(32), nullable=False),
        sa.Column("source_code", sa.String(128)),
        sa.Column("source_subcode", sa.String(128)),
        sa.Column("source_dimensions", JSON_DOCUMENT, nullable=False),
        sa.Column("source_fields_sha256", sa.String(64), nullable=False),
        sa.Column("raw_value_text", sa.Text()),
        sa.Column("parsed_decimal_value", sa.Numeric(asdecimal=True)),
        sa.Column("disclosure_state", sa.String(64), nullable=False),
        sa.Column("source_unit", sa.String(64), nullable=False),
        sa.Column("source_currency", sa.String(16)),
        sa.Column("source_multiplier", sa.BigInteger()),
        sa.Column("source_date", sa.Date()),
        sa.Column("parser_contract_version", sa.String(96), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], [f"{SNAPSHOTS}.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reporting_subject_id"], [f"{SUBJECTS}.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "observation_fingerprint", name="uq_cbr_bank_raw_observations_fingerprint"
        ),
        sa.UniqueConstraint(
            "snapshot_id", "archive_member_name", "source_row_number", "source_value_field",
            name="uq_cbr_bank_raw_observations_snapshot_row_field",
        ),
        sa.CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_raw_observations_contract_valid",
        ),
        sa.CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_raw_observations_form_valid",
        ),
        sa.CheckConstraint(
            "source_row_number > 0", name="cbr_bank_raw_observations_row_number_valid"
        ),
        sa.CheckConstraint(
            "disclosure_state in ('PUBLIC_VALUE', 'PUBLIC_VALUE_BLANK', "
            "'SUPPRESSED_OR_REDUCED', 'NOT_PRESENT_IN_CURRENT_PUBLIC_ARTIFACT', 'UNKNOWN')",
            name="cbr_bank_raw_observations_disclosure_state_valid",
        ),
        sa.CheckConstraint(
            "(disclosure_state != 'PUBLIC_VALUE' or "
            "(raw_value_text is not null and parsed_decimal_value is not null)) and "
            "(disclosure_state != 'PUBLIC_VALUE_BLANK' or "
            "(parsed_decimal_value is null and (raw_value_text is null or raw_value_text = '')))",
            name="cbr_bank_raw_observations_value_state_valid",
        ),
        sa.CheckConstraint(
            "length(source_row_fingerprint) = 64 and length(source_fields_sha256) = 64 "
            "and length(observation_fingerprint) = 64",
            name="cbr_bank_raw_observations_hashes_valid",
        ),
    )
    op.create_index("ix_cbr_bank_raw_observations_snapshot_id", OBSERVATIONS, ["snapshot_id"])
    op.create_index(
        "ix_cbr_bank_raw_observations_report_subject", OBSERVATIONS,
        ["report_date", "subject_regn"],
    )
    op.create_index(
        "ix_cbr_bank_raw_observations_form_source_code", OBSERVATIONS,
        ["form", "source_code"],
    )

    op.create_table(
        IDENTITY_EVIDENCE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reporting_subject_id", sa.Integer(), nullable=False),
        sa.Column(
            "contract_version", sa.String(64), nullable=False,
            server_default="cbr-bank-raw-financial-evidence-v1",
        ),
        sa.Column("subject_regn", sa.String(16), nullable=False),
        sa.Column("bridge_contract_version", sa.String(64), nullable=False),
        sa.Column("bridge_state", sa.String(32), nullable=False),
        sa.Column("observed_ogrn", sa.String(13)),
        sa.Column("observed_inn", sa.String(10)),
        sa.Column("observed_cbr_name", sa.String(512)),
        sa.Column("legal_issuer_id", sa.Integer()),
        sa.Column("legal_issuer_identity_source", sa.String(32)),
        sa.Column("legal_issuer_source_issuer_id", sa.String(64)),
        sa.Column("registry_as_of", sa.Date(), nullable=False),
        sa.Column("finorg_last_update", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("diagnostic_codes", JSON_DOCUMENT, nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["reporting_subject_id"], [f"{SUBJECTS}.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["legal_issuer_id"], ["legal_issuers.id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint(
            "evidence_fingerprint",
            name="uq_cbr_bank_subject_legal_issuer_evidence_fingerprint",
        ),
        sa.CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1' "
            "and bridge_contract_version = 'cbr-legal-issuer-bridge-v1'",
            name="cbr_bank_subject_legal_issuer_evidence_contract_valid",
        ),
        sa.CheckConstraint(
            "bridge_state in ('VERIFIED', 'NOT_FOUND', 'AMBIGUOUS', 'NOT_VERIFIED', "
            "'SOURCE_IDENTITY_BLOCKED', 'NOT_EVALUATED')",
            name="cbr_bank_subject_legal_issuer_evidence_state_valid",
        ),
        sa.CheckConstraint(
            "bridge_state = 'VERIFIED' or legal_issuer_id is null",
            name="cbr_bank_subject_legal_issuer_evidence_link_fail_closed",
        ),
        sa.CheckConstraint(
            "length(evidence_fingerprint) = 64",
            name="cbr_bank_subject_legal_issuer_evidence_hash_valid",
        ),
    )
    op.create_index(
        "ix_cbr_bank_subject_legal_issuer_evidence_subject_observed",
        IDENTITY_EVIDENCE, ["reporting_subject_id", "observed_at"],
    )
    op.create_index(
        "ix_cbr_bank_subject_legal_issuer_evidence_legal_issuer_id",
        IDENTITY_EVIDENCE, ["legal_issuer_id"],
    )

    op.create_table(
        IDENTITY_PROFILES,
        sa.Column("reporting_subject_id", sa.Integer(), primary_key=True),
        sa.Column(
            "contract_version", sa.String(64), nullable=False,
            server_default="cbr-bank-raw-financial-evidence-v1",
        ),
        sa.Column("current_evidence_id", sa.Integer()),
        sa.Column("bridge_state", sa.String(32), nullable=False),
        sa.Column("legal_issuer_id", sa.Integer()),
        sa.Column("legal_issuer_identity_source", sa.String(32)),
        sa.Column("legal_issuer_source_issuer_id", sa.String(64)),
        sa.Column("current_ogrn", sa.String(13)),
        sa.Column("current_inn", sa.String(10)),
        sa.Column("current_cbr_name", sa.String(512)),
        sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["reporting_subject_id"], [f"{SUBJECTS}.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["current_evidence_id"], [f"{IDENTITY_EVIDENCE}.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["legal_issuer_id"], ["legal_issuers.id"], ondelete="SET NULL"
        ),
        sa.CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_subject_legal_issuer_profiles_contract_valid",
        ),
        sa.CheckConstraint(
            "bridge_state in ('VERIFIED', 'NOT_FOUND', 'AMBIGUOUS', 'NOT_VERIFIED', "
            "'SOURCE_IDENTITY_BLOCKED', 'NOT_EVALUATED')",
            name="cbr_bank_subject_legal_issuer_profiles_state_valid",
        ),
        sa.CheckConstraint(
            "bridge_state = 'VERIFIED' or legal_issuer_id is null",
            name="cbr_bank_subject_legal_issuer_profiles_link_fail_closed",
        ),
    )
    op.create_index(
        "ix_cbr_bank_subject_legal_issuer_profiles_legal_issuer_id",
        IDENTITY_PROFILES, ["legal_issuer_id"],
    )


def downgrade() -> None:
    op.drop_table(IDENTITY_PROFILES)
    op.drop_table(IDENTITY_EVIDENCE)
    op.drop_table(OBSERVATIONS)
    op.drop_table(SNAPSHOTS)
    op.drop_table(ARTIFACTS)
    op.drop_table(SUBJECTS)
