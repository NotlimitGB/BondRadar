"""Add evidence-aware Bond legal issuer mapping v1.

Revision ID: 202608280001
Revises: 202608260001
"""

from alembic import op
import sqlalchemy as sa


revision = "202608280001"
down_revision = "202608260001"
branch_labels = None
depends_on = None

PROFILE = "bond_legal_issuer_profiles"
EVIDENCE = "bond_legal_issuer_evidence"


def upgrade() -> None:
    op.create_table(
        PROFILE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default="bond-legal-issuer-mapping-v1",
        ),
        sa.Column(
            "mapping_state",
            sa.String(16),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("mapping_source", sa.String(32)),
        sa.Column("source_issuer_id", sa.String(64)),
        sa.Column("issuer_title", sa.String(512)),
        sa.Column("issuer_inn", sa.String(32)),
        sa.Column("issuer_okpo", sa.String(32)),
        sa.Column("security_match_status", sa.String(48)),
        sa.Column("last_observed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "last_resolved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["bond_id"], ["bonds.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "bond_id",
            name="uq_bond_legal_issuer_profiles_bond_id",
        ),
        sa.CheckConstraint(
            "mapping_state in ('unknown', 'observed', 'verified', 'conflict')",
            name="bond_legal_issuer_profile_state_allowed",
        ),
        sa.CheckConstraint(
            "mapping_source is null or "
            "mapping_source = 'moex_security_reference'",
            name="bond_legal_issuer_profile_source_allowed",
        ),
        sa.CheckConstraint(
            "security_match_status is null or security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED', "
            "'EXACT_ISIN_RECOVERED')",
            name="bond_legal_issuer_profile_match_status_allowed",
        ),
        sa.CheckConstraint(
            "((mapping_state = 'verified' and source_issuer_id is not null "
            "and issuer_title is not null and security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED')) "
            "or (mapping_state = 'observed' and source_issuer_id is not null "
            "and security_match_status is not null) "
            "or (mapping_state in ('unknown', 'conflict') "
            "and source_issuer_id is null and issuer_title is null "
            "and issuer_inn is null and issuer_okpo is null "
            "and security_match_status is null))",
            name="bond_legal_issuer_profile_state_values_valid",
        ),
    )
    op.create_index(
        "ix_bond_legal_issuer_profiles_bond_id",
        PROFILE,
        ["bond_id"],
    )
    op.create_index(
        "ix_bond_legal_issuer_profiles_source_issuer_id",
        PROFILE,
        ["source_issuer_id"],
    )

    op.create_table(
        EVIDENCE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bond_id", sa.Integer(), nullable=False),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="moex_security_reference",
        ),
        sa.Column("requested_secid", sa.String(32)),
        sa.Column("expected_isin", sa.String(32)),
        sa.Column("matched_secid", sa.String(32), nullable=False),
        sa.Column("matched_isin", sa.String(32)),
        sa.Column("source_issuer_id", sa.String(64)),
        sa.Column("issuer_title", sa.String(512)),
        sa.Column("issuer_inn", sa.String(32)),
        sa.Column("issuer_okpo", sa.String(32)),
        sa.Column("security_match_status", sa.String(48), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column(
            "ingestion_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default="bond-legal-issuer-mapping-v1",
        ),
        sa.ForeignKeyConstraint(["bond_id"], ["bonds.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "evidence_fingerprint",
            name="uq_bond_legal_issuer_evidence_fingerprint",
        ),
        sa.CheckConstraint(
            "source = 'moex_security_reference'",
            name="bond_legal_issuer_evidence_source_allowed",
        ),
        sa.CheckConstraint(
            "security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED', "
            "'EXACT_ISIN_RECOVERED')",
            name="bond_legal_issuer_evidence_match_status_allowed",
        ),
        sa.CheckConstraint(
            "length(evidence_fingerprint) = 64",
            name="bond_legal_issuer_evidence_fingerprint_valid",
        ),
    )
    op.create_index(
        "ix_bond_legal_issuer_evidence_bond_source_observed",
        EVIDENCE,
        ["bond_id", "source", "observed_at"],
    )
    op.create_index(
        "ix_bond_legal_issuer_evidence_source_issuer_id",
        EVIDENCE,
        ["source_issuer_id"],
    )


def downgrade() -> None:
    op.drop_table(EVIDENCE)
    op.drop_table(PROFILE)
