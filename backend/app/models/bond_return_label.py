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


RETURN_LABEL_SQL = (
    "label in ('outperform', 'underperform', 'positive_return', "
    "'negative_return', 'insufficient_data')"
)
RETURN_METHOD_SQL = "return_method in ('price', 'total_return', 'risk_adjusted')"


class BondReturnLabel(Base):
    __tablename__ = "bond_return_labels"
    __table_args__ = (
        UniqueConstraint(
            "bond_id",
            "as_of_date",
            "horizon_days",
            "return_method",
            name="bond_return_labels_bond_as_of_horizon_method_unique",
        ),
        CheckConstraint(RETURN_LABEL_SQL, name="bond_return_labels_label_allowed"),
        CheckConstraint(
            RETURN_METHOD_SQL,
            name="bond_return_labels_return_method_allowed",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True
    )
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    start_market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_market_snapshots.id", ondelete="SET NULL"), index=True
    )
    end_market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_market_snapshots.id", ondelete="SET NULL"), index=True
    )
    return_method: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="price",
        server_default="price",
        index=True,
    )
    start_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    end_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    future_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    benchmark_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    excess_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    price_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    coupon_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    amortization_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    redemption_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    gross_total_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    estimated_costs_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    net_total_return: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    risk_adjusted_excess_return: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6)
    )
    required_risk_premium: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    return_calculation_warnings: Mapped[list[str] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    return_calculation_details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite")
    )
    label: Mapped[str] = mapped_column(
        String(32), nullable=False, default="insufficient_data"
    )
    label_binary: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    bond: Mapped["Bond"] = relationship()
    start_market_snapshot: Mapped["BondMarketSnapshot | None"] = relationship(
        foreign_keys=[start_market_snapshot_id]
    )
    end_market_snapshot: Mapped["BondMarketSnapshot | None"] = relationship(
        foreign_keys=[end_market_snapshot_id]
    )
