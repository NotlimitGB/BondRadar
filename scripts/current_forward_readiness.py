"""Explicit REPORT entrypoint; no default database, collectors or APPLY mode."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import stat
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
SECRET_LIMIT = 16 * 1024
SECRET_QUERY_KEYS = frozenset(("password", "passwd", "pwd", "sslpassword", "token", "access_token",
    "refresh_token", "secret", "api_key", "authorization", "headers", "cookies", "options", "service",
    "host", "hostaddr", "port", "dbname", "database", "user", "username"))


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


def validate_secret_stat(info, *, owner_uid=None):
    if not stat.S_ISREG(info.st_mode) or info.st_size > SECRET_LIMIT:
        raise ValueError("CREDENTIAL_FILE_INVALID")
    if owner_uid is not None and (info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) & 0o077):
        raise ValueError("CREDENTIAL_FILE_NOT_PRIVATE")


def checked_database_url(value, *, allow_password=False):
    try:
        url = make_url(value)
    except Exception:
        raise ValueError("DATABASE_CONFIGURATION_INVALID") from None
    if url.get_backend_name() not in ("sqlite", "postgresql"):
        raise ValueError("DIALECT_UNSUPPORTED")
    if (not allow_password and url.password is not None) or any(k.lower() in SECRET_QUERY_KEYS for k in url.query):
        raise ValueError("COMMAND_LINE_CREDENTIALS_FORBIDDEN")
    if url.get_backend_name() == "postgresql":
        if not all(type(v) is str and bool(v.strip()) for v in (url.host, url.database, url.username)):
            raise ValueError("EXPLICIT_DATABASE_ENDPOINT_REQUIRED")
        if url.port is None:
            url = url.set(port=5432)
    elif url.database in (None, "", ":memory:"):
        if url.query:
            raise ValueError("SQLITE_MEMORY_QUERY_FORBIDDEN")
    else:
        # query_only begins after connect; without URI mode=ro SQLite could
        # already create a missing persistent database during connection.
        if not (url.database.startswith("file:") and "?" not in url.database and "#" not in url.database
                and url.query.get("uri") == "true" and url.query.get("mode") == "ro"):
            raise ValueError("SQLITE_READ_ONLY_URI_REQUIRED")
    return url


def read_database_url_file(path):
    """Read only an explicit bounded regular secret file; never copy it to reports.

    POSIX ownership/mode is enforced. Windows DACL provisioning remains an
    explicit operator prerequisite; POSIX mode bits do not verify a DACL.
    """
    before = path.lstat()
    if path.is_symlink() or getattr(before, "st_file_attributes", 0) & 0x400:
        raise ValueError("CREDENTIAL_PATH_INVALID")
    owner = os.geteuid() if os.name == "posix" else None
    validate_secret_stat(before, owner_uid=owner)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        validate_secret_stat(opened, owner_uid=owner)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("CREDENTIAL_FILE_CHANGED")
        raw = stream.read(SECRET_LIMIT+1)
    if len(raw) > SECRET_LIMIT:
        raise ValueError("CREDENTIAL_FILE_TOO_LARGE")
    value = raw.decode("utf-8").rstrip("\r\n")
    if not value or "\r" in value or "\n" in value:
        raise ValueError("CREDENTIAL_FILE_INVALID")
    return checked_database_url(value, allow_password=True)


def evaluate(request, database_url):
    url = checked_database_url(database_url, allow_password=True)
    engine = create_engine(url, echo=False, hide_parameters=True)
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
    database = parser.add_mutually_exclusive_group(required=True)
    database.add_argument("--database-url",help="Passwordless URL only; use --database-url-file for credentials")
    database.add_argument("--database-url-file",type=Path,help="Explicit private UTF-8 file containing the database URL")
    parser.add_argument("--expected-source-sha256",required=True)
    parser.add_argument("--expected-code-sha",required=True)
    parser.add_argument("--output",type=Path)
    try:
        args = parser.parse_args(argv)
        if args.output and args.output.resolve() == args.input.resolve():
            raise ValueError("OUTPUT_INPUT_OVERLAP")
        if args.database_url_file and (args.database_url_file.resolve() == args.input.resolve() or
                (args.output and args.database_url_file.resolve() == args.output.resolve())):
            raise ValueError("CREDENTIAL_PATH_OVERLAP")
        raw, before = read_input(args.input)
        request = CurrentForwardReadinessRequest.model_validate_json(raw)
        if (fingerprint(request.source_context) != args.expected_source_sha256 or
                request.implementation_sha != args.expected_code_sha):
            raise ValueError("EXPECTED_HASH_MISMATCH")
        url = read_database_url_file(args.database_url_file) if args.database_url_file else checked_database_url(args.database_url)
        result = evaluate(request,url)
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
