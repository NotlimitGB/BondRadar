"""Add credit rating and default evidence foundation v1.

Revision ID: 202609150003
Revises: 202609150002
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202609150003"
down_revision = "202609150002"
branch_labels = None
depends_on = None

ARTIFACTS = "credit_risk_source_artifacts"
RATINGS = "credit_rating_events"
DEFAULTS = "credit_default_events"
TASK263_TABLES = (ARTIFACTS, RATINGS, DEFAULTS)


def _lower_hex_sha256_sql(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return (
        f"length({column}) = 64 and {column} = lower({column}) "
        f"and length({remainder}) = 0"
    )


PUBLICATION_CHECK = (
    "(publication_precision = 'DATE' and publication_date is not null "
    "and publication_at is null) or "
    "(publication_precision = 'TIMESTAMP' and publication_date is null "
    "and publication_at is not null) or "
    "(publication_precision = 'UNKNOWN' and publication_date is null "
    "and publication_at is null)"
)


def _create_artifacts() -> None:
    op.create_table(
        ARTIFACTS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contract_version", sa.String(64), nullable=False, server_default="credit-risk-source-artifact-v1"),
        sa.Column("source_provider", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_provider", "source_url", "content_sha256", name="uq_credit_risk_source_artifacts_identity"),
        sa.CheckConstraint("contract_version = 'credit-risk-source-artifact-v1'", name="credit_risk_source_artifacts_contract_valid"),
        sa.CheckConstraint("source_provider in ('ACRA', 'EXPERT_RA', 'NRA', 'NKR', 'MOEX')", name="credit_risk_source_artifacts_provider_valid"),
        sa.CheckConstraint("source_kind in ('RATING_ISSUER_PAGE', 'RATING_ISSUE_PAGE', 'RATING_RELEASE', 'DEFAULT_INFORMATION', 'OTHER')", name="credit_risk_source_artifacts_kind_valid"),
        sa.CheckConstraint("length(content_bytes) > 0 and length(source_url) > 0 and length(content_type) > 0", name="credit_risk_source_artifacts_content_valid"),
        sa.CheckConstraint(_lower_hex_sha256_sql("content_sha256"), name="credit_risk_source_artifacts_hash_valid"),
    )
    op.create_index("ix_credit_risk_source_artifacts_provider_retrieved", ARTIFACTS, ["source_provider", "retrieved_at"])


def _create_ratings() -> None:
    op.create_table(
        RATINGS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contract_version", sa.String(64), nullable=False, server_default="credit-rating-event-v1"),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("rating_agency", sa.String(16), nullable=False),
        sa.Column("target_kind", sa.String(16), nullable=False),
        sa.Column("resolution_state", sa.String(16), nullable=False),
        sa.Column("source_object_id", sa.String(256), nullable=False),
        sa.Column("source_issuer_inn", sa.String(10), nullable=True),
        sa.Column("source_bond_isin", sa.String(12), nullable=True),
        sa.Column("source_name_raw", sa.String(512), nullable=True),
        sa.Column("legal_issuer_id", sa.Integer(), nullable=True),
        sa.Column("bond_id", sa.Integer(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("publication_precision", sa.String(16), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("publication_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rating_scale_raw", sa.String(128), nullable=False),
        sa.Column("rating_value_raw", sa.String(128), nullable=True),
        sa.Column("rating_outlook_raw", sa.String(256), nullable=True),
        sa.Column("rating_watch_raw", sa.String(256), nullable=True),
        sa.Column("rating_action_raw", sa.String(256), nullable=True),
        sa.Column("event_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["artifact_id"], [f"{ARTIFACTS}.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["legal_issuer_id"], ["legal_issuers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bond_id"], ["bonds.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("event_fingerprint", name="uq_credit_rating_events_fingerprint"),
        sa.CheckConstraint("contract_version = 'credit-rating-event-v1'", name="credit_rating_events_contract_valid"),
        sa.CheckConstraint("rating_agency in ('ACRA', 'EXPERT_RA', 'NRA', 'NKR')", name="credit_rating_events_agency_valid"),
        sa.CheckConstraint("target_kind in ('LEGAL_ISSUER', 'BOND') and resolution_state in ('RESOLVED', 'UNRESOLVED', 'AMBIGUOUS')", name="credit_rating_events_target_state_valid"),
        sa.CheckConstraint("((target_kind = 'LEGAL_ISSUER' and source_issuer_inn is not null and source_bond_isin is null and ((resolution_state = 'RESOLVED' and legal_issuer_id is not null and bond_id is null) or (resolution_state != 'RESOLVED' and legal_issuer_id is null and bond_id is null))) or (target_kind = 'BOND' and source_bond_isin is not null and source_issuer_inn is null and ((resolution_state = 'RESOLVED' and bond_id is not null and legal_issuer_id is null) or (resolution_state != 'RESOLVED' and bond_id is null and legal_issuer_id is null))))", name="credit_rating_events_target_link_valid"),
        sa.CheckConstraint("length(rating_scale_raw) > 0 and (rating_value_raw is not null or rating_outlook_raw is not null or rating_watch_raw is not null or rating_action_raw is not null)", name="credit_rating_events_raw_rating_valid"),
        sa.CheckConstraint(PUBLICATION_CHECK, name="credit_rating_events_publication_valid"),
        sa.CheckConstraint(_lower_hex_sha256_sql("event_fingerprint"), name="credit_rating_events_fingerprint_valid"),
    )
    op.create_index("ix_credit_rating_events_agency_date", RATINGS, ["rating_agency", "event_date"])
    op.create_index("ix_credit_rating_events_issuer_date", RATINGS, ["legal_issuer_id", "event_date"])
    op.create_index("ix_credit_rating_events_bond_date", RATINGS, ["bond_id", "event_date"])


def _create_defaults() -> None:
    op.create_table(
        DEFAULTS,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("contract_version", sa.String(64), nullable=False, server_default="credit-default-event-v1"),
        sa.Column("artifact_id", sa.Integer(), nullable=False),
        sa.Column("source_provider", sa.String(16), nullable=False, server_default="MOEX"),
        sa.Column("resolution_state", sa.String(16), nullable=False),
        sa.Column("source_object_id", sa.String(256), nullable=False),
        sa.Column("source_bond_isin", sa.String(12), nullable=False),
        sa.Column("source_name_raw", sa.String(512), nullable=True),
        sa.Column("bond_id", sa.Integer(), nullable=True),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("publication_precision", sa.String(16), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("publication_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("default_class", sa.String(32), nullable=False),
        sa.Column("obligation_type", sa.String(16), nullable=False),
        sa.Column("default_status_raw", sa.String(256), nullable=True),
        sa.Column("default_reason_raw", sa.Text(), nullable=True),
        sa.Column("amount_raw", sa.String(128), nullable=True),
        sa.Column("event_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["artifact_id"], [f"{ARTIFACTS}.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bond_id"], ["bonds.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("event_fingerprint", name="uq_credit_default_events_fingerprint"),
        sa.CheckConstraint("contract_version = 'credit-default-event-v1' and source_provider = 'MOEX'", name="credit_default_events_contract_provider_valid"),
        sa.CheckConstraint("default_class in ('TECHNICAL_DEFAULT', 'DEFAULT', 'OTHER', 'UNKNOWN')", name="credit_default_events_class_valid"),
        sa.CheckConstraint("obligation_type in ('COUPON', 'PRINCIPAL', 'OFFER', 'OTHER', 'UNKNOWN')", name="credit_default_events_obligation_valid"),
        sa.CheckConstraint("resolution_state in ('RESOLVED', 'UNRESOLVED', 'AMBIGUOUS') and source_bond_isin is not null and ((resolution_state = 'RESOLVED' and bond_id is not null) or (resolution_state != 'RESOLVED' and bond_id is null))", name="credit_default_events_target_link_valid"),
        sa.CheckConstraint(PUBLICATION_CHECK, name="credit_default_events_publication_valid"),
        sa.CheckConstraint(_lower_hex_sha256_sql("event_fingerprint"), name="credit_default_events_fingerprint_valid"),
    )
    op.create_index("ix_credit_default_events_bond_date", DEFAULTS, ["bond_id", "event_date"])
    op.create_index("ix_credit_default_events_class_date", DEFAULTS, ["default_class", "event_date"])


def _validate_sqlite(inspector: sa.Inspector) -> None:
    expected_columns = {
        ARTIFACTS: {"id", "contract_version", "source_provider", "source_kind", "source_url", "content_type", "content_bytes", "content_sha256", "retrieved_at", "created_at"},
        RATINGS: {"id", "contract_version", "artifact_id", "rating_agency", "target_kind", "resolution_state", "source_object_id", "source_issuer_inn", "source_bond_isin", "source_name_raw", "legal_issuer_id", "bond_id", "event_date", "publication_precision", "publication_date", "publication_at", "rating_scale_raw", "rating_value_raw", "rating_outlook_raw", "rating_watch_raw", "rating_action_raw", "event_fingerprint", "created_at"},
        DEFAULTS: {"id", "contract_version", "artifact_id", "source_provider", "resolution_state", "source_object_id", "source_bond_isin", "source_name_raw", "bond_id", "event_date", "publication_precision", "publication_date", "publication_at", "default_class", "obligation_type", "default_status_raw", "default_reason_raw", "amount_raw", "event_fingerprint", "created_at"},
    }
    expected_indexes = {
        ARTIFACTS: {"ix_credit_risk_source_artifacts_provider_retrieved"},
        RATINGS: {"ix_credit_rating_events_agency_date", "ix_credit_rating_events_issuer_date", "ix_credit_rating_events_bond_date"},
        DEFAULTS: {"ix_credit_default_events_bond_date", "ix_credit_default_events_class_date"},
    }
    required_checks = {
        ARTIFACTS: ("contract_valid", "provider_valid", "kind_valid", "content_valid", "hash_valid"),
        RATINGS: ("contract_valid", "agency_valid", "target_state_valid", "target_link_valid", "raw_rating_valid", "publication_valid", "fingerprint_valid"),
        DEFAULTS: ("contract_provider_valid", "class_valid", "obligation_valid", "target_link_valid", "publication_valid", "fingerprint_valid"),
    }
    expected_types = {
        ARTIFACTS: {
            "id": sa.Integer, "contract_version": sa.String,
            "source_provider": sa.String, "source_kind": sa.String,
            "source_url": sa.String, "content_type": sa.String,
            "content_bytes": sa.LargeBinary, "content_sha256": sa.String,
            "retrieved_at": sa.DateTime, "created_at": sa.DateTime,
        },
        RATINGS: {
            "id": sa.Integer, "contract_version": sa.String,
            "artifact_id": sa.Integer, "rating_agency": sa.String,
            "target_kind": sa.String, "resolution_state": sa.String,
            "source_object_id": sa.String, "source_issuer_inn": sa.String,
            "source_bond_isin": sa.String, "source_name_raw": sa.String,
            "legal_issuer_id": sa.Integer, "bond_id": sa.Integer,
            "event_date": sa.Date, "publication_precision": sa.String,
            "publication_date": sa.Date, "publication_at": sa.DateTime,
            "rating_scale_raw": sa.String, "rating_value_raw": sa.String,
            "rating_outlook_raw": sa.String, "rating_watch_raw": sa.String,
            "rating_action_raw": sa.String, "event_fingerprint": sa.String,
            "created_at": sa.DateTime,
        },
        DEFAULTS: {
            "id": sa.Integer, "contract_version": sa.String,
            "artifact_id": sa.Integer, "source_provider": sa.String,
            "resolution_state": sa.String, "source_object_id": sa.String,
            "source_bond_isin": sa.String, "source_name_raw": sa.String,
            "bond_id": sa.Integer, "event_date": sa.Date,
            "publication_precision": sa.String, "publication_date": sa.Date,
            "publication_at": sa.DateTime, "default_class": sa.String,
            "obligation_type": sa.String, "default_status_raw": sa.String,
            "default_reason_raw": sa.Text, "amount_raw": sa.String,
            "event_fingerprint": sa.String, "created_at": sa.DateTime,
        },
    }
    nullable_columns = {
        ARTIFACTS: set(),
        RATINGS: {
            "source_issuer_inn", "source_bond_isin", "source_name_raw",
            "legal_issuer_id", "bond_id", "publication_date", "publication_at",
            "rating_value_raw", "rating_outlook_raw", "rating_watch_raw",
            "rating_action_raw",
        },
        DEFAULTS: {
            "source_name_raw", "bond_id", "publication_date", "publication_at",
            "default_status_raw", "default_reason_raw", "amount_raw",
        },
    }
    for table in TASK263_TABLES:
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        if set(columns) != expected_columns[table]:
            raise RuntimeError("Partial or incompatible Task263 SQLite columns exist")
        for name, expected_type in expected_types[table].items():
            if not isinstance(columns[name]["type"], expected_type):
                raise RuntimeError("Incompatible Task263 SQLite column types exist")
            expected_nullable = name in nullable_columns[table]
            if bool(columns[name]["nullable"]) != expected_nullable:
                raise RuntimeError("Incompatible Task263 SQLite nullability exists")
        if inspector.get_pk_constraint(table).get("constrained_columns") != ["id"]:
            raise RuntimeError("Incompatible Task263 SQLite primary key exists")
        index_names = {item.get("name") for item in inspector.get_indexes(table)}
        if not expected_indexes[table].issubset(index_names):
            raise RuntimeError("Task263 SQLite required indexes are missing")
        check_names = {str(item.get("name") or "") for item in inspector.get_check_constraints(table)}
        if any(not any(name.endswith(suffix) for name in check_names) for suffix in required_checks[table]):
            raise RuntimeError("Task263 SQLite required checks are missing")
    artifact_unique = {tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints(ARTIFACTS)}
    if ("source_provider", "source_url", "content_sha256") not in artifact_unique:
        raise RuntimeError("Task263 SQLite artifact uniqueness is missing")
    for table in (RATINGS, DEFAULTS):
        uniques = {tuple(item.get("column_names") or ()) for item in inspector.get_unique_constraints(table)}
        if ("event_fingerprint",) not in uniques:
            raise RuntimeError("Task263 SQLite event uniqueness is missing")
    expected_fks = {
        RATINGS: {("artifact_id", ARTIFACTS), ("legal_issuer_id", "legal_issuers"), ("bond_id", "bonds")},
        DEFAULTS: {("artifact_id", ARTIFACTS), ("bond_id", "bonds")},
    }
    for table, expected in expected_fks.items():
        found = {
            ((item.get("constrained_columns") or [None])[0], item.get("referred_table"))
            for item in inspector.get_foreign_keys(table)
            if item.get("options", {}).get("ondelete") == "RESTRICT"
        }
        if found != expected:
            raise RuntimeError("Task263 SQLite foreign keys are incompatible")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        inspector = sa.inspect(bind)
        present = set(TASK263_TABLES).intersection(inspector.get_table_names())
        if present:
            if present != set(TASK263_TABLES):
                raise RuntimeError("Partial Task263 SQLite schema exists")
            _validate_sqlite(inspector)
            return
    _create_artifacts()
    _create_ratings()
    _create_defaults()


def downgrade() -> None:
    op.drop_table(DEFAULTS)
    op.drop_table(RATINGS)
    op.drop_table(ARTIFACTS)
