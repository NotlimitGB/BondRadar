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


LEGAL_ISSUER_MAPPING_CONTRACT_VERSION = "bond-legal-issuer-mapping-v1"
LEGAL_ISSUER_MAPPING_SOURCE = "moex_security_reference"
LEGAL_ISSUER_MAPPING_STATES = {"unknown", "observed", "verified", "conflict"}
LEGAL_ISSUER_SUCCESSFUL_MATCH_STATUSES = {
    "EXACT_SECID",
    "EXACT_SECID_ISIN_CORROBORATED",
    "EXACT_ISIN_RECOVERED",
}


class BondLegalIssuerProfile(Base):
    __tablename__ = "bond_legal_issuer_profiles"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            name="uq_bond_legal_issuer_profiles_bond_id",
        ),
        Index(
            "ix_bond_legal_issuer_profiles_source_issuer_id",
            "source_issuer_id",
        ),
        CheckConstraint(
            "mapping_state in ('unknown', 'observed', 'verified', 'conflict')",
            name="bond_legal_issuer_profile_state_allowed",
        ),
        CheckConstraint(
            "mapping_source is null or "
            "mapping_source = 'moex_security_reference'",
            name="bond_legal_issuer_profile_source_allowed",
        ),
        CheckConstraint(
            "security_match_status is null or security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED', "
            "'EXACT_ISIN_RECOVERED')",
            name="bond_legal_issuer_profile_match_status_allowed",
        ),
        CheckConstraint(
            "((mapping_state = 'verified' and source_issuer_id is not null "
            "and issuer_title is not null and security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED')) "
            "or (mapping_state = 'observed' and source_issuer_id is not null "
            "and security_match_status is not null) "
            "or (mapping_state in ('unknown', 'conflict') "
            "and source_issuer_id is null and issuer_title is null "
            "and issuer_inn is null and issuer_okpo is null "
            "and security_match_status is null))",
            name="bond_legal_issuer_profile_state_values_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contract_version: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
        server_default=LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    )
    mapping_state: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="unknown",
        server_default="unknown",
    )
    mapping_source: Mapped[str | None] = mapped_column(String(32))
    source_issuer_id: Mapped[str | None] = mapped_column(String(64))
    issuer_title: Mapped[str | None] = mapped_column(String(512))
    issuer_inn: Mapped[str | None] = mapped_column(String(32))
    issuer_okpo: Mapped[str | None] = mapped_column(String(32))
    security_match_status: Mapped[str | None] = mapped_column(String(48))
    last_observed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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

    bond: Mapped["Bond"] = relationship(back_populates="legal_issuer_profile")
