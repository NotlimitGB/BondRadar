"""Allow truthful frozen T-Invest Security Master evidence; no data rewrite."""
from alembic import op
import sqlalchemy as sa

revision = "202610030001"
down_revision = "202609160001"
branch_labels = None
depends_on = None
TABLE = "bond_security_master_evidence"


def _change(include):
    bind = op.get_bind()
    if not include and bind.execute(sa.text(
        "SELECT 1 FROM bond_security_master_evidence WHERE source = 'tinvest_universe' LIMIT 1"
    )).first() is not None:
        raise RuntimeError("T-Invest evidence must be removed by a separately authorized operation before downgrade")
    matches = [c["name"] for c in sa.inspect(bind).get_check_constraints(TABLE)
               if all(s in c.get("sqltext", "") for s in ("source", "moex_universe", "moex_description", "moex_cashflows"))]
    if len(matches) != 1 or not matches[0]:
        raise RuntimeError("Security Master source constraint missing or ambiguous")
    allowed = "'moex_universe', 'moex_description', 'moex_cashflows'" + (", 'tinvest_universe'" if include else "")
    with op.batch_alter_table(TABLE) as batch:
        batch.drop_constraint(op.f(matches[0]), type_="check")
        batch.create_check_constraint(op.f(matches[0]), "source in (" + allowed + ")")


def upgrade():
    _change(True)


def downgrade():
    _change(False)
