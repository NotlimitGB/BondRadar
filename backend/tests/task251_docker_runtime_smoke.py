"""Dependency-free in-image runtime proof for Task251's exact CBR fixtures."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import dbfread
import rarfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.cbr_bank_reporting import (
    CbrBankForm,
    CbrBankRegulatoryBundleService,
)
from app.services.cbr_bank_reporting.archive import resolve_libarchive_executable
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
)
from app.services.cbr_bank_reporting.dbf import ExactDecimalFieldParser
from app.services.cbr_bank_reporting.parsers import APPROVED_FORM_SCHEMA_FINGERPRINTS


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"
REPORT_DATE = date(2026, 8, 1)
OBSERVED_AT = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
EXPECTED_ARTIFACTS = {
    CbrBankForm.FORM_101: (360046, "7863e1f4e8c6d81ab15576275556c32aebffccca50c48bedf0f8e61163adb54a"),
    CbrBankForm.FORM_102: (74392, "0a4bc3606d42faefd2af73ab6443d24fa57e7251d8cedc491f4f47aef1321c21"),
    CbrBankForm.FORM_123: (33042, "6da408180123fa6748399acb89c717e3fc32380ee818679248043daa9a60baab"),
    CbrBankForm.FORM_135: (33181, "061a00791196d660bdb070de890228c820ea7e0d8af7978309b11b4226ed4776"),
}


def _artifact(form: CbrBankForm) -> CbrBankArtifact:
    filename = f"{form.short_code}-20260801.rar"
    content = (FIXTURE_ROOT / filename).read_bytes()
    expected_size, expected_hash = EXPECTED_ARTIFACTS[form]
    assert len(content) == expected_size
    assert hashlib.sha256(content).hexdigest() == expected_hash
    reference = CbrArtifactReference(
        form=form,
        source_href=f"/vfs/credit/forms/{filename}",
        source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
        artifact_filename=filename,
        report_date=REPORT_DATE,
        discovered_at=OBSERVED_AT,
    )
    return CbrBankArtifact(
        reference=reference,
        content=content,
        content_sha256=expected_hash,
        compressed_size=expected_size,
        content_type="application/octet-stream",
        retrieved_at=OBSERVED_AT,
    )


def _all_values(snapshot):
    for result in snapshot.forms:
        for record in result.records:
            yield record.source_value
            for _name, value in record.source_fields:
                yield value
        for row in result.supporting_rows:
            for _name, value in row:
                yield value
        for row in result.nomenclature_rows:
            for _name, value in row.source_fields:
                yield value


def main() -> None:
    tool = resolve_libarchive_executable()
    assert tool == "/usr/bin/bsdtar"
    snapshot = CbrBankRegulatoryBundleService(
        archive_executable=tool
    ).build_snapshot(
        report_date=REPORT_DATE,
        artifacts=tuple(_artifact(form) for form in CbrBankForm),
    )
    assert dict(snapshot.records_by_form) == {
        "0409101": 25654,
        "0409102": 10079,
        "0409123": 1400,
        "0409135": 1709,
    }
    assert dict(snapshot.subjects_by_form) == {
        "0409101": 353,
        "0409102": 212,
        "0409123": 352,
        "0409135": 345,
    }
    assert dict(snapshot.subject_set_hashes) == {
        "0409101": "692b9d3d9363eec48585ceee3a55a1de1326464dad71508616a7c1fa850be3cd",
        "0409102": "90597ce74009c57587355c15088af63b23d46f5adf5f1028c117fbc94e67f1e8",
        "0409123": "5dc0b52ec11e505dcbb868bac2760dc247982de03b89d9f150c798ad0cbc5ecc",
        "0409135": "660686ab74aef07f773a7001874d3d82567cb236a5f28ab1f55362b0e120c619",
    }
    overlap = dict(snapshot.cross_form_overlap)
    assert overlap["101_102"] == 212
    assert overlap["101_123"] == 352
    assert overlap["101_135"] == 345
    assert overlap["102_123"] == 211
    assert overlap["102_135"] == 211
    assert overlap["123_135"] == 345
    assert overlap["101_102_123_135"] == 211
    exclusive = dict(snapshot.exclusive_membership_counts)
    assert exclusive["101_102"] == 1
    assert exclusive["101_123"] == 7
    assert exclusive["101_123_135"] == 134
    assert exclusive["101_102_123_135"] == 211
    assert sum(exclusive.values()) == 353
    assert all(
        result.form_schema_fingerprint == APPROVED_FORM_SCHEMA_FINGERPRINTS[result.form]
        for result in snapshot.forms
    )

    values = tuple(_all_values(snapshot))
    assert sum(isinstance(value, float) for value in values) == 0
    assert any(value is None for value in values)
    assert any(value == Decimal("0") for value in values if isinstance(value, Decimal))
    assert any(
        value.as_tuple().exponent == -3
        for value in values
        if isinstance(value, Decimal)
    )
    parser = object.__new__(ExactDecimalFieldParser)
    assert parser.parseN(None, b"        ") is None
    assert parser.parseN(None, b"0") == Decimal("0")
    assert parser.parseN(None, b"123.450") == Decimal("123.450")
    assert parser.parseN(None, b"-0.125") == Decimal("-0.125")

    print(
        json.dumps(
            {
                "status": "PASS",
                "bsdtar": tool,
                "rarfile": getattr(rarfile, "__version__", "4.5"),
                "dbfread": getattr(dbfread, "__version__", "2.0.7"),
                "fixtures": 4,
                "all_four": 211,
                "raw_numeric_float_count": 0,
                "decimal_safe": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
