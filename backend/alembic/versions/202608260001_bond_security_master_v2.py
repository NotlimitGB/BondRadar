"""Add evidence-aware bond security master v2.

Revision ID: 202608260001
Revises: 202608080001
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202608260001"
down_revision = "202608080001"
branch_labels = None
depends_on = None

PROFILE = "bond_security_master_profiles"
EVIDENCE = "bond_security_master_evidence"
JSON_TYPE = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def _state_pair(state: str, value: str, valid: str = "1 = 1") -> str:
    return (
        f"(({state} = 'verified' and {value} is not null and ({valid})) or "
        f"({state} in ('unknown', 'conflict') and {value} is null))"
    )


def upgrade() -> None:
    op.create_table(
        PROFILE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False, server_default="bond-security-master-v2"),
        sa.Column("currency_code", sa.String(3)),
        sa.Column("currency_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("nominal_value", sa.Numeric(24, 8)),
        sa.Column("nominal_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("coupon_rate", sa.Numeric(18, 8)),
        sa.Column("coupon_rate_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("maturity_date", sa.Date()),
        sa.Column("maturity_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("coupon_structure", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("amortization_structure", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("subordination_structure", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("perpetual_structure", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("offer_structure", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("lot_size", sa.Integer()),
        sa.Column("lot_size_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("trading_board", sa.String(32)),
        sa.Column("trading_board_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("coupon_frequency_per_year", sa.Integer()),
        sa.Column("coupon_frequency_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("coupon_formula", sa.Text()),
        sa.Column("coupon_formula_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("outstanding_nominal", sa.Numeric(24, 8)),
        sa.Column("outstanding_nominal_state", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("listing_status", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("last_resolved_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["bond_id"], ["bonds.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("bond_id", name="uq_bond_security_master_profiles_bond_id"),
        sa.CheckConstraint("currency_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_currency_state_allowed"),
        sa.CheckConstraint("nominal_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_nominal_state_allowed"),
        sa.CheckConstraint("coupon_rate_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_coupon_rate_state_allowed"),
        sa.CheckConstraint("maturity_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_maturity_state_allowed"),
        sa.CheckConstraint("lot_size_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_lot_size_state_allowed"),
        sa.CheckConstraint("trading_board_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_trading_board_state_allowed"),
        sa.CheckConstraint("coupon_frequency_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_coupon_frequency_state_allowed"),
        sa.CheckConstraint("coupon_formula_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_coupon_formula_state_allowed"),
        sa.CheckConstraint("outstanding_nominal_state in ('unknown', 'verified', 'conflict')", name="bond_security_master_outstanding_nominal_state_allowed"),
        sa.CheckConstraint("coupon_structure in ('unknown', 'fixed', 'floating', 'conflict')", name="bond_security_master_coupon_structure_allowed"),
        sa.CheckConstraint("amortization_structure in ('unknown', 'bullet', 'amortizing', 'conflict')", name="bond_security_master_amortization_structure_allowed"),
        sa.CheckConstraint("subordination_structure in ('unknown', 'senior', 'subordinated', 'conflict')", name="bond_security_master_subordination_structure_allowed"),
        sa.CheckConstraint("perpetual_structure in ('unknown', 'dated', 'perpetual', 'conflict')", name="bond_security_master_perpetual_structure_allowed"),
        sa.CheckConstraint("offer_structure in ('unknown', 'none', 'present', 'conflict')", name="bond_security_master_offer_structure_allowed"),
        sa.CheckConstraint("listing_status in ('unknown', 'active', 'inactive', 'delisted', 'defaulted', 'conflict')", name="bond_security_master_listing_status_allowed"),
        sa.CheckConstraint(_state_pair("currency_state", "currency_code", "length(currency_code) = 3 and currency_code = upper(currency_code)"), name="bond_security_master_currency_pair_valid"),
        sa.CheckConstraint(_state_pair("nominal_state", "nominal_value", "nominal_value > 0"), name="bond_security_master_nominal_pair_valid"),
        sa.CheckConstraint(_state_pair("coupon_rate_state", "coupon_rate", "coupon_rate >= 0"), name="bond_security_master_coupon_rate_pair_valid"),
        sa.CheckConstraint(_state_pair("maturity_state", "maturity_date"), name="bond_security_master_maturity_pair_valid"),
        sa.CheckConstraint(_state_pair("lot_size_state", "lot_size", "lot_size > 0"), name="bond_security_master_lot_size_pair_valid"),
        sa.CheckConstraint(_state_pair("trading_board_state", "trading_board", "length(trim(trading_board)) > 0"), name="bond_security_master_trading_board_pair_valid"),
        sa.CheckConstraint(_state_pair("coupon_frequency_state", "coupon_frequency_per_year", "coupon_frequency_per_year > 0"), name="bond_security_master_coupon_frequency_pair_valid"),
        sa.CheckConstraint(_state_pair("coupon_formula_state", "coupon_formula", "length(trim(coupon_formula)) > 0"), name="bond_security_master_coupon_formula_pair_valid"),
        sa.CheckConstraint(_state_pair("outstanding_nominal_state", "outstanding_nominal", "outstanding_nominal > 0"), name="bond_security_master_outstanding_nominal_pair_valid"),
    )
    op.create_index("ix_bond_security_master_profiles_bond_id", PROFILE, ["bond_id"])

    op.create_table(
        EVIDENCE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_key", sa.String(128)),
        sa.Column("source_table", sa.String(128)),
        sa.Column("assertion_type", sa.String(32), nullable=False),
        sa.Column("normalized_value_json", JSON_TYPE),
        sa.Column("raw_value_json", JSON_TYPE),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False, server_default="bond-security-master-v2"),
        sa.ForeignKeyConstraint(["bond_id"], ["bonds.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("evidence_fingerprint", name="uq_bond_security_master_evidence_fingerprint"),
        sa.CheckConstraint("source in ('moex_universe', 'moex_description', 'moex_cashflows')", name="bond_security_master_evidence_source_allowed"),
        sa.CheckConstraint("assertion_type in ('scalar_value', 'classification')", name="bond_security_master_evidence_assertion_type_allowed"),
    )
    op.create_index("ix_bond_security_master_evidence_bond_id", EVIDENCE, ["bond_id"])
    op.create_index("ix_bond_security_master_evidence_field_name", EVIDENCE, ["field_name"])
    op.create_index("ix_bond_security_master_evidence_source", EVIDENCE, ["source"])
    op.create_index("ix_bond_security_master_evidence_observed_at", EVIDENCE, ["observed_at"])


def downgrade() -> None:
    op.drop_table(EVIDENCE)
    op.drop_table(PROFILE)
