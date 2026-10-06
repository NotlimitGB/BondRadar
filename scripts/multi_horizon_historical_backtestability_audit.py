"""Task306A2 REPORT only; production RCA requires separate authorization."""
import argparse
import json
from pathlib import Path
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0,str(ROOT / "backend"))

from app.services.multi_horizon_historical_backtestability_audit_service import (
    MultiHorizonHistoricalBacktestabilityAuditService, blocked,
)


def report(engine):
    try:
        if engine.dialect.name not in ("sqlite","postgresql"):
            return blocked("UNSUPPORTED_READ_ONLY_DIALECT")
        with engine.connect() as connection:
            sqlite = engine.dialect.name == "sqlite"
            if sqlite:
                prior = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
                connection.exec_driver_sql("PRAGMA query_only=ON")
                connection.rollback()
                connection.exec_driver_sql("BEGIN")
            else:
                connection = connection.execution_options(isolation_level="REPEATABLE READ",postgresql_readonly=True)
                connection.begin()
            try:
                with Session(bind=connection,autoflush=False,join_transaction_mode="rollback_only") as db:
                    return MultiHorizonHistoricalBacktestabilityAuditService(db).build()
            finally:
                connection.rollback()
                if sqlite:
                    connection.exec_driver_sql("PRAGMA query_only=ON" if prior else "PRAGMA query_only=OFF")
                    connection.rollback()
    except Exception:
        return blocked("READ_ONLY_RCA_CONNECTION_FAILED")


def main(argv=None,*,engine=None,stdout=None):
    parser = argparse.ArgumentParser(description="Task306A2 persisted-only blocker RCA; REPORT only")
    parser.add_argument("--mode",choices=("REPORT",),default="REPORT")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args(argv)
    own_engine = engine is None
    try:
        if own_engine:
            try:
                from app.core.config import settings
                engine = create_engine(settings.DATABASE_URL,pool_pre_ping=True)
            except Exception:
                result = blocked("READ_ONLY_RCA_CONNECTION_FAILED")
                (stdout or sys.stdout).write(result.model_dump_json()+"\n")
                return 1
        result = report(engine)
        encoded = json.dumps(result.model_dump(mode="json"),sort_keys=True,ensure_ascii=True,separators=(",",":"),allow_nan=False)+"\n"
        if args.output is not None:
            try:args.output.write_text(encoded,encoding="utf-8")
            except OSError:
                (stdout or sys.stdout).write(blocked("EXPLICIT_OUTPUT_WRITE_FAILED").model_dump_json()+"\n")
                return 1
        else:(stdout or sys.stdout).write(encoded)
        return 0 if result.status=="COMPLETE" else 1
    finally:
        if own_engine and engine is not None:engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
