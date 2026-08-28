"""Add evidence-aware canonical Legal Issuer Master v1.

Revision ID: 202608280002
Revises: 202608280001
"""

from alembic import op
import sqlalchemy as sa


revision = "202608280002"
down_revision = "202608280001"
branch_labels = None
depends_on = None

ISSUERS = "legal_issuers"
EVIDENCE = "legal_issuer_evidence"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        existing = set(sa.inspect(bind).get_table_names())
        task244_tables = {ISSUERS, EVIDENCE}
        if task244_tables.issubset(existing):
            return
        if task244_tables.intersection(existing):
            raise RuntimeError("Partial Task244 schema already exists")
    op.create_table(
        ISSUERS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default="legal-issuer-master-v1",
        ),
        sa.Column(
            "identity_source",
            sa.String(32),
            nullable=False,
            server_default="moex_security_reference",
        ),
        sa.Column("source_issuer_id", sa.String(64), nullable=False),
        sa.Column(
            "resolution_state",
            sa.String(16),
            nullable=False,
            server_default="observed",
        ),
        sa.Column("issuer_title", sa.String(512)),
        sa.Column("issuer_inn", sa.String(32)),
        sa.Column("issuer_okpo", sa.String(32)),
        sa.Column("first_observed_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint(
            "identity_source",
            "source_issuer_id",
            name="uq_legal_issuers_source_identity",
        ),
        sa.CheckConstraint(
            "contract_version = 'legal-issuer-master-v1'",
            name="legal_issuers_contract_version_valid",
        ),
        sa.CheckConstraint(
            "identity_source = 'moex_security_reference'",
            name="legal_issuers_identity_source_allowed",
        ),
        sa.CheckConstraint(
            "length(source_issuer_id) > 0",
            name="legal_issuers_source_issuer_id_present",
        ),
        sa.CheckConstraint(
            "resolution_state in ('observed', 'verified', 'conflict')",
            name="legal_issuers_resolution_state_allowed",
        ),
        sa.CheckConstraint(
            "resolution_state != 'verified' or issuer_title is not null",
            name="legal_issuers_verified_title_present",
        ),
    )
    op.create_index(
        "ix_legal_issuers_source_issuer_id",
        ISSUERS,
        ["source_issuer_id"],
    )

    op.create_table(
        EVIDENCE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("legal_issuer_id", sa.Integer(), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default="legal-issuer-master-v1",
        ),
        sa.Column(
            "source",
            sa.String(32),
            nullable=False,
            server_default="moex_security_reference",
        ),
        sa.Column("source_issuer_id", sa.String(64), nullable=False),
        sa.Column(
            "upstream_contract_version",
            sa.String(64),
            nullable=False,
            server_default="bond-legal-issuer-mapping-v1",
        ),
        sa.Column("upstream_evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_bond_id", sa.Integer(), nullable=False),
        sa.Column("source_security_secid", sa.String(32), nullable=False),
        sa.Column("source_security_isin", sa.String(32)),
        sa.Column("security_match_status", sa.String(48), nullable=False),
        sa.Column("issuer_title", sa.String(512)),
        sa.Column("issuer_inn", sa.String(32)),
        sa.Column("issuer_okpo", sa.String(32)),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True)),
        sa.Column(
            "upstream_ingestion_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "ingestion_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["legal_issuer_id"],
            ["legal_issuers.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "evidence_fingerprint",
            name="uq_legal_issuer_evidence_fingerprint",
        ),
        sa.UniqueConstraint(
            "upstream_contract_version",
            "upstream_evidence_fingerprint",
            name="uq_legal_issuer_evidence_upstream_lineage",
        ),
        sa.CheckConstraint(
            "contract_version = 'legal-issuer-master-v1'",
            name="legal_issuer_evidence_contract_version_valid",
        ),
        sa.CheckConstraint(
            "source = 'moex_security_reference'",
            name="legal_issuer_evidence_source_allowed",
        ),
        sa.CheckConstraint(
            "upstream_contract_version = 'bond-legal-issuer-mapping-v1'",
            name="legal_issuer_evidence_upstream_contract_valid",
        ),
        sa.CheckConstraint(
            "security_match_status in "
            "('EXACT_SECID', 'EXACT_SECID_ISIN_CORROBORATED', "
            "'EXACT_ISIN_RECOVERED')",
            name="legal_issuer_evidence_match_status_allowed",
        ),
        sa.CheckConstraint(
            "length(source_issuer_id) > 0 and length(source_security_secid) > 0",
            name="legal_issuer_evidence_source_identity_present",
        ),
        sa.CheckConstraint(
            "length(upstream_evidence_fingerprint) = 64",
            name="legal_issuer_evidence_upstream_fingerprint_valid",
        ),
        sa.CheckConstraint(
            "length(evidence_fingerprint) = 64",
            name="legal_issuer_evidence_fingerprint_valid",
        ),
    )
    op.create_index(
        "ix_legal_issuer_evidence_issuer_security_observed",
        EVIDENCE,
        ["legal_issuer_id", "source_security_secid", "observed_at"],
    )
    op.create_index(
        "ix_legal_issuer_evidence_upstream_fingerprint",
        EVIDENCE,
        ["upstream_evidence_fingerprint"],
    )


def downgrade() -> None:
    op.drop_table(EVIDENCE)
    op.drop_table(ISSUERS)
