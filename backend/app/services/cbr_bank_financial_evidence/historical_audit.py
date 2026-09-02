from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.services.cbr_bank_financial_evidence.fingerprints import (
    ordered_fingerprints_sha256,
)
from app.services.cbr_bank_financial_evidence.lexical import (
    extract_exact_form_evidence,
)
from app.services.cbr_bank_reporting.bundle import (
    CbrBankRegulatoryBundleService,
    subject_set_sha256,
)
from app.services.cbr_bank_reporting.client import CbrBankRegulatoryClient
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
)


SCHEMA_VERSION = "bondradar.cbr_bank_historical_audit.v1"
MAX_PROBE_DATES = 12
REQUIRED_FORMS = tuple(CbrBankForm)

_INVALID_CONTENT_CODES = frozenset(
    {
        CbrSourceStatus.INVALID_CONTENT,
        CbrSourceStatus.ARTIFACT_TOO_LARGE,
        CbrSourceStatus.ARTIFACT_MUTATED,
        CbrSourceStatus.INVALID_ARCHIVE,
        CbrSourceStatus.ARCHIVE_PATH_TRAVERSAL,
        CbrSourceStatus.ARCHIVE_DUPLICATE_MEMBER,
        CbrSourceStatus.ARCHIVE_TOO_MANY_MEMBERS,
        CbrSourceStatus.ARCHIVE_MEMBER_TOO_LARGE,
        CbrSourceStatus.ARCHIVE_TOTAL_TOO_LARGE,
        CbrSourceStatus.UNSUPPORTED_ARCHIVE_FEATURE,
        CbrSourceStatus.INVALID_DBF,
        CbrSourceStatus.VALUE_PARSE_ERROR,
    }
)


class HistoricalAuditError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class HistoricalCatalog:
    report: dict[str, Any]
    references_by_date: tuple[
        tuple[date, tuple[CbrArtifactReference, ...]], ...
    ]


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise HistoricalAuditError("INVALID_ARGUMENTS")


def _iso_datetime(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise HistoricalAuditError("INVALID_ARGUMENTS")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise HistoricalAuditError("INVALID_ARGUMENTS") from exc


def _date_text(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _safety_projection() -> dict[str, Any]:
    return {
        "database_accessed": False,
        "database_mutation_executed": False,
        "database_persistence": False,
        "normalization": False,
        "scoring": False,
        "publication_availability": "UNKNOWN",
        "publication_inference": False,
        "production_actions": "NONE",
        "backfill_ready": False,
    }


def build_catalog(
    references: Sequence[CbrArtifactReference],
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> HistoricalCatalog:
    if from_date is not None and to_date is not None and from_date > to_date:
        raise HistoricalAuditError("INVALID_ARGUMENTS")

    unique: dict[tuple[date, CbrBankForm], CbrArtifactReference] = {}
    for reference in references:
        key = (reference.report_date, reference.form)
        if key in unique:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT,
                "duplicate or conflicting artifact references",
            )
        unique[key] = reference

    filtered = tuple(
        reference
        for key, reference in sorted(
            unique.items(), key=lambda item: (item[0][0], item[0][1].value)
        )
        if (from_date is None or reference.report_date >= from_date)
        and (to_date is None or reference.report_date <= to_date)
    )
    grouped: dict[date, list[CbrArtifactReference]] = {}
    for reference in filtered:
        grouped.setdefault(reference.report_date, []).append(reference)

    complete_dates: list[date] = []
    incomplete: list[dict[str, Any]] = []
    ordered_groups: list[tuple[date, tuple[CbrArtifactReference, ...]]] = []
    required = set(REQUIRED_FORMS)
    for report_date, items in sorted(grouped.items()):
        ordered = tuple(sorted(items, key=lambda item: item.form.value))
        ordered_groups.append((report_date, ordered))
        present = {item.form for item in ordered}
        missing = tuple(form for form in REQUIRED_FORMS if form not in present)
        if not missing and {item.form for item in ordered} == required:
            complete_dates.append(report_date)
        else:
            incomplete.append(
                {
                    "report_date": report_date.isoformat(),
                    "missing_forms": [form.value for form in missing],
                }
            )

    dates = tuple(grouped)
    references_by_form = {
        form.value: sum(reference.form == form for reference in filtered)
        for form in REQUIRED_FORMS
    }
    report = {
        "from_date": _date_text(from_date),
        "to_date": _date_text(to_date),
        "discovered_dates": len(dates),
        "complete_dates": len(complete_dates),
        "incomplete_dates": len(incomplete),
        "first_discovered_date": _date_text(min(dates) if dates else None),
        "last_discovered_date": _date_text(max(dates) if dates else None),
        "first_complete_date": _date_text(min(complete_dates) if complete_dates else None),
        "last_complete_date": _date_text(max(complete_dates) if complete_dates else None),
        "artifact_reference_count": len(filtered),
        "references_by_form": references_by_form,
        "complete_report_dates": [item.isoformat() for item in complete_dates],
        "incomplete_report_dates": incomplete,
    }
    return HistoricalCatalog(report=report, references_by_date=tuple(ordered_groups))


def _base_report(*, mode: str, generated_at: datetime) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "mode": mode,
        "generated_at": _iso_datetime(generated_at),
        **_safety_projection(),
    }


def run_catalog(
    *,
    client: CbrBankRegulatoryClient,
    generated_at: datetime,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[str, Any]:
    catalog = build_catalog(
        client.discover_catalog(), from_date=from_date, to_date=to_date
    )
    return {
        **_base_report(mode="catalog", generated_at=generated_at),
        "status": "ready",
        "network_accessed": True,
        "artifact_downloads": 0,
        "catalog": catalog.report,
    }


def _probe_state(code: CbrSourceStatus) -> str:
    if code == CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION:
        return "UNSUPPORTED_SCHEMA_VERSION"
    if code == CbrSourceStatus.ARTIFACT_NOT_FOUND:
        return "ARTIFACT_NOT_FOUND"
    if code in _INVALID_CONTENT_CODES:
        return "INVALID_CONTENT"
    return "SOURCE_ERROR"


def _failure_result(
    report_date: date,
    *,
    state: str,
    missing_forms: Sequence[CbrBankForm] = (),
    source_error_code: str | None = None,
    failed_stage: str | None = None,
    downloaded_artifact_count: int = 0,
) -> dict[str, Any]:
    return {
        "report_date": report_date.isoformat(),
        "state": state,
        "source_error_code": source_error_code,
        "failed_stage": failed_stage,
        "downloaded_artifact_count": downloaded_artifact_count,
        "missing_forms": [form.value for form in missing_forms],
        "forms": [],
    }


def _ready_result(
    report_date: date,
    *,
    artifacts: Sequence[Any],
    bundle: Any,
    exact_forms: Sequence[Any],
) -> dict[str, Any]:
    artifact_by_form = {item.reference.form.value: item for item in artifacts}
    result_by_form = {item.form.value: item for item in bundle.forms}
    exact_by_form = {item.form: item for item in exact_forms}
    if (
        set(artifact_by_form)
        != {form.value for form in REQUIRED_FORMS}
        or set(result_by_form) != set(artifact_by_form)
        or set(exact_by_form) != set(artifact_by_form)
    ):
        raise CbrSourceError(
            CbrSourceStatus.INVALID_CONTENT, "historical bundle projection is incomplete"
        )

    forms = []
    for form in REQUIRED_FORMS:
        key = form.value
        artifact = artifact_by_form[key]
        result = result_by_form[key]
        exact = exact_by_form[key]
        forms.append(
            {
                "form": key,
                "artifact_filename": artifact.reference.artifact_filename,
                "artifact_size": artifact.compressed_size,
                "artifact_sha256": artifact.content_sha256,
                "record_count": len(result.records),
                "subject_count": len(result.subjects),
                "subject_set_sha256": subject_set_sha256(set(result.subjects)),
                "value_member_name": exact.value_member_name,
                "form_schema_fingerprint": result.form_schema_fingerprint,
                "source_row_fingerprint_set_sha256": ordered_fingerprints_sha256(
                    [item.source_row_fingerprint for item in exact.observations]
                ),
            }
        )
    return {
        "report_date": report_date.isoformat(),
        "state": "READY",
        "source_error_code": None,
        "failed_stage": None,
        "downloaded_artifact_count": len(artifacts),
        "missing_forms": [],
        "forms": forms,
    }


def build_schema_drift(probe_results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    history: dict[str, dict[str, list[str]]] = {
        form.value: {} for form in REQUIRED_FORMS
    }
    for month in probe_results:
        if month["state"] != "READY":
            continue
        report_date = str(month["report_date"])
        for form in month["forms"]:
            fingerprint = str(form["form_schema_fingerprint"])
            history[str(form["form"])].setdefault(fingerprint, []).append(report_date)

    report: list[dict[str, Any]] = []
    for form in REQUIRED_FORMS:
        entries = history[form.value]
        all_dates = sorted(date_value for values in entries.values() for date_value in values)
        fingerprints = sorted(entries)
        report.append(
            {
                "form": form.value,
                "distinct_schema_fingerprint_count": len(fingerprints),
                "schema_fingerprints": fingerprints,
                "first_seen_report_date": all_dates[0] if all_dates else None,
                "last_seen_report_date": all_dates[-1] if all_dates else None,
                "fingerprint_history": [
                    {
                        "fingerprint": fingerprint,
                        "first_seen_report_date": min(entries[fingerprint]),
                        "last_seen_report_date": max(entries[fingerprint]),
                    }
                    for fingerprint in fingerprints
                ],
            }
        )
    return report


def run_probe(
    *,
    client: CbrBankRegulatoryClient,
    generated_at: datetime,
    probe_dates: Sequence[date],
    archive_executable: str | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
    exact_extractor: Callable[..., Any] = extract_exact_form_evidence,
) -> dict[str, Any]:
    if (
        not probe_dates
        or len(probe_dates) > MAX_PROBE_DATES
        or len(set(probe_dates)) != len(probe_dates)
    ):
        raise HistoricalAuditError("INVALID_ARGUMENTS")
    ordered_dates = tuple(sorted(probe_dates))
    catalog = build_catalog(client.discover_catalog())
    by_date = dict(catalog.references_by_date)
    service = bundle_service or CbrBankRegulatoryBundleService(
        archive_executable=archive_executable
    )
    results: list[dict[str, Any]] = []
    artifact_downloads = 0
    required = set(REQUIRED_FORMS)

    for report_date in ordered_dates:
        references = by_date.get(report_date, ())
        present = {item.form for item in references}
        missing = tuple(form for form in REQUIRED_FORMS if form not in present)
        if not references:
            results.append(
                _failure_result(
                    report_date,
                    state="ARTIFACT_NOT_FOUND",
                    missing_forms=REQUIRED_FORMS,
                    source_error_code=CbrSourceStatus.ARTIFACT_NOT_FOUND.value,
                    failed_stage="CATALOG_RESOLUTION",
                )
            )
            continue
        if present != required or len(references) != len(REQUIRED_FORMS):
            results.append(
                _failure_result(
                    report_date,
                    state="INCOMPLETE_BUNDLE",
                    missing_forms=missing,
                    failed_stage="CATALOG_RESOLUTION",
                )
            )
            continue
        downloaded_artifact_count = 0
        failed_stage = "ARTIFACT_DOWNLOAD"
        try:
            artifacts = []
            for reference in references:
                artifacts.append(
                    client.fetch_discovered_artifact_historical(reference)
                )
                downloaded_artifact_count += 1
                artifact_downloads += 1
            failed_stage = "BUNDLE_PARSE"
            bundle = service.build_snapshot(
                report_date=report_date,
                artifacts=tuple(artifacts),
                enforce_approved_schema=False,
                allow_dynamic_value_member=True,
            )
            failed_stage = "LEXICAL_EXTRACTION"
            exact_forms = tuple(
                exact_extractor(
                    item,
                    archive_executable=archive_executable,
                    allow_dynamic_value_member=True,
                )
                for item in sorted(bundle.forms, key=lambda item: item.form.value)
            )
            results.append(
                _ready_result(
                    report_date,
                    artifacts=artifacts,
                    bundle=bundle,
                    exact_forms=exact_forms,
                )
            )
        except CbrSourceError as exc:
            results.append(
                _failure_result(
                    report_date,
                    state=_probe_state(exc.code),
                    source_error_code=exc.code.value,
                    failed_stage=failed_stage,
                    downloaded_artifact_count=downloaded_artifact_count,
                )
            )
        except Exception:
            results.append(
                _failure_result(
                    report_date,
                    state="SOURCE_ERROR",
                    source_error_code=CbrSourceStatus.SOURCE_ERROR.value,
                    failed_stage=failed_stage,
                    downloaded_artifact_count=downloaded_artifact_count,
                )
            )

    ready_count = sum(item["state"] == "READY" for item in results)
    return {
        **_base_report(mode="probe", generated_at=generated_at),
        "status": "ready" if ready_count == len(results) else "complete_with_findings",
        "network_accessed": True,
        "artifact_downloads": artifact_downloads,
        "catalog": catalog.report,
        "requested_probe_dates": [item.isoformat() for item in ordered_dates],
        "ready_dates": ready_count,
        "finding_dates": len(results) - ready_count,
        "probe_results": results,
        "schema_drift": build_schema_drift(results),
    }


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="cbr-bank-historical-audit")
    parser.add_argument("--mode", choices=("catalog", "probe"), required=True)
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--probe-date", action="append", default=[])
    return parser


def _validated_args(args: argparse.Namespace) -> tuple[date | None, date | None, tuple[date, ...]]:
    from_date = _parse_date(args.from_date) if args.from_date else None
    to_date = _parse_date(args.to_date) if args.to_date else None
    probe_dates = tuple(_parse_date(item) for item in args.probe_date)
    if args.mode == "catalog":
        if probe_dates or (from_date is not None and to_date is not None and from_date > to_date):
            raise HistoricalAuditError("INVALID_ARGUMENTS")
    elif from_date is not None or to_date is not None or not probe_dates:
        raise HistoricalAuditError("INVALID_ARGUMENTS")
    if len(probe_dates) > MAX_PROBE_DATES or len(set(probe_dates)) != len(probe_dates):
        raise HistoricalAuditError("INVALID_ARGUMENTS")
    return from_date, to_date, probe_dates


def _failure(*, mode: str | None, code: str, network_accessed: bool) -> dict[str, Any]:
    payload = {
        "schema": SCHEMA_VERSION,
        "status": "failed",
        "error_code": code,
        "network_accessed": network_accessed,
        "artifact_downloads": 0,
        **_safety_projection(),
    }
    if mode in {"catalog", "probe"}:
        payload["mode"] = mode
    return payload


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def main(
    argv: Sequence[str] | None = None,
    *,
    client_factory: Callable[..., CbrBankRegulatoryClient] = CbrBankRegulatoryClient,
    clock: Callable[[], datetime] | None = None,
    archive_executable: str | None = None,
    bundle_service: CbrBankRegulatoryBundleService | None = None,
    exact_extractor: Callable[..., Any] = extract_exact_form_evidence,
) -> int:
    mode: str | None = None
    try:
        args = _parser().parse_args(argv)
        mode = args.mode
        from_date, to_date, probe_dates = _validated_args(args)
    except (HistoricalAuditError, SystemExit):
        _emit(_failure(mode=mode, code="INVALID_ARGUMENTS", network_accessed=False))
        return 2

    try:
        generated_at = (clock or (lambda: datetime.now(timezone.utc)))()
        _iso_datetime(generated_at)
        client = client_factory(now=lambda: generated_at)
        if mode == "catalog":
            report = run_catalog(
                client=client,
                generated_at=generated_at,
                from_date=from_date,
                to_date=to_date,
            )
        else:
            report = run_probe(
                client=client,
                generated_at=generated_at,
                probe_dates=probe_dates,
                archive_executable=archive_executable,
                bundle_service=bundle_service,
                exact_extractor=exact_extractor,
            )
        _emit(report)
        return 0
    except HistoricalAuditError as exc:
        _emit(_failure(mode=mode, code=exc.code, network_accessed=True))
        return 1
    except CbrSourceError as exc:
        _emit(_failure(mode=mode, code=exc.code.value, network_accessed=True))
        return 1
    except Exception:
        _emit(_failure(mode=mode, code="SOURCE_ERROR", network_accessed=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
