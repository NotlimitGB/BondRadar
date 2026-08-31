from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from .contracts import (
    FINORG_URL,
    MAX_OGRNS_PER_REQUEST,
    MAX_TOTAL_OGRNS_PER_RUN,
    CbrBridgeError,
    CbrBridgeSourceStatus,
    FinOrgRecord,
    FinOrgSearchResult,
    canonical_inn,
    canonical_ogrn,
    optional_text,
    utc_datetime,
)
from .transport import CbrIdentityHttpTransport, SOAP_LIMIT_BYTES


SOAP_NAMESPACE = "http://schemas.xmlsoap.org/soap/envelope/"
SERVICE_NAMESPACE = "http://web.cbr.ru/"
SEARCH_ACTION = f"{SERVICE_NAMESPACE}SearchByOGRNs"
LAST_UPDATE_ACTION = f"{SERVICE_NAMESPACE}GetLastUpdate"
_ALLOWED_NAMESPACES = frozenset(
    {
        "",
        SOAP_NAMESPACE,
        SERVICE_NAMESPACE,
        "http://www.w3.org/2001/XMLSchema",
        "http://www.w3.org/2001/XMLSchema-instance",
        "urn:schemas-microsoft-com:xml-diffgram-v1",
        "urn:schemas-microsoft-com:xml-msdata",
    }
)


def _tag_parts(tag: str) -> tuple[str, str]:
    if tag.startswith("{"):
        namespace, local = tag[1:].split("}", 1)
        return namespace, local
    return "", tag


def _safe_xml(content: bytes) -> ET.Element:
    if len(content) > SOAP_LIMIT_BYTES:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.RESPONSE_TOO_LARGE, "FinOrg response limit exceeded"
        )
    upper = content.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_XML, "unsafe FinOrg XML construct"
        )
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_XML, "malformed FinOrg XML"
        ) from exc
    for element in root.iter():
        namespace, _local = _tag_parts(element.tag)
        if namespace not in _ALLOWED_NAMESPACES:
            raise CbrBridgeError(
                CbrBridgeSourceStatus.INVALID_XML, "unexpected FinOrg XML namespace"
            )
    fault = next(
        (element for element in root.iter() if _tag_parts(element.tag)[1] == "Fault"),
        None,
    )
    if fault is not None:
        raise CbrBridgeError(CbrBridgeSourceStatus.SOAP_FAULT, "FinOrg SOAP fault")
    return root


def _first(root: ET.Element, local_name: str) -> ET.Element | None:
    return next(
        (element for element in root.iter() if _tag_parts(element.tag)[1] == local_name),
        None,
    )


def _child_text(root: ET.Element, local_name: str) -> str | None:
    element = next(
        (child for child in root.iter() if _tag_parts(child.tag)[1] == local_name),
        None,
    )
    return optional_text(element.text if element is not None else None)


def build_search_by_ogrns_request(ogrns: tuple[str, ...]) -> bytes:
    if not ogrns or len(ogrns) > MAX_OGRNS_PER_REQUEST:
        raise ValueError("FinOrg request must contain 1..100 OGRNs")
    normalized = tuple(canonical_ogrn(value) for value in ogrns)
    if len(set(normalized)) != len(normalized):
        raise ValueError("FinOrg request OGRNs must be unique")
    canonical = tuple(sorted(normalized))
    envelope = ET.Element(f"{{{SOAP_NAMESPACE}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_NAMESPACE}}}Body")
    operation = ET.SubElement(body, f"{{{SERVICE_NAMESPACE}}}SearchByOGRNs")
    container = ET.SubElement(operation, f"{{{SERVICE_NAMESPACE}}}OGRNs")
    for ogrn in canonical:
        ET.SubElement(container, f"{{{SERVICE_NAMESPACE}}}OGRN").text = ogrn
    ET.register_namespace("soap", SOAP_NAMESPACE)
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def build_get_last_update_request() -> bytes:
    envelope = ET.Element(f"{{{SOAP_NAMESPACE}}}Envelope")
    body = ET.SubElement(envelope, f"{{{SOAP_NAMESPACE}}}Body")
    ET.SubElement(body, f"{{{SERVICE_NAMESPACE}}}GetLastUpdate")
    ET.register_namespace("soap", SOAP_NAMESPACE)
    return ET.tostring(envelope, encoding="utf-8", xml_declaration=True)


def parse_search_by_ogrns_response(
    content: bytes,
    *,
    requested_ogrns: tuple[str, ...],
) -> FinOrgSearchResult:
    requested = tuple(canonical_ogrn(value) for value in requested_ogrns)
    requested_set = set(requested)
    root = _safe_xml(content)
    result = _first(root, "SearchByOGRNsResult")
    if result is None:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_XML, "FinOrg result is missing"
        )
    success = (_child_text(result, "IsSucess") or "").casefold()
    source_error = _child_text(result, "Error")
    if success != "true":
        raise CbrBridgeError(
            CbrBridgeSourceStatus.SOURCE_DECLARED_FAILURE,
            "FinOrg declared source failure",
        )
    ds = _first(result, "DS")
    if ds is None:
        raise CbrBridgeError(CbrBridgeSourceStatus.INVALID_XML, "FinOrg DS is missing")
    records: set[FinOrgRecord] = set()
    for node in ds.iter():
        if _tag_parts(node.tag)[1] != "Record":
            continue
        raw_ogrn = _child_text(node, "OGRN")
        if raw_ogrn is None:
            raise CbrBridgeError(
                CbrBridgeSourceStatus.INVALID_CONTENT, "FinOrg record OGRN is missing"
            )
        try:
            ogrn = canonical_ogrn(raw_ogrn)
        except ValueError as exc:
            raise CbrBridgeError(
                CbrBridgeSourceStatus.INVALID_CONTENT, "invalid FinOrg OGRN"
            ) from exc
        if ogrn not in requested_set:
            raise CbrBridgeError(
                CbrBridgeSourceStatus.INVALID_CONTENT,
                "FinOrg returned an unrequested OGRN",
            )
        raw_inn = _child_text(node, "INN")
        if raw_inn is None:
            inn = None
            inn_status = "MISSING"
        else:
            try:
                inn = canonical_inn(raw_inn)
                inn_status = "VALID"
            except ValueError:
                inn = None
                inn_status = "INVALID"
        records.add(
            FinOrgRecord(
                source_id=_child_text(node, "Id"),
                ogrn=ogrn,
                inn=inn,
                inn_status=inn_status,
                name=_child_text(node, "Name"),
                status=_child_text(node, "Status"),
                error_text=_child_text(node, "ErrorText"),
            )
        )
    return FinOrgSearchResult(
        requested_ogrns=requested,
        records=tuple(
            sorted(
                records,
                key=lambda item: (
                    item.ogrn,
                    item.inn or "",
                    item.error_text or "",
                    item.source_id or "",
                ),
            )
        ),
        source_error=source_error,
    )


def parse_get_last_update_response(content: bytes) -> datetime:
    root = _safe_xml(content)
    result = _first(root, "GetLastUpdateResult")
    value = optional_text(result.text if result is not None else None)
    if value is None:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_XML, "FinOrg last update is missing"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CbrBridgeError(
            CbrBridgeSourceStatus.INVALID_CONTENT, "invalid FinOrg last update"
        ) from exc
    return utc_datetime(parsed, field_name="finorg_last_update")


class CbrFinOrgClient:
    def __init__(self, transport: CbrIdentityHttpTransport | None = None) -> None:
        self.transport = transport or CbrIdentityHttpTransport()

    def get_last_update(self) -> datetime:
        content = self.transport.post_soap(
            FINORG_URL,
            action=LAST_UPDATE_ACTION,
            body=build_get_last_update_request(),
        )
        return parse_get_last_update_response(content)

    def search_by_ogrns(self, ogrns: tuple[str, ...]) -> FinOrgSearchResult:
        canonical = tuple(sorted({canonical_ogrn(value) for value in ogrns}))
        if len(canonical) > MAX_TOTAL_OGRNS_PER_RUN:
            raise ValueError("FinOrg bridge run exceeds 1,000 OGRNs")
        records: list[FinOrgRecord] = []
        errors: list[str] = []
        for start in range(0, len(canonical), MAX_OGRNS_PER_REQUEST):
            batch = canonical[start : start + MAX_OGRNS_PER_REQUEST]
            content = self.transport.post_soap(
                FINORG_URL,
                action=SEARCH_ACTION,
                body=build_search_by_ogrns_request(batch),
            )
            result = parse_search_by_ogrns_response(content, requested_ogrns=batch)
            records.extend(result.records)
            if result.source_error:
                errors.append(result.source_error)
        return FinOrgSearchResult(
            requested_ogrns=canonical,
            records=tuple(
                sorted(
                    set(records),
                    key=lambda item: (
                        item.ogrn,
                        item.inn or "",
                        item.error_text or "",
                        item.source_id or "",
                    ),
                )
            ),
            source_error="; ".join(sorted(set(errors))) or None,
        )
