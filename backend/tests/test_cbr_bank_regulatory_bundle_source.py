from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from dbfread import FieldParser

from backend.app.services.cbr_bank_reporting.archive import (
    extract_archive_members,
    inspect_archive_bytes,
    resolve_libarchive_executable,
)
from backend.app.services.cbr_bank_reporting.bundle import (
    CbrBankRegulatoryBundleService,
)
from backend.app.services.cbr_bank_reporting.client import (
    EXPECTED_CURRENT,
    CbrBankRegulatoryClient,
    discover_artifacts_from_html,
)
from backend.app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
    CbrSourceError,
    CbrSourceStatus,
    DbfFieldDefinition,
    DbfMember,
)
from backend.app.services.cbr_bank_reporting.dbf import (
    ExactDecimalFieldParser,
    read_dbf_member,
)
from backend.app.services.cbr_bank_reporting.parsers import (
    APPROVED_FORM_SCHEMA_FINGERPRINTS,
    FORM_NOMENCLATURE_MEMBERS,
    FORM_SUPPORT_MEMBERS,
    FORM_VALUE_MEMBERS,
    parse_form,
)
from scripts.cbr_bank_regulatory_bundle_probe import build_probe_report, main


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"
REPORT_DATE = date(2026, 8, 1)
NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _reference(form: CbrBankForm) -> CbrArtifactReference:
    filename = f"{form.short_code}-20260801.rar"
    return CbrArtifactReference(
        form=form,
        source_href=f"/vfs/credit/forms/{filename}",
        source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
        artifact_filename=filename,
        report_date=REPORT_DATE,
        discovered_at=NOW,
    )


def _artifact(form: CbrBankForm) -> CbrBankArtifact:
    reference = _reference(form)
    content = (FIXTURE_ROOT / reference.artifact_filename).read_bytes()
    return CbrBankArtifact(
        reference=reference,
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        compressed_size=len(content),
        content_type="application/octet-stream",
        retrieved_at=NOW,
    )


@pytest.fixture(scope="module")
def exact_snapshot():
    return CbrBankRegulatoryBundleService().build_snapshot(
        report_date=REPORT_DATE,
        artifacts=tuple(_artifact(form) for form in CbrBankForm),
    )


def test_discovery_is_exact_order_independent_and_fail_closed() -> None:
    html = (FIXTURE_ROOT / "source_page.html").read_text(encoding="utf-8")
    references = discover_artifacts_from_html(html, discovered_at=NOW)
    assert {item.form for item in references} == set(CbrBankForm)
    assert all(item.report_date == REPORT_DATE for item in references)
    assert all(item.source_href for item in references)
    assert all(item.source_url.startswith("https://www.cbr.ru/") for item in references)

    foreign = html.replace(
        "https://www.cbr.ru/vfs/credit/forms/123", "https://example.com/123"
    )
    with pytest.raises(CbrSourceError) as exc:
        discover_artifacts_from_html(foreign, discovered_at=NOW)
    assert exc.value.code == CbrSourceStatus.INVALID_CONTENT

    duplicate = html.replace(
        "</body>",
        '<a href="https://cbr.ru/other/101-20260801.rar">duplicate</a></body>',
    )
    with pytest.raises(CbrSourceError) as exc:
        discover_artifacts_from_html(duplicate, discovered_at=NOW)
    assert exc.value.code == CbrSourceStatus.INVALID_CONTENT


def test_http_boundary_retries_redirects_budgets_and_hashes() -> None:
    html = (FIXTURE_ROOT / "source_page.html").read_bytes()
    payload = (FIXTURE_ROOT / "101-20260801.rar").read_bytes()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path.endswith("otchetnost-kreditnykh-organizaciy/"):
            return httpx.Response(200, content=html)
        if len([item for item in calls if item.endswith("101-20260801.rar")]) == 1:
            return httpx.Response(503)
        return httpx.Response(
            200,
            content=payload,
            headers={"content-type": "application/octet-stream"},
        )

    client = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        now=lambda: NOW,
        sleep=lambda _delay: None,
    )
    reference = client.discover_artifacts(
        form=CbrBankForm.FORM_101, report_date=REPORT_DATE
    )[0]
    artifact = client.fetch_artifact(reference)
    assert artifact.content_sha256 == EXPECTED_CURRENT[(reference.form, REPORT_DATE)][1]
    assert calls.count(reference.source_url) == 2

    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    missing_client = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(missing)),
        sleep=lambda _delay: None,
    )
    with pytest.raises(CbrSourceError) as exc:
        missing_client.fetch_artifact(_reference(CbrBankForm.FORM_101))
    assert exc.value.code == CbrSourceStatus.ARTIFACT_NOT_FOUND

    def oversize(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(3 * 1024 * 1024)})

    oversize_client = CbrBankRegulatoryClient(
        http_client=httpx.Client(transport=httpx.MockTransport(oversize))
    )
    with pytest.raises(CbrSourceError) as exc:
        oversize_client.fetch_artifact(_reference(CbrBankForm.FORM_101))
    assert exc.value.code == CbrSourceStatus.ARTIFACT_TOO_LARGE


class _FakeInfo:
    def __init__(self, name: str, *, password: bool = False) -> None:
        self.filename = name
        self.file_size = 10
        self.compress_size = 8
        self.CRC = 0
        self.volume = 0
        self.file_redir = None
        self._password = password

    def is_file(self) -> bool:
        return True

    def is_symlink(self) -> bool:
        return False

    def needs_password(self) -> bool:
        return self._password


class _FakeRar:
    infos: list[_FakeInfo] = []
    solid = False

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def is_solid(self) -> bool:
        return self.solid

    def volumelist(self):
        return ["source.rar"]

    def infolist(self):
        return self.infos


@pytest.mark.parametrize(
    ("names", "code"),
    [
        (["../bad.dbf"], CbrSourceStatus.ARCHIVE_PATH_TRAVERSAL),
        (["A.dbf", "a.DBF"], CbrSourceStatus.ARCHIVE_DUPLICATE_MEMBER),
        (["not-text.txt"], CbrSourceStatus.UNSUPPORTED_ARCHIVE_FEATURE),
    ],
)
def test_archive_metadata_rejects_unsafe_members(monkeypatch, names, code) -> None:
    _FakeRar.infos = [_FakeInfo(name) for name in names]
    _FakeRar.solid = False
    monkeypatch.setattr("backend.app.services.cbr_bank_reporting.archive.rarfile.RarFile", _FakeRar)
    with pytest.raises(CbrSourceError) as exc:
        inspect_archive_bytes(b"Rar!\x1a\x07\x00synthetic")
    assert exc.value.code == code


def test_archive_runtime_and_exact_fixture_inventory() -> None:
    tool = resolve_libarchive_executable()
    assert tool.lower().endswith(("tar.exe", "bsdtar"))
    expected_members = {
        "101": {"072026B1.DBF", "072026N1.DBF", "NAMES.DBF"},
        "102": {"072026_P1.DBF", "072026NP1.DBF", "072026SP1.DBF", "SPRAV1.DBF", "SPRAV11.DBF"},
        "123": {"072026_123B.DBF", "072026_123D.DBF", "072026_123N.DBF"},
        "135": {"072026_135_3.DBF", "072026_135B.DBF"},
    }
    for form in CbrBankForm:
        extracted = extract_archive_members(_artifact(form))
        assert {member.normalized_name for member, _content in extracted} == expected_members[form.short_code]


def test_decimal_parser_preserves_blank_zero_and_exact_values() -> None:
    parser = object.__new__(ExactDecimalFieldParser)
    assert parser.parseN(None, b"        ") is None
    assert parser.parseN(None, b"0") == Decimal("0")
    assert parser.parseN(None, b"123") == Decimal("123")
    assert parser.parseN(None, b"123.450") == Decimal("123.450")
    assert parser.parseN(None, b"-0.125") == Decimal("-0.125")
    assert parser.parseN(None, b"7.625") == Decimal("7.625")
    assert parser.parseN(None, b"7,625") == Decimal("7.625")
    for invalid in (b"not-a-number", b"NaN", b"Infinity", b"-Infinity"):
        with pytest.raises(CbrSourceError) as exc:
            parser.parseN(None, invalid)
        assert exc.value.code == CbrSourceStatus.VALUE_PARSE_ERROR


def test_stock_dbfread_numeric_parser_is_never_used(monkeypatch) -> None:
    def fail_stock_parser(*_args, **_kwargs):
        raise AssertionError("stock dbfread parseN used")

    monkeypatch.setattr(FieldParser, "parseN", fail_stock_parser)
    extracted = extract_archive_members(_artifact(CbrBankForm.FORM_135))
    members = tuple(read_dbf_member(member, content) for member, content in extracted)
    assert members
    numeric_values = [
        value
        for member in members
        for record in member.records
        for _name, value in record
        if isinstance(value, Decimal)
    ]
    assert numeric_values
    assert not any(isinstance(value, float) for value in numeric_values)


def test_exact_fixtures_schema_counts_values_and_fail_closed_version(exact_snapshot) -> None:
    assert dict(exact_snapshot.records_by_form) == {
        "0409101": 25654,
        "0409102": 10079,
        "0409123": 1400,
        "0409135": 1709,
    }
    assert dict(exact_snapshot.subjects_by_form) == {
        "0409101": 353,
        "0409102": 212,
        "0409123": 352,
        "0409135": 345,
    }
    assert dict(exact_snapshot.subject_set_hashes) == {
        "0409101": "692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd",
        "0409102": "90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8",
        "0409123": "5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc",
        "0409135": "660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619",
    }
    assert sum(artifact.compressed_size for artifact in (_artifact(form) for form in CbrBankForm)) == 500661
    for result in exact_snapshot.forms:
        assert result.form_schema_fingerprint == APPROVED_FORM_SCHEMA_FINGERPRINTS[result.form]
        assert all(record.source_fields for record in result.records)
        assert all(record.regn and record.regn == record.regn.strip() for record in result.records)
    assert next(
        item for item in exact_snapshot.forms if item.form == CbrBankForm.FORM_101
    ).nomenclature_rows
    form123 = next(item for item in exact_snapshot.forms if item.form == CbrBankForm.FORM_123)
    form135 = next(item for item in exact_snapshot.forms if item.form == CbrBankForm.FORM_135)
    assert form123.source_codes == ("000", "102", "105", "203")
    assert form135.source_codes == (
        "N1.0", "N1.1", "N1.2", "N1.3", "N15", "N15.1", "N16",
        "N16.1", "N16.2", "N2", "N27", "N3", "N4",
    )
    assert "N18" not in form135.source_codes
    assert all(record.raw_unit == "RUB_THOUSANDS" for record in form123.records)
    assert all(record.raw_currency == "RUB" for record in form123.records)
    assert all(record.raw_multiplier == 1000 for record in form123.records)
    assert all(record.raw_unit == "PERCENT" for record in form135.records)
    assert all(record.raw_currency is None for record in form135.records)
    assert any(
        record.source_value is not None and record.source_value.as_tuple().exponent == -3
        for record in form135.records
    )
    assert sum(
        isinstance(value, float)
        for result in exact_snapshot.forms
        for record in result.records
        for _name, value in record.source_fields
    ) == 0

    extracted = extract_archive_members(_artifact(CbrBankForm.FORM_123))
    dbfs = tuple(read_dbf_member(member, content) for member, content in extracted)
    mutated = replace(dbfs[0], schema_fingerprint="0" * 64)
    with pytest.raises(CbrSourceError) as exc:
        parse_form(
            CbrBankForm.FORM_123,
            _artifact(CbrBankForm.FORM_123),
            (mutated, *dbfs[1:]),
        )
    assert exc.value.code == CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION


def test_bundle_overlap_is_derived_from_exact_regn_sets(exact_snapshot) -> None:
    forms = {item.form: set(item.subjects) for item in exact_snapshot.forms}
    expected = dict(exact_snapshot.cross_form_overlap)
    assert expected["101_102"] == len(forms[CbrBankForm.FORM_101] & forms[CbrBankForm.FORM_102])
    assert expected["101_123"] == len(forms[CbrBankForm.FORM_101] & forms[CbrBankForm.FORM_123])
    assert expected["101_102_123_135"] == len(set.intersection(*forms.values()))
    assert expected == {
        "101_102": 212,
        "101_123": 352,
        "101_135": 345,
        "102_123": 211,
        "102_135": 211,
        "123_135": 345,
        "101_102_123": 211,
        "101_102_135": 211,
        "101_123_135": 345,
        "102_123_135": 211,
        "101_102_123_135": 211,
    }
    assert dict(exact_snapshot.exclusive_membership_counts) == {
        "101": 0,
        "102": 0,
        "123": 0,
        "135": 0,
        "101_102": 1,
        "101_123": 7,
        "101_135": 0,
        "102_123": 0,
        "102_135": 0,
        "123_135": 0,
        "101_102_123": 0,
        "101_102_135": 0,
        "101_123_135": 134,
        "102_123_135": 0,
        "101_102_123_135": 211,
    }


def test_member_roles_and_value_bearing_subject_semantics() -> None:
    assert FORM_VALUE_MEMBERS == {
        CbrBankForm.FORM_101: "072026B1.DBF",
        CbrBankForm.FORM_102: "072026_P1.DBF",
        CbrBankForm.FORM_123: "072026_123D.DBF",
        CbrBankForm.FORM_135: "072026_135_3.DBF",
    }
    assert FORM_SUPPORT_MEMBERS[CbrBankForm.FORM_101] == {"072026N1.DBF"}
    assert FORM_NOMENCLATURE_MEMBERS[CbrBankForm.FORM_101] == {"NAMES.DBF"}

    def member(name, fields, records):
        definitions = tuple(
            DbfFieldDefinition(field_name, field_type, 20, 3)
            for field_name, field_type in fields
        )
        return DbfMember(
            name=name,
            content=b"",
            fields=definitions,
            records=tuple(tuple(row.items()) for row in records),
            encoding="cp866",
            schema_fingerprint="0" * 64,
        )

    value_member = member(
        "072026B1.dbf",
        (("REGN", "C"), ("PLAN", "C"), ("NUM_SC", "C"), ("A_P", "C"),
         ("VITG", "N"), ("IITG", "N"), ("DT", "D")),
        (
            {"REGN": "1", "PLAN": "A", "NUM_SC": "10", "A_P": "A",
             "VITG": Decimal("1"), "IITG": None, "DT": REPORT_DATE},
            {"REGN": "4", "PLAN": "A", "NUM_SC": "11", "A_P": "A",
             "VITG": None, "IITG": None, "DT": REPORT_DATE},
        ),
    )
    support_member = member(
        "072026N1.dbf",
        (("REGN", "C"), ("DT", "D")),
        ({"REGN": "2", "DT": REPORT_DATE},),
    )
    nomenclature_member = member(
        "NAMES.dbf",
        (("REGN", "C"), ("NUM_SC", "C"), ("NAME", "C")),
        ({"REGN": "3", "NUM_SC": "10", "NAME": "label"},),
    )
    result = parse_form(
        CbrBankForm.FORM_101,
        _artifact(CbrBankForm.FORM_101),
        (value_member, support_member, nomenclature_member),
        enforce_approved_schema=False,
    )
    assert result.subjects == ("1",)
    assert len(result.records) == 2
    assert result.records[1].disclosure_state.value == "PUBLIC_VALUE_BLANK"
    assert len(result.supporting_rows) == 1
    assert len(result.nomenclature_rows) == 1


def test_probe_is_compact_deterministic_and_sanitized(exact_snapshot, capsys) -> None:
    report = build_probe_report(exact_snapshot, generated_at=NOW)
    assert report["schema"] == "bondradar.cbr_bank_regulatory_bundle_probe.v1"
    assert report["status"] == "complete"
    assert report["database_accessed"] is False
    assert report["normalization_executed"] is False
    assert report["subject_set_hashes"] == dict(exact_snapshot.subject_set_hashes)
    assert report["exclusive_membership_counts"] == dict(
        exact_snapshot.exclusive_membership_counts
    )
    rendered = json.dumps(report, sort_keys=True)
    assert "NAME_B" not in rendered
    assert "source_fields" not in rendered
    assert "database_url" not in rendered.casefold()

    assert main(["--report-date", "bad", "--forms", "101"]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["error_code"] == "INVALID_ARGUMENTS"
    assert "exception" not in failed


def test_source_package_has_no_database_or_decision_engine_imports() -> None:
    source_root = Path("backend/app/services/cbr_bank_reporting")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in source_root.glob("*.py"))
    forbidden = ("sqlalchemy", "sessionlocal", "score", "legalissuer", "company")
    assert all(token not in combined.casefold() for token in forbidden)


def test_task251_audit_records_runtime_source_and_safety_contracts() -> None:
    document = Path(
        "docs/audits/TASK251_CBR_BANK_REGULATORY_READ_SOURCE_V1.md"
    ).read_text(encoding="utf-8")
    required = (
        "STARTING_SHA=071926270af44bb777f9e9e986bfa9dbe8233fa5",
        "ALEMBIC_HEAD=202608280002",
        "rarfile==4.5",
        "dbfread==2.0.7",
        "FIXTURE_COUNT=4",
        "TOTAL_COMPRESSED_BYTES=500661",
        "PIT_STATE=PIT_PARTIAL",
        "DEFAULT_DBFREAD_NUMERIC_USED_FOR_FINANCIAL_VALUES=false",
        "RAW_NUMERIC_FLOAT_COUNT=0",
        "DECIMAL_SAFETY=PASS",
        "TASK250_POST_IMPLEMENTATION_CORRECTION=true",
        "CORRECTION_SOURCE=TASK251_EXACT_IMMUTABLE_FIXTURE_VALUE_MEMBER_AUDIT",
        "RAR_RUNTIME_SCOPE=CURRENT_CBR_SUPPORTED_ARTIFACTS_ONLY",
        "SQLALCHEMY_IMPORTED=false",
        "DATABASE_PERSISTENCE=false",
        "NORMALIZATION_EXECUTED=false",
        "SCORING_EXECUTED=false",
        "PRODUCTION_ACTIONS=NONE",
        "Task252 — CBR REGN → LegalIssuer Identity Bridge v1",
    )
    assert all(item in document for item in required)
    assert "ALL_FOUR_INTERSECTION=211" in document
    assert "prior Task250 planning projection of `170`" in document
