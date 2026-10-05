"""Immutable multi-capital experiment persistence; no accounting duplication."""
from sqlalchemy import Column, Integer, String, Date, DateTime, JSON, ForeignKey, UniqueConstraint, CheckConstraint, func
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowExperimentBenchmark(Base):
    __tablename__ = 'shadow_experiment_benchmarks'
    __table_args__ = (
        UniqueConstraint('experiment_case_id'),
        UniqueConstraint('benchmark_genesis_sha256'),
        CheckConstraint("contract_version = 'ofz-total-return-benchmark-genesis-v1'", name="shadow_experiment_benchmarks_check_0"),
        CheckConstraint("matching_mode IN ('EXACT_DURATION_NODE','LINEAR_DURATION_MATCH')", name="shadow_experiment_benchmarks_check_1"),
        CheckConstraint('component_count IN (1,2)', name="shadow_experiment_benchmarks_check_2"),
        CheckConstraint('CAST(genesis_nav_rub AS NUMERIC) = CAST(initial_capital_rub AS NUMERIC)', name="shadow_experiment_benchmarks_check_3"),
        CheckConstraint('CAST(initial_capital_rub AS NUMERIC) > 0', name="shadow_experiment_benchmarks_check_4"),
        CheckConstraint("planned_end_date = date(genesis_date, '+90 days')", name="shadow_experiment_benchmarks_horizon_sqlite").ddl_if(dialect="sqlite"),
        CheckConstraint("planned_end_date = genesis_date + 90", name="shadow_experiment_benchmarks_horizon_postgres").ddl_if(dialect="postgresql"),
    )
    id = Column(Integer, primary_key=True)
    experiment_case_id = Column(Integer(), ForeignKey('shadow_experiment_cases.id', ondelete="RESTRICT"), nullable=False, index=True)
    benchmark_genesis_sha256 = Column(String(64), nullable=False)
    contract_version = Column(String(64), nullable=False)
    experiment_policy_sha256 = Column(String(64), nullable=False)
    shadow_execution_sha256 = Column(String(64), nullable=False)
    genesis_date = Column(Date(), nullable=False)
    planned_end_date = Column(Date(), nullable=False)
    common_market_trade_date = Column(Date(), nullable=False)
    initial_capital_rub = Column(ShadowDecimal(), nullable=False)
    target_duration_years = Column(ShadowDecimal(), nullable=False)
    reconstructed_duration_years = Column(ShadowDecimal(), nullable=False)
    matching_mode = Column(String(32), nullable=False)
    genesis_nav_rub = Column(ShadowDecimal(), nullable=False)
    component_count = Column(Integer(), nullable=False)
    benchmark_payload_json = Column(JSON(), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
