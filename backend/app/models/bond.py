from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.enums import ANALYSIS_SIGNAL_SQL, AnalysisSignal


class Bond(Base):
    __tablename__ = "bonds"
    __table_args__ = (
        CheckConstraint(ANALYSIS_SIGNAL_SQL, name="bonds_signal_allowed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    isin: Mapped[str | None] = mapped_column(
        String(12), nullable=True, unique=True, index=True
    )
    secid: Mapped[str | None] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    nominal_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    current_price: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    yield_to_maturity: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    duration_years: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    maturity_date: Mapped[date | None] = mapped_column(Date)
    offer_date: Mapped[date | None] = mapped_column(Date)
    is_floating_coupon: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    is_subordinated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_perpetual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    amortization: Mapped[bool | None] = mapped_column(Boolean)
    liquidity_score: Mapped[int | None] = mapped_column()
    signal: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AnalysisSignal.INSUFFICIENT_DATA.value
    )
    risk_notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    company: Mapped["Company"] = relationship(back_populates="bonds")
    scores: Mapped[list["BondScore"]] = relationship(
        back_populates="bond",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
