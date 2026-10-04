"""Immutable daily portfolio accounting."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowDailySnapshot(Base):
    __tablename__ = "shadow_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("shadow_run_id", "as_of_date"),
        CheckConstraint("active_position_count >= 0 AND redeemed_position_count >= 0 AND ledger_entry_count_to_date >= 0 AND applied_cashflow_count_for_day >= 0", name="shadow_daily_snapshot_check_1"),
        CheckConstraint("CAST(cash_rub AS NUMERIC) >= 0 AND CAST(market_value_rub AS NUMERIC) >= 0 AND CAST(nav_rub AS NUMERIC) >= 0", name="shadow_snapshot_nonnegative"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_run_id: Mapped[int] = mapped_column(ForeignKey("shadow_test_runs.id", ondelete="RESTRICT"), index=True)
    as_of_date: Mapped[date] = mapped_column(Date)
    previous_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("shadow_daily_snapshots.id", ondelete="RESTRICT"))
    cash_rub: Mapped[Decimal] = mapped_column(ShadowDecimal())
    market_value_rub: Mapped[Decimal] = mapped_column(ShadowDecimal())
    nav_rub: Mapped[Decimal] = mapped_column(ShadowDecimal())
    daily_return: Mapped[Decimal] = mapped_column(ShadowDecimal())
    cumulative_return: Mapped[Decimal] = mapped_column(ShadowDecimal())
    active_position_count: Mapped[int] = mapped_column(Integer)
    redeemed_position_count: Mapped[int] = mapped_column(Integer)
    ledger_entry_count_to_date: Mapped[int] = mapped_column(Integer)
    applied_cashflow_count_for_day: Mapped[int] = mapped_column(Integer)
    input_state_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
