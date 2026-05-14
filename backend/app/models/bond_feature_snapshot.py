from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, Numeric, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BondFeatureSnapshot(Base):
    __tablename__ = "bond_feature_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            "as_of_date",
            name="bond_feature_snapshots_bond_as_of_unique",
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
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_market_snapshots.id", ondelete="SET NULL"), index=True
    )
    bond_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_scores.id", ondelete="SET NULL"), index=True
    )
    company_score_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_scores.id", ondelete="SET NULL"), index=True
    )
    financial_report_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_reports.id", ondelete="SET NULL"), index=True
    )
    bond_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    company_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    yield_to_maturity: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    duration_years: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    liquidity_score: Mapped[int | None] = mapped_column(Integer)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    spread_to_ofz: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    net_debt_to_ebitda: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    debt_to_equity: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    interest_coverage: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cash_to_short_term_debt: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    ocf_to_total_debt: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    net_profit_margin: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    days_to_maturity: Mapped[int | None] = mapped_column(Integer)
    has_offer: Mapped[bool | None] = mapped_column(Boolean)
    has_amortization: Mapped[bool | None] = mapped_column(Boolean)
    missing_data_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    features_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    bond: Mapped["Bond"] = relationship()
    company: Mapped["Company"] = relationship()
    market_snapshot: Mapped["BondMarketSnapshot | None"] = relationship()
    bond_score_snapshot: Mapped["BondScore | None"] = relationship()
    company_score_snapshot: Mapped["CompanyScore | None"] = relationship()
    financial_report: Mapped["FinancialReport | None"] = relationship()
