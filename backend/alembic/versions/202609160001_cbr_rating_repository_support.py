"""Add CBR repository provenance and nullable source rating scales; no backfill."""
from alembic import op
import sqlalchemy as sa

revision = "202609160001"
down_revision = "202609150003"
branch_labels = None
depends_on = None

ARTIFACTS = "credit_risk_source_artifacts"
RATINGS = "credit_rating_events"


def _select_constraint(constraints, sentinels):
    matches = [c.get("name") for c in constraints if isinstance(c.get("sqltext"), str)
               and all(s.casefold() in c["sqltext"].casefold() for s in sentinels)]
    if len(matches) != 1 or not isinstance(matches[0], str) or not matches[0]:
        raise RuntimeError("CBR repository constraint missing or ambiguous")
    return matches[0]


def _change(include):
    bind = op.get_bind()
    changes = {
        ARTIFACTS: [
            (("source_provider", "EXPERT_RA", "MOEX"),
             "source_provider in ('ACRA','EXPERT_RA','NRA','NKR','MOEX'" + (",'CBR_RATINGS'" if include else "") + ")"),
            (("source_kind", "RATING_ISSUER_PAGE", "DEFAULT_INFORMATION"),
             "source_kind in ('RATING_ISSUER_PAGE','RATING_ISSUE_PAGE','RATING_RELEASE','DEFAULT_INFORMATION','OTHER'" + (",'RATING_REPOSITORY_RESPONSE'" if include else "") + ")"),
        ],
        RATINGS: [
            (("rating_scale_raw", "rating_value_raw", "rating_action_raw"),
             ("(rating_scale_raw is null or length(rating_scale_raw)>0)" if include else "length(rating_scale_raw)>0") +
             " and (rating_value_raw is not null or rating_outlook_raw is not null or rating_watch_raw is not null or rating_action_raw is not null)"),
        ],
    }
    # Reflect all names before DDL: PostgreSQL may truncate naming-convention names.
    selected = {table: [(_select_constraint(sa.inspect(bind).get_check_constraints(table), keys), sql)
                        for keys, sql in entries] for table, entries in changes.items()}
    for table, entries in selected.items():
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(table, recreate="always") as batch:
                for name, sql in entries:
                    batch.drop_constraint(op.f(name), type_="check")
                    batch.create_check_constraint(op.f(name), sql)
                if table == RATINGS:
                    batch.alter_column("rating_scale_raw", existing_type=sa.String(128), nullable=include)
        else:
            for name, sql in entries:
                op.drop_constraint(op.f(name), table, type_="check")
                op.create_check_constraint(op.f(name), table, sql)
            if table == RATINGS:
                op.alter_column(table, "rating_scale_raw", existing_type=sa.String(128), nullable=include)


def upgrade():
    _change(True)


def downgrade():
    bind = op.get_bind()
    incompatible = bind.execute(sa.text(
        "SELECT count(*) FROM credit_risk_source_artifacts WHERE source_provider='CBR_RATINGS' "
        "OR source_kind='RATING_REPOSITORY_RESPONSE'"
    )).scalar_one()
    null_scales = bind.execute(sa.text(
        "SELECT count(*) FROM credit_rating_events WHERE rating_scale_raw IS NULL"
    )).scalar_one()
    if incompatible or null_scales:
        raise RuntimeError("CBR repository evidence prevents downgrade")
    _change(False)
