from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.bond_legal_issuer_profile import (
    LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
)
from app.models.legal_issuer import (
    LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
    LEGAL_ISSUER_MASTER_SOURCE,
)


class LegalIssuerEvidence(Base):
    __tablename__ = "legal_issuer_evidence"
    __table_args__ = (
        UniqueConstraint(
            "evidence_fingerprint",
            name="uq_legal_issuer_evidence_fingerprint",
        ),
        UniqueConstraint(
            "upstream_contract_version",
            "upstream_evidence_fingerprint",
            name="uq_legal_issuer_evidence_upstream_lineage",
        ),
        Index(
            "ix_legal_issuer_evidence_issuer_security_observed",
            "legal_issuer_id",
            "source_security_secid",
            "observed_at",
        ),
        Index(
            "ix_legal_issuer_evidence_upstream_fingerprint",
            "upstream_evidence_fingerprint",
        ),
        CheckConstraint(
            "contract_version = 'legal-issuer-master-v1'",
            name="legal_issuer_evidence_contract_version_valid",
        ),
        CheckConstraint(
            "source = 'moex_security_reference'",
            name="legal_issuer_evidence_source_allowed",
        ),
        CheckConstraint(
            "upstream_contract_version = 'bond-legal-issuer-mapping-v1'",
            name="legal_issuer_evidence_upstream_contract_valid",
        ),
        CheckConstraint(
            "security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED', "
            "'EXACT_ISIN_RECOVERED')",
            name="legal_issuer_evidence_match_status_allowed",
        ),
        CheckConstraint(
            "length(source_issuer_id) > 0 and length(source_security_secid) > 0",
            name="legal_issuer_evidence_source_identity_present",
        ),
        CheckConstraint(
            "length(upstream_evidence_fingerprint) = 64",
            name="legal_issuer_evidence_upstream_fingerprint_valid",
        ),
        CheckConstraint(
            "length(evidence_fingerprint) = 64",
            name="legal_issuer_evidence_fingerprint_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    legal_issuer_id: Mapped[int] = mapped_column(
        ForeignKey("legal_issuers.id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
        server_default=LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=LEGAL_ISSUER_MASTER_SOURCE,
        server_default=LEGAL_ISSUER_MASTER_SOURCE,
    )
    source_issuer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    upstream_contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
        server_default=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    )
    upstream_evidence_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    source_bond_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_security_secid: Mapped[str] = mapped_column(String(32), nullable=False)
    source_security_isin: Mapped[str | None] = mapped_column(String(32))
    security_match_status: Mapped[str] = mapped_column(String(48), nullable=False)
    issuer_title: Mapped[str | None] = mapped_column(String(512))
    issuer_inn: Mapped[str | None] = mapped_column(String(32))
    issuer_okpo: Mapped[str | None] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upstream_ingestion_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingestion_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    legal_issuer: Mapped["LegalIssuer"] = relationship(back_populates="evidence")
