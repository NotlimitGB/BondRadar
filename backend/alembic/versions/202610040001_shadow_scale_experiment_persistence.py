"""Frozen scale persistence schema; never adopt partial or incompatible tables."""
from alembic import op
import sqlalchemy as sa
import re

revision = "202610040001"
down_revision = "202610030002"
branch_labels = None
depends_on = None


def _define_shadow_schema(create_table, create_index, dialect):
    create_table('shadow_experiment_groups',
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column('group_key_sha256', sa.String(64), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('genesis_date', sa.Date(), nullable=False),
        sa.Column('planned_end_date', sa.Date(), nullable=False),
        sa.Column('horizon_days', sa.Integer(), nullable=False),
        sa.Column('common_market_trade_date', sa.Date(), nullable=False),
        sa.Column('source_code_sha', sa.String(40), nullable=False),
        sa.Column('source_universe_sha256', sa.String(64), nullable=False),
        sa.Column('investment_batch_sha256', sa.String(64), nullable=False),
        sa.Column('experiment_policy_sha256', sa.String(64), nullable=False),
        sa.Column('scale_policy_sha256', sa.String(64), nullable=False),
        sa.Column('scale_genesis_sha256', sa.String(64), nullable=False),
        sa.Column('capital_case_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('observation_completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('group_key_sha256'),
        sa.UniqueConstraint('scale_genesis_sha256'),
        sa.CheckConstraint("status IN ('ACTIVE','OBSERVATION_COMPLETE','ABORTED')", name="task305_group_0"),
        sa.CheckConstraint('horizon_days = 90', name="task305_group_1"),
        sa.CheckConstraint('capital_case_count = 7', name="task305_group_2"),
        sa.CheckConstraint("planned_end_date = date(genesis_date, '+90 days')" if dialect == "sqlite" else "planned_end_date = genesis_date + 90", name="task305_group_horizon"),
    )
    create_table('shadow_experiment_cases',
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column('experiment_group_id', sa.Integer(), sa.ForeignKey('shadow_experiment_groups.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('capital_rub', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('case_sha256', sa.String(64), nullable=False),
        sa.Column('experiment_genesis_sha256', sa.String(64), nullable=False),
        sa.Column('strategy_genesis_plan_sha256', sa.String(64), nullable=False),
        sa.Column('shadow_execution_sha256', sa.String(64), nullable=False),
        sa.Column('benchmark_genesis_sha256', sa.String(64), nullable=False),
        sa.Column('shadow_run_id', sa.Integer(), sa.ForeignKey('shadow_test_runs.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('initial_realized_invested_weight', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('initial_cash_weight', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('initial_selected_count', sa.Integer(), nullable=False),
        sa.Column('initial_target_duration_years', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('experiment_group_id', 'ordinal'),
        sa.UniqueConstraint('experiment_group_id', 'capital_rub'),
        sa.UniqueConstraint('experiment_group_id', 'case_sha256'),
        sa.UniqueConstraint('shadow_run_id'),
        sa.CheckConstraint('ordinal BETWEEN 1 AND 7', name="task305_case_0"),
        sa.CheckConstraint('initial_selected_count > 0', name="task305_case_1"),
        sa.CheckConstraint('CAST(initial_realized_invested_weight AS NUMERIC) >= 0 AND CAST(initial_cash_weight AS NUMERIC) >= 0', name="task305_case_2"),
        sa.CheckConstraint('(ordinal = 1 AND CAST(capital_rub AS NUMERIC) = 50000) OR (ordinal = 2 AND CAST(capital_rub AS NUMERIC) = 100000) OR (ordinal = 3 AND CAST(capital_rub AS NUMERIC) = 500000) OR (ordinal = 4 AND CAST(capital_rub AS NUMERIC) = 1000000) OR (ordinal = 5 AND CAST(capital_rub AS NUMERIC) = 3000000) OR (ordinal = 6 AND CAST(capital_rub AS NUMERIC) = 5000000) OR (ordinal = 7 AND CAST(capital_rub AS NUMERIC) = 10000000)', name="task305_case_3"),
    )
    create_index("ix_shadow_experiment_cases_experiment_group_id", 'shadow_experiment_cases', ['experiment_group_id'])
    create_index("ix_shadow_experiment_cases_shadow_run_id", 'shadow_experiment_cases', ['shadow_run_id'])
    create_table('shadow_experiment_benchmarks',
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column('experiment_case_id', sa.Integer(), sa.ForeignKey('shadow_experiment_cases.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('benchmark_genesis_sha256', sa.String(64), nullable=False),
        sa.Column('contract_version', sa.String(64), nullable=False),
        sa.Column('experiment_policy_sha256', sa.String(64), nullable=False),
        sa.Column('shadow_execution_sha256', sa.String(64), nullable=False),
        sa.Column('genesis_date', sa.Date(), nullable=False),
        sa.Column('planned_end_date', sa.Date(), nullable=False),
        sa.Column('common_market_trade_date', sa.Date(), nullable=False),
        sa.Column('initial_capital_rub', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('target_duration_years', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('reconstructed_duration_years', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('matching_mode', sa.String(32), nullable=False),
        sa.Column('genesis_nav_rub', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('component_count', sa.Integer(), nullable=False),
        sa.Column('benchmark_payload_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('experiment_case_id'),
        sa.UniqueConstraint('benchmark_genesis_sha256'),
        sa.CheckConstraint("contract_version = 'ofz-total-return-benchmark-genesis-v1'", name="task305_benchmark_0"),
        sa.CheckConstraint("matching_mode IN ('EXACT_DURATION_NODE','LINEAR_DURATION_MATCH')", name="task305_benchmark_1"),
        sa.CheckConstraint('component_count IN (1,2)', name="task305_benchmark_2"),
        sa.CheckConstraint('CAST(genesis_nav_rub AS NUMERIC) = CAST(initial_capital_rub AS NUMERIC)', name="task305_benchmark_3"),
        sa.CheckConstraint('CAST(initial_capital_rub AS NUMERIC) > 0', name="task305_benchmark_4"),
        sa.CheckConstraint("planned_end_date = date(genesis_date, '+90 days')" if dialect == "sqlite" else "planned_end_date = genesis_date + 90", name="task305_benchmark_horizon"),
    )
    create_index("ix_shadow_experiment_benchmarks_experiment_case_id", 'shadow_experiment_benchmarks', ['experiment_case_id'])
    create_table('shadow_experiment_benchmark_components',
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column('benchmark_id', sa.Integer(), sa.ForeignKey('shadow_experiment_benchmarks.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('bond_id', sa.Integer(), sa.ForeignKey('bonds.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('market_snapshot_id', sa.Integer(), sa.ForeignKey('bond_market_snapshots.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('security_master_profile_id', sa.Integer(), sa.ForeignKey('bond_security_master_profiles.id', ondelete="RESTRICT"), nullable=False),
        sa.Column('isin', sa.String(32), nullable=True),
        sa.Column('secid', sa.String(32), nullable=True),
        sa.Column('weight', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('source_node_macaulay_duration_years', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('modified_duration_years', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('source_node_yield_pct', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('component_yield_pct', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('genesis_dirty_value_rub', sa.Numeric().with_variant(sa.String(), "sqlite"), nullable=False),
        sa.Column('component_sha256', sa.String(64), nullable=False),
        sa.UniqueConstraint('benchmark_id', 'ordinal'),
        sa.UniqueConstraint('benchmark_id', 'bond_id'),
        sa.UniqueConstraint('benchmark_id', 'component_sha256'),
        sa.CheckConstraint('ordinal > 0', name="task305_benchmark_component_0"),
        sa.CheckConstraint('CAST(weight AS NUMERIC) > 0 AND CAST(weight AS NUMERIC) <= 1', name="task305_benchmark_component_1"),
        sa.CheckConstraint('CAST(genesis_dirty_value_rub AS NUMERIC) > 0', name="task305_benchmark_component_2"),
    )
    create_index("ix_shadow_experiment_benchmark_components_benchmark_id", 'shadow_experiment_benchmark_components', ['benchmark_id'])
    create_index("ix_shadow_experiment_benchmark_components_bond_id", 'shadow_experiment_benchmark_components', ['bond_id'])
    create_index("ix_shadow_experiment_benchmark_components_market_snapshot_id", 'shadow_experiment_benchmark_components', ['market_snapshot_id'])
    create_index("ix_shadow_experiment_benchmark_components_security_master_profile_id", 'shadow_experiment_benchmark_components', ['security_master_profile_id'])


TASK305_TABLES = ('shadow_experiment_groups', 'shadow_experiment_cases', 'shadow_experiment_benchmarks', 'shadow_experiment_benchmark_components')


def _sql_tokens(expression):
    # Ignore formatting/keyword case, preserving literals and every operator.
    tokens = re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|!=|<>|>=|<=|[^\s]", expression)
    return tuple(token if token.startswith("'") else token.lower() for token in tokens)


def _expected_sqlite_schema():
    metadata = sa.MetaData()
    def table(name, *elements):
        return sa.Table(name, metadata, *elements)
    def index(name, table_name, columns):
        sa.Index(name, *(metadata.tables[table_name].c[column] for column in columns))
    _define_shadow_schema(table, index, "sqlite")
    return metadata


def _validate_precreated_sqlite_schema(inspector):
    # Frozen expectations come from this revision, never mutable ORM imports.
    expected = _expected_sqlite_schema()
    dialect = op.get_bind().dialect
    def require(condition):
        if not condition:
            raise RuntimeError("INCOMPATIBLE_TASK305_SQLITE_SCHEMA")
    for name, table in expected.tables.items():
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        require(set(columns) == set(table.columns.keys()))
        for column in table.columns:
            actual = columns[column.name]
            required_type = column.type.dialect_impl(dialect)
            require(actual["nullable"] == column.nullable and
                    actual["type"]._type_affinity is required_type._type_affinity)
            if isinstance(required_type, sa.String):
                require(actual["type"].length == required_type.length)
        require(inspector.get_pk_constraint(name)["constrained_columns"] == ["id"])
        uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)}
        require(uniques == {tuple(column.name for column in constraint.columns)
                           for constraint in table.constraints if isinstance(constraint, sa.UniqueConstraint)})
        foreign_keys = {(tuple(item["constrained_columns"]), item["referred_table"],
                         tuple(item["referred_columns"]), item.get("options", {}).get("ondelete"))
                        for item in inspector.get_foreign_keys(name)}
        expected_fks = {(tuple(column.name for column in constraint.columns),
                         constraint.elements[0].target_fullname.split(".")[0],
                         tuple(element.target_fullname.split(".")[1] for element in constraint.elements),
                         constraint.ondelete)
                        for constraint in table.constraints if isinstance(constraint, sa.ForeignKeyConstraint)}
        require(foreign_keys == expected_fks)
        indexes = {(tuple(item["column_names"]), bool(item["unique"]))
                   for item in inspector.get_indexes(name)
                   if item.get("dialect_options", {}).get("sqlite_where") is None}
        require({(tuple(column.name for column in index.columns), bool(index.unique))
                 for index in table.indexes} <= indexes)
        checks = {_sql_tokens(item["sqltext"]) for item in inspector.get_check_constraints(name)}
        require(checks == {_sql_tokens(str(constraint.sqltext)) for constraint in table.constraints
                           if isinstance(constraint, sa.CheckConstraint)})


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        inspector = sa.inspect(bind)
        present = set(inspector.get_table_names()).intersection(TASK305_TABLES)
        if present:
            if present != set(TASK305_TABLES):
                raise RuntimeError("PARTIAL_TASK305_SQLITE_SCHEMA")
            _validate_precreated_sqlite_schema(inspector)
            return
    _define_shadow_schema(op.create_table, op.create_index, bind.dialect.name)


def downgrade():
    tables = tuple(reversed(TASK305_TABLES))
    for table in tables:
        if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError("SCALE_HISTORY_DOWNGRADE_FORBIDDEN")
    for table in tables:
        op.drop_table(table)
