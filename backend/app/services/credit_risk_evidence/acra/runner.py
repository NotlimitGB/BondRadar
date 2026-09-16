from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
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
from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact, CreditDefaultEvent
from app.models.legal_issuer import LegalIssuer
from app.services.credit_risk_evidence.acra.client import AcraClient
from app.services.credit_risk_evidence.acra.contracts import (
    AcraError, BUNDLE_SCHEMA, CONTRACT, FetchedPage, MAX_BUNDLE_BYTES,
    MAX_BUNDLE_PAGES, MAX_MANIFEST_BYTES, REVISION, SCHEMA, issuer_url, universe_hash,
)
from app.services.credit_risk_evidence.acra.parser import parse_acra_issuer_page
from app.services.credit_risk_evidence.contracts import (
    PublicationPrecision, RatingEventInput, RatingTarget, SourceArtifactInput,
    SourceKind, SourceProvider, aware_utc, canonical_inn, canonical_isin,
    canonical_json_sha256,
)
from app.services.credit_risk_evidence.service import CreditRiskEvidenceStore


COUNT_KEYS = (
    "issuer_universe_count bond_universe_count listing_pages_fetched issuer_pages_discovered "
    "issuer_pages_fetched issuer_pages_parseable issuer_pages_failed pages_with_matching_issuer_inn "
    "pages_without_usable_inn issuer_rating_rows_seen issuer_rating_events_candidate "
    "issuer_rating_events_existing issuer_rating_events_insert_candidate issue_rating_rows_seen "
    "issue_rows_with_isin issue_rows_outside_universe issue_rows_missing_proven_scale "
    "bond_rating_events_candidate bond_rating_events_existing bond_rating_events_insert_candidate "
    "parser_duplicate_rows_removed identity_unresolved_rows identity_ambiguous_rows "
    "semantic_collision_rows source_artifacts_candidate source_artifacts_existing "
    "source_artifacts_insert_candidate"
).split()
BODY_KEYS = {
    "schema", "generated_at", "repository_contract", "required_schema_revision",
    "source_provider", "universe", "universe_sha256", "pages", "events", "counts",
}
PAGE_KEYS = {"canonical_url", "sha256", "retrieved_at", "content_type", "byte_length"}
EVENT_KEYS = {
    "page_sha256", "page_url", "target", "source_object_id", "source_issuer_inn", "source_bond_isin",
    "source_name_raw", "event_date", "rating_scale_raw", "rating_value_raw",
    "rating_outlook_raw", "rating_watch_raw", "rating_action_raw",
}
SHA = re.compile(r"[0-9a-f]{64}")


def _time(value):
    return aware_utc(value, "timestamp").isoformat().replace("+00:00", "Z")


def _parse_time(value):
    try:
        return aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")), "timestamp")
    except (ValueError, TypeError, AttributeError):
        raise AcraError("INVALID_BUNDLE_TIMESTAMP") from None


def _hash(value):
    return canonical_json_sha256(value)


def _universe(session):
    with session.no_autoflush:
        inns = session.execute(select(LegalIssuer.issuer_inn).where(
            LegalIssuer.resolution_state == "verified", LegalIssuer.issuer_inn.is_not(None)
        )).scalars()
        isins = session.execute(select(Bond.isin).where(Bond.isin.is_not(None))).scalars()
        return {
            "issuer_inns": sorted({canonical_inn(v) for v in inns}),
            "bond_isins": sorted({canonical_isin(v) for v in isins}),
        }


def _universe_digest(universe):
    return universe_hash(tuple(universe["issuer_inns"]), tuple(universe["bond_isins"]))


def _readonly(session, adapter=None):
    if adapter is not None:
        adapter.readonly(session)
        return
    session.execute(text("SET TRANSACTION READ ONLY"))
    if session.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
        raise AcraError("READ_ONLY_NOT_VERIFIED")


def _schema(session):
    revisions = tuple(session.execute(text("SELECT version_num FROM alembic_version")).scalars())
    if revisions != (REVISION,):
        raise AcraError("UNEXPECTED_SCHEMA_REVISION")
    tables = set(inspect(session.connection()).get_table_names())
    if not {"credit_risk_source_artifacts", "credit_rating_events", "credit_default_events", "legal_issuers", "bonds"}.issubset(tables):
        raise AcraError("MISSING_LINEAGE_TABLE")


def _locks(session, adapter=None):
    if adapter is not None:
        adapter.locks(session)
        return
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    for table, mode in (
        ("legal_issuers", "SHARE"), ("bonds", "SHARE"),
        ("credit_risk_source_artifacts", "SHARE ROW EXCLUSIVE"),
        ("credit_rating_events", "SHARE ROW EXCLUSIVE"),
    ):
        session.execute(text(f"LOCK TABLE {table} IN {mode} MODE"))


def _totals(session):
    return {
        "artifacts": session.scalar(select(func.count()).select_from(CreditRiskSourceArtifact)),
        "ratings": session.scalar(select(func.count()).select_from(CreditRatingEvent)),
        "defaults": session.scalar(select(func.count()).select_from(CreditDefaultEvent)),
    }


def _derive(pages, universe):
    counts = dict.fromkeys(COUNT_KEYS, 0)
    counts["issuer_universe_count"] = len(universe["issuer_inns"])
    counts["bond_universe_count"] = len(universe["bond_isins"])
    events = []
    for page in pages:
        counts["issuer_pages_fetched"] += 1
        parsed = parse_acra_issuer_page(page.content_bytes, page.canonical_url)
        usable_inn = None
        try:
            usable_inn = canonical_inn(parsed.inn_raw)
        except ValueError:
            counts["pages_without_usable_inn"] += 1
        matched = usable_inn in universe["issuer_inns"]
        if matched:
            counts["pages_with_matching_issuer_inn"] += 1
        counts["parser_duplicate_rows_removed"] += parsed.parser_duplicate_rows_removed
        # Structural failures may hide an in-universe issue identifier: do not silently discard them.
        structural = set(parsed.diagnostics) - {"INVALID_SOURCE_INN"}
        if structural:
            raise AcraError("UNSUPPORTED_ISSUER_PAGE_STRUCTURE")
        counts["issuer_pages_parseable"] += 1
        counts["issuer_rating_rows_seen"] += len(parsed.issuer_rating_rows)
        counts["issue_rating_rows_seen"] += len(parsed.issue_rating_rows)
        digest = hashlib.sha256(page.content_bytes).hexdigest()
        for row in parsed.issuer_rating_rows + parsed.issue_rating_rows:
            issue = row.source_section == "ISSUES"
            if issue:
                if row.isin is None:
                    continue
                counts["issue_rows_with_isin"] += 1
                if row.isin not in universe["bond_isins"]:
                    counts["issue_rows_outside_universe"] += 1
                    continue
                if not row.scale_raw:
                    counts["issue_rows_missing_proven_scale"] += 1
                    continue
            elif not matched:
                continue
            events.append({
                "page_sha256": digest,
                "page_url": page.canonical_url,
                "target": "BOND" if issue else "LEGAL_ISSUER",
                "source_object_id": f"ACRA_ISIN:{row.isin}" if issue else f"ACRA_ISSUER:{parsed.acra_issuer_id}",
                "source_issuer_inn": None if issue else usable_inn,
                "source_bond_isin": row.isin if issue else None,
                "source_name_raw": row.source_name_raw if issue else parsed.full_name_raw,
                "event_date": row.visible_date.isoformat(),
                "rating_scale_raw": row.scale_raw,
                "rating_value_raw": row.rating_value_raw,
                "rating_outlook_raw": row.outlook_raw,
                "rating_watch_raw": row.watch_raw,
                "rating_action_raw": row.action_raw,
            })
    unique = {json.dumps(e, sort_keys=True, separators=(",", ":")): e for e in events}
    counts["parser_duplicate_rows_removed"] += len(events) - len(unique)
    events = [unique[k] for k in sorted(unique)]
    counts["issuer_rating_events_candidate"] = sum(e["target"] == "LEGAL_ISSUER" for e in events)
    counts["bond_rating_events_candidate"] = sum(e["target"] == "BOND" for e in events)
    counts["source_artifacts_candidate"] = len({e["page_url"] for e in events})
    return events, counts


def _input(event):
    return RatingEventInput(
        agency=SourceProvider.ACRA, target=RatingTarget(event["target"]),
        source_object_id=event["source_object_id"], source_issuer_inn=event["source_issuer_inn"],
        source_bond_isin=event["source_bond_isin"], source_name_raw=event["source_name_raw"],
        event_date=date.fromisoformat(event["event_date"]), publication_precision=PublicationPrecision.DATE,
        publication_date=date.fromisoformat(event["event_date"]), publication_at=None,
        rating_scale_raw=event["rating_scale_raw"], rating_value_raw=event["rating_value_raw"],
        rating_outlook_raw=event["rating_outlook_raw"], rating_watch_raw=event["rating_watch_raw"],
        rating_action_raw=event["rating_action_raw"],
    )


def _artifact_input(page):
    return SourceArtifactInput(SourceProvider.ACRA, SourceKind.RATING_ISSUER_PAGE,
        page.canonical_url, page.content_type, page.content_bytes, page.retrieved_at)


def _reconcile(session, pages, events, counts):
    store = CreditRiskEvidenceStore(session)
    prepared, artifacts = [], {}
    for page in pages:
        digest = hashlib.sha256(page.content_bytes).hexdigest()
        if not any(e["page_url"] == page.canonical_url for e in events):
            continue
        row = session.execute(select(CreditRiskSourceArtifact).where(
            CreditRiskSourceArtifact.source_provider == "ACRA",
            CreditRiskSourceArtifact.source_url == page.canonical_url,
            CreditRiskSourceArtifact.content_sha256 == digest,
        )).scalar_one_or_none()
        if row is not None:
            store._assert_match(row, {
                "source_kind": "RATING_ISSUER_PAGE", "content_bytes": page.content_bytes,
                "content_type": page.content_type,
            })
            counts["source_artifacts_existing"] += 1
        else:
            row = CreditRiskSourceArtifact(source_provider="ACRA", source_url=page.canonical_url, content_sha256=digest)
        artifacts[page.canonical_url] = row
    for event in events:
        draft = store.preview_rating_event(artifacts[event["page_url"]], _input(event))
        values = draft.to_values()
        state = values["resolution_state"]
        if state != "RESOLVED":
            counts["identity_ambiguous_rows" if state == "AMBIGUOUS" else "identity_unresolved_rows"] += 1
        existing = session.execute(select(CreditRatingEvent).where(
            CreditRatingEvent.event_fingerprint == draft.event_fingerprint
        )).scalar_one_or_none()
        if existing is not None:
            store._assert_match(existing, values)
            counts["issuer_rating_events_existing" if event["target"] == "LEGAL_ISSUER" else "bond_rating_events_existing"] += 1
        prepared.append((event, draft))
    for prefix in ("issuer_rating_events", "bond_rating_events", "source_artifacts"):
        counts[f"{prefix}_insert_candidate"] = counts[f"{prefix}_candidate"] - counts[f"{prefix}_existing"]
    if counts["identity_unresolved_rows"] or counts["identity_ambiguous_rows"]:
        raise AcraError("IDENTITY_NOT_RESOLVED")
    return prepared


def _page_manifest(page):
    return {
        "canonical_url": page.canonical_url, "sha256": hashlib.sha256(page.content_bytes).hexdigest(),
        "retrieved_at": _time(page.retrieved_at), "content_type": page.content_type,
        "byte_length": len(page.content_bytes),
    }


def _publish(path: Path, data: bytes):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.link(temporary, path)  # atomic no-overwrite finalization
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_bundle(directory: Path, manifest, pages):
    if directory.exists():
        raise AcraError("BUNDLE_ALREADY_EXISTS")
    directory.mkdir(parents=True)
    (directory / "pages").mkdir()
    written = set()
    for page in pages:
        digest = hashlib.sha256(page.content_bytes).hexdigest()
        if digest not in written:
            _publish(directory / "pages" / f"{digest}.html", page.content_bytes)
            written.add(digest)
    _publish(directory / "manifest.json", json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())
    load_bundle(directory)


def _strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AcraError("DUPLICATE_MANIFEST_KEY")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def load_bundle(directory: Path):
    try:
        path = directory / "manifest.json"
        if directory.is_symlink() or path.is_symlink() or (directory / "pages").is_symlink():
            raise AcraError("UNSAFE_BUNDLE_PATH")
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            raise AcraError("OVERSIZED_MANIFEST")
        manifest = _strict_json(path.read_bytes())
        if set(manifest) != BODY_KEYS | {"plan_hash"}:
            raise AcraError("INVALID_BUNDLE_SCHEMA")
        body = {k: v for k, v in manifest.items() if k != "plan_hash"}
        if not isinstance(manifest["plan_hash"], str) or SHA.fullmatch(manifest["plan_hash"]) is None or not hmac.compare_digest(_hash(body), manifest["plan_hash"]):
            raise AcraError("BUNDLE_HASH_MISMATCH")
        if (manifest["schema"], manifest["repository_contract"], manifest["required_schema_revision"], manifest["source_provider"]) != (BUNDLE_SCHEMA, CONTRACT, REVISION, "ACRA"):
            raise AcraError("INVALID_BUNDLE_CONTRACT")
        _parse_time(manifest["generated_at"])
        universe = manifest["universe"]
        if set(universe) != {"issuer_inns", "bond_isins"}:
            raise AcraError("INVALID_UNIVERSE")
        for key, validator in (("issuer_inns", canonical_inn), ("bond_isins", canonical_isin)):
            if universe[key] != sorted(set(universe[key])):
                raise AcraError("NONCANONICAL_UNIVERSE")
            for value in universe[key]:
                validator(value)
        if manifest["universe_sha256"] != _universe_digest(universe):
            raise AcraError("UNIVERSE_HASH_MISMATCH")
        if not isinstance(manifest["pages"], list) or len(manifest["pages"]) > MAX_BUNDLE_PAGES:
            raise AcraError("BUNDLE_PAGE_CAP")
        if manifest["pages"] != sorted(manifest["pages"], key=lambda p: p["canonical_url"]):
            raise AcraError("NONCANONICAL_PAGE_ORDER")
        pages, urls, total = [], set(), 0
        for entry in manifest["pages"]:
            if set(entry) != PAGE_KEYS or issuer_url(entry["canonical_url"])[0] != entry["canonical_url"]:
                raise AcraError("INVALID_PAGE_ENTRY")
            if entry["canonical_url"] in urls or not isinstance(entry["sha256"], str) or SHA.fullmatch(entry["sha256"]) is None:
                raise AcraError("INVALID_PAGE_IDENTITY")
            urls.add(entry["canonical_url"])
            page_path = directory / "pages" / f"{entry['sha256']}.html"
            if page_path.is_symlink() or type(entry["byte_length"]) is not int or not 0 < entry["byte_length"] <= 4 * 1024 * 1024:
                raise AcraError("INVALID_PAGE_SIZE")
            total += entry["byte_length"]
            if total > MAX_BUNDLE_BYTES or page_path.stat().st_size != entry["byte_length"]:
                raise AcraError("BUNDLE_BYTE_CAP")
            content = page_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                raise AcraError("PAGE_HASH_MISMATCH")
            if not isinstance(entry["content_type"], str) or entry["content_type"].split(";", 1)[0].lower() not in ("text/html", "application/xhtml+xml"):
                raise AcraError("INVALID_PAGE_CONTENT_TYPE")
            pages.append(FetchedPage(entry["canonical_url"], content, entry["content_type"], _parse_time(entry["retrieved_at"])))
        if set(manifest["counts"]) != set(COUNT_KEYS) or any(type(v) is not int or v < 0 for v in manifest["counts"].values()):
            raise AcraError("INVALID_BUNDLE_COUNTS")
        if any(set(e) != EVENT_KEYS for e in manifest["events"]):
            raise AcraError("INVALID_EVENT_ENTRY")
        events, counts = _derive(pages, universe)
        if events != manifest["events"]:
            raise AcraError("DERIVED_EVENT_MISMATCH")
        for key in COUNT_KEYS:
            if key not in {"listing_pages_fetched", "issuer_pages_discovered"} and not key.endswith(("_existing", "_insert_candidate")) and counts[key] != manifest["counts"][key]:
                raise AcraError("DERIVED_COUNT_MISMATCH")
        return manifest, tuple(pages), events, counts
    except AcraError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        raise AcraError("INVALID_FROZEN_BUNDLE") from None


def _coverage(events, universe):
    issuer_ids = {e["source_issuer_inn"] for e in events if e["target"] == "LEGAL_ISSUER"}
    bonds = {e["source_bond_isin"] for e in events if e["target"] == "BOND"}
    issuer_dates = sorted(e["event_date"] for e in events if e["target"] == "LEGAL_ISSUER")
    depths = sorted(sum(e["source_issuer_inn"] == inn for e in events) for inn in issuer_ids)
    median = None if not depths else Decimal(depths[len(depths)//2]) if len(depths)%2 else Decimal(depths[len(depths)//2-1] + depths[len(depths)//2]) / 2
    def percent(n, d):
        return None if not d else str(Decimal(n) * 100 / Decimal(d))
    return {
        "bondradar_verified_issuers_with_inn": len(universe["issuer_inns"]),
        "acra_matched_issuer_count": len(issuer_ids),
        "issuer_coverage_pct": percent(len(issuer_ids), len(universe["issuer_inns"])),
        "bondradar_bonds_with_isin": len(universe["bond_isins"]),
        "acra_matched_bond_issue_count": len(bonds),
        "bond_issue_coverage_pct": percent(len(bonds), len(universe["bond_isins"])),
        "issuer_event_min_date": issuer_dates[0] if issuer_dates else None,
        "issuer_event_max_date": issuer_dates[-1] if issuer_dates else None,
        "issuer_event_count": len(issuer_dates),
        "issuer_median_events_per_matched_issuer": None if median is None else str(median),
    }


def _result(mode):
    return {
        "schema": SCHEMA, "mode": mode, "ready": False, "error_code": None,
        "counts": dict.fromkeys(COUNT_KEYS, 0), "coverage": _coverage([], {"issuer_inns": [], "bond_isins": []}),
        "plan_hash": None, "universe_sha256": None, "before_counts": None, "after_counts": None,
        "source_artifacts_inserted": 0, "credit_rating_events_inserted": 0,
        "already_materialized": 0, "commit_completed": False, "commit_outcome_unknown": False,
        "reconciliation_required": False, "database_accessed": False,
        "database_mutation_executed": False, "database_persistence": False,
        "transaction_read_only": False, "network_accessed": False, "source_bundle_written": False,
        "publication_precision": "DATE", "publication_at": None,
        "issuer_history_scope": "ACRA_ISSUER_PAGE_EXPOSED_HISTORY",
        "issue_history_scope": "ACRA_ISSUER_PAGE_EXPOSED_ROWS_ONLY", "issue_history_complete": False,
        "strict_historical_payload_availability_not_proven": True,
        "rating_scoring": False, "cross_agency_normalization": False,
        "default_ingestion": False, "issuer_to_bond_inheritance": False,
        "pit_ready": False, "production_actions": "NONE",
    }


def execute(mode, *, bundle_dir, database_url=None, expected_plan_hash=None,
            engine=None, client=None, clock=None, _adapter=None):
    output = _result(mode)
    session, own_engine = None, engine is None
    clock = clock or (lambda: datetime.now(timezone.utc))
    committed, uncertain, writes = False, False, 0
    try:
        if mode not in ("plan", "preflight", "apply"):
            raise AcraError("INVALID_MODE")
        directory = Path(bundle_dir)
        frozen = None
        if mode != "plan":
            frozen = load_bundle(directory)
            if not isinstance(expected_plan_hash, str) or SHA.fullmatch(expected_plan_hash) is None or not hmac.compare_digest(frozen[0]["plan_hash"], expected_plan_hash):
                raise AcraError("EXPECTED_PLAN_HASH_MISMATCH")
        # Offline replay above precedes environment access and engine construction.
        if engine is None:
            if not isinstance(database_url, str) or make_url(database_url).get_backend_name() != "postgresql":
                raise AcraError("POSTGRESQL_REQUIRED")
            engine = create_engine(database_url)
        elif engine.dialect.name != "postgresql" and _adapter is None:
            raise AcraError("POSTGRESQL_REQUIRED")
        output["database_accessed"] = True
        session = Session(engine, autoflush=False)
        session.begin()
        if mode != "apply":
            _readonly(session, _adapter)
            output["transaction_read_only"] = True
        else:
            _locks(session, _adapter)
        _schema(session)
        universe = _universe(session)
        digest = _universe_digest(universe)
        if mode == "plan":
            session.rollback()
            session.close()
            session = None
            owned_client = client is None
            source = client or AcraClient()
            output["network_accessed"] = True
            try:
                urls = source.discover()
                if len(urls) > MAX_BUNDLE_PAGES:
                    raise AcraError("BUNDLE_PAGE_CAP")
                pages, total = [], 0
                for url in urls:
                    page = source.fetch_issuer(url)
                    total += len(page.content_bytes)
                    if total > MAX_BUNDLE_BYTES:
                        raise AcraError("BUNDLE_BYTE_CAP")
                    pages.append(page)
                pages = tuple(sorted(pages, key=lambda p: p.canonical_url))
                events, counts = _derive(pages, universe)
                counts["listing_pages_fetched"] = source.list_pages
                counts["issuer_pages_discovered"] = len(urls)
            finally:
                if owned_client:
                    source.close()
            session = Session(engine, autoflush=False)
            session.begin()
            _readonly(session, _adapter)
            _schema(session)
            if _universe_digest(_universe(session)) != digest:
                raise AcraError("UNIVERSE_CHANGED")
            _reconcile(session, pages, events, counts)
            body = {
                "schema": BUNDLE_SCHEMA, "generated_at": _time(clock()),
                "repository_contract": CONTRACT, "required_schema_revision": REVISION,
                "source_provider": "ACRA", "universe": universe, "universe_sha256": digest,
                "pages": [_page_manifest(p) for p in pages], "events": events, "counts": counts,
            }
            manifest = dict(body, plan_hash=_hash(body))
            session.rollback()
            write_bundle(directory, manifest, pages)
            output["source_bundle_written"] = True
        else:
            manifest, pages, events, counts = frozen
            if digest != manifest["universe_sha256"]:
                raise AcraError("UNIVERSE_CHANGED")
            prepared = _reconcile(session, pages, events, counts)
            if mode == "apply":
                before = _totals(session)
                output["before_counts"] = before
                store = CreditRiskEvidenceStore(session)
                artifacts = {}
                for page in pages:
                    sha = hashlib.sha256(page.content_bytes).hexdigest()
                    if not any(e["page_url"] == page.canonical_url for e in events):
                        continue
                    result = store.persist_source_artifact(_artifact_input(page))
                    artifacts[page.canonical_url] = result.row
                    output["source_artifacts_inserted"] += int(result.inserted)
                for event, _ in prepared:
                    result = store.persist_rating_event(artifacts[event["page_url"]], _input(event))
                    output["credit_rating_events_inserted"] += int(result.inserted)
                    output["already_materialized"] += int(not result.inserted)
                readback_counts = dict.fromkeys(COUNT_KEYS, 0)
                _reconcile(session, pages, events, readback_counts)
                if readback_counts["source_artifacts_existing"] != len(artifacts) or readback_counts["issuer_rating_events_existing"] + readback_counts["bond_rating_events_existing"] != len(events):
                    raise AcraError("READBACK_COVERAGE_MISMATCH")
                after = _totals(session)
                if after != {
                    "artifacts": before["artifacts"] + output["source_artifacts_inserted"],
                    "ratings": before["ratings"] + output["credit_rating_events_inserted"],
                    "defaults": before["defaults"],
                }:
                    raise AcraError("READBACK_COUNT_MISMATCH")
                writes = output["source_artifacts_inserted"] + output["credit_rating_events_inserted"]
                try:
                    session.commit()
                except Exception:
                    uncertain = True
                    raise AcraError("COMMIT_OUTCOME_UNKNOWN") from None
                committed = True
                output["commit_completed"] = True
                session.close()
                session = Session(engine, autoflush=False)
                session.begin()
                _readonly(session, _adapter)
                _schema(session)
                if _universe_digest(_universe(session)) != digest:
                    raise AcraError("POST_COMMIT_UNIVERSE_CHANGED")
                _reconcile(session, pages, events, dict.fromkeys(COUNT_KEYS, 0))
                if _totals(session) != after:
                    raise AcraError("POST_COMMIT_COUNT_MISMATCH")
                output["after_counts"] = after
        output.update(ready=True, counts=counts, coverage=_coverage(events, universe),
                      plan_hash=manifest["plan_hash"], universe_sha256=digest)
    except Exception as exc:
        output["error_code"] = exc.code if isinstance(exc, AcraError) else "SANITIZED_RUNTIME_FAILURE"
        output["ready"] = False
    finally:
        for cleanup in (
            (lambda: session.rollback()) if session is not None else None,
            (lambda: session.close()) if session is not None else None,
            (lambda: engine.dispose()) if engine is not None and own_engine else None,
        ):
            if cleanup:
                try:
                    cleanup()
                except Exception:
                    output["ready"] = False
                    output["error_code"] = output["error_code"] or "CLEANUP_FAILURE"
        output["commit_outcome_unknown"] = uncertain
        output["reconciliation_required"] = uncertain or (committed and not output["ready"])
        output["database_mutation_executed"] = None if uncertain else bool(committed and writes)
        output["database_persistence"] = output["database_mutation_executed"]
        output["production_actions"] = (
            "ACRA_RATING_INGESTION_APPLY_OUTCOME_UNKNOWN" if uncertain else
            "ACRA_RATING_INGESTION_APPLY" if committed and writes else "NONE"
        )
        if not committed and not uncertain:
            output["source_artifacts_inserted"] = output["credit_rating_events_inserted"] = 0
    return output


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise AcraError("INVALID_ARGUMENTS")


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
        args = parser.parse_args(argv)
        mode = args.mode
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", args.database_url_env):
            raise AcraError("INVALID_ARGUMENTS")
        if args.mode == "apply":
            valid = args.confirm_write and not args.confirm_read_only
        else:
            valid = args.confirm_read_only and not args.confirm_write
        if not valid or (args.mode == "plan" and args.expected_plan_hash is not None):
            raise AcraError("INVALID_ARGUMENTS")
        if args.mode != "plan":
            if not args.expected_plan_hash or SHA.fullmatch(args.expected_plan_hash) is None:
                raise AcraError("INVALID_ARGUMENTS")
            manifest, *_ = load_bundle(Path(args.bundle_dir))
            if not hmac.compare_digest(manifest["plan_hash"], args.expected_plan_hash):
                raise AcraError("EXPECTED_PLAN_HASH_MISMATCH")
        value = os.environ.get(args.database_url_env)
        if not value:
            raise AcraError("MISSING_DATABASE_ENV")
        result = execute(args.mode, bundle_dir=args.bundle_dir, database_url=value,
                         expected_plan_hash=args.expected_plan_hash)
        exit_code = 0 if result["ready"] else 1
    except AcraError as exc:
        result = _result(mode)
        result["error_code"] = exc.code
        exit_code = 2 if exc.code == "INVALID_ARGUMENTS" else 1
    except Exception:
        result, exit_code = _result(mode), 1
        result["error_code"] = "SANITIZED_RUNTIME_FAILURE"
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
