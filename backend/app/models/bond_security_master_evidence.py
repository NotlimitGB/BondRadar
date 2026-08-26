from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, JSON, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.bond_security_master_profile import SECURITY_MASTER_CONTRACT_VERSION


SECURITY_MASTER_EVIDENCE_SOURCES = {"moex_universe", "moex_description", "moex_cashflows"}
SECURITY_MASTER_ASSERTION_TYPES = {"scalar_value", "classification"}


class BondSecurityMasterEvidence(Base):
    __tablename__ = "bond_security_master_evidence"
    __table_args__ = (
        UniqueConstraint("evidence_fingerprint", name="uq_bond_security_master_evidence_fingerprint"),
        CheckConstraint("source in ('moex_universe', 'moex_description', 'moex_cashflows')", name="bond_security_master_evidence_source_allowed"),
        CheckConstraint("assertion_type in ('scalar_value', 'classification')", name="bond_security_master_evidence_assertion_type_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_key: Mapped[str | None] = mapped_column(String(128))
    source_table: Mapped[str | None] = mapped_column(String(128))
    assertion_type: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    raw_value_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ingestion_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    evidence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False, default=SECURITY_MASTER_CONTRACT_VERSION, server_default=SECURITY_MASTER_CONTRACT_VERSION)

    bond: Mapped["Bond"] = relationship(back_populates="security_master_evidence")
