"""Explicit REPORT entrypoint; no default database, collectors or APPLY mode."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.schemas.current_forward_readiness import CurrentForwardReadinessRequest
from app.services.current_forward_readiness_report import blocked, fingerprint
from app.services.current_forward_readiness_service import CurrentForwardReadinessService
from app.services.historical_audit_canonical_json import chunks

LIMIT = 32 * 1024 * 1024


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("RUNNER_CONFIGURATION_INVALID")


def pairs(values):
    result = {}
    for key,value in values:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError("NONFINITE_JSON")


def read_input(path):
    if path.is_symlink() or getattr(path.lstat(), "st_file_attributes", 0) & 0x400:
        raise ValueError("INPUT_PATH_INVALID")
    if path.stat().st_size > LIMIT:
        raise ValueError("INPUT_LIMIT")
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("INPUT_LIMIT")
    json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)
    return raw, hashlib.sha256(raw).hexdigest()


def evaluate(request, database_url):
    url = make_url(database_url)
    if url.get_backend_name() not in ("sqlite", "postgresql"):
        raise ValueError("DIALECT_UNSUPPORTED")
    engine = create_engine(url, echo=False)
    try:
        with engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                connection = connection.execution_options(isolation_level="REPEATABLE READ")
            transaction = connection.begin()
            try:
                if connection.dialect.name == "sqlite":
                    connection.exec_driver_sql("PRAGMA query_only=ON")
                    connection.exec_driver_sql("BEGIN")
                else:
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                with Session(bind=connection,join_transaction_mode="rollback_only",autoflush=False) as db:
                    return CurrentForwardReadinessService(db).build(request=request)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def encoded(report):
    for chunk in chunks(report):
        yield chunk.encode("ascii")
    yield b"\n"


def publish(report, output):
    size = sum(len(part) for part in encoded(report))
    if size > LIMIT:
        raise ValueError("OUTPUT_LIMIT")
    if output is None:
        for part in encoded(report):
            sys.stdout.write(part.decode("ascii"))
        return
    if output.is_symlink() or (output.exists() and getattr(output.lstat(), "st_file_attributes", 0) & 0x400):
        raise ValueError("OUTPUT_PATH_INVALID")
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=".task310-",delete=False) as stream:
            name = Path(stream.name)
            for part in encoded(report): stream.write(part)
            stream.flush(); os.fsync(stream.fileno())
        if output.exists():
            if output.stat().st_size != size:
                raise ValueError("OUTPUT_CONFLICT")
            with output.open("rb") as existing, name.open("rb") as proposed:
                if existing.read(LIMIT+1) != proposed.read(LIMIT+1):
                    raise ValueError("OUTPUT_CONFLICT")
        else:
            if os.name == "nt":
                os.rename(name,output)  # Windows rename refuses an existing target.
            else:
                os.link(name,output)  # POSIX atomic no-overwrite publication.
    finally:
        if name is not None: name.unlink(missing_ok=True)


def main(argv=None):
    parser = SafeParser(description="Read-only current forward readiness REPORT")
    parser.add_argument("--mode",choices=["REPORT_CURRENT_FORWARD_READINESS"],required=True)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--database-url",required=True)
    parser.add_argument("--expected-source-sha256",required=True)
    parser.add_argument("--expected-code-sha",required=True)
    parser.add_argument("--output",type=Path)
    try:
        args = parser.parse_args(argv)
        if args.output and args.output.resolve() == args.input.resolve():
            raise ValueError("OUTPUT_INPUT_OVERLAP")
        raw, before = read_input(args.input)
        request = CurrentForwardReadinessRequest.model_validate_json(raw)
        if (fingerprint(request.source_context) != args.expected_source_sha256 or
                request.implementation_sha != args.expected_code_sha):
            raise ValueError("EXPECTED_HASH_MISMATCH")
        result = evaluate(request,args.database_url)
        if read_input(args.input)[1] != before:
            raise ValueError("INPUT_DRIFT")
        publish(result,args.output)
        return 0 if result.status == "READY" else 2
    except Exception:
        # Never expose SQL/URL, filesystem, validation inputs or chained errors.
        try:
            publish(blocked("RUNNER_INPUT_CONNECTION_OR_OUTPUT_FAILURE"),None)
        except Exception:
            # A failed/full output sink cannot carry a trustworthy partial report.
            return 2
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
