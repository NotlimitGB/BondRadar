"""Immutable multi-capital experiment persistence; no accounting duplication."""
from sqlalchemy import Column, Integer, String, Date, DateTime, JSON, ForeignKey, UniqueConstraint, CheckConstraint, func
from app.db.base import Base
from app.models.shadow_test_run import ShadowDecimal


class ShadowExperimentGroup(Base):
    __tablename__ = 'shadow_experiment_groups'
    __table_args__ = (
        UniqueConstraint('group_key_sha256'),
        UniqueConstraint('scale_genesis_sha256'),
        CheckConstraint("status IN ('ACTIVE','OBSERVATION_COMPLETE','ABORTED')", name="shadow_experiment_groups_check_0"),
        CheckConstraint('horizon_days = 90', name="shadow_experiment_groups_check_1"),
        CheckConstraint('capital_case_count = 7', name="shadow_experiment_groups_check_2"),
        CheckConstraint("planned_end_date = date(genesis_date, '+90 days')", name="shadow_experiment_groups_horizon_sqlite").ddl_if(dialect="sqlite"),
        CheckConstraint("planned_end_date = genesis_date + 90", name="shadow_experiment_groups_horizon_postgres").ddl_if(dialect="postgresql"),
    )
    id = Column(Integer, primary_key=True)
    group_key_sha256 = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    genesis_date = Column(Date(), nullable=False)
    planned_end_date = Column(Date(), nullable=False)
    horizon_days = Column(Integer(), nullable=False)
    common_market_trade_date = Column(Date(), nullable=False)
    source_code_sha = Column(String(40), nullable=False)
    source_universe_sha256 = Column(String(64), nullable=False)
    investment_batch_sha256 = Column(String(64), nullable=False)
    experiment_policy_sha256 = Column(String(64), nullable=False)
    scale_policy_sha256 = Column(String(64), nullable=False)
    scale_genesis_sha256 = Column(String(64), nullable=False)
    capital_case_count = Column(Integer(), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    activated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    observation_completed_at = Column(DateTime(timezone=True), nullable=True)
