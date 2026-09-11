"""Add CBR bank artifact availability evidence v1.

Revision ID: 202609110001
Revises: 202609010001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202609110001"
down_revision = "202609010001"
branch_labels = None
depends_on = None

ARTIFACTS = "cbr_bank_source_artifacts"
AVAILABILITY_EVIDENCE = "cbr_bank_artifact_availability_evidence"
CONTRACT_VERSION = "cbr-bank-artifact-availability-evidence-v1"


def _validate_precreated_sqlite_table(inspector: sa.Inspector) -> None:
    columns = {item["name"]: item for item in inspector.get_columns(AVAILABILITY_EVIDENCE)}
    expected_types = {
        "id": sa.Integer,
        "artifact_id": sa.Integer,
        "contract_version": sa.String,
        "evidence_source": sa.String,
        "observed_at": sa.DateTime,
        "exact_payload_bound": sa.Boolean,
        "source_reference": sa.String,
        "created_at": sa.DateTime,
    }
    if set(columns) != set(expected_types):
        raise RuntimeError("Partial or incompatible Task260B SQLite table exists")
    if any(
        columns[name]["nullable"] or not isinstance(columns[name]["type"], expected_type)
        for name, expected_type in expected_types.items()
    ):
        raise RuntimeError("Partial or incompatible Task260B SQLite columns exist")
    if inspector.get_pk_constraint(AVAILABILITY_EVIDENCE).get("constrained_columns") != [
        "id"
    ]:
        raise RuntimeError("Incompatible Task260B SQLite primary key exists")

    foreign_keys = inspector.get_foreign_keys(AVAILABILITY_EVIDENCE)
    if len(foreign_keys) != 1 or (
        foreign_keys[0].get("constrained_columns") != ["artifact_id"]
        or foreign_keys[0].get("referred_table") != ARTIFACTS
        or foreign_keys[0].get("referred_columns") != ["id"]
        or foreign_keys[0].get("options", {}).get("ondelete") != "RESTRICT"
    ):
        raise RuntimeError("Incompatible Task260B SQLite foreign key exists")

    unique_identities = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(AVAILABILITY_EVIDENCE)
    }
    if (
        "artifact_id",
        "evidence_source",
        "observed_at",
        "exact_payload_bound",
        "source_reference",
    ) not in unique_identities:
        raise RuntimeError("Task260B SQLite semantic uniqueness is missing")

    indexes = {
        item.get("name"): (tuple(item.get("column_names") or ()), item.get("unique"))
        for item in inspector.get_indexes(AVAILABILITY_EVIDENCE)
    }
    if indexes.get("ix_cbr_artifact_availability_evidence_artifact_observed") != (
        ("artifact_id", "observed_at"),
        0,
    ):
        raise RuntimeError("Task260B SQLite required index is missing")

    check_names = {
        str(item.get("name") or "")
        for item in inspector.get_check_constraints(AVAILABILITY_EVIDENCE)
    }
    for suffix in (
        "cbr_artifact_availability_evidence_contract_valid",
        "cbr_artifact_availability_evidence_source_valid",
        "cbr_artifact_availability_evidence_reference_valid",
    ):
        if not any(name.endswith(suffix) for name in check_names):
            raise RuntimeError("Task260B SQLite required check constraint is missing")


def _create_availability_table() -> None:
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


def _bootstrap_existing_artifacts(*, sqlite_precreated: bool) -> None:
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
    bootstrap = sa.select(
        artifacts.c.id,
        sa.literal(CONTRACT_VERSION),
        sa.literal("CBR_DIRECT"),
        artifacts.c.first_retrieved_at,
        sa.literal(True),
        artifacts.c.source_url,
    )
    if sqlite_precreated:
        bootstrap = bootstrap.where(
            ~sa.exists().where(
                evidence.c.artifact_id == artifacts.c.id,
                evidence.c.evidence_source == "CBR_DIRECT",
                evidence.c.observed_at == artifacts.c.first_retrieved_at,
                evidence.c.exact_payload_bound.is_(True),
                evidence.c.source_reference == artifacts.c.source_url,
            )
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
            bootstrap,
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    sqlite_precreated = False
    if bind.dialect.name == "sqlite":
        inspector = sa.inspect(bind)
        sqlite_precreated = AVAILABILITY_EVIDENCE in inspector.get_table_names()
        if sqlite_precreated:
            _validate_precreated_sqlite_table(inspector)
    if not sqlite_precreated:
        _create_availability_table()
    _bootstrap_existing_artifacts(sqlite_precreated=sqlite_precreated)


def downgrade() -> None:
    op.drop_table(AVAILABILITY_EVIDENCE)
