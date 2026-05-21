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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


DUPLICATE_MATCH_TYPE_VALUES = {
    "exact_inn",
    "exact_ogrn",
    "exact_legal_name",
    "normalized_name",
    "bond_name_phrase",
    "same_group_name",
    "manual_review",
    "mixed",
}
DUPLICATE_STATUS_VALUES = {
    "candidate",
    "accepted",
    "rejected",
    "needs_review",
    "conflict",
}
DUPLICATE_REVIEW_STATUS_VALUES = {"pending", "reviewed", "accepted", "rejected"}

DUPLICATE_MATCH_TYPE_SQL = (
    "match_type in ('exact_inn', 'exact_ogrn', 'exact_legal_name', "
    "'normalized_name', 'bond_name_phrase', 'same_group_name', "
    "'manual_review', 'mixed')"
)
DUPLICATE_STATUS_SQL = (
    "status in ('candidate', 'accepted', 'rejected', 'needs_review', 'conflict')"
)
DUPLICATE_REVIEW_STATUS_SQL = (
    "review_status in ('pending', 'reviewed', 'accepted', 'rejected')"
)
DUPLICATE_SCORE_SQL = "match_score >= 0 and match_score <= 1"


class CompanyIdentityDuplicateCandidate(Base):
    __tablename__ = "company_identity_duplicate_candidates"
    __table_args__ = (
        UniqueConstraint(
            "canonical_company_id",
            "candidate_company_id",
            name="uq_company_identity_duplicate_pair",
        ),
        CheckConstraint(
            "canonical_company_id <> candidate_company_id",
            name="company_identity_duplicate_distinct_companies",
        ),
        CheckConstraint(
            DUPLICATE_MATCH_TYPE_SQL,
            name="company_identity_duplicate_match_type_allowed",
        ),
        CheckConstraint(
            DUPLICATE_STATUS_SQL,
            name="company_identity_duplicate_status_allowed",
        ),
        CheckConstraint(
            DUPLICATE_REVIEW_STATUS_SQL,
            name="company_identity_duplicate_review_status_allowed",
        ),
        CheckConstraint(
            DUPLICATE_SCORE_SQL,
            name="company_identity_duplicate_score_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    group_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    match_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    match_score: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    match_reasons: Mapped[list[dict[str, Any]] | list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="candidate", server_default="candidate", index=True
    )
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    review_notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(
        String(64), nullable=False, default="diagnostics", server_default="diagnostics"
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
