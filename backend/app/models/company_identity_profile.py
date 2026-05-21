from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


ISSUER_ROLE_VALUES = {
    "legal_issuer",
    "spv",
    "finance_subsidiary",
    "operating_company",
    "parent_group",
    "unknown",
}
IDENTITY_STATUS_VALUES = {"unknown", "weak", "matched", "verified", "conflict"}
IDENTITY_SOURCE_VALUES = {
    "moex_iss",
    "operator_csv",
    "operator_json",
    "manual_review",
    "existing_company",
    "mixed",
}
REVIEW_STATUS_VALUES = {"pending", "reviewed", "accepted", "rejected"}

ISSUER_ROLE_SQL = (
    "issuer_role in ('legal_issuer', 'spv', 'finance_subsidiary', "
    "'operating_company', 'parent_group', 'unknown')"
)
IDENTITY_STATUS_SQL = (
    "identity_status in ('unknown', 'weak', 'matched', 'verified', 'conflict')"
)
IDENTITY_SOURCE_SQL = (
    "identity_source in ('moex_iss', 'operator_csv', 'operator_json', "
    "'manual_review', 'existing_company', 'mixed')"
)
REVIEW_STATUS_SQL = "review_status in ('pending', 'reviewed', 'accepted', 'rejected')"
IDENTITY_CONFIDENCE_SQL = (
    "identity_confidence is null or "
    "(identity_confidence >= 0 and identity_confidence <= 1)"
)


class CompanyIdentityProfile(Base):
    __tablename__ = "company_identity_profiles"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_company_identity_profiles_company_id"),
        CheckConstraint(ISSUER_ROLE_SQL, name="company_identity_issuer_role_allowed"),
        CheckConstraint(
            IDENTITY_STATUS_SQL,
            name="company_identity_status_allowed",
        ),
        CheckConstraint(
            IDENTITY_SOURCE_SQL,
            name="company_identity_source_allowed",
        ),
        CheckConstraint(REVIEW_STATUS_SQL, name="company_identity_review_status_allowed"),
        CheckConstraint(
            IDENTITY_CONFIDENCE_SQL,
            name="company_identity_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    legal_name: Mapped[str | None] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    inn: Mapped[str | None] = mapped_column(String(16), index=True)
    ogrn: Mapped[str | None] = mapped_column(String(32), index=True)
    kpp: Mapped[str | None] = mapped_column(String(16))
    okpo: Mapped[str | None] = mapped_column(String(16))
    country: Mapped[str | None] = mapped_column(String(64))
    issuer_group_name: Mapped[str | None] = mapped_column(String(255))
    issuer_group_inn: Mapped[str | None] = mapped_column(String(16))
    issuer_role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown", server_default="unknown"
    )
    identity_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="unknown",
        server_default="unknown",
        index=True,
    )
    identity_confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    identity_source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="existing_company",
        server_default="existing_company",
        index=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    source_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    company: Mapped["Company"] = relationship(back_populates="identity_profile")
