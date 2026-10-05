"""Task306A REPORT only. Production execution requires separate authorization."""
import argparse
import json
from pathlib import Path
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.schemas.modern_historical_replay_readiness import HistoricalReplayReadinessAuditV1
from app.services.modern_historical_replay_readiness_audit_service import ModernHistoricalReplayReadinessAuditService, signed


def report(engine):
    """Own a read-only consistent transaction, never complete a caller's transaction."""
    try:
        dialect = engine.dialect.name
        if dialect not in ("sqlite", "postgresql"):
            raise ValueError("UNSUPPORTED_AUDIT_DIALECT")
        with engine.connect() as connection:
            if dialect == "postgresql":
                connection = connection.execution_options(isolation_level="REPEATABLE READ", postgresql_readonly=True)
            else:
                prior_query_only = connection.exec_driver_sql("PRAGMA query_only").scalar_one()
                connection.exec_driver_sql("PRAGMA query_only=ON")
                connection.rollback()
                # Explicit BEGIN ensures SQLite legacy driver SELECTs share one snapshot.
                connection.exec_driver_sql("BEGIN")
            try:
                with Session(bind=connection, autoflush=False) as session:
                    return ModernHistoricalReplayReadinessAuditService(session).build()
            finally:
                connection.rollback()
                if dialect == "sqlite":
                    connection.exec_driver_sql("PRAGMA query_only=ON" if prior_query_only else "PRAGMA query_only=OFF")
                    connection.rollback()
    except Exception:
        return signed(HistoricalReplayReadinessAuditV1(status="BLOCKED",audit_classification="INSUFFICIENT_FOR_REPLAY",
            known_p0_blockers=("READ_ONLY_AUDIT_CONNECTION_FAILED",)))


def main(argv=None, *, engine=None, stdout=None):
    parser = argparse.ArgumentParser(description="Task306A read-only persisted-evidence audit; no profitability calculation")
    parser.add_argument("--mode", choices=("REPORT",), default="REPORT")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    own_engine = engine is None
    try:
        if own_engine:
            # Connection configuration only; never fetch a source or expose credentials.
            try:
                from app.core.config import settings
                engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
            except Exception:
                result = signed(HistoricalReplayReadinessAuditV1(status="BLOCKED",audit_classification="INSUFFICIENT_FOR_REPLAY",
                    known_p0_blockers=("READ_ONLY_AUDIT_CONNECTION_FAILED",)))
                (stdout or sys.stdout).write(result.model_dump_json()+"\n")
                return 1
        result = report(engine)
        encoded = json.dumps(result.model_dump(mode="json"),sort_keys=True,ensure_ascii=True,
            separators=(",", ":"),allow_nan=False) + "\n"
        if args.output is not None:
            try:
                args.output.write_text(encoded,encoding="utf-8")
            except OSError:
                failure = signed(HistoricalReplayReadinessAuditV1(status="BLOCKED",audit_classification="INSUFFICIENT_FOR_REPLAY",
                    known_p0_blockers=("EXPLICIT_OUTPUT_WRITE_FAILED",)))
                (stdout or sys.stdout).write(failure.model_dump_json()+"\n")
                return 1
        else:
            (stdout or sys.stdout).write(encoded)
        return 0 if result.status == "COMPLETE" else 1
    finally:
        if own_engine and engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
