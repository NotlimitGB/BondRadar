"""Immutable held and redeemed position evidence."""
from datetime import date
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowDailyPositionSnapshot(Base):
    __tablename__ = "shadow_daily_position_snapshots"
    __table_args__ = (
        UniqueConstraint("shadow_daily_snapshot_id", "bond_id"),
        CheckConstraint("(position_status = 'ACTIVE' AND quantity > 0 AND market_snapshot_id IS NOT NULL AND market_trade_date IS NOT NULL AND market_age_days >= 0 AND CAST(dirty_value_rub_per_bond AS NUMERIC) > 0) OR (position_status = 'REDEEMED' AND quantity = 0 AND CAST(market_value_rub AS NUMERIC) = 0)", name="shadow_daily_position_snapshot_check_1"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_daily_snapshot_id: Mapped[int] = mapped_column(ForeignKey("shadow_daily_snapshots.id", ondelete="RESTRICT"), index=True)
    shadow_run_id: Mapped[int] = mapped_column(ForeignKey("shadow_test_runs.id", ondelete="RESTRICT"), index=True)
    bond_id: Mapped[int] = mapped_column(ForeignKey("bonds.id", ondelete="RESTRICT"))
    position_status: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    market_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("bond_market_snapshots.id", ondelete="RESTRICT"))
    market_trade_date: Mapped[date | None] = mapped_column(Date)
    market_age_days: Mapped[int | None] = mapped_column(Integer)
    price_basis: Mapped[str | None] = mapped_column(String(32))
    dirty_value_rub_per_bond: Mapped[Decimal | None] = mapped_column(ShadowDecimal())
    market_value_rub: Mapped[Decimal] = mapped_column(ShadowDecimal())
    cashflow_rub_on_date: Mapped[Decimal] = mapped_column(ShadowDecimal())
    source_dv01_contract_version: Mapped[str | None] = mapped_column(String(64))
    source_security_master_profile_id: Mapped[int | None] = mapped_column(ForeignKey("bond_security_master_profiles.id", ondelete="RESTRICT"))
    position_state_sha256: Mapped[str] = mapped_column(String(64))
