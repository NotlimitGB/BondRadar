"""Pure, deterministic Task296C import preflight over frozen evidence."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence, Set
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from app.schemas.tinvest_bond_admission_manifest import (
    MoexSecurityMatchStatus,
    TInvestAdmissionCoverage,
    TInvestAdmissionManifestProvenance,
    TInvestAdmissionMetadataState,
    TInvestAdmissionReason,
    TInvestAdmissionState,
    TInvestBondAdmissionManifestView,
    TInvestBondAdmissionIsinAggregate,
    TInvestAdmissionUidRow,
)
from app.schemas.tinvest_bond_identity_bridge import (
    BondIdentityProjection,
    TInvestBondIdentityBridgeCoverage,
    TInvestBondIdentityBridgeProvenance,
    TInvestBondIdentityBridgeView,
    TInvestBridgeAvailabilityClass,
)
from app.schemas.tinvest_bond_import_preflight import (
    BondImportBatchIdentity,
    BondImportCompanyPlan,
    BondImportPreflightReason,
    BondImportPreflightState,
    CompanyIdentityProjection,
    MoexBondImportDescriptionProjection,
    MoexBondImportEvidenceBatch,
    MoexBondImportCandidateEvidence,
    MoexBondImportBoardCandidateEvidence,
    MoexBondImportBoardObservation,
    MoexBondImportIssuerEvidence,
    TInvestBondReadyImportRow,
    TInvestBondImportPreflightError,
    TInvestBondImportPreflightErrorCode,
    TInvestBondImportPreflightProvenance,
    TInvestBondImportPreflightReasonBreakdown,
    TInvestBondImportPreflightRow,
    TInvestBondImportPreflightSummary,
    TInvestBondImportPreflightView,
    TINVEST_BOND_IMPORT_PREFLIGHT_VERSION,
    MOEX_BOND_IMPORT_DESCRIPTION_VERSION,
    MOEX_BOND_IMPORT_EVIDENCE_BATCH_VERSION,
    MOEX_BOND_IMPORT_CANDIDATE_EVIDENCE_VERSION,
    MOEX_BOND_IMPORT_BOARD_EVIDENCE_VERSION,
    MOEX_BOND_IMPORT_ISSUER_EVIDENCE_VERSION,
)
from app.services.moex_normalization import canonicalize_moex_currency


_INACTIVE_STATUS_VALUES = frozenset(
    {"inactive", "archived", "not_traded", "not traded", "delisted", "погашен"}
)
_BOOLEAN_TRUE = frozenset({"1", "true", "yes", "y", "да"})
_BOOLEAN_FALSE = frozenset({"0", "false", "no", "n", "нет"})
_TASK296B_VERSION = "tinvest-bond-admission-manifest-v1"
_TASK296_BRIDGE_VERSION = "tinvest-bond-identity-bridge-v1"
_TASK295_VERSION = "tinvest-current-instrument-universe-v1"


def _raise(code: TInvestBondImportPreflightErrorCode) -> None:
    raise TInvestBondImportPreflightError(code)


def _sequence(value: object, code: TInvestBondImportPreflightErrorCode) -> tuple[Any, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping, Set))
        or not isinstance(value, Sequence)
    ):
        _raise(code)
    return tuple(value)


def _nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _optional_string(value: object) -> bool:
    return value is None or type(value) is str


def _identity_present(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    try:
        return hashlib.sha256(_canonical_json(value)).hexdigest()
    except (TypeError, ValueError, OverflowError):
        _raise(TInvestBondImportPreflightErrorCode.CANDIDATE_HASH_MISMATCH)


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json")


def _parse_decimal(value: object, *, positive: bool = False) -> Decimal | None:
    if value is None or type(value) is bool:
        return None
    if type(value) not in (str, int, float, Decimal):
        return None
    if type(value) is str and not value.strip():
        return None
    try:
        parsed = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or (positive and parsed <= 0):
        return None
    return parsed


def _parse_date(value: object) -> date | None:
    if type(value) is date:
        return value
    if type(value) is not str:
        return None
    text = value.strip()
    if not text or text in {"0000-00-00", "0001-01-01"}:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _parse_bool(value: object) -> bool | None:
    if type(value) is bool:
        return value
    if type(value) in (str, int):
        text = str(value).strip().lower()
        if text in _BOOLEAN_TRUE:
            return True
        if text in _BOOLEAN_FALSE:
            return False
    return None


def _trimmed(value: object, *, max_length: int | None = None) -> str | None:
    if type(value) is not str:
        return None
    result = value.strip()
    if not result:
        return None
    if max_length is not None:
        result = result[:max_length]
    return result or None


def _normalize_company_name(value: str) -> str:
    return " ".join(value.lower().split())


def _valid_manifest(
    value: object,
) -> tuple[
    TInvestBondAdmissionManifestView,
    tuple[TInvestBondAdmissionIsinAggregate, ...],
    dict[str, TInvestAdmissionUidRow],
]:
    if type(value) is not TInvestBondAdmissionManifestView:
        _raise(TInvestBondImportPreflightErrorCode.INVALID_INPUT)
    manifest = value
    if (
        manifest.contract_version != _TASK296B_VERSION
        or manifest.pit_ready is not False
        or type(manifest.identity_bridge) is not TInvestBondIdentityBridgeView
        or manifest.identity_bridge.contract_version != _TASK296_BRIDGE_VERSION
        or manifest.identity_bridge.pit_ready is not False
        or manifest.source_universe_contract_version != _TASK295_VERSION
        or type(manifest.provenance) is not TInvestAdmissionManifestProvenance
        or type(manifest.coverage) is not TInvestAdmissionCoverage
        or type(manifest.identity_bridge.provenance) is not TInvestBondIdentityBridgeProvenance
        or type(manifest.identity_bridge.coverage) is not TInvestBondIdentityBridgeCoverage
        or manifest.provenance.source_contract_version != _TASK295_VERSION
        or manifest.provenance.identity_bridge_contract_version != _TASK296_BRIDGE_VERSION
        or manifest.identity_bridge.provenance.source_contract_version != _TASK295_VERSION
    ):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)

    candidates = manifest.import_candidate_manifest
    if type(candidates) is not tuple:
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    if any(type(item) is not TInvestBondAdmissionIsinAggregate for item in candidates):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    ordered = tuple(sorted(candidates, key=lambda item: item.isin if type(item.isin) is str else ""))
    if candidates != ordered:
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)

    uid_rows = manifest.uid_admissions
    if type(uid_rows) is not tuple or any(type(item) is not TInvestAdmissionUidRow for item in uid_rows):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    if (
        type(manifest.unmatched_isin_aggregates) is not tuple
        or any(type(item) is not TInvestBondAdmissionIsinAggregate for item in manifest.unmatched_isin_aggregates)
    ):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    uid_by_id: dict[str, TInvestAdmissionUidRow] = {}
    for row in uid_rows:
        if type(row.source_uid) is not str or not row.source_uid.strip() or row.source_uid in uid_by_id:
            _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
        uid_by_id[row.source_uid] = row
    if tuple(sorted(uid_rows, key=lambda item: item.source_uid)) != uid_rows:
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)

    candidate_isins: list[str] = []
    candidate_secids: list[str] = []
    seen_uids: set[str] = set()
    for candidate in candidates:
        if (
            type(candidate.isin) is not str
            or not candidate.isin.strip()
            or type(candidate.admission_state) is not TInvestAdmissionState
            or candidate.admission_state is not TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
            or type(candidate.moex_security_match_status) is not MoexSecurityMatchStatus
            or candidate.moex_security_match_status.value != "EXACT_ISIN_RECOVERED"
            or type(candidate.matched_secid) is not str
            or not candidate.matched_secid.strip()
            or not _optional_string(candidate.matched_isin)
            or candidate.matched_isin != candidate.isin
            or type(candidate.primary_board_state) is not TInvestAdmissionMetadataState
            or candidate.primary_board_state is not TInvestAdmissionMetadataState.SOURCE_VALUE
            or candidate.primary_board != "TQCB"
            or candidate.canonical_ofz is not False
            or type(candidate.source_sector_state) is not TInvestAdmissionMetadataState
            or candidate.source_sector_state is not TInvestAdmissionMetadataState.SOURCE_VALUE
            or type(candidate.source_sector) is not str
            or candidate.source_sector in {"government", "municipal"}
            or type(candidate.currency_state) is not TInvestAdmissionMetadataState
            or candidate.currency_state is not TInvestAdmissionMetadataState.SOURCE_VALUE
            or candidate.currency != "rub"
            or type(candidate.bond_type_state) is not TInvestAdmissionMetadataState
            or candidate.bond_type_state is not TInvestAdmissionMetadataState.SOURCE_VALUE
            or type(candidate.bond_type) is not str
            or candidate.bond_type == "BOND_TYPE_REPLACED"
            or candidate.any_api_buyable_uid is not True
            or candidate.any_nonqual_flag_false_buyable_uid is not True
            or type(candidate.source_uids) is not tuple
            or not candidate.source_uids
            or any(type(uid) is not str or not uid.strip() for uid in candidate.source_uids)
            or candidate.source_uids != tuple(sorted(set(candidate.source_uids)))
            or type(candidate.uid_count) is not int
            or candidate.uid_count != len(candidate.source_uids)
            or type(candidate.reason_codes) is not tuple
            or any(type(reason) is not TInvestAdmissionReason for reason in candidate.reason_codes)
            or candidate.reason_codes != tuple(sorted(set(candidate.reason_codes), key=lambda item: item.value))
        ):
            _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
        candidate_isins.append(candidate.isin)
        candidate_secids.append(candidate.matched_secid)
        for uid in candidate.source_uids:
            if type(uid) is not str or not uid.strip() or uid not in uid_by_id or uid in seen_uids:
                _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
            seen_uids.add(uid)

    if len(candidate_isins) != len(set(candidate_isins)):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    aggregate_by_isin = {
        item.isin: item
        for item in manifest.unmatched_isin_aggregates
        if type(item) is TInvestBondAdmissionIsinAggregate and type(item.isin) is str
    }
    if len(aggregate_by_isin) != len(manifest.unmatched_isin_aggregates):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    if any(aggregate_by_isin.get(item.isin) != item for item in candidates):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    if (
        manifest.coverage.source_uid_count != len(uid_rows)
        or manifest.coverage.import_candidate_unique_isin_count != len(candidates)
        or manifest.coverage.import_candidate_uid_count
        != sum(item.uid_count for item in candidates)
        or manifest.identity_bridge.provenance.source_uid_count != len(uid_rows)
        or manifest.identity_bridge.coverage.source_bond_uid_count != len(uid_rows)
    ):
        _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)
    for candidate in candidates:
        for uid in candidate.source_uids:
            if uid_by_id[uid].source_isin != candidate.isin:
                _raise(TInvestBondImportPreflightErrorCode.ADMISSION_MANIFEST_INVALID)

    # Verify both immutable Task296B hashes before using its candidate rows.
    admission_rows_hash = _sha256([_dump(row) for row in uid_rows])
    candidate_isin_hash = _sha256(sorted(candidate_isins))
    if (
        admission_rows_hash != manifest.provenance.admission_row_set_sha256
        or candidate_isin_hash != manifest.provenance.import_candidate_isin_set_sha256
        or manifest.provenance.source_uid_count != len(uid_rows)
    ):
        _raise(TInvestBondImportPreflightErrorCode.CANDIDATE_HASH_MISMATCH)
    return manifest, candidates, uid_by_id


def _validate_internal_bonds(value: object) -> tuple[BondIdentityProjection, ...]:
    rows = _sequence(
        value,
        TInvestBondImportPreflightErrorCode.INTERNAL_IDENTITY_PROJECTION_INVALID,
    )
    seen_ids: set[int] = set()
    for row in rows:
        if (
            type(row) is not BondIdentityProjection
            or row.contract_version != "bond-identity-projection-v1"
            or type(row.bond_id) is not int
            or row.bond_id <= 0
            or row.bond_id in seen_ids
            or not _optional_string(row.isin)
            or not _optional_string(row.secid)
        ):
            _raise(TInvestBondImportPreflightErrorCode.INTERNAL_IDENTITY_PROJECTION_INVALID)
        seen_ids.add(row.bond_id)
    return tuple(rows)


def _validate_companies(value: object) -> tuple[CompanyIdentityProjection, ...]:
    rows = _sequence(value, TInvestBondImportPreflightErrorCode.COMPANY_PROJECTION_INVALID)
    seen: set[int] = set()
    for row in rows:
        if (
            type(row) is not CompanyIdentityProjection
            or row.contract_version != "company-identity-projection-v1"
            or type(row.company_id) is not int
            or row.company_id <= 0
            or row.company_id in seen
            or type(row.name) is not str
            or not row.name.strip()
            or not _optional_string(row.ticker)
            or not _optional_string(row.inn)
        ):
            _raise(TInvestBondImportPreflightErrorCode.COMPANY_PROJECTION_INVALID)
        seen.add(row.company_id)
    return tuple(rows)


def _validate_descriptions(value: object) -> tuple[MoexBondImportDescriptionProjection, ...]:
    rows = _sequence(value, TInvestBondImportPreflightErrorCode.INVALID_INPUT)
    string_fields = (
        "requested_board", "secid", "isin", "name", "shortname", "issuer_name",
        "issuer_inn", "currency", "status", "primary_board",
    )
    for row in rows:
        if (
            type(row) is not MoexBondImportDescriptionProjection
            or row.contract_version != MOEX_BOND_IMPORT_DESCRIPTION_VERSION
            or not _nonblank(row.requested_secid)
            or any(not _optional_string(getattr(row, field)) for field in string_fields)
            or (row.board_observed is not None and type(row.board_observed) is not bool)
            or (
                row.maturity_date is not None
                and type(row.maturity_date) not in (date, str)
            )
            or (row.offer_date is not None and type(row.offer_date) not in (date, str))
        ):
            _raise(TInvestBondImportPreflightErrorCode.INVALID_INPUT)
    return tuple(rows)


def _validate_evidence_batch(
    value: object,
    candidates: tuple[TInvestBondAdmissionIsinAggregate, ...],
) -> tuple[MoexBondImportEvidenceBatch, dict[str, MoexBondImportCandidateEvidence]]:
    if type(value) is not MoexBondImportEvidenceBatch:
        _raise(TInvestBondImportPreflightErrorCode.MOEX_EVIDENCE_BATCH_INVALID)
    batch = value
    expected_isins = tuple(item.isin for item in candidates)
    expected_secids = tuple(item.matched_secid for item in candidates)
    if (
        batch.contract_version != MOEX_BOND_IMPORT_EVIDENCE_BATCH_VERSION
        or batch.pit_ready is not False
        or type(batch.board_scan_status) is not str
        or batch.board_scan_status not in ("COMPLETE", "INCOMPLETE", "SOURCE_ERROR")
        or type(batch.candidate_isins) is not tuple
        or batch.candidate_isins != expected_isins
        or type(batch.candidate_secids) is not tuple
        or batch.candidate_secids != expected_secids
        or type(batch.candidate_evidence) is not tuple
        or len(batch.candidate_evidence) != len(candidates)
        or type(batch.board_scan_pages_fetched) is not int
        or batch.board_scan_pages_fetched < 0
        or (bool(candidates) and batch.board_scan_pages_fetched == 0 and batch.board_scan_status == "COMPLETE")
        or type(batch.board_scan_rows_fetched) is not int
        or batch.board_scan_rows_fetched < 0
        or type(batch.board_scan_warning_count) is not int
        or batch.board_scan_warning_count < 0
        or (batch.board_scan_warning_count > 0 and batch.board_scan_status == "COMPLETE")
        or type(batch.issuer_lookup_count) is not int
        or batch.issuer_lookup_count != len(candidates)
        or type(batch.issuer_source_query_count) is not int
        or batch.issuer_source_query_count < 0
        or type(batch.description_lookup_count) is not int
        or batch.description_lookup_count != len(candidates)
    ):
        _raise(TInvestBondImportPreflightErrorCode.MOEX_EVIDENCE_BATCH_INVALID)

    by_isin: dict[str, MoexBondImportCandidateEvidence] = {}
    query_count = 0
    for candidate, evidence in zip(candidates, batch.candidate_evidence, strict=True):
        if (
            type(evidence) is not MoexBondImportCandidateEvidence
            or evidence.contract_version != MOEX_BOND_IMPORT_CANDIDATE_EVIDENCE_VERSION
            or evidence.candidate_isin != candidate.isin
            or evidence.candidate_secid != candidate.matched_secid
            or evidence.candidate_isin in by_isin
            or type(evidence.issuer) is not MoexBondImportIssuerEvidence
            or evidence.issuer.contract_version != MOEX_BOND_IMPORT_ISSUER_EVIDENCE_VERSION
            or evidence.issuer.requested_secid != candidate.matched_secid
            or evidence.issuer.expected_isin != candidate.isin
            or type(evidence.issuer.security_match_status) is not str
            or evidence.issuer.security_match_status
            not in (
                "EXACT_SECID",
                "EXACT_SECID_ISIN_CORROBORATED",
                "EXACT_ISIN_RECOVERED",
                "SECURITY_IDENTIFIER_MISSING",
                "SECURITY_NOT_FOUND",
                "SECURITY_AMBIGUOUS",
                "SECURITY_IDENTIFIER_CONFLICT",
                "SOURCE_ERROR",
            )
            or type(evidence.issuer.issuer_metadata_status) is not str
            or evidence.issuer.issuer_metadata_status
            not in ("ISSUER_COMPLETE", "ISSUER_PARTIAL", "ISSUER_MISSING")
            or type(evidence.issuer.source_query_count) is not int
            or evidence.issuer.source_query_count < 0
            or type(evidence.board) is not MoexBondImportBoardCandidateEvidence
            or evidence.board.contract_version != MOEX_BOND_IMPORT_BOARD_EVIDENCE_VERSION
            or evidence.board.requested_board != "TQCB"
            or type(evidence.board.scan_status) is not str
            or evidence.board.scan_status not in {"COMPLETE", "INCOMPLETE", "SOURCE_ERROR"}
            or evidence.board.scan_status != batch.board_scan_status
            or type(evidence.board.observations) is not tuple
            or any(type(row) is not MoexBondImportBoardObservation for row in evidence.board.observations)
            or any(row.requested_board != "TQCB" for row in evidence.board.observations)
            or type(evidence.board.warning_count) is not int
            or evidence.board.warning_count < 0
            or evidence.board.warning_count != batch.board_scan_warning_count
            or (evidence.board.warning_count > 0 and evidence.board.scan_status == "COMPLETE")
            or type(evidence.descriptions) is not tuple
            or type(evidence.description_status) is not str
            or evidence.description_status not in {"OBSERVED", "MISSING", "SOURCE_ERROR"}
            or (evidence.description_status == "OBSERVED") != bool(evidence.descriptions)
            or evidence.description_status == "MISSING" and evidence.descriptions
            or evidence.description_status == "SOURCE_ERROR" and evidence.descriptions
        ):
            _raise(TInvestBondImportPreflightErrorCode.MOEX_EVIDENCE_BATCH_INVALID)
        query_count += evidence.issuer.source_query_count
        _validate_descriptions(evidence.descriptions)
        if any(
            item.requested_secid != candidate.matched_secid
            for item in evidence.descriptions
        ):
            _raise(TInvestBondImportPreflightErrorCode.MOEX_EVIDENCE_BATCH_INVALID)
        by_isin[evidence.candidate_isin] = evidence
    if query_count != batch.issuer_source_query_count:
        _raise(TInvestBondImportPreflightErrorCode.MOEX_EVIDENCE_BATCH_INVALID)
    return batch, by_isin


def _candidate_indexes(candidates: tuple[TInvestBondAdmissionIsinAggregate, ...]):
    secid_counts: dict[str, int] = defaultdict(int)
    for candidate in candidates:
        secid_counts[candidate.matched_secid] += 1
    return secid_counts


def _internal_indexes(rows: tuple[BondIdentityProjection, ...]):
    by_isin: dict[str, set[int]] = defaultdict(set)
    by_secid: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        if row.isin is not None and row.isin:
            by_isin[row.isin].add(row.bond_id)
        if row.secid is not None and row.secid:
            by_secid[row.secid].add(row.bond_id)
    return by_isin, by_secid


def _company_indexes(rows: tuple[CompanyIdentityProjection, ...]):
    by_inn: dict[str, set[int]] = defaultdict(set)
    by_name: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        if row.inn is not None and row.inn.strip():
            by_inn[row.inn].add(row.company_id)
        normalized = _normalize_company_name(row.name)
        if normalized:
            by_name[normalized].add(row.company_id)
    return by_inn, by_name


def _raw_structural_fields(
    projection: MoexBondImportDescriptionProjection,
) -> tuple[tuple[str, str | int | float | bool | Decimal | None], ...] | None:
    values = projection.raw_structural_fields
    if type(values) is not tuple:
        return None
    result: list[tuple[str, str | int | float | bool | Decimal | None]] = []
    seen: set[str] = set()
    for pair in values:
        if (
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not str
            or not pair[0]
            or pair[0] in seen
            or (pair[1] is not None and type(pair[1]) not in (str, int, float, bool, Decimal))
        ):
            return None
        seen.add(pair[0])
        result.append((pair[0], pair[1]))
    return tuple(sorted(result, key=lambda item: item[0]))


def _company_resolution(
    issuer_name: str | None,
    issuer_inn: str | None,
    *,
    by_inn: dict[str, set[int]],
    by_name: dict[str, set[int]],
) -> tuple[BondImportCompanyPlan | None, int | None, bool]:
    if issuer_name is None or issuer_inn is None:
        return None, None, False
    inn_ids = by_inn.get(issuer_inn, set())
    name_ids = by_name.get(_normalize_company_name(issuer_name), set())
    if len(inn_ids) > 1 or len(name_ids) > 1:
        return BondImportCompanyPlan.COMPANY_IDENTITY_CONFLICT, None, True
    by_inn_id = next(iter(inn_ids), None)
    by_name_id = next(iter(name_ids), None)
    if by_inn_id is not None:
        if by_name_id is not None and by_name_id != by_inn_id:
            return BondImportCompanyPlan.COMPANY_IDENTITY_CONFLICT, None, True
        return BondImportCompanyPlan.USE_EXISTING_BY_INN, by_inn_id, False
    if by_name_id is not None:
        return BondImportCompanyPlan.USE_EXISTING_BY_NAME, by_name_id, False
    return BondImportCompanyPlan.CREATE_NEW, None, False


def _internal_match_state(
    candidate: TInvestBondAdmissionIsinAggregate,
    projection: MoexBondImportDescriptionProjection | None,
    by_isin: dict[str, set[int]],
    by_secid: dict[str, set[int]],
) -> tuple[bool, bool]:
    identifiers = {(candidate.isin, candidate.matched_secid)}
    if projection is not None:
        identifiers.add((projection.isin, projection.secid))
    found_by_isin: set[int] = set()
    found_by_secid: set[int] = set()
    conflict = False
    for isin, secid in identifiers:
        ids_isin = by_isin.get(isin, set()) if type(isin) is str and isin else set()
        ids_secid = by_secid.get(secid, set()) if type(secid) is str and secid else set()
        if len(ids_isin) > 1 or len(ids_secid) > 1:
            conflict = True
        found_by_isin.update(ids_isin)
        found_by_secid.update(ids_secid)
    if found_by_isin and found_by_secid and found_by_isin != found_by_secid:
        conflict = True
    all_found = found_by_isin | found_by_secid
    if len(all_found) > 1:
        conflict = True
    return conflict, bool(all_found)


def _status(reasons: set[BondImportPreflightReason]) -> BondImportPreflightState:
    if (
        BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT in reasons
        or BondImportPreflightReason.INTERNAL_IDENTITY_CONFLICT in reasons
    ):
        return BondImportPreflightState.IDENTITY_CONFLICT
    if BondImportPreflightReason.ALREADY_IMPORTED_OR_COLLISION in reasons:
        return BondImportPreflightState.ALREADY_IMPORTED_OR_COLLISION
    diagnostics = {
        BondImportPreflightReason.MOEX_DESCRIPTION_EXACT_IDENTITY,
        BondImportPreflightReason.ISSUER_IDENTITY_READY,
        BondImportPreflightReason.COMPANY_EXISTING_BY_INN,
        BondImportPreflightReason.COMPANY_EXISTING_BY_NAME,
        BondImportPreflightReason.COMPANY_CREATE_NEW,
        BondImportPreflightReason.ACTIVE_STATUS_UNKNOWN,
        BondImportPreflightReason.READY,
    }
    if reasons - diagnostics:
        return BondImportPreflightState.REVIEW_REQUIRED
    return BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT


def _required_test_summary(
    uid_rows: tuple[TInvestAdmissionUidRow, ...],
) -> tuple[tuple[str, ...] | None, str]:
    # The complete per-UID values remain nested on the result row. This compact
    # convenience field deterministically takes the first sorted UID's value.
    first = uid_rows[0]
    return first.required_tests, first.required_tests_state


class TInvestBondImportPreflightService:
    """Build a read-only import preflight from frozen caller-supplied evidence."""

    @staticmethod
    def validate_admission_manifest(
        admission_manifest: TInvestBondAdmissionManifestView,
    ) -> None:
        """Validate frozen candidate input before any evidence acquisition."""
        _valid_manifest(admission_manifest)

    @staticmethod
    def build(
        *,
        admission_manifest: TInvestBondAdmissionManifestView,
        moex_evidence: MoexBondImportEvidenceBatch,
        internal_bonds: Sequence[BondIdentityProjection],
        company_projections: Sequence[CompanyIdentityProjection],
    ) -> TInvestBondImportPreflightView:
        manifest, candidates, uid_by_id = _valid_manifest(admission_manifest)
        evidence_batch, evidence_by_isin = _validate_evidence_batch(moex_evidence, candidates)
        internal_rows = _validate_internal_bonds(internal_bonds)
        companies = _validate_companies(company_projections)

        by_isin, by_secid = _internal_indexes(internal_rows)
        company_by_inn, company_by_name = _company_indexes(companies)
        secid_counts = _candidate_indexes(candidates)

        candidate_isins = tuple(sorted(row.isin for row in candidates))
        candidate_secids_unique = tuple(sorted({row.matched_secid for row in candidates}))
        candidate_dump = [_dump(row) for row in sorted(candidates, key=lambda item: item.isin)]
        candidate_isin_hash = _sha256(list(candidate_isins))
        candidate_secid_hash = _sha256(list(candidate_secids_unique))
        candidate_row_hash = _sha256(candidate_dump)

        rows: list[TInvestBondImportPreflightRow] = []
        ready: list[TInvestBondReadyImportRow] = []
        for candidate in candidates:
            candidate_evidence = evidence_by_isin[candidate.isin]
            issuer = candidate_evidence.issuer
            board = candidate_evidence.board
            descriptions = candidate_evidence.descriptions
            projection = descriptions[0] if len(descriptions) == 1 else None
            reasons: set[BondImportPreflightReason] = set()
            if candidate_evidence.description_status == "MISSING":
                reasons.add(BondImportPreflightReason.DESCRIPTION_MISSING)
            elif candidate_evidence.description_status == "SOURCE_ERROR":
                reasons.add(BondImportPreflightReason.DESCRIPTION_SOURCE_ERROR)
            elif len(descriptions) > 1:
                reasons.add(BondImportPreflightReason.DESCRIPTION_CONFLICT)

            if secid_counts[candidate.matched_secid] > 1:
                reasons.add(BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT)
            identity_claims: list[tuple[str | None, str | None]] = [
                (candidate.matched_secid, candidate.isin),
                (issuer.matched_secid, issuer.matched_isin),
                *((item.secid, item.isin) for item in board.observations),
                *((item.secid, item.isin) for item in descriptions),
            ]
            for identity_index, identity in enumerate(identity_claims):
                for other in identity_claims[identity_index + 1 :]:
                    if (
                        _identity_present(identity[0])
                        and _identity_present(other[0])
                        and identity[0] != other[0]
                    ) or (
                        _identity_present(identity[1])
                        and _identity_present(other[1])
                        and identity[1] != other[1]
                    ):
                        reasons.add(BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT)
            if issuer.security_match_status == "SECURITY_IDENTIFIER_CONFLICT":
                reasons.add(BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT)
            if projection is not None:
                source_has_identity = _identity_present(projection.secid) and _identity_present(projection.isin)
                if (
                    source_has_identity
                    and candidate.isin == projection.isin
                    and candidate.matched_secid == projection.secid
                    and candidate.matched_isin == projection.isin
                ):
                    reasons.add(BondImportPreflightReason.MOEX_DESCRIPTION_EXACT_IDENTITY)
                elif (
                    (_identity_present(projection.secid) and projection.secid != candidate.matched_secid)
                    or (_identity_present(projection.isin) and projection.isin != candidate.isin)
                ):
                    reasons.add(BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT)
                else:
                    reasons.add(BondImportPreflightReason.DESCRIPTION_IDENTITY_INCOMPLETE)

            internal_conflict, internal_collision = _internal_match_state(
                candidate, projection, by_isin, by_secid
            )
            if internal_conflict:
                reasons.add(BondImportPreflightReason.INTERNAL_IDENTITY_CONFLICT)
            elif internal_collision:
                reasons.add(BondImportPreflightReason.ALREADY_IMPORTED_OR_COLLISION)

            empty_values: dict[str, Any] = {
                "name": None,
                "shortname": None,
                "issuer_name": None,
                "issuer_inn": None,
                "currency": None,
                "canonical_currency": None,
                "nominal_value": None,
                "nominal_value_raw": None,
                "coupon_rate": None,
                "coupon_rate_raw": None,
                "maturity_date": None,
                "maturity_date_raw": None,
                "offer_date": None,
                "offer_date_raw": None,
                "is_subordinated": None,
                "is_perpetual": None,
                "has_amortization": None,
                "status_source": None,
                "is_traded": None,
                "active_status_unknown": False,
                "requested_secid": None,
                "requested_board": None,
                "board_observed": None,
                "primary_board": None,
                "raw_structural_fields": (),
                "company_plan": None,
                "company_id": None,
                "issuer_evidence": issuer,
                "board_evidence": board,
                "description_source_status": candidate_evidence.description_status,
            }
            ready_evidence: dict[str, Any] | None = None

            uid_admissions = tuple(uid_by_id[uid] for uid in candidate.source_uids)
            required_tests, required_tests_state = _required_test_summary(uid_admissions)
            accepted_issuer_match = issuer.security_match_status in {
                "EXACT_SECID",
                "EXACT_SECID_ISIN_CORROBORATED",
                "EXACT_ISIN_RECOVERED",
            }
            issuer_identity_ready = (
                accepted_issuer_match
                and issuer.issuer_metadata_status in {"ISSUER_COMPLETE", "ISSUER_PARTIAL"}
                and issuer.matched_secid == candidate.matched_secid
                and issuer.matched_isin == candidate.isin
            )
            issuer_name = (
                _trimmed(issuer.issuer_title, max_length=255)
                if issuer_identity_ready
                else None
            )
            raw_inn = _trimmed(issuer.issuer_inn) if issuer_identity_ready else None
            issuer_inn = raw_inn if raw_inn is not None and len(raw_inn) <= 16 else None
            primary_board = issuer.primary_board if issuer_identity_ready else None
            empty_values.update(
                {
                    "issuer_name": issuer_name,
                    "issuer_inn": issuer_inn,
                    "primary_board": primary_board,
                    "requested_board": board.requested_board,
                    "board_observed": False,
                }
            )
            exact_board_observations = [
                item
                for item in board.observations
                if item.secid == candidate.matched_secid and item.isin == candidate.isin
            ]
            if issuer.security_match_status == "SOURCE_ERROR":
                reasons.add(BondImportPreflightReason.ISSUER_REFERENCE_SOURCE_ERROR)
            elif issuer.security_match_status == "SECURITY_AMBIGUOUS":
                reasons.add(BondImportPreflightReason.ISSUER_REFERENCE_AMBIGUOUS)
            elif issuer.security_match_status == "SECURITY_IDENTIFIER_CONFLICT":
                reasons.add(BondImportPreflightReason.SECURITY_IDENTITY_CONFLICT)
            elif not accepted_issuer_match or not issuer_identity_ready:
                reasons.add(BondImportPreflightReason.ISSUER_REFERENCE_MISSING)
            if issuer_name is None:
                reasons.add(BondImportPreflightReason.ISSUER_NAME_MISSING)
            if issuer_inn is None:
                reasons.add(BondImportPreflightReason.ISSUER_INN_MISSING_OR_INVALID)
            if issuer_name is not None and issuer_inn is not None:
                reasons.add(BondImportPreflightReason.ISSUER_IDENTITY_READY)
            if _trimmed(primary_board) is None:
                reasons.add(BondImportPreflightReason.PRIMARY_BOARD_MISSING)
            elif primary_board != "TQCB":
                reasons.add(BondImportPreflightReason.PRIMARY_BOARD_MISMATCH)

            if board.requested_board != "TQCB":
                reasons.add(BondImportPreflightReason.BOARD_CONFLICT)
            if board.scan_status == "SOURCE_ERROR":
                reasons.add(BondImportPreflightReason.BOARD_SOURCE_ERROR)
            elif board.scan_status == "INCOMPLETE":
                reasons.add(BondImportPreflightReason.BOARD_SCAN_INCOMPLETE)
            if not exact_board_observations:
                reasons.add(BondImportPreflightReason.BOARD_OBSERVATION_MISSING)

            activity_values: list[tuple[bool | None, str | None]] = []
            if projection is not None:
                activity_values.append(
                    (_parse_bool(projection.is_traded), _trimmed(projection.status))
                )
            activity_values.extend(
                (_parse_bool(item.is_traded), _trimmed(item.status))
                for item in board.observations
            )
            status_inactive = any(
                status is not None and status.lower() in _INACTIVE_STATUS_VALUES
                for _, status in activity_values
            )
            traded_false = any(value is False for value, _ in activity_values)
            traded_true = any(value is True for value, _ in activity_values)
            traded = False if traded_false else True if traded_true else None
            active_unknown = traded is None and not status_inactive
            status_text = (
                _trimmed(projection.status) if projection is not None else None
            )
            if status_text is None:
                status_text = next(
                    (status for _, status in activity_values if status is not None),
                    None,
                )
            if traded is False or status_inactive:
                reasons.add(BondImportPreflightReason.NOT_ACTIVE_OR_NOT_TRADED)
            elif active_unknown:
                reasons.add(BondImportPreflightReason.ACTIVE_STATUS_UNKNOWN)
            empty_values.update(
                {
                    "is_traded": traded,
                    "status_source": status_text,
                    "active_status_unknown": active_unknown,
                    "requested_board": board.requested_board,
                    "board_observed": bool(exact_board_observations),
                    "primary_board": primary_board,
                }
            )

            nominal_value: Decimal | None = None
            maturity: date | None = None
            perpetual: bool | None = None

            if projection is not None:
                raw_structural = _raw_structural_fields(projection)
                if raw_structural is None:
                    reasons.add(BondImportPreflightReason.DESCRIPTION_CONFLICT)
                    raw_structural = ()

                clean_name = _trimmed(projection.name, max_length=255)
                clean_shortname = _trimmed(projection.shortname, max_length=255)
                # Issuer identity is authoritative only from security reference.
                canonical_currency = canonicalize_moex_currency(projection.currency)
                nominal_value = _parse_decimal(projection.nominal_value, positive=True)
                coupon_rate = _parse_decimal(projection.coupon_rate)
                maturity = _parse_date(projection.maturity_date)
                offer_date = _parse_date(projection.offer_date)
                is_subordinated = _parse_bool(projection.is_subordinated)
                perpetual = _parse_bool(projection.is_perpetual)
                has_amortization = _parse_bool(projection.has_amortization)
                empty_values.update(
                    {
                        "name": clean_name,
                        "shortname": clean_shortname,
                        "issuer_name": issuer_name,
                        "issuer_inn": issuer_inn,
                        "currency": projection.currency,
                        "canonical_currency": canonical_currency,
                        "nominal_value": nominal_value,
                        "nominal_value_raw": projection.nominal_value,
                        "coupon_rate": coupon_rate,
                        "coupon_rate_raw": projection.coupon_rate,
                        "maturity_date": maturity,
                        "maturity_date_raw": projection.maturity_date,
                        "offer_date": offer_date,
                        "offer_date_raw": projection.offer_date,
                        "is_subordinated": is_subordinated,
                        "is_perpetual": perpetual,
                        "has_amortization": has_amortization,
                        "status_source": status_text,
                        "is_traded": traded,
                        "active_status_unknown": active_unknown,
                        "requested_secid": projection.requested_secid,
                        "requested_board": board.requested_board,
                        "board_observed": bool(exact_board_observations),
                        "primary_board": primary_board,
                        "raw_structural_fields": raw_structural,
                    }
                )

                if canonical_currency != "RUB":
                    reasons.add(BondImportPreflightReason.NOMINAL_CURRENCY_CONFLICT)
                if nominal_value is None:
                    reasons.add(BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID)
                if maturity is None and perpetual is not True:
                    reasons.add(BondImportPreflightReason.MATURITY_STRUCTURE_UNRESOLVED)
                if clean_name is None and clean_shortname is None:
                    # The importer's fallback to SECID is intentionally not
                    # accepted by the controlled first-batch readiness gate.
                    reasons.add(BondImportPreflightReason.BOND_NAME_MISSING)

                company_plan, company_id, company_conflict = _company_resolution(
                    issuer_name,
                    issuer_inn,
                    by_inn=company_by_inn,
                    by_name=company_by_name,
                )
                if company_conflict:
                    reasons.add(BondImportPreflightReason.COMPANY_IDENTITY_CONFLICT)
                elif company_plan is BondImportCompanyPlan.USE_EXISTING_BY_INN:
                    reasons.add(BondImportPreflightReason.COMPANY_EXISTING_BY_INN)
                elif company_plan is BondImportCompanyPlan.USE_EXISTING_BY_NAME:
                    reasons.add(BondImportPreflightReason.COMPANY_EXISTING_BY_NAME)
                elif company_plan is BondImportCompanyPlan.CREATE_NEW:
                    reasons.add(BondImportPreflightReason.COMPANY_CREATE_NEW)
                empty_values["company_plan"] = company_plan
                empty_values["company_id"] = company_id

                if (
                    projection.is_traded is not None
                    and traded is None
                    and type(projection.is_traded) is not bool
                ):
                    # The source value is present but the current importer
                    # cannot interpret it; preserve it as unknown activity.
                    empty_values["is_traded"] = None

                ready_evidence = {
                    "name": clean_name,
                    "shortname": clean_shortname,
                    "issuer_name": issuer_name,
                    "issuer_inn": issuer_inn,
                    "canonical_currency": canonical_currency,
                    "nominal_value": nominal_value,
                    "coupon_rate": coupon_rate,
                    "maturity_date": maturity,
                    "offer_date": offer_date,
                    "is_perpetual": perpetual is True,
                    "is_subordinated": is_subordinated,
                    "has_amortization": has_amortization,
                    "status_source": status_text,
                    "is_traded": traded,
                    "requested_board": board.requested_board,
                    "board_observed": bool(exact_board_observations),
                    "primary_board": primary_board,
                    "raw_structural_fields": raw_structural,
                    "issuer_evidence": issuer,
                    "board_evidence": board,
                    "moex_description": projection,
                    "required_tests": required_tests,
                    "uid_admissions": uid_admissions,
                    "task296b_admission_reasons": candidate.reason_codes,
                    "company_plan": company_plan,
                    "company_id": company_id,
                }

            row_status = _status(reasons)
            if row_status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT:
                reasons.add(BondImportPreflightReason.READY)

            preflight_row = TInvestBondImportPreflightRow(
                isin=candidate.isin,
                secid=projection.secid if projection is not None else candidate.matched_secid,
                moex_description=projection,
                source_uids=candidate.source_uids,
                uid_admissions=uid_admissions,
                status=row_status,
                reason_codes=tuple(sorted(reasons, key=lambda item: item.value)),
                task296b_admission_reasons=candidate.reason_codes,
                required_tests=required_tests,
                required_tests_state=required_tests_state,
                **empty_values,
            )
            rows.append(preflight_row)

            if (
                row_status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
                and projection is not None
                and ready_evidence is not None
            ):
                assert ready_evidence["issuer_name"] is not None
                assert ready_evidence["issuer_inn"] is not None
                assert ready_evidence["nominal_value"] is not None
                assert ready_evidence["canonical_currency"] == "RUB"
                # Exact Task296B board and fresh board observation were both
                # checked above; this row freezes those already-passed facts.
                ready.append(
                    TInvestBondReadyImportRow(
                        isin=candidate.isin,
                        secid=candidate.matched_secid,
                        issuer_name=ready_evidence["issuer_name"],
                        issuer_inn=ready_evidence["issuer_inn"],
                        planned_company_action=ready_evidence["company_plan"],
                        company_id=ready_evidence["company_id"],
                        currency="RUB",
                        nominal_value=ready_evidence["nominal_value"],
                        maturity_date=ready_evidence["maturity_date"],
                        is_perpetual=ready_evidence["is_perpetual"],
                        required_tests=ready_evidence["required_tests"],
                        uid_admissions=ready_evidence["uid_admissions"],
                        task296b_admission_reasons=ready_evidence["task296b_admission_reasons"],
                        issuer_evidence=ready_evidence["issuer_evidence"],
                        board_evidence=ready_evidence["board_evidence"],
                        moex_description=ready_evidence["moex_description"],
                        name=ready_evidence["name"],
                        shortname=ready_evidence["shortname"],
                        coupon_rate=ready_evidence["coupon_rate"],
                        offer_date=ready_evidence["offer_date"],
                        is_subordinated=ready_evidence["is_subordinated"],
                        has_amortization=ready_evidence["has_amortization"],
                        status_source=ready_evidence["status_source"],
                        is_traded=ready_evidence["is_traded"],
                        requested_board="TQCB",
                        board_observed=True,
                        primary_board="TQCB",
                        raw_structural_fields=ready_evidence["raw_structural_fields"],
                    )
                )

        ordered_rows = tuple(sorted(rows, key=lambda item: item.isin))
        ordered_ready = tuple(sorted(ready, key=lambda item: (item.isin, item.secid)))
        review = tuple(
            row
            for row in ordered_rows
            if row.status is not BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT
        )
        reason_members: dict[BondImportPreflightReason, list[TInvestBondImportPreflightRow]] = defaultdict(list)
        for row in ordered_rows:
            for reason in row.reason_codes:
                reason_members[reason].append(row)
        reason_breakdown = tuple(
            TInvestBondImportPreflightReasonBreakdown(
                reason_code=reason,
                count=len(members),
                isins=tuple(sorted(row.isin for row in members)),
                secids=tuple(sorted({row.secid for row in members if row.secid is not None})),
                source_uids=tuple(sorted({uid for row in members for uid in row.source_uids})),
            )
            for reason, members in sorted(reason_members.items(), key=lambda item: item[0].value)
        )

        summary = TInvestBondImportPreflightSummary(
            candidate_count=len(ordered_rows),
            ready_count=sum(row.status is BondImportPreflightState.READY_FOR_CONTROLLED_IMPORT for row in ordered_rows),
            review_count=sum(row.status is BondImportPreflightState.REVIEW_REQUIRED for row in ordered_rows),
            collision_count=sum(row.status is BondImportPreflightState.ALREADY_IMPORTED_OR_COLLISION for row in ordered_rows),
            identity_conflict_count=sum(row.status is BondImportPreflightState.IDENTITY_CONFLICT for row in ordered_rows),
            existing_company_by_inn_count=sum(row.company_plan is BondImportCompanyPlan.USE_EXISTING_BY_INN for row in ordered_rows),
            existing_company_by_name_count=sum(row.company_plan is BondImportCompanyPlan.USE_EXISTING_BY_NAME for row in ordered_rows),
            new_company_count=sum(row.company_plan is BondImportCompanyPlan.CREATE_NEW for row in ordered_rows),
            missing_nominal_count=sum(BondImportPreflightReason.NOMINAL_VALUE_MISSING_OR_INVALID in row.reason_codes for row in ordered_rows),
            missing_maturity_count=sum(row.maturity_date is None for row in ordered_rows),
            perpetual_count=sum(row.is_perpetual is True for row in ordered_rows),
            active_unknown_count=sum(row.active_status_unknown for row in ordered_rows),
        )

        ready_hash = _sha256([_dump(row) for row in ordered_ready])
        review_hash = _sha256([_dump(row) for row in review])
        identity = BondImportBatchIdentity(
            admission_manifest_version=manifest.contract_version,
            admission_row_set_sha256=manifest.provenance.admission_row_set_sha256,
            import_candidate_isin_set_sha256=manifest.provenance.import_candidate_isin_set_sha256,
            candidate_count=len(candidates),
            candidate_isins=candidate_isins,
            candidate_secids=candidate_secids_unique,
            candidate_isin_set_sha256=candidate_isin_hash,
            candidate_secid_set_sha256=candidate_secid_hash,
            candidate_row_set_sha256=candidate_row_hash,
        )
        provenance = TInvestBondImportPreflightProvenance(
            task295_source_contract_version=manifest.source_universe_contract_version,
            task296_identity_bridge_contract_version=manifest.identity_bridge.contract_version,
            task296b_admission_manifest_contract_version=manifest.contract_version,
            candidate_count=len(candidates),
            description_projection_count=sum(
                len(item.descriptions) for item in evidence_batch.candidate_evidence
            ),
            candidate_evidence_count=len(evidence_batch.candidate_evidence),
            issuer_lookup_count=evidence_batch.issuer_lookup_count,
            issuer_source_query_count=evidence_batch.issuer_source_query_count,
            board_scan_status=evidence_batch.board_scan_status,
            board_scan_pages_fetched=evidence_batch.board_scan_pages_fetched,
            board_scan_rows_fetched=evidence_batch.board_scan_rows_fetched,
            board_scan_warning_count=evidence_batch.board_scan_warning_count,
            description_lookup_count=evidence_batch.description_lookup_count,
            internal_bond_projection_count=len(internal_rows),
            company_projection_count=len(companies),
            admission_row_set_sha256=manifest.provenance.admission_row_set_sha256,
            import_candidate_isin_set_sha256=manifest.provenance.import_candidate_isin_set_sha256,
            candidate_isin_set_sha256=candidate_isin_hash,
            candidate_secid_set_sha256=candidate_secid_hash,
            candidate_row_set_sha256=candidate_row_hash,
            ready_for_import_manifest_sha256=ready_hash,
            review_manifest_sha256=review_hash,
        )
        return TInvestBondImportPreflightView(
            identity=identity,
            candidate_rows=ordered_rows,
            ready_for_import_manifest=ordered_ready,
            review_manifest=review,
            candidate_isin_set_sha256=candidate_isin_hash,
            candidate_secid_set_sha256=candidate_secid_hash,
            candidate_row_set_sha256=candidate_row_hash,
            ready_for_import_manifest_sha256=ready_hash,
            review_manifest_sha256=review_hash,
            summary=summary,
            reason_breakdown=reason_breakdown,
            provenance=provenance,
        )
