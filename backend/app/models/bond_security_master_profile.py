from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


SECURITY_MASTER_CONTRACT_VERSION = "bond-security-master-v2"
SCALAR_STATE_VALUES = {"unknown", "verified", "conflict"}
COUPON_STRUCTURE_VALUES = {"unknown", "fixed", "floating", "conflict"}
AMORTIZATION_STRUCTURE_VALUES = {"unknown", "bullet", "amortizing", "conflict"}
SUBORDINATION_STRUCTURE_VALUES = {"unknown", "senior", "subordinated", "conflict"}
PERPETUAL_STRUCTURE_VALUES = {"unknown", "dated", "perpetual", "conflict"}
OFFER_STRUCTURE_VALUES = {"unknown", "none", "present", "conflict"}
LISTING_STATUS_VALUES = {
    "unknown",
    "active",
    "inactive",
    "delisted",
    "defaulted",
    "conflict",
}


def _state_value_pair(state: str, value: str, verified_check: str = "1 = 1") -> str:
    return (
        f"(({state} = 'verified' and {value} is not null and ({verified_check})) or "
        f"({state} in ('unknown', 'conflict') and {value} is null))"
    )


class BondSecurityMasterProfile(Base):
    __tablename__ = "bond_security_master_profiles"
    __table_args__ = (
        UniqueConstraint("bond_id", name="uq_bond_security_master_profiles_bond_id"),
        CheckConstraint("currency_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_currency_state_allowed"),
        CheckConstraint("nominal_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_nominal_state_allowed"),
        CheckConstraint("coupon_rate_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_coupon_rate_state_allowed"),
        CheckConstraint("maturity_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_maturity_state_allowed"),
        CheckConstraint("lot_size_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_lot_size_state_allowed"),
        CheckConstraint("trading_board_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_trading_board_state_allowed"),
        CheckConstraint("coupon_frequency_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_coupon_frequency_state_allowed"),
        CheckConstraint("coupon_formula_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_coupon_formula_state_allowed"),
        CheckConstraint("outstanding_nominal_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_outstanding_nominal_state_allowed"),
        CheckConstraint("coupon_structure in ('unknown', 'fixed', 'floating', 'conflict')", name="bond_security_master_coupon_structure_allowed"),
        CheckConstraint("amortization_structure in ('unknown', 'bullet', 'amortizing', 'conflict')", name="bond_security_master_amortization_structure_allowed"),
        CheckConstraint("subordination_structure in ('unknown', 'senior', 'subordinated', 'conflict')", name="bond_security_master_subordination_structure_allowed"),
        CheckConstraint("perpetual_structure in ('unknown', 'dated', 'perpetual', 'conflict')", name="bond_security_master_perpetual_structure_allowed"),
        CheckConstraint("offer_structure in ('unknown', 'none', 'present', 'conflict')", name="bond_security_master_offer_structure_allowed"),
        CheckConstraint("listing_status in ('unknown', 'active', 'inactive', 'delisted', 'defaulted', 'conflict')", name="bond_security_master_listing_status_allowed"),
        CheckConstraint(_state_value_pair("currency_state", "currency_code", "length(currency_code) = 3 and currency_code = upper(currency_code)"), name="bond_security_master_currency_pair_valid"),
        CheckConstraint(_state_value_pair("nominal_state", "nominal_value", "nominal_value > 0"), name="bond_security_master_nominal_pair_valid"),
        CheckConstraint(_state_value_pair("coupon_rate_state", "coupon_rate", "coupon_rate >= 0"), name="bond_security_master_coupon_rate_pair_valid"),
        CheckConstraint(_state_value_pair("maturity_state", "maturity_date"), name="bond_security_master_maturity_pair_valid"),
        CheckConstraint(_state_value_pair("lot_size_state", "lot_size", "lot_size > 0"), name="bond_security_master_lot_size_pair_valid"),
        CheckConstraint(_state_value_pair("trading_board_state", "trading_board", "length(trim(trading_board)) > 0"), name="bond_security_master_trading_board_pair_valid"),
        CheckConstraint(_state_value_pair("coupon_frequency_state", "coupon_frequency_per_year", "coupon_frequency_per_year > 0"), name="bond_security_master_coupon_frequency_pair_valid"),
        CheckConstraint(_state_value_pair("coupon_formula_state", "coupon_formula", "length(trim(coupon_formula)) > 0"), name="bond_security_master_coupon_formula_pair_valid"),
        CheckConstraint(_state_value_pair("outstanding_nominal_state", "outstanding_nominal", "outstanding_nominal > 0"), name="bond_security_master_outstanding_nominal_pair_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(ForeignKey("bonds.id", ondelete="CASCADE"), nullable=False, index=True)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False, default=SECURITY_MASTER_CONTRACT_VERSION, server_default=SECURITY_MASTER_CONTRACT_VERSION)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    currency_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    nominal_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    nominal_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    coupon_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    coupon_rate_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    maturity_date: Mapped[date | None] = mapped_column(Date)
    maturity_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    coupon_structure: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    amortization_structure: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    subordination_structure: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    perpetual_structure: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    offer_structure: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    lot_size: Mapped[int | None] = mapped_column(Integer)
    lot_size_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    trading_board: Mapped[str | None] = mapped_column(String(32))
    trading_board_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    coupon_frequency_per_year: Mapped[int | None] = mapped_column(Integer)
    coupon_frequency_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    coupon_formula: Mapped[str | None] = mapped_column(Text)
    coupon_formula_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    outstanding_nominal: Mapped[Decimal | None] = mapped_column(Numeric(24, 8))
    outstanding_nominal_state: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    listing_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown", server_default="unknown")
    last_resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    bond: Mapped["Bond"] = relationship(back_populates="security_master_profile")
