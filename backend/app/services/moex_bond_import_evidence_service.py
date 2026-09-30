"""Read-only, source-separated MOEX evidence acquisition for Task296C1."""

from __future__ import annotations

from typing import Any

from app.schemas.tinvest_bond_import_preflight import (
    MoexBondImportBoardCandidateEvidence,
    MoexBondImportBoardObservation,
    MoexBondImportCandidateEvidence,
    MoexBondImportDescriptionProjection,
    MoexBondImportEvidenceBatch,
    MoexBondImportIssuerEvidence,
)
from app.schemas.tinvest_bond_admission_manifest import TInvestBondAdmissionManifestView
from app.services.moex_iss_client import MoexIssClient
from app.services.moex_issuer_identity_source_service import (
    MoexIssuerIdentitySourceService,
)
from app.services.tinvest_bond_import_preflight_service import (
    TInvestBondImportPreflightService,
)


_TQCB = "TQCB"
_PAGE_SIZE = 100
_STRUCTURAL_FIELDS = (
    "BONDTYPE",
    "BONDSUBTYPE",
    "COUPONFREQUENCY",
    "ISPERPETUAL",
    "ISSUBORDINATED",
    "AMORTIZATION",
)
_ISSUER_MATCH_STATUSES = {
    "EXACT_SECID",
    "EXACT_SECID_ISIN_CORROBORATED",
    "EXACT_ISIN_RECOVERED",
    "SECURITY_IDENTIFIER_MISSING",
    "SECURITY_NOT_FOUND",
    "SECURITY_AMBIGUOUS",
    "SECURITY_IDENTIFIER_CONFLICT",
    "SOURCE_ERROR",
}
_ISSUER_METADATA_STATUSES = {"ISSUER_COMPLETE", "ISSUER_PARTIAL", "ISSUER_MISSING"}


def _optional_text(value: object) -> str | None:
    return value if type(value) is str else None


class MoexBondImportEvidenceService:
    """Acquire reference, TQCB-universe and description evidence separately.

    The service never persists data. Its output is an immutable evidence batch
    consumed by the pure Task296C classifier.
    """

    def __init__(self, client: MoexIssClient) -> None:
        self.client = client

    def build(self, *, admission_manifest: TInvestBondAdmissionManifestView) -> MoexBondImportEvidenceBatch:
        # Reject stale/tampered candidate input before any source request.
        TInvestBondImportPreflightService.validate_admission_manifest(admission_manifest)
        candidates = admission_manifest.import_candidate_manifest
        board_rows, board_status, board_pages, board_warnings = self._scan_tqcb() if candidates else (
            [],
            "COMPLETE",
            0,
            0,
        )

        candidate_rows: list[MoexBondImportCandidateEvidence] = []
        total_reference_queries = 0
        for candidate in candidates:
            try:
                resolution = MoexIssuerIdentitySourceService(self.client).lookup(
                    requested_secid=candidate.matched_secid,
                    expected_isin=candidate.isin,
                )
            except Exception:
                resolution = None
            security_status = (
                resolution.security_match_status
                if resolution is not None
                and resolution.security_match_status in _ISSUER_MATCH_STATUSES
                else "SOURCE_ERROR"
            )
            issuer_status = (
                resolution.issuer_metadata_status
                if resolution is not None
                and resolution.issuer_metadata_status in _ISSUER_METADATA_STATUSES
                else "ISSUER_MISSING"
            )
            issuer = MoexBondImportIssuerEvidence(
                requested_secid=candidate.matched_secid,
                expected_isin=candidate.isin,
                matched_secid=_optional_text(resolution.matched_secid) if resolution is not None else None,
                matched_isin=_optional_text(resolution.matched_isin) if resolution is not None else None,
                security_match_status=security_status,
                issuer_metadata_status=issuer_status,
                issuer_title=_optional_text(resolution.issuer_title) if resolution is not None else None,
                issuer_inn=_optional_text(resolution.issuer_inn) if resolution is not None else None,
                issuer_okpo=_optional_text(resolution.issuer_okpo) if resolution is not None else None,
                primary_board=_optional_text(resolution.primary_board) if resolution is not None else None,
                source_query_count=(
                    resolution.source_query_count
                    if resolution is not None
                    and type(resolution.source_query_count) is int
                    and resolution.source_query_count >= 0
                    else 1
                    if resolution is None
                    else 0
                ),
            )
            total_reference_queries += issuer.source_query_count

            matched_rows = [
                row
                for row in board_rows
                if type(row) is dict
                and (
                    row.get("secid") == candidate.matched_secid
                    or row.get("isin") == candidate.isin
                )
            ]
            observations = tuple(
                sorted(
                    (
                        MoexBondImportBoardObservation(
                            secid=_optional_text(row.get("secid")),
                            isin=_optional_text(row.get("isin")),
                            is_traded=row.get("is_traded")
                            if row.get("is_traded") is None
                            or type(row.get("is_traded")) in (str, int, float, bool)
                            else None,
                            status=_optional_text(row.get("status")),
                        )
                        for row in matched_rows
                    ),
                    key=lambda item: (
                        item.secid or "",
                        item.isin or "",
                        item.status or "",
                        type(item.is_traded).__name__,
                        repr(item.is_traded),
                    ),
                )
            )
            board = MoexBondImportBoardCandidateEvidence(
                requested_board=_TQCB,
                scan_status=board_status,
                observations=observations,
                warning_count=board_warnings,
            )

            description_status = "OBSERVED"
            descriptions: tuple[MoexBondImportDescriptionProjection, ...]
            try:
                raw, warnings = self.client.fetch_bond_description(
                    candidate.matched_secid,
                    board=_TQCB,
                )
                if type(raw) is not dict or type(warnings) is not list:
                    description_status = "SOURCE_ERROR"
                    descriptions = ()
                else:
                    normalized = self._description_projection(candidate.matched_secid, raw)
                    description_status = (
                        "MISSING"
                        if not any(
                            getattr(normalized, field) is not None
                            for field in (
                                "name",
                                "shortname",
                                "currency",
                                "nominal_value",
                                "coupon_rate",
                                "maturity_date",
                                "offer_date",
                                "is_perpetual",
                                "is_subordinated",
                                "has_amortization",
                            )
                        )
                        else "OBSERVED"
                    )
                    descriptions = (normalized,) if description_status == "OBSERVED" else ()
            except Exception:
                description_status = "SOURCE_ERROR"
                descriptions = ()

            candidate_rows.append(
                MoexBondImportCandidateEvidence(
                    candidate_secid=candidate.matched_secid,
                    candidate_isin=candidate.isin,
                    issuer=issuer,
                    board=board,
                    description_status=description_status,
                    descriptions=descriptions,
                )
            )

        return MoexBondImportEvidenceBatch(
            candidate_evidence=tuple(candidate_rows),
            candidate_isins=tuple(item.isin for item in candidates),
            candidate_secids=tuple(item.matched_secid for item in candidates),
            board_scan_status=board_status,
            board_scan_pages_fetched=board_pages,
            board_scan_rows_fetched=len(board_rows),
            board_scan_warning_count=board_warnings,
            issuer_lookup_count=len(candidates),
            issuer_source_query_count=total_reference_queries,
            description_lookup_count=len(candidates),
        )

    def _scan_tqcb(self) -> tuple[list[dict[str, Any]], str, int, int]:
        rows: list[dict[str, Any]] = []
        pages = 0
        warning_count = 0
        try:
            max_pages = self.client.max_pages
            if type(max_pages) is not int or max_pages < 1:
                return [], "SOURCE_ERROR", 0, 1
            start = 0
            for page_index in range(max_pages):
                page_rows, warnings = self.client.fetch_bond_universe(
                    _TQCB,
                    start=start,
                    limit=_PAGE_SIZE,
                )
                pages += 1
                if (
                    type(page_rows) is not list
                    or type(warnings) is not list
                    or any(type(row) is not dict for row in page_rows)
                ):
                    return rows, "SOURCE_ERROR", pages, warning_count + 1
                warning_count += len(warnings)
                rows.extend(page_rows)
                if warnings:
                    return rows, "INCOMPLETE", pages, warning_count
                if len(page_rows) < _PAGE_SIZE:
                    return rows, "COMPLETE", pages, warning_count
                start += len(page_rows)
                if page_index == max_pages - 1:
                    return rows, "INCOMPLETE", pages, warning_count + 1
        except Exception:
            return rows, "SOURCE_ERROR", pages, warning_count + 1
        return rows, "INCOMPLETE", pages, warning_count

    @staticmethod
    def _description_projection(
        requested_secid: str,
        raw: dict[str, Any],
    ) -> MoexBondImportDescriptionProjection:
        return MoexBondImportDescriptionProjection(
            requested_secid=requested_secid,
            requested_board=_TQCB,
            secid=_optional_text(raw.get("secid")),
            isin=_optional_text(raw.get("isin")),
            name=_optional_text(raw.get("name")),
            shortname=_optional_text(raw.get("shortname")),
            # These legacy fields are intentionally not copied into this
            # projection: neither issuer nor board facts come from description.
            issuer_name=None,
            issuer_inn=None,
            currency=_optional_text(raw.get("currency")),
            nominal_value=raw.get("nominal_value"),
            coupon_rate=raw.get("coupon_rate"),
            maturity_date=raw.get("maturity_date"),
            offer_date=raw.get("offer_date"),
            is_subordinated=raw.get("is_subordinated"),
            is_perpetual=raw.get("is_perpetual"),
            has_amortization=raw.get("has_amortization"),
            status=_optional_text(raw.get("status")),
            is_traded=raw.get("is_traded"),
            board_observed=None,
            primary_board=None,
            raw_structural_fields=_raw_structural_fields(raw),
        )


def _raw_structural_fields(raw: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    source = raw.get("raw")
    if type(source) is not dict:
        return ()
    result = []
    for name in _STRUCTURAL_FIELDS:
        for key, value in source.items():
            if str(key).casefold() == name.casefold() and (
                value is None or type(value) in (str, int, float, bool)
            ):
                result.append((str(key), value))
    return tuple(sorted(result, key=lambda item: item[0]))
