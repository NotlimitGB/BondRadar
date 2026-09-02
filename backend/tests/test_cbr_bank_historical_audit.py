from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services.cbr_bank_financial_evidence import historical_audit as audit
from app.services.cbr_bank_reporting.client import CbrBankRegulatoryClient
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
)


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
DATE_1 = date(2026, 6, 1)
DATE_2 = date(2026, 7, 1)
DATE_3 = date(2026, 8, 1)


def _reference(form: CbrBankForm, report_date: date) -> CbrArtifactReference:
    filename = f"{form.short_code}-{report_date:%Y%m%d}.rar"
    return CbrArtifactReference(
        form=form,
        source_href=f"/vfs/credit/forms/{filename}",
        source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
        artifact_filename=filename,
        report_date=report_date,
        discovered_at=NOW,
    )


def _complete_references(report_date: date) -> tuple[CbrArtifactReference, ...]:
    return tuple(_reference(form, report_date) for form in CbrBankForm)


class _Source:
    def __init__(
        self,
        references: tuple[CbrArtifactReference, ...],
        *,
        discovery_error: CbrSourceError | None = None,
    ) -> None:
        self.references = references
        self.discovery_error = discovery_error
        self.catalog_calls = 0
        self.fetch_calls: list[CbrArtifactReference] = []

    def discover_catalog(self) -> tuple[CbrArtifactReference, ...]:
        self.catalog_calls += 1
        if self.discovery_error is not None:
            raise self.discovery_error
        return self.references

    def fetch_discovered_artifact(
        self, reference: CbrArtifactReference
    ) -> CbrBankArtifact:
        self.fetch_calls.append(reference)
        content = f"{reference.form.value}:{reference.report_date}".encode("ascii")
        return CbrBankArtifact(
            reference=reference,
            content=content,
            content_sha256=hashlib.sha256(content).hexdigest(),
            compressed_size=len(content),
            content_type="application/vnd.rar",
            retrieved_at=NOW,
        )


class _BundleService:
    def __init__(
        self,
        fingerprints: dict[tuple[date, CbrBankForm], str] | None = None,
        failures: dict[date, CbrSourceStatus] | None = None,
    ) -> None:
        self.fingerprints = fingerprints or {}
        self.failures = failures or {}
        self.calls: list[date] = []

    def build_snapshot(self, *, report_date, artifacts, enforce_approved_schema):
        self.calls.append(report_date)
        assert enforce_approved_schema is False
        if report_date in self.failures:
            raise CbrSourceError(self.failures[report_date], "password=secret")
        forms = tuple(
            SimpleNamespace(
                form=artifact.reference.form,
                artifact=artifact,
                records=(object(), object()),
                subjects=("1", "2"),
                form_schema_fingerprint=self.fingerprints.get(
                    (report_date, artifact.reference.form), "a" * 64
                ),
            )
            for artifact in artifacts
        )
        return SimpleNamespace(forms=forms)


def _extractor_calls():
    calls: list[tuple[date, CbrBankForm]] = []

    def extract(result, *, archive_executable=None):
        assert archive_executable is None
        calls.append((result.artifact.reference.report_date, result.form))
        fingerprints = tuple(
            SimpleNamespace(
                source_row_fingerprint=hashlib.sha256(
                    f"{result.artifact.reference.report_date}:{result.form.value}:{index}".encode(
                        "ascii"
                    )
                ).hexdigest()
            )
            for index in range(2)
        )
        return SimpleNamespace(
            form=result.form.value,
            value_member_name=f"{result.form.short_code}_VALUE.DBF",
            observations=fingerprints,
        )

    return extract, calls


def test_client_catalog_fetches_one_bounded_source_page() -> None:
    html = "<html><body>" + "".join(
        f'<a href="{item.source_href}">{item.artifact_filename}</a>'
        for item in (*_complete_references(DATE_1), *_complete_references(DATE_2))
    ) + "</body></html>"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=html, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = CbrBankRegulatoryClient(http_client=http_client, now=lambda: NOW)
    references = client.discover_catalog()
    assert len(references) == 8
    assert len(requests) == 1
    assert all(request.method == "GET" for request in requests)
    http_client.close()


def test_catalog_groups_filters_and_reports_exact_missing_forms() -> None:
    references = tuple(
        reversed(
            (
                *_complete_references(DATE_1),
                *_complete_references(DATE_2)[:3],
            )
        )
    )
    catalog = audit.build_catalog(references)
    assert catalog.report == {
        "from_date": None,
        "to_date": None,
        "discovered_dates": 2,
        "complete_dates": 1,
        "incomplete_dates": 1,
        "first_discovered_date": "2026-06-01",
        "last_discovered_date": "2026-07-01",
        "first_complete_date": "2026-06-01",
        "last_complete_date": "2026-06-01",
        "artifact_reference_count": 7,
        "references_by_form": {
            "0409101": 2,
            "0409102": 2,
            "0409123": 2,
            "0409135": 1,
        },
        "complete_report_dates": ["2026-06-01"],
        "incomplete_report_dates": [
            {"report_date": "2026-07-01", "missing_forms": ["0409135"]}
        ],
    }

    filtered = audit.build_catalog(
        references, from_date=DATE_2, to_date=DATE_2
    ).report
    assert filtered["discovered_dates"] == 1
    assert filtered["artifact_reference_count"] == 3
    empty = audit.build_catalog(references, from_date=DATE_3).report
    assert empty["discovered_dates"] == 0
    assert empty["first_discovered_date"] is None
    with pytest.raises(audit.HistoricalAuditError, match="INVALID_ARGUMENTS"):
        audit.build_catalog(references, from_date=DATE_2, to_date=DATE_1)
    with pytest.raises(CbrSourceError) as error:
        audit.build_catalog((*references, references[0]))
    assert error.value.code == CbrSourceStatus.INVALID_CONTENT


def test_catalog_mode_never_downloads_artifacts() -> None:
    source = _Source(_complete_references(DATE_1))
    report = audit.run_catalog(client=source, generated_at=NOW)
    assert report["status"] == "ready"
    assert report["artifact_downloads"] == 0
    assert source.catalog_calls == 1
    assert source.fetch_calls == []
    assert report["database_accessed"] is False
    assert report["backfill_ready"] is False


def test_probe_absent_and_incomplete_dates_download_nothing() -> None:
    source = _Source(_complete_references(DATE_1)[:2])
    report = audit.run_probe(
        client=source,
        generated_at=NOW,
        probe_dates=(DATE_1, DATE_2),
    )
    assert report["status"] == "complete_with_findings"
    assert report["artifact_downloads"] == 0
    assert source.fetch_calls == []
    by_date = {item["report_date"]: item for item in report["probe_results"]}
    assert by_date["2026-06-01"]["state"] == "INCOMPLETE_BUNDLE"
    assert by_date["2026-06-01"]["missing_forms"] == ["0409123", "0409135"]
    assert by_date["2026-07-01"]["state"] == "ARTIFACT_NOT_FOUND"
    assert by_date["2026-07-01"]["missing_forms"] == [
        form.value for form in CbrBankForm
    ]


@pytest.mark.parametrize(
    "dates",
    [(), (DATE_1, DATE_1), tuple(date(2025, month, 1) for month in range(1, 13)) + (DATE_1,)],
)
def test_probe_requires_one_to_twelve_unique_explicit_dates(dates) -> None:
    source = _Source(())
    with pytest.raises(audit.HistoricalAuditError, match="INVALID_ARGUMENTS"):
        audit.run_probe(client=source, generated_at=NOW, probe_dates=dates)
    assert source.catalog_calls == 0


def test_probe_accepts_twelve_explicit_dates_without_inferred_downloads() -> None:
    dates = tuple(date(2025, month, 1) for month in range(1, 13))
    source = _Source(())
    report = audit.run_probe(client=source, generated_at=NOW, probe_dates=dates)
    assert len(report["probe_results"]) == audit.MAX_PROBE_DATES
    assert all(item["state"] == "ARTIFACT_NOT_FOUND" for item in report["probe_results"])
    assert source.fetch_calls == []


def test_probe_projects_ready_months_and_aggregates_schema_drift() -> None:
    references = (
        *_complete_references(DATE_2),
        *_complete_references(DATE_1),
    )
    source = _Source(tuple(reversed(references)))
    service = _BundleService(
        fingerprints={(DATE_2, CbrBankForm.FORM_101): "b" * 64}
    )
    extractor, calls = _extractor_calls()
    report = audit.run_probe(
        client=source,
        generated_at=NOW,
        probe_dates=(DATE_2, DATE_1),
        bundle_service=service,
        exact_extractor=extractor,
    )
    assert report["status"] == "ready"
    assert report["requested_probe_dates"] == ["2026-06-01", "2026-07-01"]
    assert report["artifact_downloads"] == 8
    assert report["ready_dates"] == 2
    assert len(calls) == 8
    assert all(len(item["forms"]) == 4 for item in report["probe_results"])
    assert all(
        len(form["source_row_fingerprint_set_sha256"]) == 64
        for item in report["probe_results"]
        for form in item["forms"]
    )
    drift = {item["form"]: item for item in report["schema_drift"]}
    assert drift["0409101"]["distinct_schema_fingerprint_count"] == 2
    assert drift["0409101"]["schema_fingerprints"] == ["a" * 64, "b" * 64]
    assert drift["0409102"]["distinct_schema_fingerprint_count"] == 1
    assert drift["0409102"]["first_seen_report_date"] == "2026-06-01"
    assert drift["0409102"]["last_seen_report_date"] == "2026-07-01"


@pytest.mark.parametrize(
    ("code", "state"),
    [
        (CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION, "UNSUPPORTED_SCHEMA_VERSION"),
        (CbrSourceStatus.INVALID_DBF, "INVALID_CONTENT"),
        (CbrSourceStatus.TIMEOUT, "SOURCE_ERROR"),
    ],
)
def test_probe_classifies_month_failure_without_exception_text(code, state) -> None:
    source = _Source(_complete_references(DATE_1))
    report = audit.run_probe(
        client=source,
        generated_at=NOW,
        probe_dates=(DATE_1,),
        bundle_service=_BundleService(failures={DATE_1: code}),
    )
    result = report["probe_results"][0]
    assert result["state"] == state
    assert result["source_error_code"] == code.value
    assert "secret" not in json.dumps(report)


def test_cli_is_deterministic_and_failures_are_sanitized(capsys) -> None:
    source = _Source(_complete_references(DATE_1))
    factory = lambda **_kwargs: source
    argv = ["--mode", "catalog", "--from-date", "2026-06-01"]
    assert audit.main(argv, client_factory=factory, clock=lambda: NOW) == 0
    first = capsys.readouterr().out
    assert audit.main(argv, client_factory=factory, clock=lambda: NOW) == 0
    second = capsys.readouterr().out
    assert first == second
    success = json.loads(first)
    assert success["schema"] == audit.SCHEMA_VERSION
    assert success["generated_at"] == "2026-09-02T10:00:00Z"

    assert audit.main([], client_factory=factory, clock=lambda: NOW) == 2
    invalid = capsys.readouterr().out
    assert json.loads(invalid)["error_code"] == "INVALID_ARGUMENTS"

    failed_source = _Source(
        (),
        discovery_error=CbrSourceError(
            CbrSourceStatus.SOURCE_ERROR, "password=secret host=private"
        ),
    )
    assert audit.main(
        ["--mode", "catalog"],
        client_factory=lambda **_kwargs: failed_source,
        clock=lambda: NOW,
    ) == 1
    failed = capsys.readouterr().out
    assert json.loads(failed)["error_code"] == "SOURCE_ERROR"
    assert "secret" not in failed and "private" not in failed


def test_historical_audit_has_no_database_or_persistence_surface() -> None:
    source = Path(
        "backend/app/services/cbr_bank_financial_evidence/historical_audit.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "import sqlalchemy",
        "from sqlalchemy",
        "create_engine",
        "app.db",
        "app.models",
        "database_url",
        ".commit(",
        ".flush(",
        ".add(",
    )
    assert all(item not in source for item in forbidden)
