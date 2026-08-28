from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.bond_legal_issuer_profile import (
    LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
)


class BondLegalIssuerEvidence(Base):
    __tablename__ = "bond_legal_issuer_evidence"
    __table_args__ = (
        UniqueConstraint(
            "evidence_fingerprint",
            name="uq_bond_legal_issuer_evidence_fingerprint",
        ),
        Index(
            "ix_bond_legal_issuer_evidence_bond_source_observed",
            "bond_id",
            "source",
            "observed_at",
        ),
        Index(
            "ix_bond_legal_issuer_evidence_source_issuer_id",
            "source_issuer_id",
        ),
        CheckConstraint(
            "source = 'moex_security_reference'",
            name="bond_legal_issuer_evidence_source_allowed",
        ),
        CheckConstraint(
            "security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED', "
            "'EXACT_ISIN_RECOVERED')",
            name="bond_legal_issuer_evidence_match_status_allowed",
        ),
        CheckConstraint(
            "length(evidence_fingerprint) = 64",
            name="bond_legal_issuer_evidence_fingerprint_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="moex_security_reference",
        server_default="moex_security_reference",
    )
    requested_secid: Mapped[str | None] = mapped_column(String(32))
    expected_isin: Mapped[str | None] = mapped_column(String(32))
    matched_secid: Mapped[str] = mapped_column(String(32), nullable=False)
    matched_isin: Mapped[str | None] = mapped_column(String(32))
    source_issuer_id: Mapped[str | None] = mapped_column(String(64))
    issuer_title: Mapped[str | None] = mapped_column(String(512))
    issuer_inn: Mapped[str | None] = mapped_column(String(32))
    issuer_okpo: Mapped[str | None] = mapped_column(String(32))
    security_match_status: Mapped[str] = mapped_column(String(48), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingestion_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
        server_default=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    )

    bond: Mapped["Bond"] = relationship(back_populates="legal_issuer_evidence")
