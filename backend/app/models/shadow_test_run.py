"""Dedicated Shadow run and lossless Decimal storage."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import CheckConstraint, Date, DateTime, Numeric, String, Integer, func
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class ShadowDecimal(TypeDecorator):
    impl = Numeric
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String() if dialect.name == "sqlite" else Numeric(asdecimal=True))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if type(value) is not Decimal or not value.is_finite():
            raise ValueError("SHADOW_DECIMAL_INVALID")
        return str(value) if dialect.name == "sqlite" else value

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


class ShadowTestRun(Base):
    __tablename__ = "shadow_test_runs"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE','OBSERVATION_COMPLETE','ABORTED')", name="shadow_test_run_check_1"),
        CheckConstraint("horizon_days = 90", name="shadow_test_run_check_2"),
        CheckConstraint("CAST(initial_capital_rub AS NUMERIC) > 0", name="shadow_test_run_check_3"),
        CheckConstraint("base_currency = 'RUB' AND market_source = 'moex'", name="shadow_test_run_check_4"),
        CheckConstraint("planned_end_date = date(genesis_date, '+90 days')", name="shadow_horizon_sqlite").ddl_if(dialect="sqlite"),
        CheckConstraint("planned_end_date = genesis_date + 90", name="shadow_horizon_postgresql").ddl_if(dialect="postgresql"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    run_key_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    genesis_date: Mapped[date] = mapped_column(Date, nullable=False)
    planned_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    initial_capital_rub: Mapped[Decimal] = mapped_column(ShadowDecimal(), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    market_source: Mapped[str] = mapped_column(String(8), nullable=False)
    source_code_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    source_universe_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    genesis_shadow_plan_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    shadow_execution_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    strategy_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    observation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
