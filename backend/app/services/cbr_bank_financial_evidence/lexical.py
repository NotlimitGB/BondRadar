from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.services.cbr_bank_reporting.archive import (
    MAX_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    extract_archive_members,
)
from app.services.cbr_bank_reporting.contracts import (
    CbrBankForm,
    CbrFormResult,
    CbrSourceError,
    CbrSourceStatus,
    DbfFieldDefinition,
)
from app.services.cbr_bank_reporting.dbf import read_dbf_member
from app.services.cbr_bank_reporting.parsers import resolve_data_member

from .contracts import CONTRACT_VERSION, ExactFormEvidence, ExactLexicalObservation
from .fingerprints import json_scalar, sha256_canonical


_VALUE_FIELDS = {
    CbrBankForm.FORM_102: "SIM_ITOGO",
    CbrBankForm.FORM_123: "C3",
    CbrBankForm.FORM_135: "C2_3",
}
_DIMENSION_FIELDS = {
    CbrBankForm.FORM_101: ("PLAN", "NUM_SC", "A_P"),
    CbrBankForm.FORM_102: ("CODE",),
    CbrBankForm.FORM_123: ("C1",),
    CbrBankForm.FORM_135: ("C1_3",),
}


@dataclass(frozen=True, slots=True)
class _FieldSlice:
    definition: DbfFieldDefinition
    offset: int


def _dbf_layout(content: bytes) -> tuple[int, int, int, tuple[_FieldSlice, ...]]:
    if len(content) < 33:
        raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "DBF header is truncated")
    record_count = int.from_bytes(content[4:8], "little")
    header_length = int.from_bytes(content[8:10], "little")
    record_length = int.from_bytes(content[10:12], "little")
    if header_length < 33 or record_length < 2 or header_length > len(content):
        raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "invalid DBF layout")
    fields: list[_FieldSlice] = []
    descriptor_offset = 32
    field_offset = 1
    while descriptor_offset < header_length:
        if content[descriptor_offset] == 0x0D:
            break
        descriptor = content[descriptor_offset : descriptor_offset + 32]
        if len(descriptor) != 32:
            raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "DBF field is truncated")
        try:
            name = (
                descriptor[:11].split(b"\0", 1)[0].decode("ascii", errors="strict").upper()
            )
            field_type = chr(descriptor[11]).upper()
        except (UnicodeError, ValueError) as exc:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_DBF, "invalid DBF field descriptor"
            ) from exc
        length = int(descriptor[16])
        decimal_count = int(descriptor[17])
        if not name or length < 1:
            raise CbrSourceError(
                CbrSourceStatus.INVALID_DBF, "invalid DBF field descriptor"
            )
        fields.append(
            _FieldSlice(
                definition=DbfFieldDefinition(
                    name=name,
                    field_type=field_type,
                    length=length,
                    decimal_count=decimal_count,
                ),
                offset=field_offset,
            )
        )
        field_offset += length
        descriptor_offset += 32
    if descriptor_offset >= header_length or content[descriptor_offset] != 0x0D:
        raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "DBF terminator is missing")
    if field_offset != record_length:
        raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "DBF record layout mismatch")
    if header_length + record_count * record_length > len(content):
        raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "DBF records are truncated")
    return header_length, record_length, record_count, tuple(fields)


def _parse_numeric_lexical(raw: bytes) -> tuple[str, Decimal | None]:
    try:
        lexical = raw.replace(b"\0", b"").strip(b" ").decode(
            "ascii", errors="strict"
        )
    except UnicodeError as exc:
        raise CbrSourceError(
            CbrSourceStatus.VALUE_PARSE_ERROR, "invalid DBF numeric lexical value"
        ) from exc
    if not lexical:
        return "", None
    decimal_text = lexical.replace(",", ".") if "," in lexical and "." not in lexical else lexical
    try:
        value = Decimal(decimal_text)
    except InvalidOperation as exc:
        raise CbrSourceError(
            CbrSourceStatus.VALUE_PARSE_ERROR, "invalid DBF numeric lexical value"
        ) from exc
    if not value.is_finite():
        raise CbrSourceError(
            CbrSourceStatus.VALUE_PARSE_ERROR, "non-finite DBF numeric lexical value"
        )
    return lexical, value


def extract_exact_form_evidence(
    form_result: CbrFormResult,
    *,
    archive_executable: str | None = None,
    allow_dynamic_value_member: bool = False,
    max_archive_member_bytes: int = MAX_MEMBER_BYTES,
    max_archive_total_uncompressed_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES,
) -> ExactFormEvidence:
    extracted = extract_archive_members(
        form_result.artifact,
        executable=archive_executable,
        max_member_bytes=max_archive_member_bytes,
        max_total_uncompressed_bytes=max_archive_total_uncompressed_bytes,
    )
    dbf_members = tuple(
        read_dbf_member(member, payload) for member, payload in extracted
    )
    actual_inventory = tuple(
        (member.name.upper(), member.schema_fingerprint)
        for member in sorted(dbf_members, key=lambda item: item.name.upper())
    )
    if actual_inventory != form_result.member_schema_fingerprints:
        raise CbrSourceError(
            CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
            "Task251 member schema inventory changed",
        )
    data_member = resolve_data_member(
        form_result.form,
        dbf_members,
        allow_dynamic_value_member=allow_dynamic_value_member,
    )
    matches = [
        (member, payload)
        for (archive_member, payload), member in zip(extracted, dbf_members)
        if member is data_member
    ]
    if len(matches) != 1:
        raise CbrSourceError(
            CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
            "Task251 value member is not unique",
        )
    member, content = matches[0]
    expected_member_name = member.name.upper()
    header_length, record_length, record_count, fields = _dbf_layout(content)
    if tuple(item.definition for item in fields) != member.fields:
        raise CbrSourceError(
            CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
            "DBF descriptor inventory changed",
        )
    if record_count != len(member.records) or record_count != len(form_result.records):
        raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "DBF record count mismatch")
    field_slices = {item.definition.name: item for item in fields}
    observations: list[ExactLexicalObservation] = []
    for expected_index, (parsed_row, record) in enumerate(
        zip(member.records, form_result.records), start=1
    ):
        if record.source_row_index != expected_index or record.source_member.upper() != expected_member_name:
            raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "Task251 row lineage mismatch")
        start = header_length + (expected_index - 1) * record_length
        raw_record = content[start : start + record_length]
        if len(raw_record) != record_length or raw_record[:1] != b" ":
            raise CbrSourceError(CbrSourceStatus.INVALID_DBF, "deleted or invalid DBF row")
        parsed_values = dict(parsed_row)
        if form_result.form == CbrBankForm.FORM_101:
            value_field = "VITG" if parsed_values.get("VITG") is not None else "IITG"
        else:
            value_field = _VALUE_FIELDS[form_result.form]
        selected = field_slices.get(value_field)
        if selected is None or selected.definition.field_type not in {"N", "F"}:
            raise CbrSourceError(
                CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
                "Task251 selected value field is invalid",
            )
        raw_value = raw_record[
            selected.offset : selected.offset + selected.definition.length
        ]
        lexical, decimal_value = _parse_numeric_lexical(raw_value)
        if decimal_value != record.source_value:
            raise CbrSourceError(
                CbrSourceStatus.VALUE_PARSE_ERROR,
                "raw lexical value disagrees with Task251 Decimal",
            )
        raw_fields = []
        for item in fields:
            raw_field = raw_record[
                item.offset : item.offset + item.definition.length
            ]
            raw_fields.append(
                {
                    "name": item.definition.name,
                    "type": item.definition.field_type,
                    "length": item.definition.length,
                    "decimal_count": item.definition.decimal_count,
                    "raw": raw_field,
                }
            )
        source_fields_sha256 = sha256_canonical(raw_fields)
        dimensions = tuple(
            (name, json_scalar(parsed_values.get(name)))
            for name in _DIMENSION_FIELDS[form_result.form]
        )
        source_row_fingerprint = sha256_canonical(
            {
                "contract_version": CONTRACT_VERSION,
                "form": form_result.form.value,
                "report_date": form_result.artifact.reference.report_date,
                "archive_member_name": member.name.upper(),
                "source_row_number": expected_index,
                "source_fields_sha256": source_fields_sha256,
            }
        )
        observations.append(
            ExactLexicalObservation(
                record=record,
                source_value_field=value_field,
                raw_value_text=lexical,
                parsed_decimal_value=decimal_value,
                source_dimensions=dimensions,
                source_fields_sha256=source_fields_sha256,
                source_row_fingerprint=source_row_fingerprint,
            )
        )
    return ExactFormEvidence(
        form=form_result.form.value,
        report_date=form_result.artifact.reference.report_date,
        value_member_name=member.name,
        observations=tuple(observations),
    )
