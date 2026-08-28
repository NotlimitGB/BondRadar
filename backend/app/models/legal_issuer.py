from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, Index, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


if TYPE_CHECKING:
    from app.models.legal_issuer_evidence import LegalIssuerEvidence


LEGAL_ISSUER_MASTER_CONTRACT_VERSION = "legal-issuer-master-v1"
LEGAL_ISSUER_MASTER_SOURCE = "moex_security_reference"
LEGAL_ISSUER_RESOLUTION_STATES = {"observed", "verified", "conflict"}


class LegalIssuer(Base):
    __tablename__ = "legal_issuers"
    __table_args__ = (
        UniqueConstraint(
            "identity_source",
            "source_issuer_id",
            name="uq_legal_issuers_source_identity",
        ),
        Index("ix_legal_issuers_source_issuer_id", "source_issuer_id"),
        CheckConstraint(
            "contract_version = 'legal-issuer-master-v1'",
            name="legal_issuers_contract_version_valid",
        ),
        CheckConstraint(
            "identity_source = 'moex_security_reference'",
            name="legal_issuers_identity_source_allowed",
        ),
        CheckConstraint(
            "length(source_issuer_id) > 0",
            name="legal_issuers_source_issuer_id_present",
        ),
        CheckConstraint(
            "resolution_state in ('observed', 'verified', 'conflict')",
            name="legal_issuers_resolution_state_allowed",
        ),
        CheckConstraint(
            "resolution_state != 'verified' or issuer_title is not null",
            name="legal_issuers_verified_title_present",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
        server_default=LEGAL_ISSUER_MASTER_CONTRACT_VERSION,
    )
    identity_source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=LEGAL_ISSUER_MASTER_SOURCE,
        server_default=LEGAL_ISSUER_MASTER_SOURCE,
    )
    source_issuer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="observed",
        server_default="observed",
    )
    issuer_title: Mapped[str | None] = mapped_column(String(512))
    issuer_inn: Mapped[str | None] = mapped_column(String(32))
    issuer_okpo: Mapped[str | None] = mapped_column(String(32))
    first_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    evidence: Mapped[list["LegalIssuerEvidence"]] = relationship(
        back_populates="legal_issuer",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
