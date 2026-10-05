"""Immutable multi-capital experiment persistence; no accounting duplication."""
from sqlalchemy import Column, Integer, String, Date, DateTime, JSON, ForeignKey, UniqueConstraint, CheckConstraint, func
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowExperimentCase(Base):
    __tablename__ = 'shadow_experiment_cases'
    __table_args__ = (
        UniqueConstraint('experiment_group_id', 'ordinal'),
        UniqueConstraint('experiment_group_id', 'capital_rub'),
        UniqueConstraint('experiment_group_id', 'case_sha256'),
        UniqueConstraint('shadow_run_id'),
        CheckConstraint('ordinal BETWEEN 1 AND 7', name="shadow_experiment_cases_check_0"),
        CheckConstraint('initial_selected_count > 0', name="shadow_experiment_cases_check_1"),
        CheckConstraint('CAST(initial_realized_invested_weight AS NUMERIC) >= 0 AND CAST(initial_cash_weight AS NUMERIC) >= 0', name="shadow_experiment_cases_check_2"),
        CheckConstraint('(ordinal = 1 AND CAST(capital_rub AS NUMERIC) = 50000) OR (ordinal = 2 AND CAST(capital_rub AS NUMERIC) = 100000) OR (ordinal = 3 AND CAST(capital_rub AS NUMERIC) = 500000) OR (ordinal = 4 AND CAST(capital_rub AS NUMERIC) = 1000000) OR (ordinal = 5 AND CAST(capital_rub AS NUMERIC) = 3000000) OR (ordinal = 6 AND CAST(capital_rub AS NUMERIC) = 5000000) OR (ordinal = 7 AND CAST(capital_rub AS NUMERIC) = 10000000)', name="shadow_experiment_cases_check_3"),
    )
    id = Column(Integer, primary_key=True)
    experiment_group_id = Column(Integer(), ForeignKey('shadow_experiment_groups.id', ondelete="RESTRICT"), nullable=False, index=True)
    ordinal = Column(Integer(), nullable=False)
    capital_rub = Column(ShadowDecimal(), nullable=False)
    case_sha256 = Column(String(64), nullable=False)
    experiment_genesis_sha256 = Column(String(64), nullable=False)
    strategy_genesis_plan_sha256 = Column(String(64), nullable=False)
    shadow_execution_sha256 = Column(String(64), nullable=False)
    benchmark_genesis_sha256 = Column(String(64), nullable=False)
    shadow_run_id = Column(Integer(), ForeignKey('shadow_test_runs.id', ondelete="RESTRICT"), nullable=False, index=True)
    initial_realized_invested_weight = Column(ShadowDecimal(), nullable=False)
    initial_cash_weight = Column(ShadowDecimal(), nullable=False)
    initial_selected_count = Column(Integer(), nullable=False)
    initial_target_duration_years = Column(ShadowDecimal(), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
