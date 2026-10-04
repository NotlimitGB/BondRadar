"""Append-only Shadow accounting events."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowLedgerEntry(Base):
    __tablename__ = "shadow_ledger_entries"
    __table_args__ = (
        UniqueConstraint("shadow_run_id", "sequence_number"),
        CheckConstraint("sequence_number > 0", name="shadow_ledger_entry_check_1"),
        CheckConstraint("event_type IN ('INITIAL_CAPITAL','GENESIS_PURCHASE','COUPON','AMORTIZATION','REDEMPTION')", name="shadow_ledger_entry_check_2"),
        CheckConstraint("(event_type = 'INITIAL_CAPITAL' AND bond_id IS NULL AND quantity_delta = 0) OR (event_type != 'INITIAL_CAPITAL' AND bond_id IS NOT NULL)", name="shadow_ledger_entry_check_3"),
        CheckConstraint("(event_type = 'INITIAL_CAPITAL' AND CAST(cash_delta_rub AS NUMERIC) > 0) OR (event_type = 'GENESIS_PURCHASE' AND quantity_delta > 0 AND CAST(cash_delta_rub AS NUMERIC) < 0) OR (event_type IN ('COUPON','AMORTIZATION') AND quantity_delta = 0 AND CAST(cash_delta_rub AS NUMERIC) >= 0) OR (event_type = 'REDEMPTION' AND quantity_delta < 0 AND CAST(cash_delta_rub AS NUMERIC) >= 0)", name="shadow_event_signs"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    shadow_run_id: Mapped[int] = mapped_column(ForeignKey("shadow_test_runs.id", ondelete="RESTRICT"), index=True)
    event_key_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_date: Mapped[date] = mapped_column(Date, index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(32))
    bond_id: Mapped[int | None] = mapped_column(ForeignKey("bonds.id", ondelete="RESTRICT"))
    quantity_delta: Mapped[int] = mapped_column(Integer)
    cash_delta_rub: Mapped[Decimal] = mapped_column(ShadowDecimal())
    unit_amount_rub: Mapped[Decimal | None] = mapped_column(ShadowDecimal())
    source_market_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("bond_market_snapshots.id", ondelete="RESTRICT"))
    source_cashflow_event_id: Mapped[int | None] = mapped_column(ForeignKey("bond_cashflow_events.id", ondelete="RESTRICT"))
    source_contract_version: Mapped[str] = mapped_column(String(64))
    source_fingerprint_sha256: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
