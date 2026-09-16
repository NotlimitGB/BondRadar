from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


CREDIT_RISK_SOURCE_ARTIFACT_CONTRACT_VERSION = "credit-risk-source-artifact-v1"
CREDIT_RATING_EVENT_CONTRACT_VERSION = "credit-rating-event-v1"
CREDIT_DEFAULT_EVENT_CONTRACT_VERSION = "credit-default-event-v1"


def _lower_hex_sha256_sql(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column}) = 64 and {column} = lower({column}) "
        f"and length({remainder}) = 0"
    )


_PUBLICATION_CHECK = (
    "(publication_precision = 'DATE' and publication_date is not null "
    "and publication_at is null) or "
    "(publication_precision = 'TIMESTAMP' and publication_date is null "
    "and publication_at is not null) or "
    "(publication_precision = 'UNKNOWN' and publication_date is null "
    "and publication_at is null)"
)


class CreditRiskSourceArtifact(Base):
    __tablename__ = "credit_risk_source_artifacts"
    __table_args__ = (
        UniqueConstraint(
            "source_provider",
            "source_url",
            "content_sha256",
            name="uq_credit_risk_source_artifacts_identity",
        ),
        Index(
            "ix_credit_risk_source_artifacts_provider_retrieved",
            "source_provider",
            "retrieved_at",
        ),
        CheckConstraint(
            "contract_version = 'credit-risk-source-artifact-v1'",
            name="credit_risk_source_artifacts_contract_valid",
        ),
        CheckConstraint(
            "source_provider in ('ACRA', 'EXPERT_RA', 'NRA', 'NKR', 'MOEX', 'CBR_RATINGS')",
            name="credit_risk_source_artifacts_provider_valid",
        ),
        CheckConstraint(
            "source_kind in ('RATING_ISSUER_PAGE', 'RATING_ISSUE_PAGE', "
            "'RATING_RELEASE', 'DEFAULT_INFORMATION', 'OTHER', 'RATING_REPOSITORY_RESPONSE')",
            name="credit_risk_source_artifacts_kind_valid",
        ),
        CheckConstraint(
            "length(content_bytes) > 0 and length(source_url) > 0 "
            "and length(content_type) > 0",
            name="credit_risk_source_artifacts_content_valid",
        ),
        CheckConstraint(
            _lower_hex_sha256_sql("content_sha256"),
            name="credit_risk_source_artifacts_hash_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=CREDIT_RISK_SOURCE_ARTIFACT_CONTRACT_VERSION,
        server_default=CREDIT_RISK_SOURCE_ARTIFACT_CONTRACT_VERSION,
    )
    source_provider: Mapped[str] = mapped_column(String(16), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    rating_events: Mapped[list["CreditRatingEvent"]] = relationship(
        back_populates="artifact", passive_deletes=True
    )
    default_events: Mapped[list["CreditDefaultEvent"]] = relationship(
        back_populates="artifact", passive_deletes=True
    )


class CreditRatingEvent(Base):
    __tablename__ = "credit_rating_events"
    __table_args__ = (
        UniqueConstraint(
            "event_fingerprint", name="uq_credit_rating_events_fingerprint"
        ),
        Index("ix_credit_rating_events_agency_date", "rating_agency", "event_date"),
        Index(
            "ix_credit_rating_events_issuer_date", "legal_issuer_id", "event_date"
        ),
        Index("ix_credit_rating_events_bond_date", "bond_id", "event_date"),
        CheckConstraint(
            "contract_version = 'credit-rating-event-v1'",
            name="credit_rating_events_contract_valid",
        ),
        CheckConstraint(
            "rating_agency in ('ACRA', 'EXPERT_RA', 'NRA', 'NKR')",
            name="credit_rating_events_agency_valid",
        ),
        CheckConstraint(
            "target_kind in ('LEGAL_ISSUER', 'BOND') and "
            "resolution_state in ('RESOLVED', 'UNRESOLVED', 'AMBIGUOUS')",
            name="credit_rating_events_target_state_valid",
        ),
        CheckConstraint(
            "((target_kind = 'LEGAL_ISSUER' and source_issuer_inn is not null "
            "and source_bond_isin is null and "
            "((resolution_state = 'RESOLVED' and legal_issuer_id is not null "
            "and bond_id is null) or "
            "(resolution_state != 'RESOLVED' and legal_issuer_id is null "
            "and bond_id is null))) or "
            "(target_kind = 'BOND' and source_bond_isin is not null "
            "and source_issuer_inn is null and "
            "((resolution_state = 'RESOLVED' and bond_id is not null "
            "and legal_issuer_id is null) or "
            "(resolution_state != 'RESOLVED' and bond_id is null "
            "and legal_issuer_id is null))))",
            name="credit_rating_events_target_link_valid",
        ),
        CheckConstraint(
            "(rating_scale_raw is null or length(rating_scale_raw) > 0) and "
            "(rating_value_raw is not null or rating_outlook_raw is not null "
            "or rating_watch_raw is not null or rating_action_raw is not null)",
            name="credit_rating_events_raw_rating_valid",
        ),
        CheckConstraint(_PUBLICATION_CHECK, name="credit_rating_events_publication_valid"),
        CheckConstraint(
            _lower_hex_sha256_sql("event_fingerprint"),
            name="credit_rating_events_fingerprint_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=CREDIT_RATING_EVENT_CONTRACT_VERSION,
        server_default=CREDIT_RATING_EVENT_CONTRACT_VERSION,
    )
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("credit_risk_source_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    rating_agency: Mapped[str] = mapped_column(String(16), nullable=False)
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution_state: Mapped[str] = mapped_column(String(16), nullable=False)
    source_object_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_issuer_inn: Mapped[str | None] = mapped_column(String(10))
    source_bond_isin: Mapped[str | None] = mapped_column(String(12))
    source_name_raw: Mapped[str | None] = mapped_column(String(512))
    legal_issuer_id: Mapped[int | None] = mapped_column(
        ForeignKey("legal_issuers.id", ondelete="RESTRICT")
    )
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="RESTRICT")
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    publication_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date)
    publication_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rating_scale_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    rating_value_raw: Mapped[str | None] = mapped_column(String(128))
    rating_outlook_raw: Mapped[str | None] = mapped_column(String(256))
    rating_watch_raw: Mapped[str | None] = mapped_column(String(256))
    rating_action_raw: Mapped[str | None] = mapped_column(String(256))
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    artifact: Mapped[CreditRiskSourceArtifact] = relationship(
        back_populates="rating_events"
    )
    legal_issuer: Mapped["LegalIssuer | None"] = relationship()
    bond: Mapped["Bond | None"] = relationship()


class CreditDefaultEvent(Base):
    __tablename__ = "credit_default_events"
    __table_args__ = (
        UniqueConstraint(
            "event_fingerprint", name="uq_credit_default_events_fingerprint"
        ),
        Index("ix_credit_default_events_bond_date", "bond_id", "event_date"),
        Index(
            "ix_credit_default_events_class_date", "default_class", "event_date"
        ),
        CheckConstraint(
            "contract_version = 'credit-default-event-v1' and source_provider = 'MOEX'",
            name="credit_default_events_contract_provider_valid",
        ),
        CheckConstraint(
            "default_class in ('TECHNICAL_DEFAULT', 'DEFAULT', 'OTHER', 'UNKNOWN')",
            name="credit_default_events_class_valid",
        ),
        CheckConstraint(
            "obligation_type in ('COUPON', 'PRINCIPAL', 'OFFER', 'OTHER', 'UNKNOWN')",
            name="credit_default_events_obligation_valid",
        ),
        CheckConstraint(
            "resolution_state in ('RESOLVED', 'UNRESOLVED', 'AMBIGUOUS') and "
            "source_bond_isin is not null and "
            "((resolution_state = 'RESOLVED' and bond_id is not null) or "
            "(resolution_state != 'RESOLVED' and bond_id is null))",
            name="credit_default_events_target_link_valid",
        ),
        CheckConstraint(_PUBLICATION_CHECK, name="credit_default_events_publication_valid"),
        CheckConstraint(
            _lower_hex_sha256_sql("event_fingerprint"),
            name="credit_default_events_fingerprint_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=CREDIT_DEFAULT_EVENT_CONTRACT_VERSION,
        server_default=CREDIT_DEFAULT_EVENT_CONTRACT_VERSION,
    )
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("credit_risk_source_artifacts.id", ondelete="RESTRICT"), nullable=False
    )
    source_provider: Mapped[str] = mapped_column(
        String(16), nullable=False, default="MOEX", server_default="MOEX"
    )
    resolution_state: Mapped[str] = mapped_column(String(16), nullable=False)
    source_object_id: Mapped[str] = mapped_column(String(256), nullable=False)
    source_bond_isin: Mapped[str] = mapped_column(String(12), nullable=False)
    source_name_raw: Mapped[str | None] = mapped_column(String(512))
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="RESTRICT")
    )
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    publication_precision: Mapped[str] = mapped_column(String(16), nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date)
    publication_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    default_class: Mapped[str] = mapped_column(String(32), nullable=False)
    obligation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    default_status_raw: Mapped[str | None] = mapped_column(String(256))
    default_reason_raw: Mapped[str | None] = mapped_column(Text)
    amount_raw: Mapped[str | None] = mapped_column(String(128))
    event_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    artifact: Mapped[CreditRiskSourceArtifact] = relationship(
        back_populates="default_events"
    )
    bond: Mapped["Bond | None"] = relationship()
