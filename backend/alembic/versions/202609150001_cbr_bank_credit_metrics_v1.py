"""Add CBR bank credit metrics v1.

Revision ID: 202609150001
Revises: 202609140001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202609150001"
down_revision = "202609140001"
branch_labels = None
depends_on = None

TABLE = "cbr_bank_credit_metrics"
NORMALIZED_TABLE = "cbr_bank_normalized_observations"
CONTRACT_VERSION = "cbr-bank-credit-metric-v1"


def _lower_hex_sha256_sql(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column}) = 64 and {column} = lower({column}) "
        f"and length({remainder}) = 0"
    )


def _validate_precreated_sqlite_table(inspector: sa.Inspector) -> None:
    columns = {item["name"]: item for item in inspector.get_columns(TABLE)}
    expected_types = {
        "id": sa.Integer,
        "contract_version": sa.String,
        "normalized_observation_id": sa.Integer,
        "subject_regn": sa.String,
        "report_date": sa.Date,
        "metric_key": sa.String,
        "metric_family": sa.String,
        "source_form": sa.String,
        "source_code": sa.String,
        "metric_value": sa.Numeric,
        "metric_unit": sa.String,
        "metric_fingerprint": sa.String,
        "created_at": sa.DateTime,
    }
    if set(columns) != set(expected_types):
        raise RuntimeError("Partial or incompatible Task262 SQLite table exists")
    for name, expected_type in expected_types.items():
        if not isinstance(columns[name]["type"], expected_type):
            raise RuntimeError("Partial or incompatible Task262 SQLite columns exist")
        if columns[name]["nullable"]:
            raise RuntimeError("Task262 SQLite columns must be non-nullable")
    if inspector.get_pk_constraint(TABLE).get("constrained_columns") != ["id"]:
        raise RuntimeError("Incompatible Task262 SQLite primary key exists")

    foreign_keys = inspector.get_foreign_keys(TABLE)
    if len(foreign_keys) != 1 or (
        foreign_keys[0].get("constrained_columns")
        != ["normalized_observation_id"]
        or foreign_keys[0].get("referred_table") != NORMALIZED_TABLE
        or foreign_keys[0].get("referred_columns") != ["id"]
        or foreign_keys[0].get("options", {}).get("ondelete") != "RESTRICT"
    ):
        raise RuntimeError("Incompatible Task262 SQLite foreign key exists")

    uniques = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(TABLE)
    }
    if not {
        ("normalized_observation_id",),
        ("metric_fingerprint",),
    }.issubset(uniques):
        raise RuntimeError("Task262 SQLite uniqueness is missing")

    indexes = {
        item.get("name"): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(TABLE)
    }
    expected_indexes = {
        "ix_cbr_bank_credit_metrics_subject_report": (
            "subject_regn",
            "report_date",
        ),
        "ix_cbr_bank_credit_metrics_key_report": (
            "metric_key",
            "report_date",
        ),
    }
    if any(indexes.get(name) != value for name, value in expected_indexes.items()):
        raise RuntimeError("Task262 SQLite required indexes are missing")

    checks = {
        str(item.get("name") or "")
        for item in inspector.get_check_constraints(TABLE)
    }
    required_checks = (
        "contract_valid",
        "regn_canonical",
        "identity_present",
        "family_valid",
        "mapping_valid",
        "fingerprint_valid",
    )
    if any(not any(name.endswith(suffix) for name in checks) for suffix in required_checks):
        raise RuntimeError("Task262 SQLite required checks are missing")


def _mapping_check_sql() -> str:
    return (
        "((source_form = '0409123' "
        "and metric_family = 'REGULATORY_CAPITAL' and metric_unit = 'RUB' "
        "and ((source_code = '000' and metric_key = 'CBR_123_000') "
        "or (source_code = '102' and metric_key = 'CBR_123_102') "
        "or (source_code = '105' and metric_key = 'CBR_123_105') "
        "or (source_code = '203' and metric_key = 'CBR_123_203'))) "
        "or (source_form = '0409135' "
        "and metric_family = 'REGULATORY_RATIO' and metric_unit = 'PERCENT' "
        "and ((source_code = 'N1.0' and metric_key = 'CBR_135_N1_0') "
        "or (source_code = 'N1.1' and metric_key = 'CBR_135_N1_1') "
        "or (source_code = 'N1.2' and metric_key = 'CBR_135_N1_2') "
        "or (source_code = 'N1.3' and metric_key = 'CBR_135_N1_3') "
        "or (source_code = 'N2' and metric_key = 'CBR_135_N2') "
        "or (source_code = 'N3' and metric_key = 'CBR_135_N3') "
        "or (source_code = 'N4' and metric_key = 'CBR_135_N4') "
        "or (source_code = 'N15' and metric_key = 'CBR_135_N15') "
        "or (source_code = 'N15.1' and metric_key = 'CBR_135_N15_1') "
        "or (source_code = 'N16' and metric_key = 'CBR_135_N16') "
        "or (source_code = 'N16.1' and metric_key = 'CBR_135_N16_1') "
        "or (source_code = 'N16.2' and metric_key = 'CBR_135_N16_2') "
        "or (source_code = 'N27' and metric_key = 'CBR_135_N27'))))"
    )


def _create_table() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "contract_version",
            sa.String(64),
            nullable=False,
            server_default=CONTRACT_VERSION,
        ),
        sa.Column("normalized_observation_id", sa.Integer(), nullable=False),
        sa.Column("subject_regn", sa.String(16), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("metric_key", sa.String(64), nullable=False),
        sa.Column("metric_family", sa.String(32), nullable=False),
        sa.Column("source_form", sa.String(8), nullable=False),
        sa.Column("source_code", sa.String(128), nullable=False),
        sa.Column("metric_value", sa.Numeric(asdecimal=True), nullable=False),
        sa.Column("metric_unit", sa.String(32), nullable=False),
        sa.Column("metric_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["normalized_observation_id"],
            [f"{NORMALIZED_TABLE}.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "normalized_observation_id",
            name="uq_cbr_bank_credit_metrics_normalized_observation",
        ),
        sa.UniqueConstraint(
            "metric_fingerprint",
            name="uq_cbr_bank_credit_metrics_fingerprint",
        ),
        sa.CheckConstraint(
            f"contract_version = '{CONTRACT_VERSION}'",
            name="cbr_bank_credit_metrics_contract_valid",
        ),
        sa.CheckConstraint(
            "cast(cast(subject_regn as bigint) as varchar) = subject_regn "
            "and cast(subject_regn as bigint) > 0",
            name="cbr_bank_credit_metrics_regn_canonical",
        ),
        sa.CheckConstraint(
            "metric_key <> '' and source_code <> ''",
            name="cbr_bank_credit_metrics_identity_present",
        ),
        sa.CheckConstraint(
            "metric_family in ('REGULATORY_CAPITAL', 'REGULATORY_RATIO')",
            name="cbr_bank_credit_metrics_family_valid",
        ),
        sa.CheckConstraint(
            _mapping_check_sql(),
            name="cbr_bank_credit_metrics_mapping_valid",
        ),
        sa.CheckConstraint(
            _lower_hex_sha256_sql("metric_fingerprint"),
            name="cbr_bank_credit_metrics_fingerprint_valid",
        ),
    )
    op.create_index(
        "ix_cbr_bank_credit_metrics_subject_report",
        TABLE,
        ["subject_regn", "report_date"],
    )
    op.create_index(
        "ix_cbr_bank_credit_metrics_key_report",
        TABLE,
        ["metric_key", "report_date"],
    )


def upgrade() -> None:
    bind = op.get_bind()
    sqlite_precreated = False
    if bind.dialect.name == "sqlite":
        inspector = sa.inspect(bind)
        sqlite_precreated = TABLE in inspector.get_table_names()
        if sqlite_precreated:
            _validate_precreated_sqlite_table(inspector)
    if not sqlite_precreated:
        _create_table()


def downgrade() -> None:
    op.drop_table(TABLE)
