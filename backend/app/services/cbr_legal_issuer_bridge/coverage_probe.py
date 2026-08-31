from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, create_engine, func, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models.bond_legal_issuer_profile import (
    LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
    LEGAL_ISSUER_MAPPING_SOURCE,
    BondLegalIssuerProfile,
)
from app.models.legal_issuer import LegalIssuer
from app.services.cbr_bank_reporting import CbrBankForm, CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.client import EXPECTED_CURRENT
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
)

from .contracts import CbrBridgeState, CbrLegalIssuerBridgeSnapshot, utc_datetime
from .service import CbrLegalIssuerBridgeService, LegalIssuerInnResolver


SCHEMA_VERSION = "bondradar.cbr_legal_issuer_production_coverage_probe.v1"
PIT_STATUS = "CURRENT_ONLY"
_ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,127}")
_FIXTURE_ROOT = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "cbr_bank_reporting"


class CoverageProbeError(RuntimeError):
    """A fixed-category error whose details must never enter CLI output."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError("invalid arguments")


def _iso(value: datetime) -> str:
    normalized = utc_datetime(value, field_name="timestamp")
    return normalized.isoformat().replace("+00:00", "Z")


def _id_set_sha256(values: Sequence[int] | set[int]) -> str:
    canonical = sorted({int(value) for value in values})
    if any(value <= 0 for value in canonical):
        raise ValueError("identifier must be positive")
    payload = json.dumps(canonical, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def load_task251_fixture_regns(
    report_date: date,
    *,
    retrieved_at: datetime,
    fixture_root: Path | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
) -> tuple[tuple[str, ...], dict[str, Any]]:
    """Verify and parse the approved Task251 artifacts, returning value-row REGNs."""

    observed = utc_datetime(retrieved_at, field_name="retrieved_at")
    root = fixture_root or _FIXTURE_ROOT
    artifacts: list[CbrBankArtifact] = []
    for form in CbrBankForm:
        expected = EXPECTED_CURRENT.get((form, report_date))
        if expected is None:
            raise CoverageProbeError("unsupported Task251 fixture date")
        filename = f"{form.short_code}-{report_date:%Y%m%d}.rar"
        content = (root / filename).read_bytes()
        content_hash = hashlib.sha256(content).hexdigest()
        expected_size, expected_hash = expected
        if len(content) != expected_size or content_hash != expected_hash:
            raise CoverageProbeError("Task251 fixture identity mismatch")
        reference = CbrArtifactReference(
            form=form,
            source_href=filename,
            source_url=f"https://www.cbr.ru/{filename}",
            artifact_filename=filename,
            report_date=report_date,
            discovered_at=observed,
        )
        artifacts.append(
            CbrBankArtifact(
                reference=reference,
                content=content,
                content_sha256=content_hash,
                compressed_size=len(content),
                content_type="application/vnd.rar",
                retrieved_at=observed,
            )
        )

    snapshot = (bundle_service or CbrBankRegulatoryBundleService()).build_snapshot(
        report_date=report_date,
        artifacts=tuple(artifacts),
    )
    subjects = tuple(
        sorted(
            {regn for form_result in snapshot.forms for regn in form_result.subjects},
            key=int,
        )
    )
    if not subjects:
        raise CoverageProbeError("Task251 fixture subject set is empty")
    return subjects, {
        "task251_form_count": len(snapshot.forms),
        "task251_value_subject_union_count": len(subjects),
        "task251_records_by_form": dict(snapshot.records_by_form),
        "task251_subjects_by_form": dict(snapshot.subjects_by_form),
        "task251_subject_set_hashes": dict(snapshot.subject_set_hashes),
    }


def enforce_postgresql_read_only(session: Session) -> None:
    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        raise CoverageProbeError("PostgreSQL is required")
    session.execute(text("SET TRANSACTION READ ONLY"))
    read_only = session.execute(text("SHOW transaction_read_only")).scalar_one()
    if read_only != "on":
        raise CoverageProbeError("read-only transaction verification failed")


def _legal_issuer_inventory(session: Session) -> dict[str, int]:
    row = session.execute(
        select(
            func.count(LegalIssuer.id).label("legal_issuer_total"),
            func.sum(
                case((LegalIssuer.resolution_state == "verified", 1), else_=0)
            ).label("legal_issuer_verified"),
            func.sum(case((LegalIssuer.issuer_inn.is_not(None), 1), else_=0)).label(
                "legal_issuer_with_inn"
            ),
            func.sum(case((LegalIssuer.issuer_inn.is_(None), 1), else_=0)).label(
                "legal_issuer_without_inn"
            ),
        )
    ).one()
    values = row._mapping
    return {key: int(values[key] or 0) for key in values}


def _matched_bond_profiles(
    session: Session, source_issuer_ids: set[str]
) -> tuple[tuple[int, str], ...]:
    if not source_issuer_ids:
        return ()
    rows = session.execute(
        select(
            BondLegalIssuerProfile.bond_id,
            BondLegalIssuerProfile.source_issuer_id,
        )
        .where(
            BondLegalIssuerProfile.contract_version
            == LEGAL_ISSUER_MAPPING_CONTRACT_VERSION,
            BondLegalIssuerProfile.mapping_state == "verified",
            BondLegalIssuerProfile.mapping_source == LEGAL_ISSUER_MAPPING_SOURCE,
            BondLegalIssuerProfile.source_issuer_id.in_(source_issuer_ids),
        )
        .order_by(BondLegalIssuerProfile.bond_id)
    ).all()
    return tuple((int(row.bond_id), str(row.source_issuer_id)) for row in rows)


def _state_projection(snapshot: CbrLegalIssuerBridgeSnapshot) -> dict[str, int]:
    counts = {state.value: 0 for state in CbrBridgeState}
    for result in snapshot.bridge_results:
        counts[result.bridge_state.value] += 1
    return counts


def build_coverage_report(
    session: Session,
    *,
    report_date: date,
    retrieved_at: datetime,
    generated_at: datetime,
    bridge_service: CbrLegalIssuerBridgeService | None = None,
    fixture_loader: Callable[..., tuple[tuple[str, ...], dict[str, Any]]] = load_task251_fixture_regns,
    fixture_root: Path | None = None,
) -> dict[str, Any]:
    """Run the guarded current-only coverage audit and return aggregate data only."""

    enforce_postgresql_read_only(session)
    observed = utc_datetime(retrieved_at, field_name="retrieved_at")
    generated = utc_datetime(generated_at, field_name="generated_at")
    regns, task251 = fixture_loader(
        report_date,
        retrieved_at=observed,
        fixture_root=fixture_root,
    )
    bridge = (bridge_service or CbrLegalIssuerBridgeService()).bridge_regns(
        regns,
        retrieved_at=observed,
        legal_issuer_resolver=LegalIssuerInnResolver(session),
    )
    state_counts = _state_projection(bridge)
    verified = tuple(
        result
        for result in bridge.bridge_results
        if result.bridge_state == CbrBridgeState.VERIFIED
    )
    matched_issuer_ids = {
        int(result.legal_issuer_id)
        for result in verified
        if result.legal_issuer_id is not None
    }
    source_issuer_ids = {
        str(result.legal_issuer_source_issuer_id)
        for result in verified
        if result.legal_issuer_source_issuer_id is not None
    }
    profile_rows = _matched_bond_profiles(session, source_issuer_ids)
    bond_ids = {row[0] for row in profile_rows}
    source_identity_states = {
        CbrBridgeState.CBR_REGN_NOT_FOUND,
        CbrBridgeState.CBR_REGN_AMBIGUOUS,
        CbrBridgeState.CBR_OGRN_MISSING,
        CbrBridgeState.CBR_OGRN_CONFLICT,
        CbrBridgeState.FINORG_NOT_FOUND,
        CbrBridgeState.FINORG_SOURCE_ERROR,
        CbrBridgeState.FINORG_OGRN_MISMATCH,
        CbrBridgeState.FINORG_INN_MISSING,
        CbrBridgeState.FINORG_INN_INVALID,
        CbrBridgeState.FINORG_INN_CONFLICT,
    }
    identity_quality_states = {
        CbrBridgeState.LEGAL_ISSUER_INN_AMBIGUOUS,
        CbrBridgeState.LEGAL_ISSUER_NOT_VERIFIED,
    }

    return {
        "schema": SCHEMA_VERSION,
        "status": "complete",
        "task251_report_date": report_date.isoformat(),
        "cbr_registry_as_of": bridge.registry_as_of.isoformat(),
        "finorg_last_update": _iso(bridge.finorg_last_update),
        "source_retrieved_at": _iso(bridge.retrieved_at),
        "generated_at": _iso(generated),
        **task251,
        "requested_regn_count": len(bridge.requested_regns),
        "source_resolved_regn_count": sum(
            result.ogrn is not None and result.inn is not None
            for result in bridge.bridge_results
        ),
        "legalissuer_verified_regn_count": len(verified),
        "task252_state_counts": state_counts,
        "source_identity_failure_count": sum(
            result.bridge_state in source_identity_states
            for result in bridge.bridge_results
        ),
        "legal_issuer_not_found_count": state_counts[
            CbrBridgeState.LEGAL_ISSUER_NOT_FOUND.value
        ],
        "identity_quality_blocker_count": sum(
            result.bridge_state in identity_quality_states
            for result in bridge.bridge_results
        ),
        **_legal_issuer_inventory(session),
        "matched_legal_issuer_count": len(matched_issuer_ids),
        "matched_bond_profile_row_count": len(profile_rows),
        "matched_bond_count": len(bond_ids),
        "regn_set_hash": bridge.regn_set_hash,
        "source_resolved_regn_set_hash": bridge.source_resolved_regn_set_hash,
        "legalissuer_verified_regn_set_hash": bridge.legal_issuer_verified_regn_set_hash,
        "matched_legal_issuer_id_set_hash": _id_set_sha256(matched_issuer_ids),
        "matched_bond_id_set_hash": _id_set_sha256(bond_ids),
        "pit_status": PIT_STATUS,
        "historical_backcast_allowed": False,
        "transaction_read_only": True,
        "database_read_only": True,
        "database_mutation_executed": False,
        "database_persistence": False,
        "fuzzy_matching": False,
        "title_identity": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
    }


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="cbr-legal-issuer-production-coverage-probe")
    parser.add_argument("--task251-fixture-report-date", required=True)
    parser.add_argument("--database-url-env", required=True)
    parser.add_argument("--confirm-read-only", action="store_true", required=True)
    return parser


def _failure(error_code: str) -> dict[str, str]:
    return {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "error_code": error_code,
    }


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    engine_factory: Callable[..., Engine] | None = None,
    bridge_service_factory: Callable[[], CbrLegalIssuerBridgeService] | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
        try:
            report_date = date.fromisoformat(args.task251_fixture_report_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid arguments") from exc
        if not _ENV_NAME.fullmatch(args.database_url_env):
            raise ValueError("invalid arguments")
    except (ValueError, SystemExit):
        print(json.dumps(_failure("INVALID_ARGUMENTS"), sort_keys=True, separators=(",", ":")))
        return 2

    environment = os.environ if environ is None else environ
    database_url = environment.get(args.database_url_env)
    if not database_url:
        print(json.dumps(_failure("DATABASE_CONFIGURATION_UNAVAILABLE"), sort_keys=True, separators=(",", ":")))
        return 1
    try:
        if make_url(database_url).get_backend_name() != "postgresql":
            raise CoverageProbeError("PostgreSQL is required")
    except Exception:
        print(json.dumps(_failure("DATABASE_CONFIGURATION_INVALID"), sort_keys=True, separators=(",", ":")))
        return 1

    engine: Engine | None = None
    session: Session | None = None
    try:
        engine = (engine_factory or create_engine)(database_url, pool_pre_ping=True)
        factory = sessionmaker(
            bind=engine,
            autoflush=False,
            expire_on_commit=False,
        )
        session = factory()
        retrieved_at = datetime.now(timezone.utc)
        report = build_coverage_report(
            session,
            report_date=report_date,
            retrieved_at=retrieved_at,
            generated_at=datetime.now(timezone.utc),
            bridge_service=(bridge_service_factory or CbrLegalIssuerBridgeService)(),
        )
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception:
        print(json.dumps(_failure("COVERAGE_PROBE_FAILED"), sort_keys=True, separators=(",", ":")))
        return 1
    finally:
        if session is not None:
            with suppress(Exception):
                session.rollback()
            with suppress(Exception):
                session.close()
        if engine is not None:
            with suppress(Exception):
                engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
