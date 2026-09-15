"""Add historical CBR N18 credit metric support.

Revision ID: 202609150002
Revises: 202609150001
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "202609150002"
down_revision = "202609150001"
branch_labels = None
depends_on = None

TABLE = "cbr_bank_credit_metrics"
MAPPING_CONSTRAINT_SUFFIX = "cbr_bank_credit_metrics_mapping_valid"


def _mapping_check_sql(*, include_n18: bool) -> str:
    n18_clause = (
        "or (source_code = 'N18' and metric_key = 'CBR_135_N18') "
        if include_n18
        else ""
    )
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
        f"{n18_clause}"
        "or (source_code = 'N27' and metric_key = 'CBR_135_N27'))))"
    )


def _mapping_constraint_name(bind: sa.Connection) -> str:
    matches = [
        item.get("name")
        for item in sa.inspect(bind).get_check_constraints(TABLE)
        if str(item.get("name") or "").endswith(MAPPING_CONSTRAINT_SUFFIX)
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise RuntimeError("Task262 mapping constraint is missing or ambiguous")
    return matches[0]


def _replace_mapping_constraint(*, include_n18: bool) -> None:
    bind = op.get_bind()
    constraint_name = _mapping_constraint_name(bind)
    predicate = _mapping_check_sql(include_n18=include_n18)
    if bind.dialect.name == "sqlite":
        naming_prefix = f"ck_{TABLE}_"
        batch_name = (
            constraint_name.removeprefix(naming_prefix)
            if constraint_name.startswith(naming_prefix)
            else constraint_name
        )
        with op.batch_alter_table(TABLE, recreate="always") as batch_op:
            batch_op.drop_constraint(batch_name, type_="check")
            batch_op.create_check_constraint(batch_name, predicate)
        return
    op.drop_constraint(op.f(constraint_name), TABLE, type_="check")
    op.create_check_constraint(op.f(constraint_name), TABLE, predicate)


def upgrade() -> None:
    _replace_mapping_constraint(include_n18=True)


def downgrade() -> None:
    bind = op.get_bind()
    n18_rows = bind.execute(
        sa.text(
            "SELECT count(*) FROM cbr_bank_credit_metrics "
            "WHERE source_form = '0409135' AND source_code = 'N18'"
        )
    ).scalar_one()
    if n18_rows:
        raise RuntimeError(
            "Cannot downgrade Task262 N18 constraint while N18 metrics exist"
        )
    _replace_mapping_constraint(include_n18=False)
