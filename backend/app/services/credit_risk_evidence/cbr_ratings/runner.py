"""Controlled CBR repository ingestion. CLI never accepts a raw database URL."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.legal_issuer import LegalIssuer
from app.models.credit_risk_evidence import CreditRiskSourceArtifact, CreditRatingEvent, CreditDefaultEvent
from app.services.credit_risk_evidence.contracts import (
    SourceArtifactInput, SourceProvider, SourceKind, canonical_inn, canonical_isin,
    canonical_json_sha256, aware_utc, CreditRiskEvidenceCollision,
)
from app.services.credit_risk_evidence.service import CreditRiskEvidenceStore
from .contracts import (
    SCHEMA, BUNDLE_SCHEMA, CONTRACT, REVISION, SHA, MAX_BUNDLE_BYTES, MAX_RESPONSE_BYTES,
    MAX_REQUESTS, MAX_MANIFEST_BYTES, SourceResponse, RepositoryError,
)
from .client import CbrRatingsClient
from .parser import derive, _pairs

COUNT_KEYS = ("issuer_universe_count bond_universe_count issuer_queries_attempted issuer_queries_succeeded "
    "search_pages_fetched search_rows_seen unique_object_ids_seen issuer_objects_in_universe "
    "bond_objects_in_universe bond_objects_outside_universe object_histories_fetched history_rows_seen "
    "agency_acra_events agency_expert_ra_events agency_nra_events agency_nkr_events unknown_agency_rows "
    "issuer_rating_events_candidate bond_rating_events_candidate semantic_reobservations_existing "
    "identity_unresolved_rows identity_ambiguous_rows semantic_collision_rows source_artifacts_candidate "
    "source_artifacts_existing source_artifacts_insert_candidate rating_events_existing rating_events_insert_candidate "
    "current_history_duplicates").split()
BODY_KEYS = {"schema", "contract", "required_schema_revision", "source_provider", "generated_at", "universe",
             "universe_sha256", "responses", "events", "counts", "http_requests"}
ENTRY_KEYS = {"action", "fields", "sha256", "byte_length", "content_type", "retrieved_at"}


def _time(value):
    return aware_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _parse_time(value):
    try:
        return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), "timestamp")
    except (ValueError, TypeError, AttributeError):
        raise RepositoryError("INVALID_BUNDLE_TIMESTAMP") from None


def _universe(session):
    with session.no_autoflush:
        inns = session.scalars(select(LegalIssuer.issuer_inn).where(
            LegalIssuer.resolution_state == "verified", LegalIssuer.issuer_inn.is_not(None)))
        isins = session.scalars(select(Bond.isin).where(Bond.isin.is_not(None)))
        return {"issuer_inns": sorted({canonical_inn(v) for v in inns}),
                "bond_isins": sorted({canonical_isin(v) for v in isins})}


def _readonly(session, adapter):
    if adapter is not None:
        adapter.readonly(session)
    else:
        session.execute(text("SET TRANSACTION READ ONLY"))
        if session.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise RepositoryError("READ_ONLY_NOT_VERIFIED")


def _schema(session):
    if tuple(session.scalars(text("SELECT version_num FROM alembic_version"))) != (REVISION,):
        raise RepositoryError("UNEXPECTED_SCHEMA_REVISION")
    if not {"legal_issuers", "bonds", "credit_risk_source_artifacts", "credit_rating_events", "credit_default_events"}.issubset(inspect(session.connection()).get_table_names()):
        raise RepositoryError("MISSING_LINEAGE_TABLE")


def _locks(session, adapter):
    if adapter is not None:
        adapter.locks(session)
    else:
        session.execute(text("SET LOCAL lock_timeout = '5s'"))
        for table, mode in (("legal_issuers", "SHARE"), ("bonds", "SHARE"),
                            ("credit_risk_source_artifacts", "SHARE ROW EXCLUSIVE"),
                            ("credit_rating_events", "SHARE ROW EXCLUSIVE")):
            session.execute(text(f"LOCK TABLE {table} IN {mode} MODE"))


def _totals(session):
    return {k: session.scalar(select(func.count()).select_from(m)) for k, m in
            (("artifacts", CreditRiskSourceArtifact), ("ratings", CreditRatingEvent), ("defaults", CreditDefaultEvent))}


def _artifact_input(response):
    return SourceArtifactInput(SourceProvider.CBR_RATINGS, SourceKind.RATING_REPOSITORY_RESPONSE,
        response.source_url, response.content_type, response.content, response.retrieved_at)


def _projection(candidates):
    events = []
    for c in candidates:
        values = asdict(c.value)
        values.update(event_date=c.value.event_date.isoformat(), publication_date=c.value.publication_date.isoformat())
        events.append({"response_index": c.response_index, "value": values})
    return events


def _counts(responses, candidates, measured, universe):
    result = dict.fromkeys(COUNT_KEYS, 0)
    result.update(measured)
    result.update(issuer_universe_count=len(universe["issuer_inns"]), bond_universe_count=len(universe["bond_isins"]))
    result["source_artifacts_candidate"] = len({(r.source_url, hashlib.sha256(r.content).hexdigest()) for r in responses})
    for c in candidates:
        result["agency_" + c.value.agency.value.lower() + "_events"] += 1
        result["issuer_rating_events_candidate" if c.value.target.value == "LEGAL_ISSUER" else "bond_rating_events_candidate"] += 1
    return result


def _reconcile(session, responses, candidates, counts):
    store, artifacts = CreditRiskEvidenceStore(session), {}
    for response in responses:
        key = (response.source_url, hashlib.sha256(response.content).hexdigest())
        if key in artifacts:
            continue
        row = session.scalar(select(CreditRiskSourceArtifact).where(
            CreditRiskSourceArtifact.source_provider == "CBR_RATINGS", CreditRiskSourceArtifact.source_url == key[0],
            CreditRiskSourceArtifact.content_sha256 == key[1]))
        if row is not None:
            store._assert_match(row, {"source_kind": "RATING_REPOSITORY_RESPONSE", "content_bytes": response.content,
                                     "content_type": response.content_type})
            counts["source_artifacts_existing"] += 1
        else:
            row = CreditRiskSourceArtifact(source_provider="CBR_RATINGS", source_kind="RATING_REPOSITORY_RESPONSE",
                                          source_url=key[0], content_sha256=key[1])
        artifacts[key] = row
    fingerprints = set()
    for c in candidates:
        response = responses[c.response_index]
        key = (response.source_url, hashlib.sha256(response.content).hexdigest())
        draft = store.preview_rating_event(artifacts[key], c.value)
        values = draft.to_values()
        if values["resolution_state"] != "RESOLVED":
            counts["identity_ambiguous_rows" if values["resolution_state"] == "AMBIGUOUS" else "identity_unresolved_rows"] += 1
        if draft.event_fingerprint in fingerprints:
            raise RepositoryError("DUPLICATE_SEMANTIC_CANDIDATE")
        fingerprints.add(draft.event_fingerprint)
        row = session.scalar(select(CreditRatingEvent).where(CreditRatingEvent.event_fingerprint == draft.event_fingerprint))
        if row is not None:
            store._assert_match(row, values, ignore={"artifact_id", "source_name_raw"})
            if row.artifact.source_provider != "CBR_RATINGS":
                raise CreditRiskEvidenceCollision("invalid semantic provenance")
            counts["rating_events_existing"] += 1
            counts["semantic_reobservations_existing"] += int(row.artifact_id != artifacts[key].id)
    counts["source_artifacts_insert_candidate"] = counts["source_artifacts_candidate"] - counts["source_artifacts_existing"]
    counts["rating_events_insert_candidate"] = len(candidates) - counts["rating_events_existing"]
    if counts["identity_unresolved_rows"] or counts["identity_ambiguous_rows"]:
        raise RepositoryError("IDENTITY_NOT_RESOLVED")


def _entry(response):
    return {"action": response.action, "fields": [list(p) for p in response.fields],
            "sha256": hashlib.sha256(response.content).hexdigest(), "byte_length": len(response.content),
            "content_type": response.content_type, "retrieved_at": _time(response.retrieved_at)}


def _publish(path, payload):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temporary = Path(f.name); f.write(payload); f.flush(); os.fsync(f.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_bundle(directory, manifest, responses):
    if directory.exists():
        raise RepositoryError("BUNDLE_ALREADY_EXISTS")
    directory.mkdir(parents=True); (directory / "responses").mkdir()
    written = set()
    for response in responses:
        digest = hashlib.sha256(response.content).hexdigest()
        if digest not in written:
            _publish(directory / "responses" / (digest + ".json"), response.content); written.add(digest)
    _publish(directory / "manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    load_bundle(directory)


def load_bundle(directory):
    try:
        path = directory / "manifest.json"
        if directory.is_symlink() or path.is_symlink() or (directory / "responses").is_symlink():
            raise RepositoryError("UNSAFE_BUNDLE_PATH")
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise RepositoryError("MANIFEST_LIMIT")
        manifest = json.loads(path.read_bytes(), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(RepositoryError("INVALID_MANIFEST")))
        if set(manifest) != BODY_KEYS | {"plan_hash"} or not isinstance(manifest["plan_hash"], str) or not SHA.fullmatch(manifest["plan_hash"]):
            raise RepositoryError("INVALID_BUNDLE_SCHEMA")
        body = {k: v for k, v in manifest.items() if k != "plan_hash"}
        if not hmac.compare_digest(canonical_json_sha256(body), manifest["plan_hash"]):
            raise RepositoryError("BUNDLE_HASH_MISMATCH")
        if (manifest["schema"], manifest["contract"], manifest["required_schema_revision"], manifest["source_provider"]) != (BUNDLE_SCHEMA, CONTRACT, REVISION, "CBR_RATINGS"):
            raise RepositoryError("INVALID_BUNDLE_CONTRACT")
        _parse_time(manifest["generated_at"])
        universe = manifest["universe"]
        if set(universe) != {"issuer_inns", "bond_isins"}:
            raise RepositoryError("INVALID_UNIVERSE")
        for key, validator in (("issuer_inns", canonical_inn), ("bond_isins", canonical_isin)):
            if universe[key] != sorted(set(universe[key])):
                raise RepositoryError("INVALID_UNIVERSE")
            for value in universe[key]:
                validator(value)
        if manifest["universe_sha256"] != canonical_json_sha256(universe):
            raise RepositoryError("UNIVERSE_HASH_MISMATCH")
        if type(manifest["http_requests"]) is not int or not 0 <= manifest["http_requests"] <= MAX_REQUESTS or not isinstance(manifest["responses"], list) or len(manifest["responses"]) > MAX_REQUESTS:
            raise RepositoryError("RESPONSE_COUNT_LIMIT")
        responses, total = [], 0
        for entry in manifest["responses"]:
            if set(entry) != ENTRY_KEYS or not isinstance(entry["sha256"], str) or not SHA.fullmatch(entry["sha256"]) or type(entry["byte_length"]) is not int or not 0 < entry["byte_length"] <= MAX_RESPONSE_BYTES:
                raise RepositoryError("INVALID_RESPONSE_ENTRY")
            file = directory / "responses" / (entry["sha256"] + ".json")
            total += entry["byte_length"]
            if file.is_symlink() or total > MAX_BUNDLE_BYTES or file.stat().st_size != entry["byte_length"]:
                raise RepositoryError("INVALID_RESPONSE_SIZE")
            payload = file.read_bytes()
            if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                raise RepositoryError("RESPONSE_HASH_MISMATCH")
            if not isinstance(entry["content_type"], str) or entry["content_type"].split(";", 1)[0].lower() != "application/json":
                raise RepositoryError("INVALID_CONTENT_TYPE")
            responses.append(SourceResponse(entry["action"], tuple(tuple(p) for p in entry["fields"]), payload,
                                            _parse_time(entry["retrieved_at"]), entry["content_type"]))
        candidates, measured, _ = derive(responses, universe)
        counts = _counts(responses, candidates, measured, universe)
        if _projection(candidates) != manifest["events"] or set(manifest["counts"]) != set(COUNT_KEYS) or any(type(v) is not int or v < 0 for v in manifest["counts"].values()):
            raise RepositoryError("DERIVED_EVENT_MISMATCH")
        db_keys = {"semantic_reobservations_existing", "source_artifacts_existing", "source_artifacts_insert_candidate",
                   "rating_events_existing", "rating_events_insert_candidate"}
        if any(manifest["counts"][k] != counts[k] for k in COUNT_KEYS if k not in db_keys):
            raise RepositoryError("DERIVED_COUNT_MISMATCH")
        return manifest, tuple(responses), candidates, counts
    except RepositoryError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise RepositoryError("INVALID_FROZEN_BUNDLE") from None


def _coverage(candidates, universe):
    groups = {}
    dates = []
    for c in candidates:
        v = c.value; key = (v.target.value, v.source_issuer_inn or v.source_bond_isin)
        groups[key] = groups.get(key, 0) + 1; dates.append(v.event_date.isoformat())
    issuers = sum(k[0] == "LEGAL_ISSUER" for k in groups); bonds = len(groups) - issuers
    depths = sorted(groups.values()); mid = len(depths) // 2
    median = None if not depths else Decimal(depths[mid]) if len(depths) % 2 else Decimal(depths[mid - 1] + depths[mid]) / 2
    def pct(n, d):
        return str(Decimal(n) * 100 / Decimal(d)) if d else None
    return {"issuer_coverage_count": issuers, "issuer_coverage_pct": pct(issuers, len(universe["issuer_inns"])),
        "bond_coverage_count": bonds, "bond_coverage_pct": pct(bonds, len(universe["bond_isins"])),
        "event_min_date": min(dates) if dates else None, "event_max_date": max(dates) if dates else None,
        "median_events_per_matched_target": str(median) if median is not None else None}


def _result(mode):
    return {"schema": SCHEMA, "mode": mode, "ready": False, "error_code": None, "counts": dict.fromkeys(COUNT_KEYS, 0),
        "coverage": _coverage([], {"issuer_inns": [], "bond_isins": []}), "plan_hash": None, "universe_sha256": None,
        "before_counts": None, "after_counts": None, "source_artifacts_inserted": 0, "rating_events_inserted": 0,
        "rating_events_reused": 0, "commit_completed": False, "commit_outcome_unknown": False,
        "reconciliation_required": False, "database_accessed": False, "transaction_read_only": False,
        "database_mutation_executed": False, "database_persistence": False, "network_accessed": False,
        "source_bundle_written": False, "http_requests": 0, "publication_precision": "DATE", "publication_at": None,
        "rating_scoring": False, "cross_agency_normalization": False, "default_ingestion": False,
        "issuer_to_bond_inheritance": False, "pit_ready": False, "production_actions": "NONE"}


def execute(mode, *, bundle_dir, database_url=None, expected_plan_hash=None, engine=None,
            client=None, clock=None, _adapter=None):
    output = _result(mode)
    session, owned_engine = None, engine is None
    committed, uncertain, writes = False, False, 0
    clock = clock or (lambda: datetime.now(timezone.utc))
    try:
        if mode not in ("plan", "preflight", "apply"):
            raise RepositoryError("INVALID_MODE")
        directory = Path(bundle_dir)
        frozen = None
        if mode != "plan":
            frozen = load_bundle(directory)
            if not isinstance(expected_plan_hash, str) or not SHA.fullmatch(expected_plan_hash) or not hmac.compare_digest(frozen[0]["plan_hash"], expected_plan_hash):
                raise RepositoryError("EXPECTED_PLAN_HASH_MISMATCH")
        elif directory.exists():
            raise RepositoryError("BUNDLE_ALREADY_EXISTS")
        if engine is None:
            if not isinstance(database_url, str) or make_url(database_url).get_backend_name() != "postgresql":
                raise RepositoryError("POSTGRESQL_REQUIRED")
            engine = create_engine(database_url)
        elif engine.dialect.name != "postgresql" and _adapter is None:
            raise RepositoryError("POSTGRESQL_REQUIRED")
        output["database_accessed"] = True
        session = Session(engine, autoflush=False); session.begin()
        if mode == "apply":
            _locks(session, _adapter)
        else:
            _readonly(session, _adapter); output["transaction_read_only"] = True
        _schema(session); universe = _universe(session)
        if mode == "plan":
            session.rollback(); session.close(); session = None
            source = client or CbrRatingsClient(clock=clock)
            output["network_accessed"] = True
            try:
                responses = source.collect(universe)
            finally:
                output["http_requests"] = source.requests
                if client is None:
                    source.close()
            candidates, measured, _ = derive(responses, universe)
            counts = _counts(responses, candidates, measured, universe)
            session = Session(engine, autoflush=False); session.begin(); _readonly(session, _adapter)
            _schema(session)
            if _universe(session) != universe:
                raise RepositoryError("UNIVERSE_CHANGED")
            _reconcile(session, responses, candidates, counts)
            body = {"schema": BUNDLE_SCHEMA, "contract": CONTRACT, "required_schema_revision": REVISION,
                "source_provider": "CBR_RATINGS", "generated_at": _time(clock()), "universe": universe,
                "universe_sha256": canonical_json_sha256(universe), "responses": [_entry(r) for r in responses],
                "events": _projection(candidates), "counts": counts, "http_requests": output["http_requests"]}
            manifest = dict(body, plan_hash=canonical_json_sha256(body))
            session.rollback(); write_bundle(directory, manifest, responses)
            output["source_bundle_written"] = True
        else:
            manifest, responses, candidates, counts = frozen
            if _universe(session) != manifest["universe"]:
                raise RepositoryError("UNIVERSE_CHANGED")
            _reconcile(session, responses, candidates, counts)
            if mode == "apply":
                before = _totals(session); output["before_counts"] = before
                store, artifacts = CreditRiskEvidenceStore(session), {}
                for response in responses:
                    key = (response.source_url, hashlib.sha256(response.content).hexdigest())
                    if key not in artifacts:
                        result = store.persist_source_artifact(_artifact_input(response)); artifacts[key] = result.row
                        output["source_artifacts_inserted"] += int(result.inserted)
                for c in candidates:
                    response = responses[c.response_index]
                    result = store.persist_rating_event(artifacts[(response.source_url, hashlib.sha256(response.content).hexdigest())], c.value)
                    output["rating_events_inserted"] += int(result.inserted); output["rating_events_reused"] += int(not result.inserted)
                check = _counts(responses, candidates, {}, manifest["universe"])
                _reconcile(session, responses, candidates, check)
                if check["source_artifacts_existing"] != len(artifacts) or check["rating_events_existing"] != len(candidates):
                    raise RepositoryError("READBACK_COVERAGE_MISMATCH")
                after = _totals(session)
                if after != {"artifacts": before["artifacts"] + output["source_artifacts_inserted"],
                             "ratings": before["ratings"] + output["rating_events_inserted"], "defaults": before["defaults"]}:
                    raise RepositoryError("READBACK_COUNT_MISMATCH")
                writes = output["source_artifacts_inserted"] + output["rating_events_inserted"]
                try:
                    session.commit()
                except Exception:
                    uncertain = True
                    raise RepositoryError("COMMIT_OUTCOME_UNKNOWN") from None
                committed = True; output["commit_completed"] = True
                session.close(); session = Session(engine, autoflush=False); session.begin(); _readonly(session, _adapter)
                _schema(session)
                if _universe(session) != manifest["universe"]:
                    raise RepositoryError("POST_COMMIT_UNIVERSE_CHANGED")
                _reconcile(session, responses, candidates, _counts(responses, candidates, {}, manifest["universe"]))
                if _totals(session) != after:
                    raise RepositoryError("POST_COMMIT_COUNT_MISMATCH")
                output["after_counts"] = after
        output.update(ready=True, counts=counts, coverage=_coverage(candidates, manifest["universe"]),
                      plan_hash=manifest["plan_hash"], universe_sha256=manifest["universe_sha256"])
    except Exception as e:
        output["ready"] = False
        output["error_code"] = e.code if isinstance(e, RepositoryError) else "SEMANTIC_COLLISION" if isinstance(e, CreditRiskEvidenceCollision) else "SANITIZED_RUNTIME_FAILURE"
    finally:
        for cleanup in ((lambda: session.rollback()) if session is not None else None,
                        (lambda: session.close()) if session is not None else None,
                        (lambda: engine.dispose()) if engine is not None and owned_engine else None):
            if cleanup:
                try:
                    cleanup()
                except Exception:
                    output["ready"] = False; output["error_code"] = output["error_code"] or "CLEANUP_FAILURE"
        output["commit_outcome_unknown"] = uncertain
        output["reconciliation_required"] = uncertain or (committed and not output["ready"])
        output["database_mutation_executed"] = None if uncertain else bool(committed and writes)
        output["database_persistence"] = output["database_mutation_executed"]
        output["production_actions"] = "CBR_RATING_REPOSITORY_APPLY_OUTCOME_UNKNOWN" if uncertain else "CBR_RATING_REPOSITORY_APPLY" if committed and writes else "NONE"
        if not committed and not uncertain:
            output["source_artifacts_inserted"] = output["rating_events_inserted"] = 0
    return output


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise RepositoryError("INVALID_ARGUMENTS")


def main(argv=None):
    mode = None
    try:
        parser = _ArgumentParser(add_help=False)
        parser.add_argument("--mode", choices=("plan", "preflight", "apply"), required=True)
        parser.add_argument("--database-url-env", required=True)
        parser.add_argument("--bundle-dir", required=True)
        parser.add_argument("--expected-plan-hash")
        parser.add_argument("--confirm-read-only", action="store_true")
        parser.add_argument("--confirm-write", action="store_true")
        args = parser.parse_args(argv); mode = args.mode
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.database_url_env):
            raise RepositoryError("INVALID_ARGUMENTS")
        if not (args.confirm_write and not args.confirm_read_only if mode == "apply" else args.confirm_read_only and not args.confirm_write):
            raise RepositoryError("INVALID_ARGUMENTS")
        if mode == "plan" and args.expected_plan_hash is not None:
            raise RepositoryError("INVALID_ARGUMENTS")
        if mode != "plan":
            if not args.expected_plan_hash or not SHA.fullmatch(args.expected_plan_hash):
                raise RepositoryError("INVALID_ARGUMENTS")
            frozen = load_bundle(Path(args.bundle_dir))
            if not hmac.compare_digest(frozen[0]["plan_hash"], args.expected_plan_hash):
                raise RepositoryError("EXPECTED_PLAN_HASH_MISMATCH")
        url = os.environ.get(args.database_url_env)
        if not url:
            raise RepositoryError("MISSING_DATABASE_ENV")
        output = execute(mode, bundle_dir=args.bundle_dir, database_url=url, expected_plan_hash=args.expected_plan_hash)
        code = 0 if output["ready"] else 1
    except RepositoryError as e:
        output = _result(mode); output["error_code"] = e.code
        code = 2 if e.code == "INVALID_ARGUMENTS" else 1
    except Exception:
        output = _result(mode); output["error_code"] = "SANITIZED_RUNTIME_FAILURE"; code = 1
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return code


if __name__ == "__main__":
    sys.exit(main())
