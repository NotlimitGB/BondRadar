from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dbfread import DBF, FieldParser

from .contracts import (
    ArchiveMember,
    CbrSourceError,
    CbrSourceStatus,
    DbfFieldDefinition,
    DbfMember,
    RawScalar,
)


MAX_DBF_RECORDS = 65_536
SUPPORTED_FIELD_TYPES = frozenset({"C", "D", "L", "M", "N", "F"})
CBR_DOS_LANGUAGE_DRIVER = 0
CBR_DOS_ENCODING = "cp866"


class ExactDecimalFieldParser(FieldParser):
    def parseN(self, field: Any, data: bytes) -> Decimal | None:  # noqa: N802
        try:
            text = data.replace(b"\0", b"").strip().decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise CbrSourceError(
                CbrSourceStatus.VALUE_PARSE_ERROR, "invalid DBF numeric value"
            ) from exc
        if not text:
            return None
        if "," in text and "." not in text:
            text = text.replace(",", ".")
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise CbrSourceError(
                CbrSourceStatus.VALUE_PARSE_ERROR, "invalid DBF numeric value"
            ) from exc
        if not value.is_finite():
            raise CbrSourceError(
                CbrSourceStatus.VALUE_PARSE_ERROR, "non-finite DBF numeric value"
            )
        return value

    def parseF(self, field: Any, data: bytes) -> Decimal | None:  # noqa: N802
        return self.parseN(field, data)

    def parseB(self, field: Any, data: bytes) -> RawScalar:  # noqa: N802
        raise ValueError("binary floating-point DBF fields are unsupported")

    def parseO(self, field: Any, data: bytes) -> RawScalar:  # noqa: N802
        raise ValueError("binary double DBF fields are unsupported")


def _schema_fingerprint(fields: tuple[DbfFieldDefinition, ...]) -> str:
    projection = [
        [field.name.upper(), field.field_type.upper(), field.length, field.decimal_count]
        for field in fields
    ]
    return hashlib.sha256(
        json.dumps(projection, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def read_dbf_member(member: ArchiveMember, content: bytes) -> DbfMember:
    if len(content) < 32 or content[29] != CBR_DOS_LANGUAGE_DRIVER:
        raise CbrSourceError(
            CbrSourceStatus.INVALID_DBF, "unknown CBR DBF codepage marker"
        )
    with tempfile.TemporaryDirectory(prefix="bondradar-task251-dbf-") as directory:
        path = Path(directory) / "member.dbf"
        path.write_bytes(content)
        try:
            table = DBF(
                path,
                load=True,
                raw=False,
                ignore_missing_memofile=False,
                char_decode_errors="strict",
                parserclass=ExactDecimalFieldParser,
                encoding=CBR_DOS_ENCODING,
            )
            encoding = table.encoding
            fields = tuple(
                DbfFieldDefinition(
                    name=str(field.name).upper(),
                    field_type=str(field.type).upper(),
                    length=int(field.length),
                    decimal_count=int(field.decimal_count),
                )
                for field in table.fields
            )
            if any(field.field_type not in SUPPORTED_FIELD_TYPES for field in fields):
                raise CbrSourceError(
                    CbrSourceStatus.INVALID_DBF, "unsupported DBF field type"
                )
            deleted = tuple(table.deleted)
            if deleted:
                raise CbrSourceError(
                    CbrSourceStatus.INVALID_DBF, "deleted DBF rows are unsupported"
                )
            if len(table) > MAX_DBF_RECORDS:
                raise CbrSourceError(
                    CbrSourceStatus.INVALID_DBF, "DBF record limit exceeded"
                )
            records: list[tuple[tuple[str, RawScalar], ...]] = []
            for row in table:
                values: list[tuple[str, RawScalar]] = []
                for field in fields:
                    value = row[field.name]
                    if value is not None and not isinstance(
                        value, (str, Decimal, date, datetime, bool, bytes)
                    ):
                        raise CbrSourceError(
                            CbrSourceStatus.INVALID_DBF,
                            "unsupported DBF scalar value",
                        )
                    values.append((field.name, value))
                records.append(tuple(values))
        except CbrSourceError:
            raise
        except (LookupError, UnicodeError, ValueError, OSError) as exc:
            raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "invalid DBF content") from exc
    return DbfMember(
        name=member.name,
        content=content,
        fields=fields,
        records=tuple(records),
        encoding=str(encoding),
        schema_fingerprint=_schema_fingerprint(fields),
    )
