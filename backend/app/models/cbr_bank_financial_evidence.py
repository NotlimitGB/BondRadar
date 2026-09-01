from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION = "cbr-bank-raw-financial-evidence-v1"
CBR_BANK_SOURCE = "CBR_BANK_REPORTING"
CBR_REPORTING_SUBJECT_SOURCE = "CBR"
CBR_REPORTING_SUBJECT_TYPE = "CREDIT_ORGANIZATION_REGN"
CBR_BANK_FORMS = ("0409101", "0409102", "0409123", "0409135")
CBR_PUBLICATION_STATES = ("KNOWN", "UNKNOWN")
CBR_IDENTITY_LINK_STATES = (
    "VERIFIED",
    "NOT_FOUND",
    "AMBIGUOUS",
    "NOT_VERIFIED",
    "SOURCE_IDENTITY_BLOCKED",
    "NOT_EVALUATED",
)
CBR_DISCLOSURE_STATES = (
    "PUBLIC_VALUE",
    "PUBLIC_VALUE_BLANK",
    "SUPPRESSED_OR_REDUCED",
    "NOT_PRESENT_IN_CURRENT_PUBLIC_ARTIFACT",
    "UNKNOWN",
)

JSON_DOCUMENT = JSONB().with_variant(JSON(), "sqlite")


class CbrBankReportingSubject(Base):
    __tablename__ = "cbr_bank_reporting_subjects"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "subject_type",
            "subject_regn",
            name="uq_cbr_bank_reporting_subjects_identity",
        ),
        Index("ix_cbr_bank_reporting_subjects_regn", "subject_regn"),
        CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_reporting_subjects_contract_valid",
        ),
        CheckConstraint(
            "source = 'CBR' and subject_type = 'CREDIT_ORGANIZATION_REGN'",
            name="cbr_bank_reporting_subjects_identity_source_valid",
        ),
        CheckConstraint(
            "cast(cast(subject_regn as bigint) as varchar) = subject_regn "
            "and cast(subject_regn as bigint) > 0",
            name="cbr_bank_reporting_subjects_regn_canonical",
        ),
        CheckConstraint(
            "last_observed_at >= first_observed_at",
            name="cbr_bank_reporting_subjects_observation_order_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        server_default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    )
    source: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=CBR_REPORTING_SUBJECT_SOURCE,
        server_default=CBR_REPORTING_SUBJECT_SOURCE,
    )
    subject_type: Mapped[str] = mapped_column(
        String(48),
        nullable=False,
        default=CBR_REPORTING_SUBJECT_TYPE,
        server_default=CBR_REPORTING_SUBJECT_TYPE,
    )
    subject_regn: Mapped[str] = mapped_column(String(16), nullable=False)
    first_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    observations: Mapped[list["CbrBankRawObservation"]] = relationship(
        back_populates="reporting_subject", passive_deletes=True
    )
    legal_issuer_evidence: Mapped[list["CbrBankSubjectLegalIssuerEvidence"]] = (
        relationship(back_populates="reporting_subject", passive_deletes=True)
    )
    legal_issuer_profile: Mapped["CbrBankSubjectLegalIssuerProfile | None"] = (
        relationship(back_populates="reporting_subject", passive_deletes=True)
    )


class CbrBankSourceArtifact(Base):
    __tablename__ = "cbr_bank_source_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "content_sha256",
            name="uq_cbr_bank_source_artifacts_source_content",
        ),
        UniqueConstraint(
            "artifact_fingerprint",
            name="uq_cbr_bank_source_artifacts_fingerprint",
        ),
        Index("ix_cbr_bank_source_artifacts_content_sha256", "content_sha256"),
        Index(
            "ix_cbr_bank_source_artifacts_form_report_date", "form", "report_date"
        ),
        CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1' "
            "and source = 'CBR_BANK_REPORTING'",
            name="cbr_bank_source_artifacts_contract_valid",
        ),
        CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_source_artifacts_form_valid",
        ),
        CheckConstraint(
            "compressed_size > 0 and length(content_bytes) = compressed_size",
            name="cbr_bank_source_artifacts_size_valid",
        ),
        CheckConstraint(
            "length(content_sha256) = 64 and content_sha256 = lower(content_sha256) "
            "and length(artifact_fingerprint) = 64 "
            "and artifact_fingerprint = lower(artifact_fingerprint)",
            name="cbr_bank_source_artifacts_hashes_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        server_default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=CBR_BANK_SOURCE,
        server_default=CBR_BANK_SOURCE,
    )
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    artifact_filename: Mapped[str] = mapped_column(String(256), nullable=False)
    form: Mapped[str] = mapped_column(String(8), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    compressed_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    first_retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    parser_contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    archive_runtime_contract: Mapped[str] = mapped_column(String(128), nullable=False)
    artifact_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    snapshots: Mapped[list["CbrBankReportSnapshot"]] = relationship(
        back_populates="artifact", passive_deletes=True
    )


class CbrBankReportSnapshot(Base):
    __tablename__ = "cbr_bank_report_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_fingerprint",
            name="uq_cbr_bank_report_snapshots_fingerprint",
        ),
        Index(
            "ix_cbr_bank_report_snapshots_form_report_date", "form", "report_date"
        ),
        CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_report_snapshots_contract_valid",
        ),
        CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_report_snapshots_form_valid",
        ),
        CheckConstraint(
            "publication_status in ('KNOWN', 'UNKNOWN') and "
            "((publication_status = 'KNOWN' and publication_at is not null) or "
            "(publication_status = 'UNKNOWN' and publication_at is null))",
            name="cbr_bank_report_snapshots_publication_valid",
        ),
        CheckConstraint(
            "record_count >= 0 and subject_count >= 0",
            name="cbr_bank_report_snapshots_counts_valid",
        ),
        CheckConstraint(
            "length(form_schema_fingerprint) = 64 "
            "and length(subject_set_sha256) = 64 "
            "and length(observation_set_sha256) = 64 "
            "and length(snapshot_fingerprint) = 64",
            name="cbr_bank_report_snapshots_hashes_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("cbr_bank_source_artifacts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        server_default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    )
    form: Mapped[str] = mapped_column(String(8), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_member_name: Mapped[str] = mapped_column(String(256), nullable=False)
    member_schema_inventory: Mapped[list[Any]] = mapped_column(
        JSON_DOCUMENT, nullable=False
    )
    form_schema_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    publication_status: Mapped[str] = mapped_column(String(16), nullable=False)
    publication_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_count: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_count: Mapped[int] = mapped_column(Integer, nullable=False)
    subject_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    observation_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    artifact: Mapped["CbrBankSourceArtifact"] = relationship(back_populates="snapshots")
    observations: Mapped[list["CbrBankRawObservation"]] = relationship(
        back_populates="snapshot", passive_deletes=True
    )


class CbrBankRawObservation(Base):
    __tablename__ = "cbr_bank_raw_observations"
    __table_args__ = (
        UniqueConstraint(
            "observation_fingerprint",
            name="uq_cbr_bank_raw_observations_fingerprint",
        ),
        UniqueConstraint(
            "snapshot_id",
            "archive_member_name",
            "source_row_number",
            "source_value_field",
            name="uq_cbr_bank_raw_observations_snapshot_row_field",
        ),
        Index("ix_cbr_bank_raw_observations_snapshot_id", "snapshot_id"),
        Index(
            "ix_cbr_bank_raw_observations_report_subject",
            "report_date",
            "subject_regn",
        ),
        Index(
            "ix_cbr_bank_raw_observations_form_source_code", "form", "source_code"
        ),
        CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_raw_observations_contract_valid",
        ),
        CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_raw_observations_form_valid",
        ),
        CheckConstraint(
            "source_row_number > 0",
            name="cbr_bank_raw_observations_row_number_valid",
        ),
        CheckConstraint(
            "disclosure_state in ('PUBLIC_VALUE', 'PUBLIC_VALUE_BLANK', "
            "'SUPPRESSED_OR_REDUCED', 'NOT_PRESENT_IN_CURRENT_PUBLIC_ARTIFACT', "
            "'UNKNOWN')",
            name="cbr_bank_raw_observations_disclosure_state_valid",
        ),
        CheckConstraint(
            "(disclosure_state != 'PUBLIC_VALUE' or "
            "(raw_value_text is not null and parsed_decimal_value is not null)) "
            "and (disclosure_state != 'PUBLIC_VALUE_BLANK' or "
            "(parsed_decimal_value is null and "
            "(raw_value_text is null or raw_value_text = '')))",
            name="cbr_bank_raw_observations_value_state_valid",
        ),
        CheckConstraint(
            "length(source_row_fingerprint) = 64 "
            "and length(source_fields_sha256) = 64 "
            "and length(observation_fingerprint) = 64",
            name="cbr_bank_raw_observations_hashes_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("cbr_bank_report_snapshots.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reporting_subject_id: Mapped[int] = mapped_column(
        ForeignKey("cbr_bank_reporting_subjects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        server_default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    )
    form: Mapped[str] = mapped_column(String(8), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    subject_regn: Mapped[str] = mapped_column(String(16), nullable=False)
    archive_member_name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_value_field: Mapped[str] = mapped_column(String(32), nullable=False)
    source_code: Mapped[str | None] = mapped_column(String(128))
    source_subcode: Mapped[str | None] = mapped_column(String(128))
    source_dimensions: Mapped[list[Any]] = mapped_column(JSON_DOCUMENT, nullable=False)
    source_fields_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_value_text: Mapped[str | None] = mapped_column(Text)
    parsed_decimal_value: Mapped[Decimal | None] = mapped_column(
        Numeric(asdecimal=True)
    )
    disclosure_state: Mapped[str] = mapped_column(String(64), nullable=False)
    source_unit: Mapped[str] = mapped_column(String(64), nullable=False)
    source_currency: Mapped[str | None] = mapped_column(String(16))
    source_multiplier: Mapped[int | None] = mapped_column(BigInteger)
    source_date: Mapped[date | None] = mapped_column(Date)
    parser_contract_version: Mapped[str] = mapped_column(String(96), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    snapshot: Mapped["CbrBankReportSnapshot"] = relationship(
        back_populates="observations"
    )
    reporting_subject: Mapped["CbrBankReportingSubject"] = relationship(
        back_populates="observations"
    )


class CbrBankSubjectLegalIssuerEvidence(Base):
    __tablename__ = "cbr_bank_subject_legal_issuer_evidence"
    __table_args__ = (
        UniqueConstraint(
            "evidence_fingerprint",
            name="uq_cbr_bank_subject_legal_issuer_evidence_fingerprint",
        ),
        Index(
            "ix_cbr_bank_subject_legal_issuer_evidence_subject_observed",
            "reporting_subject_id",
            "observed_at",
        ),
        Index(
            "ix_cbr_bank_subject_legal_issuer_evidence_legal_issuer_id",
            "legal_issuer_id",
        ),
        CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1' "
            "and bridge_contract_version = 'cbr-legal-issuer-bridge-v1'",
            name="cbr_bank_subject_legal_issuer_evidence_contract_valid",
        ),
        CheckConstraint(
            "bridge_state in ('VERIFIED', 'NOT_FOUND', 'AMBIGUOUS', "
            "'NOT_VERIFIED', 'SOURCE_IDENTITY_BLOCKED', 'NOT_EVALUATED')",
            name="cbr_bank_subject_legal_issuer_evidence_state_valid",
        ),
        CheckConstraint(
            "bridge_state = 'VERIFIED' or legal_issuer_id is null",
            name="cbr_bank_subject_legal_issuer_evidence_link_fail_closed",
        ),
        CheckConstraint(
            "length(evidence_fingerprint) = 64",
            name="cbr_bank_subject_legal_issuer_evidence_hash_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporting_subject_id: Mapped[int] = mapped_column(
        ForeignKey("cbr_bank_reporting_subjects.id", ondelete="RESTRICT"),
        nullable=False,
    )
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        server_default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    )
    subject_regn: Mapped[str] = mapped_column(String(16), nullable=False)
    bridge_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    bridge_state: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_ogrn: Mapped[str | None] = mapped_column(String(13))
    observed_inn: Mapped[str | None] = mapped_column(String(10))
    observed_cbr_name: Mapped[str | None] = mapped_column(String(512))
    legal_issuer_id: Mapped[int | None] = mapped_column(
        ForeignKey("legal_issuers.id", ondelete="SET NULL")
    )
    legal_issuer_identity_source: Mapped[str | None] = mapped_column(String(32))
    legal_issuer_source_issuer_id: Mapped[str | None] = mapped_column(String(64))
    registry_as_of: Mapped[date] = mapped_column(Date, nullable=False)
    finorg_last_update: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    diagnostic_codes: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    reporting_subject: Mapped["CbrBankReportingSubject"] = relationship(
        back_populates="legal_issuer_evidence"
    )


class CbrBankSubjectLegalIssuerProfile(Base):
    __tablename__ = "cbr_bank_subject_legal_issuer_profiles"
    __table_args__ = (
        Index(
            "ix_cbr_bank_subject_legal_issuer_profiles_legal_issuer_id",
            "legal_issuer_id",
        ),
        CheckConstraint(
            "contract_version = 'cbr-bank-raw-financial-evidence-v1'",
            name="cbr_bank_subject_legal_issuer_profiles_contract_valid",
        ),
        CheckConstraint(
            "bridge_state in ('VERIFIED', 'NOT_FOUND', 'AMBIGUOUS', "
            "'NOT_VERIFIED', 'SOURCE_IDENTITY_BLOCKED', 'NOT_EVALUATED')",
            name="cbr_bank_subject_legal_issuer_profiles_state_valid",
        ),
        CheckConstraint(
            "bridge_state = 'VERIFIED' or legal_issuer_id is null",
            name="cbr_bank_subject_legal_issuer_profiles_link_fail_closed",
        ),
    )

    reporting_subject_id: Mapped[int] = mapped_column(
        ForeignKey("cbr_bank_reporting_subjects.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
        server_default=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    )
    current_evidence_id: Mapped[int | None] = mapped_column(
        ForeignKey("cbr_bank_subject_legal_issuer_evidence.id", ondelete="RESTRICT")
    )
    bridge_state: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_issuer_id: Mapped[int | None] = mapped_column(
        ForeignKey("legal_issuers.id", ondelete="SET NULL")
    )
    legal_issuer_identity_source: Mapped[str | None] = mapped_column(String(32))
    legal_issuer_source_issuer_id: Mapped[str | None] = mapped_column(String(64))
    current_ogrn: Mapped[str | None] = mapped_column(String(13))
    current_inn: Mapped[str | None] = mapped_column(String(10))
    current_cbr_name: Mapped[str | None] = mapped_column(String(512))
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    reporting_subject: Mapped["CbrBankReportingSubject"] = relationship(
        back_populates="legal_issuer_profile"
    )
    current_evidence: Mapped["CbrBankSubjectLegalIssuerEvidence | None"] = relationship(
        foreign_keys=[current_evidence_id]
    )
