"""Immutable multi-capital experiment persistence; no accounting duplication."""
from sqlalchemy import Column, Integer, String, Date, DateTime, JSON, ForeignKey, UniqueConstraint, CheckConstraint, func
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowExperimentBenchmarkComponent(Base):
    __tablename__ = 'shadow_experiment_benchmark_components'
    __table_args__ = (
        UniqueConstraint('benchmark_id', 'ordinal'),
        UniqueConstraint('benchmark_id', 'bond_id'),
        UniqueConstraint('benchmark_id', 'component_sha256'),
        CheckConstraint('ordinal > 0', name="shadow_experiment_benchmark_components_check_0"),
        CheckConstraint('CAST(weight AS NUMERIC) > 0 AND CAST(weight AS NUMERIC) <= 1', name="shadow_experiment_benchmark_components_check_1"),
        CheckConstraint('CAST(genesis_dirty_value_rub AS NUMERIC) > 0', name="shadow_experiment_benchmark_components_check_2"),
    )
    id = Column(Integer, primary_key=True)
    benchmark_id = Column(Integer(), ForeignKey('shadow_experiment_benchmarks.id', ondelete="RESTRICT"), nullable=False, index=True)
    ordinal = Column(Integer(), nullable=False)
    bond_id = Column(Integer(), ForeignKey('bonds.id', ondelete="RESTRICT"), nullable=False, index=True)
    market_snapshot_id = Column(Integer(), ForeignKey('bond_market_snapshots.id', ondelete="RESTRICT"), nullable=False, index=True)
    security_master_profile_id = Column(Integer(), ForeignKey('bond_security_master_profiles.id', ondelete="RESTRICT"), nullable=False, index=True)
    isin = Column(String(32), nullable=True)
    secid = Column(String(32), nullable=True)
    weight = Column(ShadowDecimal(), nullable=False)
    source_node_macaulay_duration_years = Column(ShadowDecimal(), nullable=False)
    modified_duration_years = Column(ShadowDecimal(), nullable=False)
    source_node_yield_pct = Column(ShadowDecimal(), nullable=False)
    component_yield_pct = Column(ShadowDecimal(), nullable=False)
    genesis_dirty_value_rub = Column(ShadowDecimal(), nullable=False)
    component_sha256 = Column(String(64), nullable=False)
