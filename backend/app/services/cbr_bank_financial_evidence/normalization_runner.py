from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import bindparam, create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
    CbrBankNormalizedObservation,
    CbrBankRawObservation,
)

from .fingerprints import canonical_json_bytes, sha256_canonical
from .normalization import (
    CbrBankNormalizedObservationStore,
    NormalizationSemanticCollision,
    UnsupportedNormalizationInput,
    assert_normalized_observation_matches,
    normalize_raw_observation,
)


SCHEMA_VERSION = "bondradar.cbr_bank_normalization_runner.v1"
EXPECTED_ALEMBIC_REVISION = "202609140001"
BATCH_SIZE = 2_000
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_REQUIRED_TABLES = (
    "alembic_version",
    "cbr_bank_reporting_subjects",
    "cbr_bank_source_artifacts",
    "cbr_bank_artifact_availability_evidence",
    "cbr_bank_report_snapshots",
    "cbr_bank_raw_observations",
    "cbr_bank_normalized_observations",
)


class NormalizationRunnerError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CommitOutcomeUnknown(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _SchemaState:
    revisions: tuple[str, ...]
    tables: frozenset[str]


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise NormalizationRunnerError("INVALID_ARGUMENTS")


def _enforce_postgresql(connection: Any, *, allow_non_postgresql: bool) -> None:
    if not allow_non_postgresql and connection.dialect.name != "postgresql":
        raise NormalizationRunnerError("POSTGRESQL_REQUIRED")


def _enforce_read_only(session: Session) -> None:
    session.execute(text("SET TRANSACTION READ ONLY"))
    if session.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
        raise NormalizationRunnerError("READ_ONLY_VERIFICATION_FAILED")


def _read_schema_state(session: Session) -> _SchemaState:
    revisions = tuple(
        str(value)
        for value in session.execute(
            text("SELECT version_num FROM alembic_version ORDER BY version_num")
        ).scalars()
    )
    statement = text(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = current_schema() AND table_name IN :table_names "
        "ORDER BY table_name"
    ).bindparams(bindparam("table_names", expanding=True))
    tables = frozenset(
        str(value)
        for value in session.execute(
            statement, {"table_names": _REQUIRED_TABLES}
        ).scalars()
    )
    return _SchemaState(revisions=revisions, tables=tables)


def _validate_schema_state(state: _SchemaState) -> None:
    if state.revisions != (EXPECTED_ALEMBIC_REVISION,):
        raise NormalizationRunnerError("ALEMBIC_REVISION_MISMATCH")
    if not set(_REQUIRED_TABLES).issubset(state.tables):
        raise NormalizationRunnerError("NORMALIZATION_SCHEMA_MISSING")


def _load_existing(
    session: Session, raw_ids: list[int]
) -> dict[int, CbrBankNormalizedObservation]:
    if not raw_ids:
        return {}
    rows = session.execute(
        select(CbrBankNormalizedObservation).where(
            CbrBankNormalizedObservation.raw_observation_id.in_(raw_ids)
        )
    ).scalars()
    return {row.raw_observation_id: row for row in rows}


def _raw_batch(
    session: Session, *, after_id: int, through_id: int
) -> list[CbrBankRawObservation]:
    return list(
        session.execute(
            select(CbrBankRawObservation)
            .where(
                CbrBankRawObservation.id > after_id,
                CbrBankRawObservation.id <= through_id,
            )
            .order_by(CbrBankRawObservation.id)
            .limit(BATCH_SIZE)
        ).scalars()
    )


def _plan_body(session: Session, *, through_id: int | None = None) -> dict[str, Any]:
    current_max = int(session.scalar(select(func.max(CbrBankRawObservation.id))) or 0)
    boundary = current_max if through_id is None else through_id
    if not isinstance(boundary, int) or isinstance(boundary, bool) or boundary < 0:
        raise NormalizationRunnerError("INVALID_RAW_BOUNDARY")
    if boundary > current_max:
        raise NormalizationRunnerError("RAW_BOUNDARY_UNAVAILABLE")

    rows_by_form = {form: 0 for form in ("0409101", "0409102", "0409123", "0409135")}
    raw_rows = already = candidates = unsupported = collisions = 0
    value_rows = unavailable_rows = 0
    unsupported_by_code: dict[str, int] = {}
    scope_hasher = hashlib.sha256()
    last_id = 0
    while True:
        raw_batch = _raw_batch(session, after_id=last_id, through_id=boundary)
        if not raw_batch:
            break
        existing = _load_existing(session, [row.id for row in raw_batch])
        for raw in raw_batch:
            raw_rows += 1
            rows_by_form[raw.form] = rows_by_form.get(raw.form, 0) + 1
            status = "INSERT_CANDIDATE"
            normalized_fingerprint: str | None = None
            try:
                draft = normalize_raw_observation(raw)
                normalized_fingerprint = draft.normalization_fingerprint
                if draft.normalized_value is None:
                    unavailable_rows += 1
                else:
                    value_rows += 1
                normalized = existing.get(raw.id)
                if normalized is None:
                    candidates += 1
                else:
                    try:
                        assert_normalized_observation_matches(normalized, draft)
                        already += 1
                        status = "ALREADY_NORMALIZED"
                    except NormalizationSemanticCollision:
                        collisions += 1
                        status = "SEMANTIC_COLLISION"
            except UnsupportedNormalizationInput as exc:
                unsupported += 1
                code = exc.code
                unsupported_by_code[code] = unsupported_by_code.get(code, 0) + 1
                status = code
            scope_hasher.update(
                canonical_json_bytes(
                    {
                        "raw_observation_id": raw.id,
                        "raw_observation_fingerprint": raw.observation_fingerprint,
                        "status": status,
                        "normalization_fingerprint": normalized_fingerprint,
                    }
                )
            )
            scope_hasher.update(b"\n")
        last_id = raw_batch[-1].id

    body = {
        "schema_revision": EXPECTED_ALEMBIC_REVISION,
        "normalization_contract_version": (
            CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION
        ),
        "through_raw_observation_id": boundary,
        "raw_rows_in_scope": raw_rows,
        "already_normalized_rows": already,
        "insert_candidate_rows": candidates,
        "unsupported_input_rows": unsupported,
        "semantic_collision_rows": collisions,
        "rows_by_form": dict(sorted(rows_by_form.items())),
        "value_rows": value_rows,
        "unavailable_value_rows": unavailable_rows,
        "unsupported_by_code": dict(sorted(unsupported_by_code.items())),
        "raw_scope_sha256": scope_hasher.hexdigest(),
    }
    body["plan_hash"] = sha256_canonical(body)
    return body


def _read_plan(
    engine: Engine,
    *,
    through_id: int | None = None,
    schema_reader: Callable[[Session], _SchemaState] = _read_schema_state,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
    allow_non_postgresql: bool = False,
) -> dict[str, Any]:
    connection = transaction = session = None
    try:
        connection = engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, expire_on_commit=False)
        _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
        read_only_enforcer(session)
        _validate_schema_state(schema_reader(session))
        body = _plan_body(session, through_id=through_id)
        transaction.rollback()
        return body
    finally:
        if transaction is not None and transaction.is_active:
            transaction.rollback()
        if session is not None:
            session.close()
        if connection is not None:
            connection.close()


def _safety(*, database_accessed: bool) -> dict[str, Any]:
    return {
        "database_accessed": database_accessed,
        "database_mutation_executed": False,
        "normalization_executed": False,
        "normalization_network_required": False,
        "network_accessed": False,
        "pit_selection_executed": False,
        "pit_ready": False,
        "legal_issuer_inference": False,
        "credit_metrics_calculated": False,
        "scoring": False,
        "production_actions": "NONE",
    }


def execute_plan(
    engine: Engine,
    *,
    through_id: int | None = None,
    schema_reader: Callable[[Session], _SchemaState] = _read_schema_state,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
    allow_non_postgresql: bool = False,
) -> dict[str, Any]:
    body = _read_plan(
        engine,
        through_id=through_id,
        schema_reader=schema_reader,
        read_only_enforcer=read_only_enforcer,
        allow_non_postgresql=allow_non_postgresql,
    )
    ready = not body["unsupported_input_rows"] and not body["semantic_collision_rows"]
    return {
        "schema": SCHEMA_VERSION,
        "status": "complete",
        "mode": "plan",
        "error_code": None,
        "ready": ready,
        **body,
        **_safety(database_accessed=True),
    }


def execute_preflight(
    engine: Engine,
    *,
    through_id: int,
    expected_plan_hash: str,
    schema_reader: Callable[[Session], _SchemaState] = _read_schema_state,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
    allow_non_postgresql: bool = False,
) -> dict[str, Any]:
    body = _read_plan(
        engine,
        through_id=through_id,
        schema_reader=schema_reader,
        read_only_enforcer=read_only_enforcer,
        allow_non_postgresql=allow_non_postgresql,
    )
    if not hmac.compare_digest(body["plan_hash"], expected_plan_hash):
        raise NormalizationRunnerError("PLAN_HASH_MISMATCH")
    if body["unsupported_input_rows"]:
        raise NormalizationRunnerError("UNSUPPORTED_NORMALIZATION_INPUT")
    if body["semantic_collision_rows"]:
        raise NormalizationRunnerError("NORMALIZATION_SEMANTIC_COLLISION")
    return {
        "schema": SCHEMA_VERSION,
        "status": "ready",
        "mode": "preflight",
        "error_code": None,
        "ready": True,
        **body,
        "transaction_read_only": True,
        "database_read_only": True,
        **_safety(database_accessed=True),
    }


def _apply_failure(
    code: str,
    *,
    through_id: int,
    expected_plan_hash: str,
    inserted_rows: int,
    committed_batches: int,
    committed_through_id: int | None,
    commit_outcome_unknown: bool = False,
) -> dict[str, Any]:
    known_mutation = inserted_rows > 0
    return {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "mode": "apply",
        "error_code": code,
        "ready": False,
        "through_raw_observation_id": through_id,
        "expected_plan_hash": expected_plan_hash,
        "committed_batches": committed_batches,
        "committed_through_raw_observation_id": committed_through_id,
        "commit_outcome_unknown": commit_outcome_unknown,
        "partial_apply": known_mutation or commit_outcome_unknown,
        "reconciliation_required": known_mutation or commit_outcome_unknown,
        "database_accessed": True,
        "database_mutation_executed": (
            True if known_mutation else (None if commit_outcome_unknown else False)
        ),
        "normalization_executed": known_mutation or commit_outcome_unknown,
        "normalization_network_required": False,
        "network_accessed": False,
        "pit_selection_executed": False,
        "pit_ready": False,
        "legal_issuer_inference": False,
        "credit_metrics_calculated": False,
        "scoring": False,
        "production_actions": (
            "CBR_BANK_NORMALIZATION_APPLY_OUTCOME_UNKNOWN"
            if commit_outcome_unknown
            else ("CBR_BANK_NORMALIZATION_APPLY" if known_mutation else "NONE")
        ),
    }


def execute_apply(
    engine: Engine,
    *,
    through_id: int,
    expected_plan_hash: str,
    schema_reader: Callable[[Session], _SchemaState] = _read_schema_state,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
    allow_non_postgresql: bool = False,
    store_factory: Callable[[Session], CbrBankNormalizedObservationStore] = (
        CbrBankNormalizedObservationStore
    ),
    commit_transaction: Callable[[Any], None] = lambda transaction: transaction.commit(),
) -> dict[str, Any]:
    execute_preflight(
        engine,
        through_id=through_id,
        expected_plan_hash=expected_plan_hash,
        schema_reader=schema_reader,
        read_only_enforcer=read_only_enforcer,
        allow_non_postgresql=allow_non_postgresql,
    )
    inserted = reused = committed_batches = 0
    committed_through_id: int | None = None
    after_id = 0
    while True:
        connection = transaction = session = None
        commit_attempted = False
        try:
            connection = engine.connect()
            transaction = connection.begin()
            session = Session(bind=connection, autoflush=False, expire_on_commit=False)
            _enforce_postgresql(connection, allow_non_postgresql=allow_non_postgresql)
            _validate_schema_state(schema_reader(session))
            raw_batch = _raw_batch(session, after_id=after_id, through_id=through_id)
            if not raw_batch:
                transaction.rollback()
                break
            existing = _load_existing(session, [row.id for row in raw_batch])
            drafts = []
            batch_reused = 0
            for raw in raw_batch:
                draft = normalize_raw_observation(raw)
                row = existing.get(raw.id)
                if row is None:
                    drafts.append(draft)
                else:
                    assert_normalized_observation_matches(row, draft)
                    batch_reused += 1
            counts = store_factory(session).persist(drafts)
            if counts.reused:
                batch_reused += counts.reused
            commit_attempted = True
            try:
                commit_transaction(transaction)
            except Exception as exc:
                raise CommitOutcomeUnknown() from exc
            committed_batches += 1
            inserted += counts.inserted
            reused += batch_reused
            committed_through_id = raw_batch[-1].id
            after_id = raw_batch[-1].id
        except CommitOutcomeUnknown:
            return _apply_failure(
                "COMMIT_OUTCOME_UNKNOWN",
                through_id=through_id,
                expected_plan_hash=expected_plan_hash,
                inserted_rows=inserted,
                committed_batches=committed_batches,
                committed_through_id=committed_through_id,
                commit_outcome_unknown=True,
            )
        except Exception as exc:
            if transaction is not None and transaction.is_active:
                transaction.rollback()
            code = getattr(exc, "code", "NORMALIZATION_APPLY_FAILED")
            return _apply_failure(
                str(code),
                through_id=through_id,
                expected_plan_hash=expected_plan_hash,
                inserted_rows=inserted,
                committed_batches=committed_batches,
                committed_through_id=committed_through_id,
            )
        finally:
            if (
                transaction is not None
                and not commit_attempted
                and transaction.is_active
            ):
                transaction.rollback()
            if session is not None:
                session.close()
            if connection is not None:
                connection.close()

    try:
        final = _read_plan(
            engine,
            through_id=through_id,
            schema_reader=schema_reader,
            read_only_enforcer=read_only_enforcer,
            allow_non_postgresql=allow_non_postgresql,
        )
    except Exception:
        return _apply_failure(
            "FINAL_READBACK_FAILED",
            through_id=through_id,
            expected_plan_hash=expected_plan_hash,
            inserted_rows=inserted,
            committed_batches=committed_batches,
            committed_through_id=committed_through_id,
        )
    if (
        final["insert_candidate_rows"]
        or final["unsupported_input_rows"]
        or final["semantic_collision_rows"]
        or final["already_normalized_rows"] != final["raw_rows_in_scope"]
    ):
        return _apply_failure(
            "FINAL_READBACK_FAILED",
            through_id=through_id,
            expected_plan_hash=expected_plan_hash,
            inserted_rows=inserted,
            committed_batches=committed_batches,
            committed_through_id=committed_through_id,
        )
    mutated = inserted > 0
    return {
        "schema": SCHEMA_VERSION,
        "status": "complete",
        "mode": "apply",
        "error_code": None,
        "ready": True,
        "through_raw_observation_id": through_id,
        "expected_plan_hash": expected_plan_hash,
        "inserted_rows": inserted,
        "reused_rows": reused,
        "committed_batches": committed_batches,
        "committed_through_raw_observation_id": committed_through_id,
        "post_normalized_rows": final["already_normalized_rows"],
        "commit_outcome_unknown": False,
        "partial_apply": False,
        "reconciliation_required": False,
        "database_accessed": True,
        "database_mutation_executed": mutated,
        "normalization_executed": mutated,
        "normalization_network_required": False,
        "network_accessed": False,
        "pit_selection_executed": False,
        "pit_ready": False,
        "legal_issuer_inference": False,
        "credit_metrics_calculated": False,
        "scoring": False,
        "production_actions": (
            "CBR_BANK_NORMALIZATION_APPLY" if mutated else "NONE"
        ),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="cbr-bank-normalization-runner")
    parser.add_argument("--mode", choices=("plan", "preflight", "apply"), required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--confirm-read-only", action="store_true")
    parser.add_argument("--confirm-write", action="store_true")
    parser.add_argument("--through-raw-observation-id", type=int)
    parser.add_argument("--expected-plan-hash")
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if not _ENV_NAME.fullmatch(args.database_url_env or ""):
        raise NormalizationRunnerError("INVALID_ARGUMENTS")
    frozen = args.through_raw_observation_id is not None and bool(
        args.expected_plan_hash and _SHA256.fullmatch(args.expected_plan_hash)
    )
    if args.mode == "plan":
        valid = (
            args.confirm_read_only
            and not args.confirm_write
            and args.through_raw_observation_id is None
            and args.expected_plan_hash is None
        )
    elif args.mode == "preflight":
        valid = args.confirm_read_only and not args.confirm_write and frozen
    else:
        valid = args.confirm_write and not args.confirm_read_only and frozen
    if not valid or (
        args.through_raw_observation_id is not None
        and args.through_raw_observation_id < 0
    ):
        raise NormalizationRunnerError("INVALID_ARGUMENTS")


def _database_url(env_name: str, environment: Mapping[str, str]) -> str:
    value = environment.get(env_name)
    if not value:
        raise NormalizationRunnerError("DATABASE_CONFIGURATION_UNAVAILABLE")
    try:
        if make_url(value).get_backend_name() != "postgresql":
            raise NormalizationRunnerError("DATABASE_CONFIGURATION_INVALID")
    except NormalizationRunnerError:
        raise
    except Exception as exc:
        raise NormalizationRunnerError("DATABASE_CONFIGURATION_INVALID") from exc
    return value


def _failure(
    code: str, *, mode: str | None = None, database_accessed: bool = False
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "mode": mode if mode in {"plan", "preflight", "apply"} else "invalid",
        "error_code": code,
        "ready": False,
        **_safety(database_accessed=database_accessed),
    }


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[..., Engine] = create_engine,
    schema_reader: Callable[[Session], _SchemaState] = _read_schema_state,
    read_only_enforcer: Callable[[Session], None] = _enforce_read_only,
    allow_non_postgresql_test_adapter: bool = False,
) -> int:
    try:
        args = _parser().parse_args(argv)
        _validate_args(args)
    except (NormalizationRunnerError, SystemExit):
        _emit(_failure("INVALID_ARGUMENTS"))
        return 2
    mode = args.mode
    environment = os.environ if environ is None else environ
    try:
        database_url = _database_url(args.database_url_env, environment)
        engine = engine_factory(database_url, pool_pre_ping=True)
    except NormalizationRunnerError as exc:
        _emit(_failure(exc.code, mode=mode))
        return 1
    except Exception:
        _emit(_failure("DATABASE_CONNECTION_FAILED", mode=mode))
        return 1
    try:
        common = {
            "schema_reader": schema_reader,
            "read_only_enforcer": read_only_enforcer,
            "allow_non_postgresql": allow_non_postgresql_test_adapter,
        }
        if mode == "plan":
            result = execute_plan(engine, **common)
        elif mode == "preflight":
            result = execute_preflight(
                engine,
                through_id=args.through_raw_observation_id,
                expected_plan_hash=args.expected_plan_hash,
                **common,
            )
        else:
            result = execute_apply(
                engine,
                through_id=args.through_raw_observation_id,
                expected_plan_hash=args.expected_plan_hash,
                **common,
            )
        _emit(result)
        return 0 if result["status"] in {"complete", "ready"} else 1
    except NormalizationRunnerError as exc:
        _emit(_failure(exc.code, mode=mode, database_accessed=True))
        return 1
    except Exception:
        _emit(
            _failure(
                "NORMALIZATION_RUNNER_FAILED", mode=mode, database_accessed=True
            )
        )
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
