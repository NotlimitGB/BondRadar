from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal

from .contracts import (
    CbrBankArtifact,
    CbrBankForm,
    CbrFormResult,
    CbrNomenclatureRow,
    CbrRawRecord,
    CbrSourceError,
    CbrSourceStatus,
    DbfMember,
    DisclosureState,
    RawScalar,
)


MAX_BUNDLE_RECORDS = 131_072
REQUIRED_DATA_FIELDS = {
    CbrBankForm.FORM_101: frozenset({"REGN", "PLAN", "NUM_SC", "A_P", "VITG", "IITG", "DT"}),
    CbrBankForm.FORM_102: frozenset({"REGN", "CODE", "SIM_R", "SIM_V", "SIM_ITOGO", "DT"}),
    CbrBankForm.FORM_123: frozenset({"REGN", "C1", "C3"}),
    CbrBankForm.FORM_135: frozenset({"REGN", "C1_3", "C2_3"}),
}

FORM_VALUE_MEMBERS = {
    CbrBankForm.FORM_101: "072026B1.DBF",
    CbrBankForm.FORM_102: "072026_P1.DBF",
    CbrBankForm.FORM_123: "072026_123D.DBF",
    CbrBankForm.FORM_135: "072026_135_3.DBF",
}
FORM_SUPPORT_MEMBERS = {
    CbrBankForm.FORM_101: frozenset({"072026N1.DBF"}),
    CbrBankForm.FORM_102: frozenset({"072026NP1.DBF", "072026SP1.DBF"}),
    CbrBankForm.FORM_123: frozenset({"072026_123B.DBF"}),
    CbrBankForm.FORM_135: frozenset({"072026_135B.DBF"}),
}
FORM_NOMENCLATURE_MEMBERS = {
    CbrBankForm.FORM_101: frozenset({"NAMES.DBF"}),
    CbrBankForm.FORM_102: frozenset({"SPRAV1.DBF", "SPRAV11.DBF"}),
    CbrBankForm.FORM_123: frozenset({"072026_123N.DBF"}),
    CbrBankForm.FORM_135: frozenset(),
}
FORM_LABEL_MEMBERS = {
    CbrBankForm.FORM_101: frozenset({"NAMES.DBF"}),
    CbrBankForm.FORM_102: frozenset({"SPRAV1.DBF"}),
    CbrBankForm.FORM_123: frozenset({"072026_123N.DBF"}),
    CbrBankForm.FORM_135: frozenset(),
}

# Filled only with fingerprints measured from the four approved 2026-08-01 fixtures.
APPROVED_FORM_SCHEMA_FINGERPRINTS: dict[CbrBankForm, str] = {
    CbrBankForm.FORM_101: "aa5ca40686c9dbc7b9eb1e2957d14b359fcc16fbe0797e201ff19b3627de38c6",
    CbrBankForm.FORM_102: "cfd92a9ec3148c4ef0c40741864930f3154bb3e961b78eab257cbdddefd3161b",
    CbrBankForm.FORM_123: "99dd4c23639bbc181afe40777a7fd52377024c87163bf76c6af9622ac9ec94d4",
    CbrBankForm.FORM_135: "bab052841d8a949af2ffb6f84b363921cb020b31aaefe64063e5e8bdc68f9809",
}


def compute_form_schema_fingerprint(members: tuple[DbfMember, ...]) -> str:
    projection = [
        [member.name.upper(), member.schema_fingerprint]
        for member in sorted(members, key=lambda item: item.name.upper())
    ]
    return hashlib.sha256(
        json.dumps(projection, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    ).hexdigest()


def _row_dict(row: tuple[tuple[str, RawScalar], ...]) -> dict[str, RawScalar]:
    return dict(row)


def _text(value: RawScalar) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    return text or None


def _decimal(value: RawScalar) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal):
        raise CbrSourceError(CbrSourceStatus.VALUE_PARSE_ERROR, "expected exact decimal")
    if not value.is_finite():
        raise CbrSourceError(CbrSourceStatus.VALUE_PARSE_ERROR, "non-finite decimal")
    return value


def _date(value: RawScalar) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise CbrSourceError(CbrSourceStatus.VALUE_PARSE_ERROR, "invalid source date")
    return value


def _data_member(form: CbrBankForm, members: tuple[DbfMember, ...]) -> DbfMember:
    required = REQUIRED_DATA_FIELDS[form]
    expected_name = FORM_VALUE_MEMBERS[form]
    candidates = [member for member in members if member.name.upper() == expected_name]
    if len(candidates) != 1 or not required.issubset(
        {field.name for field in candidates[0].fields}
    ):
        raise CbrSourceError(
            CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
            "required form value member is missing or invalid",
        )
    return candidates[0]


def _canonical_source_code(form: CbrBankForm, value: RawScalar) -> str | None:
    text = _text(value)
    if form == CbrBankForm.FORM_135 and text and text.startswith("Н"):
        return "N" + text[1:]
    return text


def _source_dates(members: tuple[DbfMember, ...]) -> dict[str, date]:
    result: dict[str, date] = {}
    for member in members:
        fields = {field.name for field in member.fields}
        if not {"REGN", "DT"}.issubset(fields):
            continue
        for row in member.records:
            values = _row_dict(row)
            regn = _text(values.get("REGN"))
            value = _date(values.get("DT"))
            if regn and value:
                previous = result.get(regn)
                if previous is not None and previous != value:
                    raise CbrSourceError(
                        CbrSourceStatus.VALUE_PARSE_ERROR,
                        "conflicting source dates for REGN",
                    )
                result[regn] = value
    return result


def _nomenclature(
    form: CbrBankForm, members: tuple[DbfMember, ...]
) -> tuple[dict[str, str], tuple[CbrNomenclatureRow, ...]]:
    nomenclature_members = FORM_NOMENCLATURE_MEMBERS[form]
    label_members = FORM_LABEL_MEMBERS[form]
    key_name = {
        CbrBankForm.FORM_101: "NUM_SC",
        CbrBankForm.FORM_102: "CODE",
        CbrBankForm.FORM_123: "C1",
        CbrBankForm.FORM_135: "C1_3",
    }[form]
    label_names = ("NAME", "NAME1", "C2_1", "C2_2", "C2_3")
    result: dict[str, str] = {}
    rows: list[CbrNomenclatureRow] = []
    for member in members:
        member_name = member.name.upper()
        if member_name not in nomenclature_members:
            continue
        fields = {field.name for field in member.fields}
        if key_name not in fields or not any(name in fields for name in label_names):
            continue
        for row in member.records:
            values = _row_dict(row)
            key = _canonical_source_code(form, values.get(key_name))
            labels = [_text(values.get(name)) for name in label_names]
            label = " ".join(item for item in labels if item) or None
            if key and label and member_name in label_members:
                previous = result.get(key)
                if previous is not None and previous != label:
                    raise CbrSourceError(
                        CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
                        "conflicting nomenclature labels",
                    )
                result[key] = label
            if key:
                rows.append(
                    CbrNomenclatureRow(
                        form=form,
                        source_member=member.name,
                        source_key=key,
                        label=label,
                        source_fields=row,
                    )
                )
    return result, tuple(rows)


def parse_form(
    form: CbrBankForm,
    artifact: CbrBankArtifact,
    members: tuple[DbfMember, ...],
    *,
    enforce_approved_schema: bool = True,
) -> CbrFormResult:
    fingerprint = compute_form_schema_fingerprint(members)
    approved = APPROVED_FORM_SCHEMA_FINGERPRINTS.get(form)
    if enforce_approved_schema and approved != fingerprint:
        raise CbrSourceError(
            CbrSourceStatus.UNSUPPORTED_SCHEMA_VERSION,
            "unapproved CBR form schema",
        )
    data_member = _data_member(form, members)
    dates_by_regn = _source_dates(members)
    labels_by_code, nomenclature_rows = _nomenclature(form, members)
    records: list[CbrRawRecord] = []
    subjects: set[str] = set()
    codes: set[str] = set()
    for index, row in enumerate(data_member.records, start=1):
        values = _row_dict(row)
        regn = _text(values.get("REGN"))
        if regn is None:
            raise CbrSourceError(
                CbrSourceStatus.VALUE_PARSE_ERROR, "missing reporting subject REGN"
            )
        source_code: str | None
        source_value: Decimal | None
        source_date = _date(values.get("DT")) or dates_by_regn.get(regn)
        raw_unit: str | None = None
        raw_currency: str | None = None
        raw_multiplier: int | None = None
        if form == CbrBankForm.FORM_101:
            source_code = _canonical_source_code(form, values.get("NUM_SC"))
            vitg = _decimal(values.get("VITG"))
            iitg = _decimal(values.get("IITG"))
            source_value = vitg if vitg is not None else iitg
            raw_unit = "RUB_THOUSANDS"
            raw_currency = "RUB"
            raw_multiplier = 1000
        elif form == CbrBankForm.FORM_102:
            source_code = _canonical_source_code(form, values.get("CODE"))
            source_value = _decimal(values.get("SIM_ITOGO"))
            raw_unit = "RUB_THOUSANDS"
            raw_currency = "RUB"
            raw_multiplier = 1000
        elif form == CbrBankForm.FORM_123:
            source_code = _canonical_source_code(form, values.get("C1"))
            source_value = _decimal(values.get("C3"))
            raw_unit = "RUB_THOUSANDS"
            raw_currency = "RUB"
            raw_multiplier = 1000
        else:
            source_code = _canonical_source_code(form, values.get("C1_3"))
            source_value = _decimal(values.get("C2_3"))
            raw_unit = "PERCENT"
        if source_code:
            codes.add(source_code)
        if source_value is not None:
            subjects.add(regn)
        records.append(
            CbrRawRecord(
                form=form,
                regn=regn,
                source_member=data_member.name,
                source_row_index=index,
                source_date=source_date,
                source_fields=row,
                source_code=source_code,
                source_label=labels_by_code.get(source_code) if source_code else None,
                source_value=source_value,
                raw_unit=raw_unit,
                raw_currency=raw_currency,
                raw_multiplier=raw_multiplier,
                disclosure_state=(
                    DisclosureState.PUBLIC_VALUE
                    if source_value is not None
                    else DisclosureState.PUBLIC_VALUE_BLANK
                ),
            )
        )
    support_members = FORM_SUPPORT_MEMBERS[form]
    supporting = tuple(
        row
        for member in members
        if member.name.upper() in support_members
        for row in member.records
    )
    return CbrFormResult(
        form=form,
        artifact=artifact,
        member_schema_fingerprints=tuple(
            (member.name.upper(), member.schema_fingerprint)
            for member in sorted(members, key=lambda item: item.name.upper())
        ),
        form_schema_fingerprint=fingerprint,
        records=tuple(records),
        nomenclature_rows=nomenclature_rows,
        supporting_rows=supporting,
        subjects=tuple(sorted(subjects)),
        source_codes=tuple(sorted(codes)),
    )
