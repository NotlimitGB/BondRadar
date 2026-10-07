"""Independent historical archive and bounded transaction receipts."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision="202610070001"
down_revision="202610040001"
branch_labels=None
depends_on=None

def schema():
    m=sa.MetaData()
    sa.Table("bonds",m,sa.Column("id",sa.Integer(),primary_key=True))
    j=JSONB().with_variant(sa.JSON(),"sqlite")
    sa.Table('historical_evidence_runs',m,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('run_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('source_manifest_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('history_start',sa.Date(),primary_key=False,nullable=False),
        sa.Column('history_end',sa.Date(),primary_key=False,nullable=False),
        sa.Column('policy_json',j,primary_key=False,nullable=False),
        sa.Column('scope_json',j,primary_key=False,nullable=False),
        sa.Column('planned_work_count',sa.Integer(),primary_key=False,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint('history_end >= history_start',name='ck_historical_evidence_runs_history_range'),
        sa.UniqueConstraint('run_sha256',name='uq_historical_evidence_runs_run_sha256'),
    )
    sa.Table('historical_evidence_work_items',m,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('run_id',sa.Integer(),sa.ForeignKey('historical_evidence_runs.id',ondelete="RESTRICT",name='fk_historical_evidence_work_items_run_id_historical_evidence_runs'),primary_key=False,nullable=False),
        sa.Column('query_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('query_json',j,primary_key=False,nullable=False),
        sa.Column('next_offset',sa.Integer(),primary_key=False,nullable=False),
        sa.Column('status',sa.String(16),primary_key=False,nullable=False),
        sa.Column('failure_code',sa.String(64),primary_key=False,nullable=True),
        sa.CheckConstraint('next_offset >= 0',name='ck_historical_evidence_work_items_nonnegative_offset'),
        sa.CheckConstraint("status in ('PENDING','COMPLETE','FAILED')",name='ck_historical_evidence_work_items_work_status'),
        sa.UniqueConstraint('run_id','query_sha256',name='uq_historical_work_query'),
    )
    sa.Index('ix_historical_evidence_work_items_run_id',m.tables['historical_evidence_work_items'].c['run_id'],unique=False)
    sa.Table('historical_evidence_source_pages',m,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('work_id',sa.Integer(),sa.ForeignKey('historical_evidence_work_items.id',ondelete="RESTRICT",name='fk_historical_evidence_source_pages_work_id_historical_evidence_work_items'),primary_key=False,nullable=False),
        sa.Column('offset',sa.Integer(),primary_key=False,nullable=False),
        sa.Column('page_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('page_json',j,primary_key=False,nullable=False),
        sa.Column('observed_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),
        sa.Column('ingested_at',sa.DateTime(timezone=True),primary_key=False,nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint('offset >= 0',name='ck_historical_evidence_source_pages_nonnegative_page_offset'),
        sa.UniqueConstraint('work_id','offset','page_sha256',name='uq_historical_page_version'),
    )
    sa.Index('ix_historical_evidence_source_pages_page_sha256',m.tables['historical_evidence_source_pages'].c['page_sha256'],unique=False)
    sa.Index('ix_historical_evidence_source_pages_work_id',m.tables['historical_evidence_source_pages'].c['work_id'],unique=False)
    sa.Table('historical_evidence_securities',m,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('identity_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('secid',sa.String(32),primary_key=False,nullable=False),
        sa.Column('isin',sa.String(32),primary_key=False,nullable=True),
        sa.Column('bond_id',sa.Integer(),sa.ForeignKey('bonds.id',ondelete="RESTRICT",name='fk_historical_evidence_securities_bond_id_bonds'),primary_key=False,nullable=True),
        sa.UniqueConstraint('identity_sha256',name='uq_historical_evidence_securities_identity_sha256'),
    )
    sa.Index('ix_historical_evidence_securities_bond_id',m.tables['historical_evidence_securities'].c['bond_id'],unique=False)
    sa.Index('ix_historical_evidence_securities_isin',m.tables['historical_evidence_securities'].c['isin'],unique=False)
    sa.Index('ix_historical_evidence_securities_secid',m.tables['historical_evidence_securities'].c['secid'],unique=False)
    sa.Table('historical_evidence_observations',m,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('page_id',sa.Integer(),sa.ForeignKey('historical_evidence_source_pages.id',ondelete="RESTRICT",name='fk_historical_evidence_observations_page_id_historical_evidence_source_pages'),primary_key=False,nullable=False),
        sa.Column('security_id',sa.Integer(),sa.ForeignKey('historical_evidence_securities.id',ondelete="RESTRICT",name='fk_historical_evidence_observations_security_id_historical_evidence_securities'),primary_key=False,nullable=True),
        sa.Column('ordinal',sa.Integer(),primary_key=False,nullable=False),
        sa.Column('family',sa.String(32),primary_key=False,nullable=False),
        sa.Column('event_date',sa.Date(),primary_key=False,nullable=True),
        sa.Column('effective_date',sa.Date(),primary_key=False,nullable=True),
        sa.Column('available_at',sa.DateTime(timezone=True),primary_key=False,nullable=True),
        sa.Column('proof_state',sa.String(24),primary_key=False,nullable=False),
        sa.Column('normalized_json',j,primary_key=False,nullable=False),
        sa.Column('raw_json',j,primary_key=False,nullable=False),
        sa.Column('row_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.CheckConstraint('ordinal >= 0',name='ck_historical_evidence_observations_nonnegative_ordinal'),
        sa.CheckConstraint("proof_state in ('DATED_SOURCE','CURRENT_OBSERVATION','UNPROVEN')",name='ck_historical_evidence_observations_proof_state'),
        sa.UniqueConstraint('page_id','ordinal',name='uq_historical_observation_row'),
    )
    sa.Index('ix_historical_evidence_observations_event_date',m.tables['historical_evidence_observations'].c['event_date'],unique=False)
    sa.Index('ix_historical_evidence_observations_family',m.tables['historical_evidence_observations'].c['family'],unique=False)
    sa.Index('ix_historical_evidence_observations_page_id',m.tables['historical_evidence_observations'].c['page_id'],unique=False)
    sa.Index('ix_historical_evidence_observations_row_sha256',m.tables['historical_evidence_observations'].c['row_sha256'],unique=False)
    sa.Index('ix_historical_evidence_observations_security_id',m.tables['historical_evidence_observations'].c['security_id'],unique=False)
    sa.Table('historical_evidence_application_receipts',m,
        sa.Column('id',sa.Integer(),primary_key=True,nullable=False),
        sa.Column('run_id',sa.Integer(),sa.ForeignKey('historical_evidence_runs.id',ondelete="RESTRICT",name='fk_historical_evidence_application_receipts_run_id_historical_evidence_runs'),primary_key=False,nullable=False),
        sa.Column('batch_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('operation',sa.String(16),primary_key=False,nullable=False),
        sa.Column('plan_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('before_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('after_sha256',sa.String(64),primary_key=False,nullable=False),
        sa.Column('receipt_json',j,primary_key=False,nullable=False),
        sa.Column('created_at',sa.DateTime(timezone=True),primary_key=False,nullable=False,server_default=sa.func.now()),
        sa.CheckConstraint("operation in ('ACQUIRE','APPLY')",name='ck_historical_evidence_application_receipts_receipt_operation'),
        sa.UniqueConstraint('batch_sha256',name='uq_historical_evidence_application_receipts_batch_sha256'),
    )
    sa.Index('ix_historical_evidence_application_receipts_run_id',m.tables['historical_evidence_application_receipts'].c['run_id'],unique=False)
    return m


def _compatible(connection,table):
    inspector=sa.inspect(connection)
    columns=inspector.get_columns(table.name)
    expected={c.name:c for c in table.columns}
    if set(expected)!={c["name"] for c in columns}:return False
    for actual in columns:
        column=expected[actual["name"]]
        if actual["nullable"]!=column.nullable or str(actual["type"]).upper()!=str(column.type.compile(dialect=connection.dialect)).upper():return False
    if inspector.get_pk_constraint(table.name)["constrained_columns"]!=[c.name for c in table.primary_key]:return False
    uniques={tuple(u["column_names"]) for u in inspector.get_unique_constraints(table.name)}
    if uniques!={tuple(c.name for c in u.columns) for u in table.constraints if isinstance(u,sa.UniqueConstraint)}:return False
    fks={(tuple(f["constrained_columns"]),f["referred_table"],tuple(f["referred_columns"]),f.get("options",{}).get("ondelete")) for f in inspector.get_foreign_keys(table.name)}
    want={(tuple(c.name for c in f.columns),f.referred_table.name,tuple(e.column.name for e in f.elements),f.ondelete) for f in table.foreign_key_constraints}
    if fks!=want:return False
    def normalize(expression):
        import re
        # Preserve string literal contents; remove formatting outside literals only.
        pieces=re.split("('[^']*')",expression)
        return ''.join(piece if n%2 else re.sub(r'\s+','',piece).lower() for n,piece in enumerate(pieces))
    checks={normalize(c["sqltext"]) for c in inspector.get_check_constraints(table.name)}
    if checks!={normalize(str(c.sqltext)) for c in table.constraints if isinstance(c,sa.CheckConstraint)}:return False
    indices={(tuple(i["column_names"]),bool(i["unique"])) for i in inspector.get_indexes(table.name)}
    return indices=={(tuple(c.name for c in i.columns),bool(i.unique)) for i in table.indexes}


def upgrade():
    connection=op.get_bind();metadata=schema()
    tables=[t for t in metadata.sorted_tables if t.name!="bonds"]
    existing=set(sa.inspect(connection).get_table_names())&{t.name for t in tables}
    if connection.dialect.name=="sqlite" and existing:
        if len(existing)!=len(tables):raise RuntimeError("PARTIAL_HISTORICAL_EVIDENCE_SCHEMA")
        if not all(_compatible(connection,t) for t in tables):raise RuntimeError("INCOMPATIBLE_HISTORICAL_EVIDENCE_SCHEMA")
        return
    for table in tables:table.create(connection,checkfirst=False)


def downgrade():
    connection=op.get_bind();tables=[t for t in schema().sorted_tables if t.name!="bonds"]
    for table in tables:
        if connection.execute(sa.select(sa.func.count()).select_from(table)).scalar_one():raise RuntimeError("HISTORICAL_EVIDENCE_DOWNGRADE_REFUSED_NONEMPTY")
    for table in reversed(tables):table.drop(connection,checkfirst=False)
