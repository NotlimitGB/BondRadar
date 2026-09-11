"""Add CBR bank artifact availability evidence v1.

Revision ID: 202609110001
Revises: 202609010001
"""

from alembic import op
import sqlalchemy as sa


revision = "202609110001"
down_revision = "202609010001"
branch_labels = None
depends_on = None

ARTIFACTS = "cbr_bank_source_artifacts"
AVAILABILITY_EVIDENCE = "cbr_bank_artifact_availability_evidence"
CONTRACT_VERSION = "cbr-bank-artifact-availability-evidence-v1"


def upgrade() -> None:
    op.create_table(
        AVAILABILITY_EVIDENCE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default=CONTRACT_VERSION,
        ),
        sa.Column("evidence_source", sa.String(32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("exact_payload_bound", sa.Boolean(), nullable=False),
        sa.Column("source_reference", sa.String(2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id"], [f"{ARTIFACTS}.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "artifact_id",
            "evidence_source",
            "observed_at",
            "exact_payload_bound",
            "source_reference",
            name="uq_cbr_artifact_availability_evidence_identity",
        ),
        sa.CheckConstraint(
            f"contract_version = '{CONTRACT_VERSION}'",
            name="cbr_artifact_availability_evidence_contract_valid",
        ),
        sa.CheckConstraint(
            "evidence_source in ('CBR_DIRECT', 'WAYBACK', 'COMMON_CRAWL', "
            "'OTHER_ARCHIVE')",
            name="cbr_artifact_availability_evidence_source_valid",
        ),
        sa.CheckConstraint(
            "length(source_reference) > 0",
            name="cbr_artifact_availability_evidence_reference_valid",
        ),
    )
    op.create_index(
        "ix_cbr_artifact_availability_evidence_artifact_observed",
        AVAILABILITY_EVIDENCE,
        ["artifact_id", "observed_at"],
    )

    artifacts = sa.table(
        ARTIFACTS,
        sa.column("id", sa.Integer()),
        sa.column("first_retrieved_at", sa.DateTime(timezone=True)),
        sa.column("source_url", sa.String(2048)),
    )
    evidence = sa.table(
        AVAILABILITY_EVIDENCE,
        sa.column("artifact_id", sa.Integer()),
        sa.column("contract_version", sa.String(64)),
        sa.column("evidence_source", sa.String(32)),
        sa.column("observed_at", sa.DateTime(timezone=True)),
        sa.column("exact_payload_bound", sa.Boolean()),
        sa.column("source_reference", sa.String(2048)),
    )
    op.get_bind().execute(
        evidence.insert().from_select(
            (
                "artifact_id",
                "contract_version",
                "evidence_source",
                "observed_at",
                "exact_payload_bound",
                "source_reference",
            ),
            sa.select(
                artifacts.c.id,
                sa.literal(CONTRACT_VERSION),
                sa.literal("CBR_DIRECT"),
                artifacts.c.first_retrieved_at,
                sa.literal(True),
                artifacts.c.source_url,
            ),
        )
    )


def downgrade() -> None:
    op.drop_table(AVAILABILITY_EVIDENCE)
