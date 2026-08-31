from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from typing import Any

from backend.app.services.cbr_bank_reporting import (
    CbrBankForm,
    CbrBankRegulatoryBundleService,
    CbrSourceError,
)
from backend.app.services.cbr_bank_reporting.contracts import PROBE_SCHEMA_VERSION


def build_probe_report(snapshot: Any, *, generated_at: datetime) -> dict[str, Any]:
    forms = []
    for result in snapshot.forms:
        forms.append(
            {
                "form": result.form.value,
                "artifact_filename": result.artifact.reference.artifact_filename,
                "artifact_sha256": result.artifact.content_sha256,
                "compressed_bytes": result.artifact.compressed_size,
                "form_schema_fingerprint": result.form_schema_fingerprint,
                "record_count": len(result.records),
                "subject_count": len(result.subjects),
                "source_codes": list(result.source_codes),
            }
        )
    return {
        "schema": PROBE_SCHEMA_VERSION,
        "status": "complete",
        "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
        "report_date": snapshot.report_date.isoformat(),
        "forms": forms,
        "records_by_form": dict(snapshot.records_by_form),
        "subjects_by_form": dict(snapshot.subjects_by_form),
        "subject_set_hashes": dict(snapshot.subject_set_hashes),
        "cross_form_overlap": dict(snapshot.cross_form_overlap),
        "exclusive_membership_counts": dict(snapshot.exclusive_membership_counts),
        "pit_state": snapshot.pit_state,
        "published_at_known": snapshot.published_at is not None,
        "warnings": list(snapshot.warnings),
        "read_only": True,
        "database_accessed": False,
        "database_persistence_executed": False,
        "normalization_executed": False,
        "scoring_executed": False,
        "identity_join_executed": False,
        "production_action_executed": False,
    }


def run_probe(
    *,
    report_date: date,
    forms: tuple[CbrBankForm, ...],
    service: CbrBankRegulatoryBundleService | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    source = service or CbrBankRegulatoryBundleService()
    snapshot = source.fetch_bundle(report_date=report_date, forms=forms)
    return build_probe_report(
        snapshot, generated_at=generated_at or datetime.now(timezone.utc)
    )


def _error_report(code: str) -> dict[str, Any]:
    return {
        "schema": PROBE_SCHEMA_VERSION,
        "status": "failed",
        "error_code": code,
        "read_only": True,
        "database_accessed": False,
        "database_persistence_executed": False,
        "normalization_executed": False,
        "scoring_executed": False,
        "identity_join_executed": False,
        "production_action_executed": False,
    }


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print(json.dumps(_error_report("INVALID_ARGUMENTS"), sort_keys=True))
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _SanitizedArgumentParser(add_help=True)
    parser.add_argument("--report-date", required=True)
    parser.add_argument("--forms", required=True, nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report_date = date.fromisoformat(args.report_date)
        forms = tuple(CbrBankForm.parse(value) for value in args.forms)
        if not forms or len(forms) > 4 or len(set(forms)) != len(forms):
            raise ValueError("forms must be unique")
    except (TypeError, ValueError):
        print(json.dumps(_error_report("INVALID_ARGUMENTS"), sort_keys=True))
        return 2
    try:
        report = run_probe(report_date=report_date, forms=forms)
    except CbrSourceError as exc:
        print(json.dumps(_error_report(exc.code.value), sort_keys=True))
        return 1
    except Exception:
        print(json.dumps(_error_report("INTERNAL_SOURCE_FAILURE"), sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
