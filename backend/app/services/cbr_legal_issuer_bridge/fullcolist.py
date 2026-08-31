from __future__ import annotations

import re
from datetime import date, datetime
from html.parser import HTMLParser

from .contracts import (
    FULLCOLIST_URL,
    CbrBridgeError,
    CbrBridgeSourceStatus,
    CbrCreditOrganizationRegistryRecord,
    CbrCreditOrganizationRegistrySnapshot,
    canonical_ogrn,
    canonical_regn,
    optional_text,
    utc_datetime,
)
from .transport import CbrIdentityHttpTransport


_AS_OF = re.compile(r"по\s+состоянию\s+на\s+(\d{2}\.\d{2}\.\d{4})", re.IGNORECASE)


def _normal_header(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page_text: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        self.page_text.append(data)
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


def _column_indexes(headers: list[str]) -> dict[str, int]:
    normalized = [_normal_header(item) for item in headers]

    def find(*needles: str) -> int | None:
        for index, header in enumerate(normalized):
            if all(needle in header for needle in needles):
                return index
        return None

    mapping = {
        "regn": find("регистрацион", "номер"),
        "ogrn": find("огрн"),
        "name": find("наименование"),
        "organization_type": find("вид"),
        "legal_form": find("организационно-правовая"),
        "registration_date": find("дата", "регистрац"),
        "license_status": find("статус", "лиценз"),
        "location": find("местонахожд"),
    }
    if any(mapping[key] is None for key in ("regn", "ogrn", "name")):
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT,
            "FullCoList required columns are missing",
        )
    return {key: value for key, value in mapping.items() if value is not None}


def parse_fullcolist_html(
    content: bytes,
    *,
    retrieved_at: datetime,
) -> CbrCreditOrganizationRegistrySnapshot:
    try:
        html = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT, "invalid FullCoList encoding"
        ) from exc
    parser = _TableParser()
    try:
        parser.feed(html)
    except Exception as exc:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT, "malformed FullCoList HTML"
        ) from exc
    match = _AS_OF.search(" ".join(parser.page_text))
    if match is None:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT,
            "FullCoList registry date is missing",
        )
    registry_as_of = datetime.strptime(match.group(1), "%d.%m.%Y").date()
    selected: tuple[list[list[str]], dict[str, int]] | None = None
    for table in parser.tables:
        for row_index, headers in enumerate(table):
            try:
                indexes = _column_indexes(headers)
            except CbrBridgeError:
                continue
            selected = (table[row_index + 1 :], indexes)
            break
        if selected is not None:
            break
    if selected is None:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT, "FullCoList table is missing"
        )
    rows, indexes = selected
    observed = utc_datetime(retrieved_at, field_name="retrieved_at")
    records: set[CbrCreditOrganizationRegistryRecord] = set()
    max_index = max(indexes.values())
    for row in rows:
        if len(row) <= max_index or not any(cell.strip() for cell in row):
            continue
        try:
            regn = canonical_regn(row[indexes["regn"]])
        except ValueError as exc:
            raise CbrBridgeError(
                CbrBridgeSourceStatus.INVALID_CONTENT, "invalid FullCoList REGN"
            ) from exc
        raw_ogrn = row[indexes["ogrn"]].strip()
        if raw_ogrn:
            try:
                ogrn = canonical_ogrn(raw_ogrn)
            except ValueError as exc:
                raise CbrBridgeError(
                    CbrBridgeSourceStatus.INVALID_CONTENT, "invalid FullCoList OGRN"
                ) from exc
        else:
            ogrn = None
        name = optional_text(row[indexes["name"]])
        if name is None:
            raise CbrBridgeError(
                CbrBridgeSourceStatus.INVALID_CONTENT, "missing FullCoList name"
            )
        registration_date: date | None = None
        if "registration_date" in indexes:
            raw_date = row[indexes["registration_date"]].strip()
            if raw_date:
                try:
                    registration_date = datetime.strptime(raw_date, "%d.%m.%Y").date()
                except ValueError as exc:
                    raise CbrBridgeError(
                        CbrBridgeSourceStatus.INVALID_CONTENT,
                        "invalid FullCoList registration date",
                    ) from exc
        records.add(
            CbrCreditOrganizationRegistryRecord(
                regn=regn,
                ogrn=ogrn,
                name=name,
                organization_type=optional_text(row[indexes["organization_type"]])
                if "organization_type" in indexes
                else None,
                legal_form=optional_text(row[indexes["legal_form"]])
                if "legal_form" in indexes
                else None,
                registration_date=registration_date,
                license_status=optional_text(row[indexes["license_status"]])
                if "license_status" in indexes
                else None,
                location=optional_text(row[indexes["location"]])
                if "location" in indexes
                else None,
                registry_as_of=registry_as_of,
                retrieved_at=observed,
            )
        )
    if not records:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT, "FullCoList has no records"
        )
    by_regn: dict[str, set[str]] = {}
    by_ogrn: dict[str, set[str]] = {}
    for record in records:
        if record.ogrn is not None:
            by_regn.setdefault(record.regn, set()).add(record.ogrn)
            by_ogrn.setdefault(record.ogrn, set()).add(record.regn)
    ambiguous_regns = tuple(
        sorted((regn for regn, ogrns in by_regn.items() if len(ogrns) > 1), key=int)
    )
    conflicting_ogrns = tuple(
        sorted(ogrn for ogrn, regns in by_ogrn.items() if len(regns) > 1)
    )
    ordered = tuple(
        sorted(
            records,
            key=lambda item: (int(item.regn), item.ogrn or "", item.name),
        )
    )
    return CbrCreditOrganizationRegistrySnapshot(
        records=ordered,
        registry_as_of=registry_as_of,
        retrieved_at=observed,
        ambiguous_regns=ambiguous_regns,
        conflicting_ogrns=conflicting_ogrns,
    )


class CbrFullCoListClient:
    def __init__(self, transport: CbrIdentityHttpTransport | None = None) -> None:
        self.transport = transport or CbrIdentityHttpTransport()

    def fetch(self, *, retrieved_at: datetime) -> CbrCreditOrganizationRegistrySnapshot:
        return parse_fullcolist_html(
            self.transport.get_html(FULLCOLIST_URL), retrieved_at=retrieved_at
        )
