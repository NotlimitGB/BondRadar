from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.services.moex_iss_client import (
    MoexIssClient,
    MoexSecurityReferenceCandidate,
)


SECURITY_MATCH_STATUSES = {
    "EXACT_SECID",
    "EXACT_SECID_ISIN_CORROBORATED",
    "EXACT_ISIN_RECOVERED",
    "SECURITY_IDENTIFIER_MISSING",
    "SECURITY_NOT_FOUND",
    "SECURITY_AMBIGUOUS",
    "SECURITY_IDENTIFIER_CONFLICT",
    "SOURCE_ERROR",
}
ISSUER_METADATA_STATUSES = {
    "ISSUER_COMPLETE",
    "ISSUER_PARTIAL",
    "ISSUER_MISSING",
}


@dataclass(frozen=True)
class MoexIssuerIdentitySourceResolution:
    requested_secid: str | None
    expected_isin: str | None
    matched_secid: str | None
    matched_isin: str | None
    candidate_count: int
    matched_candidate_count: int
    security_match_status: str
    issuer_metadata_status: str
    issuer_id: str | None
    issuer_title: str | None
    issuer_inn: str | None
    issuer_okpo: str | None
    short_name: str | None
    full_name: str | None
    primary_board: str | None
    source_query_count: int


class MoexIssuerIdentitySourceService:
    def __init__(self, client: MoexIssClient) -> None:
        self.client = client

    def lookup(
        self,
        *,
        requested_secid: str | None,
        expected_isin: str | None = None,
    ) -> MoexIssuerIdentitySourceResolution:
        secid = _identifier(requested_secid)
        isin = _identifier(expected_isin)
        if secid is None and isin is None:
            return _empty_resolution(
                requested_secid=None,
                expected_isin=None,
                status="SECURITY_IDENTIFIER_MISSING",
                source_query_count=0,
            )

        query = secid or isin
        assert query is not None
        try:
            candidates = self.client.fetch_security_reference_candidates(query)
        except Exception:
            return _empty_resolution(
                requested_secid=secid,
                expected_isin=isin,
                status="SOURCE_ERROR",
                source_query_count=1,
            )

        resolution = resolve_security_reference(
            candidates,
            requested_secid=secid,
            expected_isin=isin,
            source_query_count=1,
        )
        if (
            resolution.security_match_status != "SECURITY_NOT_FOUND"
            or isin is None
            or isin == query
        ):
            return resolution

        try:
            fallback = self.client.fetch_security_reference_candidates(isin)
        except Exception:
            return _empty_resolution(
                requested_secid=secid,
                expected_isin=isin,
                status="SOURCE_ERROR",
                source_query_count=2,
            )
        return resolve_security_reference(
            [*candidates, *fallback],
            requested_secid=secid,
            expected_isin=isin,
            source_query_count=2,
        )


def resolve_security_reference(
    candidates: Iterable[MoexSecurityReferenceCandidate],
    *,
    requested_secid: str | None,
    expected_isin: str | None = None,
    source_query_count: int = 0,
) -> MoexIssuerIdentitySourceResolution:
    secid = _identifier(requested_secid)
    isin = _identifier(expected_isin)
    unique_candidates = sorted(
        set(candidates),
        key=_candidate_sort_key,
    )
    if secid is None and isin is None:
        return _empty_resolution(
            requested_secid=None,
            expected_isin=None,
            status="SECURITY_IDENTIFIER_MISSING",
            candidate_count=len(unique_candidates),
            source_query_count=source_query_count,
        )

    secid_matches = [row for row in unique_candidates if row.secid == secid]
    if secid_matches:
        if isin is not None:
            observed_isins = {
                row.isin for row in secid_matches if row.isin is not None
            }
            if any(value != isin for value in observed_isins):
                return _empty_resolution(
                    requested_secid=secid,
                    expected_isin=isin,
                    status="SECURITY_IDENTIFIER_CONFLICT",
                    candidate_count=len(unique_candidates),
                    matched_candidate_count=len(secid_matches),
                    source_query_count=source_query_count,
                )
        merged = _merge_compatible_candidates(secid_matches)
        if merged is None:
            return _empty_resolution(
                requested_secid=secid,
                expected_isin=isin,
                status="SECURITY_AMBIGUOUS",
                candidate_count=len(unique_candidates),
                matched_candidate_count=len(secid_matches),
                source_query_count=source_query_count,
            )
        corroborated = isin is not None and merged.isin == isin
        return _resolved(
            merged,
            requested_secid=secid,
            expected_isin=isin,
            status=(
                "EXACT_SECID_ISIN_CORROBORATED"
                if corroborated
                else "EXACT_SECID"
            ),
            candidate_count=len(unique_candidates),
            matched_candidate_count=len(secid_matches),
            source_query_count=source_query_count,
        )

    isin_matches = [
        row for row in unique_candidates if isin is not None and row.isin == isin
    ]
    if isin_matches:
        merged = _merge_compatible_candidates(isin_matches)
        if merged is None:
            return _empty_resolution(
                requested_secid=secid,
                expected_isin=isin,
                status="SECURITY_AMBIGUOUS",
                candidate_count=len(unique_candidates),
                matched_candidate_count=len(isin_matches),
                source_query_count=source_query_count,
            )
        return _resolved(
            merged,
            requested_secid=secid,
            expected_isin=isin,
            status="EXACT_ISIN_RECOVERED",
            candidate_count=len(unique_candidates),
            matched_candidate_count=len(isin_matches),
            source_query_count=source_query_count,
        )

    return _empty_resolution(
        requested_secid=secid,
        expected_isin=isin,
        status="SECURITY_NOT_FOUND",
        candidate_count=len(unique_candidates),
        source_query_count=source_query_count,
    )


def _merge_compatible_candidates(
    candidates: list[MoexSecurityReferenceCandidate],
) -> MoexSecurityReferenceCandidate | None:
    fields = (
        "secid",
        "isin",
        "short_name",
        "full_name",
        "primary_board",
        "issuer_id",
        "issuer_title",
        "issuer_inn",
        "issuer_okpo",
    )
    merged: dict[str, str | None] = {}
    for field_name in fields:
        values = {
            getattr(candidate, field_name)
            for candidate in candidates
            if getattr(candidate, field_name) is not None
        }
        if len(values) > 1:
            return None
        merged[field_name] = next(iter(values), None)
    return MoexSecurityReferenceCandidate(**merged)


def _resolved(
    candidate: MoexSecurityReferenceCandidate,
    *,
    requested_secid: str | None,
    expected_isin: str | None,
    status: str,
    candidate_count: int,
    matched_candidate_count: int,
    source_query_count: int,
) -> MoexIssuerIdentitySourceResolution:
    issuer_values = (
        candidate.issuer_id,
        candidate.issuer_title,
        candidate.issuer_inn,
        candidate.issuer_okpo,
    )
    if all(value is not None for value in issuer_values):
        issuer_status = "ISSUER_COMPLETE"
    elif any(value is not None for value in issuer_values):
        issuer_status = "ISSUER_PARTIAL"
    else:
        issuer_status = "ISSUER_MISSING"
    return MoexIssuerIdentitySourceResolution(
        requested_secid=requested_secid,
        expected_isin=expected_isin,
        matched_secid=candidate.secid,
        matched_isin=candidate.isin,
        candidate_count=candidate_count,
        matched_candidate_count=matched_candidate_count,
        security_match_status=status,
        issuer_metadata_status=issuer_status,
        issuer_id=candidate.issuer_id,
        issuer_title=candidate.issuer_title,
        issuer_inn=candidate.issuer_inn,
        issuer_okpo=candidate.issuer_okpo,
        short_name=candidate.short_name,
        full_name=candidate.full_name,
        primary_board=candidate.primary_board,
        source_query_count=source_query_count,
    )


def _empty_resolution(
    *,
    requested_secid: str | None,
    expected_isin: str | None,
    status: str,
    candidate_count: int = 0,
    matched_candidate_count: int = 0,
    source_query_count: int,
) -> MoexIssuerIdentitySourceResolution:
    return MoexIssuerIdentitySourceResolution(
        requested_secid=requested_secid,
        expected_isin=expected_isin,
        matched_secid=None,
        matched_isin=None,
        candidate_count=candidate_count,
        matched_candidate_count=matched_candidate_count,
        security_match_status=status,
        issuer_metadata_status="ISSUER_MISSING",
        issuer_id=None,
        issuer_title=None,
        issuer_inn=None,
        issuer_okpo=None,
        short_name=None,
        full_name=None,
        primary_board=None,
        source_query_count=source_query_count,
    )


def _identifier(value: str | None) -> str | None:
    text = str(value or "").strip().upper()
    return text or None


def _candidate_sort_key(candidate: MoexSecurityReferenceCandidate) -> tuple[str, ...]:
    return tuple(
        value or ""
        for value in (
            candidate.secid,
            candidate.isin,
            candidate.issuer_id,
            candidate.issuer_inn,
            candidate.issuer_okpo,
            candidate.issuer_title,
            candidate.primary_board,
            candidate.short_name,
            candidate.full_name,
        )
    )
