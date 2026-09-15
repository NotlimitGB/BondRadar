"""Add CBR bank normalized financial observations v1.

Revision ID: 202609140001
Revises: 202609110001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "202609140001"
down_revision = "202609110001"
branch_labels = None
depends_on = None

TABLE = "cbr_bank_normalized_observations"
RAW_TABLE = "cbr_bank_raw_observations"
CONTRACT_VERSION = "cbr-bank-normalized-financial-observation-v1"
JSON_DOCUMENT = postgresql.JSONB(astext_type=sa.Text()).with_variant(
    sa.JSON(), "sqlite"
)


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
        "raw_observation_id": sa.Integer,
        "form": sa.String,
        "report_date": sa.Date,
        "subject_regn": sa.String,
        "source_date": sa.Date,
        "source_code": sa.String,
        "source_subcode": sa.String,
        "source_dimensions": sa.JSON,
        "source_disclosure_state": sa.String,
        "value_state": sa.String,
        "normalized_value": sa.Numeric,
        "normalized_unit": sa.String,
        "normalized_currency": sa.String,
        "transformation_kind": sa.String,
        "applied_multiplier": sa.Integer,
        "item_fingerprint": sa.String,
        "normalization_fingerprint": sa.String,
        "created_at": sa.DateTime,
    }
    nullable = {
        "source_date",
        "source_subcode",
        "normalized_value",
        "normalized_currency",
        "applied_multiplier",
    }
    if set(columns) != set(expected_types):
        raise RuntimeError("Partial or incompatible Task261 SQLite table exists")
    for name, expected_type in expected_types.items():
        if not isinstance(columns[name]["type"], expected_type):
            raise RuntimeError("Partial or incompatible Task261 SQLite columns exist")
        if bool(columns[name]["nullable"]) != (name in nullable):
            raise RuntimeError("Partial or incompatible Task261 SQLite columns exist")
    if inspector.get_pk_constraint(TABLE).get("constrained_columns") != ["id"]:
        raise RuntimeError("Incompatible Task261 SQLite primary key exists")

    foreign_keys = inspector.get_foreign_keys(TABLE)
    if len(foreign_keys) != 1 or (
        foreign_keys[0].get("constrained_columns") != ["raw_observation_id"]
        or foreign_keys[0].get("referred_table") != RAW_TABLE
        or foreign_keys[0].get("referred_columns") != ["id"]
        or foreign_keys[0].get("options", {}).get("ondelete") != "RESTRICT"
    ):
        raise RuntimeError("Incompatible Task261 SQLite foreign key exists")

    uniques = {
        tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(TABLE)
    }
    if not {
        ("raw_observation_id",),
        ("normalization_fingerprint",),
    }.issubset(uniques):
        raise RuntimeError("Task261 SQLite uniqueness is missing")

    indexes = {
        item.get("name"): tuple(item.get("column_names") or ())
        for item in inspector.get_indexes(TABLE)
    }
    expected_indexes = {
        "ix_cbr_bank_normalized_observations_subject_report": (
            "subject_regn",
            "report_date",
        ),
        "ix_cbr_bank_normalized_observations_form_code_report": (
            "form",
            "source_code",
            "report_date",
        ),
        "ix_cbr_bank_normalized_observations_item_fingerprint": (
            "item_fingerprint",
        ),
    }
    if any(indexes.get(name) != value for name, value in expected_indexes.items()):
        raise RuntimeError("Task261 SQLite required indexes are missing")

    checks = {
        str(item.get("name") or "")
        for item in inspector.get_check_constraints(TABLE)
    }
    required_checks = (
        "contract_valid",
        "form_valid",
        "regn_canonical",
        "source_code_present",
        "disclosure_valid",
        "value_state_valid",
        "transformation_valid",
        "form_semantics_valid",
        "hashes_valid",
    )
    if any(not any(name.endswith(suffix) for name in checks) for suffix in required_checks):
        raise RuntimeError("Task261 SQLite required checks are missing")


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
        sa.Column("raw_observation_id", sa.Integer(), nullable=False),
        sa.Column("form", sa.String(8), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("subject_regn", sa.String(16), nullable=False),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("source_code", sa.String(128), nullable=False),
        sa.Column("source_subcode", sa.String(128), nullable=True),
        sa.Column("source_dimensions", JSON_DOCUMENT, nullable=False),
        sa.Column("source_disclosure_state", sa.String(64), nullable=False),
        sa.Column("value_state", sa.String(32), nullable=False),
        sa.Column("normalized_value", sa.Numeric(asdecimal=True), nullable=True),
        sa.Column("normalized_unit", sa.String(32), nullable=False),
        sa.Column("normalized_currency", sa.String(16), nullable=True),
        sa.Column("transformation_kind", sa.String(64), nullable=False),
        sa.Column("applied_multiplier", sa.BigInteger(), nullable=True),
        sa.Column("item_fingerprint", sa.String(64), nullable=False),
        sa.Column("normalization_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["raw_observation_id"], [f"{RAW_TABLE}.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "raw_observation_id",
            name="uq_cbr_bank_normalized_observations_raw_observation",
        ),
        sa.UniqueConstraint(
            "normalization_fingerprint",
            name="uq_cbr_bank_normalized_observations_fingerprint",
        ),
        sa.CheckConstraint(
            f"contract_version = '{CONTRACT_VERSION}'",
            name="cbr_bank_normalized_observations_contract_valid",
        ),
        sa.CheckConstraint(
            "form in ('0409101', '0409102', '0409123', '0409135')",
            name="cbr_bank_normalized_observations_form_valid",
        ),
        sa.CheckConstraint(
            "cast(cast(subject_regn as bigint) as varchar) = subject_regn "
            "and cast(subject_regn as bigint) > 0",
            name="cbr_bank_normalized_observations_regn_canonical",
        ),
        sa.CheckConstraint(
            "source_code <> ''",
            name="cbr_bank_normalized_observations_source_code_present",
        ),
        sa.CheckConstraint(
            "source_disclosure_state in ('PUBLIC_VALUE', 'PUBLIC_VALUE_BLANK', "
            "'SUPPRESSED_OR_REDUCED', 'NOT_PRESENT_IN_CURRENT_PUBLIC_ARTIFACT', "
            "'UNKNOWN')",
            name="cbr_bank_normalized_observations_disclosure_valid",
        ),
        sa.CheckConstraint(
            "value_state in ('VALUE', 'SOURCE_VALUE_UNAVAILABLE') and "
            "((value_state = 'VALUE' and normalized_value is not null "
            "and source_disclosure_state = 'PUBLIC_VALUE') or "
            "(value_state = 'SOURCE_VALUE_UNAVAILABLE' "
            "and normalized_value is null "
            "and source_disclosure_state <> 'PUBLIC_VALUE'))",
            name="cbr_bank_normalized_observations_value_state_valid",
        ),
        sa.CheckConstraint(
            "transformation_kind in ('SCALE_BY_SOURCE_MULTIPLIER', 'IDENTITY')",
            name="cbr_bank_normalized_observations_transformation_valid",
        ),
        sa.CheckConstraint(
            "((form in ('0409101', '0409102', '0409123') "
            "and normalized_unit = 'RUB' and normalized_currency = 'RUB' "
            "and transformation_kind = 'SCALE_BY_SOURCE_MULTIPLIER' "
            "and applied_multiplier = 1000) or "
            "(form = '0409135' and normalized_unit = 'PERCENT' "
            "and normalized_currency is null and transformation_kind = 'IDENTITY' "
            "and applied_multiplier is null))",
            name="cbr_bank_normalized_observations_form_semantics_valid",
        ),
        sa.CheckConstraint(
            f"{_lower_hex_sha256_sql('item_fingerprint')} and "
            f"{_lower_hex_sha256_sql('normalization_fingerprint')}",
            name="cbr_bank_normalized_observations_hashes_valid",
        ),
    )
    op.create_index(
        "ix_cbr_bank_normalized_observations_subject_report",
        TABLE,
        ["subject_regn", "report_date"],
    )
    op.create_index(
        "ix_cbr_bank_normalized_observations_form_code_report",
        TABLE,
        ["form", "source_code", "report_date"],
    )
    op.create_index(
        "ix_cbr_bank_normalized_observations_item_fingerprint",
        TABLE,
        ["item_fingerprint"],
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
