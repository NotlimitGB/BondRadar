from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.company_credit_health_snapshot import RISK_LEVEL_SQL


DECISION_STATUS_SQL = (
    "decision_status in ('eligible_for_analysis', 'watchlist', "
    "'blocked_by_risk', 'insufficient_data')"
)


class BondRiskAssessment(Base):
    __tablename__ = "bond_risk_assessments"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            "as_of_date",
            name="bond_risk_assessments_bond_as_of_unique",
        ),
        CheckConstraint(DECISION_STATUS_SQL, name="bond_risk_decision_status_allowed"),
        CheckConstraint(RISK_LEVEL_SQL, name="bond_risk_level_allowed"),
        CheckConstraint(
            "assessment_score between 0 and 100",
            name="bond_risk_assessment_score_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    company_credit_health_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_credit_health_snapshots.id", ondelete="SET NULL"),
        index=True,
    )
    bond_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_scores.id", ondelete="SET NULL"), index=True
    )
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_market_snapshots.id", ondelete="SET NULL"), index=True
    )
    assessment_score: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    required_risk_premium: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False
    )
    yield_to_maturity: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    duration_years: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    liquidity_score: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    company_credit_status: Mapped[str | None] = mapped_column(String(32))
    company_credit_health_score: Mapped[int | None] = mapped_column(Integer)
    company_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    bond_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    gates: Mapped[dict[str, str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    warnings: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    blocking_reasons: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    positive_factors: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    negative_factors: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    missing_data: Mapped[list[str]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    explanation: Mapped[dict[str, Any]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    bond: Mapped["Bond"] = relationship()
    company: Mapped["Company"] = relationship()
    company_credit_health: Mapped["CompanyCreditHealthSnapshot | None"] = relationship()
    bond_score_snapshot: Mapped["BondScore | None"] = relationship()
    market_snapshot: Mapped["BondMarketSnapshot | None"] = relationship()
