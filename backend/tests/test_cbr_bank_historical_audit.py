from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services.cbr_bank_financial_evidence import historical_audit as audit
from app.services.cbr_bank_financial_evidence.lexical import (
    extract_exact_form_evidence,
)
from app.services.cbr_bank_reporting import archive as reporting_archive
from app.services.cbr_bank_reporting import client as reporting_client
from app.services.cbr_bank_reporting.archive import (
    extract_archive_members,
    inspect_archive_bytes,
)
from app.services.cbr_bank_reporting.client import CbrBankRegulatoryClient
from app.services.cbr_bank_reporting.contracts import (
    ArchiveMember,
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
)
from app.services.cbr_bank_reporting.dbf import read_dbf_member
from app.services.cbr_bank_reporting.parsers import (
    compute_form_schema_fingerprint,
    compute_form_structural_schema_fingerprint,
    compute_structural_schema_fingerprint,
    parse_form,
    resolve_data_member,
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
        fetch_error_form: CbrBankForm | None = None,
    ) -> None:
        self.references = references
        self.discovery_error = discovery_error
        self.fetch_error_form = fetch_error_form
        self.catalog_calls = 0
        self.fetch_calls: list[CbrArtifactReference] = []

    def discover_catalog(self) -> tuple[CbrArtifactReference, ...]:
        self.catalog_calls += 1
        if self.discovery_error is not None:
            raise self.discovery_error
        return self.references

    def fetch_discovered_artifact_historical(
        self, reference: CbrArtifactReference
    ) -> CbrBankArtifact:
        if reference.form == self.fetch_error_form:
            raise CbrSourceError(CbrSourceStatus.TIMEOUT, "password=secret")
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
        member_inventories: dict[
            tuple[date, CbrBankForm], tuple[tuple[str, str], ...]
        ]
        | None = None,
        failures: dict[date, CbrSourceStatus] | None = None,
    ) -> None:
        self.fingerprints = fingerprints or {}
        self.member_inventories = member_inventories or {}
        self.failures = failures or {}
        self.calls: list[date] = []

    def build_snapshot(
        self,
        *,
        report_date,
        artifacts,
        enforce_approved_schema,
        allow_dynamic_value_member,
        max_archive_member_bytes,
        max_archive_total_uncompressed_bytes,
    ):
        self.calls.append(report_date)
        assert enforce_approved_schema is False
        assert allow_dynamic_value_member is True
        assert max_archive_member_bytes == reporting_archive.HISTORICAL_MAX_MEMBER_BYTES
        assert (
            max_archive_total_uncompressed_bytes
            == reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
        )
        if report_date in self.failures:
            raise CbrSourceError(self.failures[report_date], "password=secret")
        forms = tuple(
            SimpleNamespace(
                form=artifact.reference.form,
                artifact=artifact,
                records=(object(), object()),
                subjects=("1", "2"),
                member_schema_fingerprints=self.member_inventories.get(
                    (report_date, artifact.reference.form),
                    ((f"{artifact.reference.form.short_code}_VALUE.DBF", "c" * 64),),
                ),
                form_schema_fingerprint=self.fingerprints.get(
                    (report_date, artifact.reference.form), "a" * 64
                ),
            )
            for artifact in artifacts
        )
        return SimpleNamespace(forms=forms)


def _extractor_calls():
    calls: list[tuple[date, CbrBankForm]] = []

    def extract(
        result,
        *,
        archive_executable=None,
        allow_dynamic_value_member=False,
        max_archive_member_bytes=None,
        max_archive_total_uncompressed_bytes=None,
    ):
        assert archive_executable is None
        assert allow_dynamic_value_member is True
        assert max_archive_member_bytes == reporting_archive.HISTORICAL_MAX_MEMBER_BYTES
        assert (
            max_archive_total_uncompressed_bytes
            == reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
        )
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
            value_member_name=result.member_schema_fingerprints[0][0],
            observations=fingerprints,
        )

    return extract, calls


def _dbf_bytes(
    fields: tuple[tuple[str, str, int, int], ...],
    rows: tuple[tuple[str, ...], ...],
) -> bytes:
    header_length = 32 + 32 * len(fields) + 1
    record_length = 1 + sum(item[2] for item in fields)
    header = bytearray(header_length)
    header[0] = 0x03
    header[1:4] = bytes((126, 8, 1))
    header[4:8] = len(rows).to_bytes(4, "little")
    header[8:10] = header_length.to_bytes(2, "little")
    header[10:12] = record_length.to_bytes(2, "little")
    header[29] = 0
    field_offset = 1
    for index, (name, field_type, length, decimal_count) in enumerate(fields):
        offset = 32 + index * 32
        encoded_name = name.encode("ascii")
        header[offset : offset + len(encoded_name)] = encoded_name
        header[offset + 11] = ord(field_type)
        header[offset + 12 : offset + 16] = field_offset.to_bytes(4, "little")
        header[offset + 16] = length
        header[offset + 17] = decimal_count
        field_offset += length
    header[-1] = 0x0D
    body = bytearray()
    for row in rows:
        body.extend(b" ")
        for value, (_name, field_type, length, _decimal_count) in zip(row, fields):
            encoded = value.encode("ascii")
            if len(encoded) > length:
                raise AssertionError("synthetic DBF value exceeds field width")
            body.extend(
                encoded.rjust(length, b" ")
                if field_type in {"N", "F"}
                else encoded.ljust(length, b" ")
            )
    return bytes(header + body + b"\x1a")


def _renamed_123_member(name: str = "062023_123d.dbf"):
    content = _dbf_bytes(
        (
            ("REGN", "C", 5, 0),
            ("C1", "C", 5, 0),
            ("C3", "N", 12, 3),
        ),
        (("1", "102", "123.450"),),
    )
    archive_member = ArchiveMember(
        name=name,
        normalized_name=name.upper(),
        compressed_size=len(content),
        uncompressed_size=len(content),
        crc32=None,
    )
    return archive_member, content, read_dbf_member(archive_member, content)


class _ArchiveInfo:
    def __init__(self, name: str, size: int) -> None:
        self.filename = name
        self.file_size = size
        self.compress_size = max(1, size // 2)
        self.CRC = None
        self.volume = 0
        self.file_redir = None

    def is_file(self) -> bool:
        return True

    def is_symlink(self) -> bool:
        return False

    def needs_password(self) -> bool:
        return False


class _ArchiveRar:
    infos: tuple[_ArchiveInfo, ...] = ()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def is_solid(self) -> bool:
        return False

    def volumelist(self):
        return ("source.rar",)

    def infolist(self):
        return self.infos


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


def test_historical_fetch_has_separate_bounded_budget() -> None:
    payload = b"R" * (reporting_client.MAX_ARTIFACT_BYTES + 1)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/vnd.rar"},
            request=request,
        )

    client = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
    )
    reference = _reference(CbrBankForm.FORM_101, DATE_1)
    with pytest.raises(CbrSourceError) as error:
        client.fetch_discovered_artifact(reference)
    assert error.value.code == CbrSourceStatus.ARTIFACT_TOO_LARGE

    artifact = client.fetch_discovered_artifact_historical(reference)
    assert artifact.content == payload
    assert reporting_client.MAX_ARTIFACT_BYTES == 2 * 1024 * 1024
    assert reporting_client.MAX_TOTAL_ARTIFACT_BYTES == 8 * 1024 * 1024
    assert reporting_client.HISTORICAL_MAX_ARTIFACT_BYTES == 32 * 1024 * 1024
    assert reporting_client.HISTORICAL_MAX_TOTAL_ARTIFACT_BYTES == 512 * 1024 * 1024
    client.http_client.close()


def test_historical_fetch_enforces_per_artifact_and_total_limits(monkeypatch) -> None:
    reference = _reference(CbrBankForm.FORM_101, DATE_1)

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-length": str(
                    reporting_client.HISTORICAL_MAX_ARTIFACT_BYTES + 1
                )
            },
            request=request,
        )

    client = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(oversized))
    )
    with pytest.raises(CbrSourceError) as error:
        client.fetch_discovered_artifact_historical(reference)
    assert error.value.code == CbrSourceStatus.ARTIFACT_TOO_LARGE
    client.http_client.close()

    monkeypatch.setattr(reporting_client, "HISTORICAL_MAX_ARTIFACT_BYTES", 5)
    monkeypatch.setattr(reporting_client, "HISTORICAL_MAX_TOTAL_ARTIFACT_BYTES", 8)

    def five_bytes(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"12345", request=request)

    cumulative = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(five_bytes))
    )
    cumulative.fetch_discovered_artifact_historical(reference)
    with pytest.raises(CbrSourceError) as error:
        cumulative.fetch_discovered_artifact_historical(reference)
    assert error.value.code == CbrSourceStatus.ARTIFACT_TOO_LARGE
    cumulative.http_client.close()


@pytest.mark.parametrize(
    "location",
    (
        "http://www.cbr.ru/vfs/credit/forms/101-20260601.rar",
        "https://example.com/101-20260601.rar",
    ),
)
def test_historical_fetch_rejects_unsafe_redirects(location) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location}, request=request)

    client = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(CbrSourceError) as error:
        client.fetch_discovered_artifact_historical(
            _reference(CbrBankForm.FORM_101, DATE_1)
        )
    assert error.value.code == CbrSourceStatus.INVALID_CONTENT
    client.http_client.close()


def test_historical_archive_limits_are_explicit_and_bounded(monkeypatch) -> None:
    monkeypatch.setattr(reporting_archive.rarfile, "RarFile", _ArchiveRar)
    content = b"Rar!\x1a\x07\x00synthetic"
    between_limits = reporting_archive.MAX_MEMBER_BYTES + 1
    _ArchiveRar.infos = (_ArchiveInfo("VALUE.DBF", between_limits),)

    with pytest.raises(CbrSourceError) as error:
        inspect_archive_bytes(content)
    assert error.value.code == CbrSourceStatus.ARCHIVE_MEMBER_TOO_LARGE

    accepted = inspect_archive_bytes(
        content,
        max_member_bytes=reporting_archive.HISTORICAL_MAX_MEMBER_BYTES,
        max_total_uncompressed_bytes=(
            reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
        ),
    )
    assert accepted[0].uncompressed_size == between_limits

    _ArchiveRar.infos = (
        _ArchiveInfo(
            "VALUE.DBF", reporting_archive.HISTORICAL_MAX_MEMBER_BYTES + 1
        ),
    )
    with pytest.raises(CbrSourceError) as error:
        inspect_archive_bytes(
            content,
            max_member_bytes=reporting_archive.HISTORICAL_MAX_MEMBER_BYTES,
            max_total_uncompressed_bytes=(
                reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
            ),
        )
    assert error.value.code == CbrSourceStatus.ARCHIVE_MEMBER_TOO_LARGE

    half_total = reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES // 2 + 1
    _ArchiveRar.infos = (
        _ArchiveInfo("A.DBF", half_total),
        _ArchiveInfo("B.DBF", half_total),
    )
    with pytest.raises(CbrSourceError) as error:
        inspect_archive_bytes(
            content,
            max_member_bytes=reporting_archive.HISTORICAL_MAX_MEMBER_BYTES,
            max_total_uncompressed_bytes=(
                reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
            ),
        )
    assert error.value.code == CbrSourceStatus.ARCHIVE_TOTAL_TOO_LARGE

    assert reporting_archive.MAX_MEMBER_BYTES == 16 * 1024 * 1024
    assert reporting_archive.MAX_TOTAL_UNCOMPRESSED_BYTES == 64 * 1024 * 1024
    assert reporting_archive.HISTORICAL_MAX_MEMBER_BYTES == 96 * 1024 * 1024
    assert (
        reporting_archive.HISTORICAL_MAX_TOTAL_UNCOMPRESSED_BYTES
        == 128 * 1024 * 1024
    )


def test_selected_member_limit_reaches_actual_extraction(monkeypatch) -> None:
    artifact = CbrBankArtifact(
        reference=_reference(CbrBankForm.FORM_123, DATE_1),
        content=b"synthetic-rar",
        content_sha256=hashlib.sha256(b"synthetic-rar").hexdigest(),
        compressed_size=len(b"synthetic-rar"),
        content_type="application/vnd.rar",
        retrieved_at=NOW,
    )
    member = ArchiveMember(
        name="VALUE.DBF",
        normalized_name="VALUE.DBF",
        compressed_size=3,
        uncompressed_size=5,
        crc32=None,
    )
    inspected_limits: list[tuple[int, int]] = []

    def inspect(_content, *, max_member_bytes, max_total_uncompressed_bytes):
        inspected_limits.append((max_member_bytes, max_total_uncompressed_bytes))
        return (member,)

    class Process:
        def __init__(self, _args, *, stdout, stderr) -> None:
            del stderr
            stdout.write(b"12345")
            self.returncode = 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(reporting_archive, "inspect_archive_bytes", inspect)
    monkeypatch.setattr(
        reporting_archive, "resolve_libarchive_executable", lambda _value=None: "bsdtar"
    )
    monkeypatch.setattr(reporting_archive.subprocess, "Popen", Process)

    extracted = extract_archive_members(
        artifact,
        max_member_bytes=5,
        max_total_uncompressed_bytes=9,
    )
    assert extracted == ((member, b"12345"),)
    assert inspected_limits == [(5, 9)]

    with pytest.raises(CbrSourceError) as error:
        extract_archive_members(
            artifact,
            max_member_bytes=4,
            max_total_uncompressed_bytes=9,
        )
    assert error.value.code == CbrSourceStatus.INVALID_ARCHIVE
    assert inspected_limits[-1] == (4, 9)


def test_dynamic_value_member_resolution_is_explicit_and_unique() -> None:
    _archive_member, _content, renamed = _renamed_123_member()
    artifact = CbrBankArtifact(
        reference=_reference(CbrBankForm.FORM_123, DATE_1),
        content=b"synthetic-rar",
        content_sha256=hashlib.sha256(b"synthetic-rar").hexdigest(),
        compressed_size=len(b"synthetic-rar"),
        content_type="application/vnd.rar",
        retrieved_at=NOW,
    )
    with pytest.raises(CbrSourceError) as error:
        parse_form(
            CbrBankForm.FORM_123,
            artifact,
            (renamed,),
            enforce_approved_schema=False,
        )
    assert error.value.code == CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION

    result = parse_form(
        CbrBankForm.FORM_123,
        artifact,
        (renamed,),
        enforce_approved_schema=False,
        allow_dynamic_value_member=True,
    )
    assert result.records[0].source_member == "062023_123d.dbf"
    assert result.records[0].source_value == Decimal("123.450")

    no_candidate = replace(
        renamed,
        fields=tuple(field for field in renamed.fields if field.name != "C3"),
    )
    with pytest.raises(CbrSourceError) as error:
        resolve_data_member(
            CbrBankForm.FORM_123,
            (no_candidate,),
            allow_dynamic_value_member=True,
        )
    assert error.value.code == CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION

    second_candidate = replace(renamed, name="OTHER.DBF")
    with pytest.raises(CbrSourceError) as error:
        resolve_data_member(
            CbrBankForm.FORM_123,
            (renamed, second_candidate),
            allow_dynamic_value_member=True,
        )
    assert error.value.code == CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION


def test_dynamic_parser_and_lexical_extractor_select_same_member(monkeypatch) -> None:
    archive_member, content, renamed = _renamed_123_member()
    artifact_content = b"synthetic-rar"
    artifact = CbrBankArtifact(
        reference=_reference(CbrBankForm.FORM_123, DATE_1),
        content=artifact_content,
        content_sha256=hashlib.sha256(artifact_content).hexdigest(),
        compressed_size=len(artifact_content),
        content_type="application/vnd.rar",
        retrieved_at=NOW,
    )
    result = parse_form(
        CbrBankForm.FORM_123,
        artifact,
        (renamed,),
        enforce_approved_schema=False,
        allow_dynamic_value_member=True,
    )
    monkeypatch.setattr(
        "app.services.cbr_bank_financial_evidence.lexical.extract_archive_members",
        lambda _artifact, **_kwargs: ((archive_member, content),),
    )
    exact = extract_exact_form_evidence(
        result,
        allow_dynamic_value_member=True,
    )
    assert exact.value_member_name == "062023_123d.dbf"
    assert exact.observations[0].record.source_member == exact.value_member_name
    assert exact.observations[0].raw_value_text == "123.450"
    assert exact.observations[0].parsed_decimal_value == Decimal("123.450")


def test_structural_fingerprints_separate_names_fields_and_multiplicity() -> None:
    _archive_member, _content, original = _renamed_123_member("062023_123D.DBF")
    renamed = replace(original, name="072026_123D.DBF")
    assert compute_form_schema_fingerprint((original,)) != compute_form_schema_fingerprint(
        (renamed,)
    )
    assert compute_form_structural_schema_fingerprint(
        (original,)
    ) == compute_form_structural_schema_fingerprint((renamed,))
    assert original.schema_fingerprint == renamed.schema_fingerprint

    changed_content = _dbf_bytes(
        (
            ("REGN", "C", 5, 0),
            ("C1", "C", 5, 0),
            ("C3", "N", 13, 4),
        ),
        (("1", "102", "123.4500"),),
    )
    changed_archive_member = ArchiveMember(
        name="072026_123D.DBF",
        normalized_name="072026_123D.DBF",
        compressed_size=len(changed_content),
        uncompressed_size=len(changed_content),
        crc32=None,
    )
    changed = read_dbf_member(changed_archive_member, changed_content)
    assert changed.schema_fingerprint != original.schema_fingerprint
    assert compute_form_structural_schema_fingerprint(
        (changed,)
    ) != compute_form_structural_schema_fingerprint((original,))

    one = compute_structural_schema_fingerprint((original.schema_fingerprint,))
    two = compute_structural_schema_fingerprint(
        (original.schema_fingerprint, original.schema_fingerprint)
    )
    assert one != two


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
    assert by_date["2026-06-01"]["failed_stage"] == "CATALOG_RESOLUTION"
    assert by_date["2026-06-01"]["downloaded_artifact_count"] == 0
    assert by_date["2026-06-01"]["missing_forms"] == ["0409123", "0409135"]
    assert by_date["2026-07-01"]["state"] == "ARTIFACT_NOT_FOUND"
    assert by_date["2026-07-01"]["failed_stage"] == "CATALOG_RESOLUTION"
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
        fingerprints={(DATE_2, CbrBankForm.FORM_101): "b" * 64},
        member_inventories={
            (DATE_2, CbrBankForm.FORM_101): (
                ("RENAMED_101_VALUE.DBF", "c" * 64),
            ),
            (DATE_2, CbrBankForm.FORM_102): (
                ("102_VALUE.DBF", "d" * 64),
            ),
        },
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
    assert all(item["failed_stage"] is None for item in report["probe_results"])
    assert all(
        item["downloaded_artifact_count"] == 4
        for item in report["probe_results"]
    )
    assert all(len(item["forms"]) == 4 for item in report["probe_results"])
    assert all(
        len(form["source_row_fingerprint_set_sha256"]) == 64
        for item in report["probe_results"]
        for form in item["forms"]
    )
    assert all(
        len(form["value_member_schema_fingerprint"]) == 64
        and len(form["form_structural_schema_fingerprint"]) == 64
        for item in report["probe_results"]
        for form in item["forms"]
    )
    drift = {item["form"]: item for item in report["schema_drift"]}
    assert drift["0409101"]["distinct_schema_fingerprint_count"] == 2
    assert drift["0409101"]["schema_fingerprints"] == ["a" * 64, "b" * 64]
    assert drift["0409102"]["distinct_schema_fingerprint_count"] == 1
    assert drift["0409102"]["first_seen_report_date"] == "2026-06-01"
    assert drift["0409102"]["last_seen_report_date"] == "2026-07-01"
    structural = {
        item["form"]: item for item in report["structural_schema_drift"]
    }
    assert (
        structural["0409101"][
            "distinct_form_structural_schema_fingerprint_count"
        ]
        == 1
    )
    assert (
        structural["0409101"]["distinct_value_member_schema_fingerprint_count"]
        == 1
    )
    assert (
        structural["0409102"][
            "distinct_form_structural_schema_fingerprint_count"
        ]
        == 2
    )
    assert (
        structural["0409102"]["distinct_value_member_schema_fingerprint_count"]
        == 2
    )
    assert structural["0409102"]["first_seen_report_date"] == "2026-06-01"
    assert structural["0409102"]["last_seen_report_date"] == "2026-07-01"
    assert len(structural["0409102"]["form_structural_fingerprint_history"]) == 2
    assert len(structural["0409102"]["value_member_fingerprint_history"]) == 2


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
    assert result["failed_stage"] == "BUNDLE_PARSE"
    assert result["downloaded_artifact_count"] == 4
    assert "secret" not in json.dumps(report)


def test_probe_reports_partial_historical_download_failure() -> None:
    source = _Source(
        _complete_references(DATE_1),
        fetch_error_form=CbrBankForm.FORM_102,
    )
    report = audit.run_probe(
        client=source,
        generated_at=NOW,
        probe_dates=(DATE_1,),
    )
    result = report["probe_results"][0]
    assert report["artifact_downloads"] == 1
    assert result["state"] == "SOURCE_ERROR"
    assert result["source_error_code"] == CbrSourceStatus.TIMEOUT.value
    assert result["failed_stage"] == "ARTIFACT_DOWNLOAD"
    assert result["downloaded_artifact_count"] == 1
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
