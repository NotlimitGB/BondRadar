from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
for import_root in (ROOT, BACKEND_DIR):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from backend.app.services.cbr_bank_reporting import (  # noqa: E402
    CbrBankForm,
    CbrBankRegulatoryBundleService,
)
from backend.app.services.cbr_bank_reporting.contracts import (  # noqa: E402
    CbrArtifactReference,
    CbrBankArtifact,
)
from backend.app.services.cbr_legal_issuer_bridge import (  # noqa: E402
    PROBE_SCHEMA_VERSION,
    CbrBridgeError,
    CbrBridgeState,
    CbrLegalIssuerBridgeService,
)


FIXTURE_ROOT = ROOT / "backend" / "tests" / "fixtures" / "cbr_bank_reporting"


def build_task251_fixture_snapshot(report_date: date):
    artifacts = []
    observed = datetime.combine(report_date, datetime.min.time(), tzinfo=timezone.utc)
    for form in CbrBankForm:
        filename = f"{form.short_code}-{report_date.strftime('%Y%m%d')}.rar"
        content = (FIXTURE_ROOT / filename).read_bytes()
        reference = CbrArtifactReference(
            form=form,
            source_href=f"/vfs/credit/forms/{filename}",
            source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
            artifact_filename=filename,
            report_date=report_date,
            discovered_at=observed,
        )
        artifacts.append(
            CbrBankArtifact(
                reference=reference,
                content=content,
                content_sha256=hashlib.sha256(content).hexdigest(),
                compressed_size=len(content),
                content_type="application/octet-stream",
                retrieved_at=observed,
            )
        )
    return CbrBankRegulatoryBundleService().build_snapshot(
        report_date=report_date,
        artifacts=tuple(artifacts),
    )


def build_probe_report(snapshot, *, report_date: date, logical_calls: dict[str, int]) -> dict[str, Any]:
    results = snapshot.bridge_results
    states = dict(snapshot.state_counts)
    source_resolved = [item for item in results if item.ogrn and item.inn]
    finorg_found_ogrns = {item.ogrn for item in snapshot.finorg_records}
    regn_missing = states.get(CbrBridgeState.CBR_REGN_NOT_FOUND.value, 0)
    finorg_missing = states.get(CbrBridgeState.FINORG_NOT_FOUND.value, 0)
    inn_missing = states.get(CbrBridgeState.FINORG_INN_MISSING.value, 0)
    registry_conflicts = sum(
        states.get(code.value, 0)
        for code in (
            CbrBridgeState.CBR_REGN_AMBIGUOUS,
            CbrBridgeState.CBR_OGRN_CONFLICT,
        )
    )
    finorg_conflicts = states.get(CbrBridgeState.FINORG_INN_CONFLICT.value, 0)
    source_errors = states.get(CbrBridgeState.FINORG_SOURCE_ERROR.value, 0)
    return {
        "schema": PROBE_SCHEMA_VERSION,
        "status": "complete",
        "task251_fixture_report_date": report_date.isoformat(),
        "task251_union_regns": len(snapshot.requested_regns),
        "fullcolist_rows": len(snapshot.registry_records),
        "regn_found_in_fullcolist": len(snapshot.requested_regns) - regn_missing,
        "regn_missing_in_fullcolist": regn_missing,
        "unique_ogrns": len({item.ogrn for item in results if item.ogrn}),
        "finorg_identities_found": len(finorg_found_ogrns),
        "finorg_identities_missing": finorg_missing,
        "finorg_inn_present": len(source_resolved),
        "finorg_inn_missing": inn_missing,
        "regn_to_inn_resolved": len(source_resolved),
        "regn_to_inn_unresolved": len(results) - len(source_resolved),
        "registry_conflicts": registry_conflicts,
        "finorg_conflicts": finorg_conflicts,
        "source_errors": source_errors,
        "state_counts": states,
        "regn_set_hash": snapshot.regn_set_hash,
        "source_resolved_regn_set_hash": snapshot.source_resolved_regn_set_hash,
        "registry_as_of": snapshot.registry_as_of.isoformat(),
        "finorg_last_update": snapshot.finorg_last_update.isoformat(),
        "bridge_retrieved_at": snapshot.retrieved_at.isoformat(),
        "pit_status": snapshot.pit_status,
        "historical_backcast_allowed": snapshot.historical_backcast_allowed,
        "legal_issuer_evaluation_performed": snapshot.legal_issuer_evaluation_performed,
        "logical_calls": dict(sorted(logical_calls.items())),
        "read_only": True,
        "database_accessed": False,
        "database_persistence": False,
        "database_mutation_executed": False,
        "fuzzy_matching": False,
        "title_matching_used_for_identity": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
    }


def run_probe(
    *,
    report_date: date,
    task251_snapshot=None,
    bridge_service: CbrLegalIssuerBridgeService | None = None,
    retrieved_at: datetime | None = None,
) -> dict[str, Any]:
    source_snapshot = task251_snapshot or build_task251_fixture_snapshot(report_date)
    regns = tuple(
        sorted(
            {regn for form_result in source_snapshot.forms for regn in form_result.subjects},
            key=int,
        )
    )
    service = bridge_service or CbrLegalIssuerBridgeService()
    identity_snapshot = service.bridge_regns(
        regns,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
        legal_issuer_resolver=None,
    )
    transport = service.fullcolist_client.transport
    calls = getattr(transport, "logical_calls", {})
    return build_probe_report(identity_snapshot, report_date=report_date, logical_calls=calls)


def _error_report(code: str) -> dict[str, Any]:
    return {
        "schema": PROBE_SCHEMA_VERSION,
        "status": "failed",
        "error_code": code,
        "read_only": True,
        "database_accessed": False,
        "database_persistence": False,
        "database_mutation_executed": False,
        "fuzzy_matching": False,
        "title_matching_used_for_identity": False,
        "normalization": False,
        "scoring": False,
        "production_actions": "NONE",
    }


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps(_error_report("INVALID_ARGUMENTS"), sort_keys=True))
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser()
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument("--task251-fixture-report-date", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.source_only:
            raise ValueError("source-only mode is required")
        report_date = date.fromisoformat(args.task251_fixture_report_date)
    except (TypeError, ValueError):
        print(json.dumps(_error_report("INVALID_ARGUMENTS"), sort_keys=True))
        return 2
    try:
        report = run_probe(report_date=report_date)
    except CbrBridgeError as exc:
        print(json.dumps(_error_report(exc.code.value), sort_keys=True))
        return 1
    except Exception:
        print(json.dumps(_error_report("INTERNAL_SOURCE_FAILURE"), sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
