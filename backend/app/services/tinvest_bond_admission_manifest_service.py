"""Pure Task296B admission and actionable-universe coverage reducer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any

from app.schemas.tinvest_bond_admission_manifest import (
    MoexBondResolutionProjection,
    MoexSecurityMatchStatus,
    TInvestAdmissionBoardSource,
    TInvestAdmissionCoverage,
    TInvestAdmissionErrorCode,
    TInvestAdmissionManifestProvenance,
    TInvestAdmissionMetadataBreakdown,
    TInvestAdmissionMetadataState,
    TInvestAdmissionMetadataValue,
    TInvestAdmissionReason,
    TInvestAdmissionReasonBreakdown,
    TInvestAdmissionState,
    TInvestBondAdmissionIsinAggregate,
    TInvestBondAdmissionManifestError,
    TInvestBondAdmissionManifestView,
    TInvestAdmissionUidRow,
    TInvestExistingBondAdmissionAggregate,
)
from app.schemas.tinvest_bond_identity_bridge import (
    BondIdentityProjection,
    TInvestBondBridgeMatchState,
    TInvestBondBridgeRow,
    TInvestBondIdentityBridgeCapabilities,
    TInvestBondIdentityBridgeCoverage,
    TInvestBondIdentityBridgeProvenance,
    TInvestBondIdentityBridgeView,
    TInvestBridgeAvailabilityClass,
    TInvestBondUidAggregate,
    TInvestUnmatchedBondEvidence,
)
from app.schemas.tinvest_instrument_universe import (
    CONTRACT_VERSION as TINVEST_UNIVERSE_CONTRACT_VERSION,
    TInvestAvailabilityClass,
    TInvestBondUniverseInstrument,
)
from app.services.ofz_identity import is_ofz_instrument


_METADATA_FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("sector", "sector", None),
    ("countryOfRisk", "countryOfRisk", None),
    ("countryOfRiskName", "countryOfRiskName", None),
    ("bondType", "bondType", None),
    ("exchange", "exchange", None),
    ("real_exchange", "realExchange", None),
    ("class_code", "classCode", "class_code"),
    ("currency", "currency", "currency"),
)

_STRING_IDENTITY_FIELDS = ("isin", "figi", "ticker", "class_code", "currency")


def _fail(code: TInvestAdmissionErrorCode) -> None:
    raise TInvestBondAdmissionManifestError(code)


def _snapshot(value: object, name: str) -> tuple[Any, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping, Set))
        or not isinstance(value, Sequence)
    ):
        _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
    return tuple(value)


def _exact_optional_string(value: object) -> bool:
    return value is None or type(value) is str


def _nonblank(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _valid_source(source: object) -> bool:
    if type(source) is not TInvestBondUniverseInstrument:
        return False
    if source.contract_version != TINVEST_UNIVERSE_CONTRACT_VERSION:
        return False
    if type(source.uid) is not str or not source.uid.strip():
        return False
    if (
        source.source_universe != "BASE"
        or source.instrument_classification != "TINVEST_BASE_BOND"
        or source.listed_in_source_universe is not True
    ):
        return False
    if any(not _exact_optional_string(getattr(source, field)) for field in _STRING_IDENTITY_FIELDS):
        return False
    if type(source.source_fields) is not dict:
        return False
    for field in (
        "api_trade_available",
        "buy_available",
        "sell_available",
        "for_qual_investor",
    ):
        value = getattr(source, field)
        if value is not None and type(value) is not bool:
            return False
    if source.required_tests is not None and (
        type(source.required_tests) is not tuple
        or any(type(item) is not str for item in source.required_tests)
    ):
        return False
    if type(source.required_tests_state) is not str or source.required_tests_state not in (
        "NOT_SUPPLIED",
        "SOURCE_EMPTY",
        "SOURCE_VALUES",
    ):
        return False
    if source.required_tests is None:
        if source.required_tests_state != "NOT_SUPPLIED":
            return False
    elif source.required_tests:
        if source.required_tests_state != "SOURCE_VALUES":
            return False
    elif source.required_tests_state != "SOURCE_EMPTY":
        return False
    if source.availability_classification is not None and type(
        source.availability_classification
    ) is not TInvestAvailabilityClass:
        return False
    if source.api_trade_available is False:
        expected_availability = TInvestAvailabilityClass.API_TRADE_UNAVAILABLE
    elif source.api_trade_available is True and source.buy_available is True:
        expected_availability = TInvestAvailabilityClass.API_BUY_AVAILABLE
    elif source.api_trade_available is True and source.buy_available is False:
        expected_availability = TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE
    else:
        expected_availability = None
    if source.availability_classification is not expected_availability:
        return False
    return True


def _metadata(
    source: TInvestBondUniverseInstrument,
    source_key: str,
    fallback_attribute: str | None = None,
) -> tuple[TInvestAdmissionMetadataState, str | None]:
    fields = source.source_fields
    if source_key in fields:
        value: object = fields[source_key]
    elif fallback_attribute is not None:
        value = getattr(source, fallback_attribute)
    else:
        value = None

    if value is None:
        return TInvestAdmissionMetadataState.NOT_SUPPLIED, None
    if type(value) is not str:
        return TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE, None
    if not value.strip():
        return TInvestAdmissionMetadataState.NOT_SUPPLIED, None
    return TInvestAdmissionMetadataState.SOURCE_VALUE, value


def _availability(source: TInvestBondUniverseInstrument) -> TInvestBridgeAvailabilityClass:
    current = source.availability_classification
    if current is None:
        return TInvestBridgeAvailabilityClass.UNKNOWN
    return TInvestBridgeAvailabilityClass(current.value)


def _percentage(numerator: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return Decimal(numerator) * Decimal("100") / Decimal(denominator)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _reason_tuple(reasons: set[TInvestAdmissionReason]) -> tuple[TInvestAdmissionReason, ...]:
    return tuple(sorted(reasons, key=lambda item: item.value))


def _expected_bridge_match(
    source_isin: str | None,
    exact_index: dict[str, list[BondIdentityProjection]],
    normalized_index: dict[str, list[BondIdentityProjection]],
) -> tuple[TInvestBondBridgeMatchState, int | None, BondIdentityProjection | None]:
    if not _nonblank(source_isin):
        return TInvestBondBridgeMatchState.UNRESOLVED_NO_SOURCE_ISIN, None, None
    assert source_isin is not None
    exact = exact_index.get(source_isin, [])
    if len(exact) == 1:
        return TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN, None, exact[0]
    if len(exact) > 1:
        return TInvestBondBridgeMatchState.CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS, None, None
    normalized = normalized_index.get(source_isin.strip().upper(), [])
    if len(normalized) == 1:
        return (
            TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE,
            normalized[0].bond_id,
            None,
        )
    if len(normalized) > 1:
        return TInvestBondBridgeMatchState.CONFLICT_NORMALIZED_ISIN_AMBIGUOUS, None, None
    return TInvestBondBridgeMatchState.UNRESOLVED_NO_INTERNAL_EXACT_ISIN, None, None


def _validate_inputs(
    source_bonds: object,
    identity_bridge: object,
    internal_bonds: object,
    moex_resolutions: object,
    core_m3_complete_bond_ids: object,
) -> tuple[
    tuple[TInvestBondUniverseInstrument, ...],
    TInvestBondIdentityBridgeView,
    tuple[BondIdentityProjection, ...],
    tuple[MoexBondResolutionProjection, ...],
    tuple[int, ...],
    dict[int, BondIdentityProjection],
    dict[str, TInvestBondBridgeRow],
    dict[str, MoexBondResolutionProjection],
    set[int],
]:
    sources = _snapshot(source_bonds, "source_bonds")
    internal = _snapshot(internal_bonds, "internal_bonds")
    resolutions = _snapshot(moex_resolutions, "moex_resolutions")
    core_ids_input = _snapshot(core_m3_complete_bond_ids, "core_m3_complete_bond_ids")
    if type(identity_bridge) is not TInvestBondIdentityBridgeView:
        _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
    bridge = identity_bridge
    if (
        bridge.contract_version != "tinvest-bond-identity-bridge-v1"
        or bridge.pit_ready is not False
        or type(bridge.bridge_rows) is not tuple
        or type(bridge.provenance) is not TInvestBondIdentityBridgeProvenance
        or type(bridge.coverage) is not TInvestBondIdentityBridgeCoverage
        or type(bridge.capabilities) is not TInvestBondIdentityBridgeCapabilities
        or bridge.capabilities.pit_ready is not False
        or bridge.provenance.source_contract_version
        != TINVEST_UNIVERSE_CONTRACT_VERSION
        or bridge.provenance.internal_projection_contract_version
        != "bond-identity-projection-v1"
        or bridge.provenance.core_m3_definition != "CORE_M3_COMPLETE_V1"
        or bridge.provenance.exact_isin_match_method != "EXACT_ISIN"
        or bridge.provenance.normalized_only_audit_method
        != "STRIP_UPPER_ONLY_NOT_AUTOMATIC"
        or bridge.provenance.coverage_percentage_method
        != "TASK283_DECIMAL_RATIO_V1"
        or any(
            type(value) is not int
            for value in (
                bridge.provenance.source_uid_count,
                bridge.provenance.internal_bond_count,
                bridge.provenance.core_m3_complete_bond_id_count,
                bridge.provenance.source_duplicate_isin_group_count,
                bridge.provenance.source_duplicate_isin_row_count,
                bridge.coverage.source_bond_uid_count,
                bridge.coverage.matched_source_uid_count,
                bridge.coverage.matched_unique_bond_count,
            )
        )
        or any(
            type(value) is not int
            for name, value in bridge.coverage.model_dump().items()
            if name.endswith("_count")
        )
    ):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    source_rows: list[TInvestBondUniverseInstrument] = []
    source_by_uid: dict[str, TInvestBondUniverseInstrument] = {}
    for source in sources:
        if not _valid_source(source):
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        assert isinstance(source, TInvestBondUniverseInstrument)
        if source.uid in source_by_uid:
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        source_by_uid[source.uid] = source
        source_rows.append(source)

    projections: dict[int, BondIdentityProjection] = {}
    exact_index: dict[str, list[BondIdentityProjection]] = {}
    normalized_index: dict[str, list[BondIdentityProjection]] = {}
    for projection in internal:
        if type(projection) is not BondIdentityProjection:
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        if (
            projection.contract_version != "bond-identity-projection-v1"
            or type(projection.bond_id) is not int
            or projection.bond_id <= 0
            or not _exact_optional_string(projection.isin)
            or not _exact_optional_string(projection.secid)
        ):
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        if projection.bond_id in projections:
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        projections[projection.bond_id] = projection
        if _nonblank(projection.isin):
            assert projection.isin is not None
            exact_index.setdefault(projection.isin, []).append(projection)
            normalized_index.setdefault(projection.isin.strip().upper(), []).append(projection)

    core_ids: set[int] = set()
    for bond_id in core_ids_input:
        if type(bond_id) is not int or bond_id <= 0 or bond_id in core_ids:
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        if bond_id not in projections:
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        core_ids.add(bond_id)

    if (
        bridge.provenance.source_uid_count != len(source_rows)
        or bridge.provenance.internal_bond_count != len(projections)
        or bridge.provenance.core_m3_complete_bond_id_count != len(core_ids)
        or bridge.coverage.source_bond_uid_count != len(source_rows)
        or len(bridge.bridge_rows) != len(source_rows)
    ):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    bridge_rows: dict[str, TInvestBondBridgeRow] = {}
    source_isin_group_sizes: dict[str, int] = {}
    for source in source_rows:
        if _nonblank(source.isin):
            assert source.isin is not None
            source_isin_group_sizes[source.isin] = source_isin_group_sizes.get(source.isin, 0) + 1
    expected_matched_by_bond: dict[int, list[TInvestBondBridgeRow]] = {}
    for row in bridge.bridge_rows:
        if type(row) is not TInvestBondBridgeRow or row.source_uid in bridge_rows:
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        source = source_by_uid.get(row.source_uid)
        if source is None or row.contract_version != bridge.contract_version:
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        if (
            row.source_isin != source.isin
            or row.source_figi != source.figi
            or row.source_ticker != source.ticker
            or row.source_class_code != source.class_code
            or row.availability_classification is not _availability(source)
            or row.api_trade_available != source.api_trade_available
            or row.buy_available != source.buy_available
            or row.sell_available != source.sell_available
            or row.for_qual_investor != source.for_qual_investor
            or row.required_tests != source.required_tests
            or row.required_tests_state != source.required_tests_state
        ):
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        expected_state, normalized_id, matched_projection = _expected_bridge_match(
            source.isin, exact_index, normalized_index
        )
        if row.match_state is not expected_state:
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        expected_group_size = (
            source_isin_group_sizes.get(source.isin)
            if _nonblank(source.isin)
            else None
        )
        if row.source_isin_group_size != expected_group_size:
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        if expected_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN:
            assert matched_projection is not None
            expected_ofz = is_ofz_instrument(
                isin=matched_projection.isin,
                secid=matched_projection.secid,
            )
            if (
                row.match_method != "EXACT_ISIN"
                or row.bond_id != matched_projection.bond_id
                or row.bond_isin != matched_projection.isin
                or row.bond_secid != matched_projection.secid
                or row.normalized_only_candidate_bond_id is not None
                or row.is_ofz is not expected_ofz
            ):
                _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
            expected_matched_by_bond.setdefault(matched_projection.bond_id, []).append(row)
        elif expected_state is TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE:
            if (
                row.match_method is not None
                or row.bond_id is not None
                or row.normalized_only_candidate_bond_id != normalized_id
                or row.is_ofz is not None
            ):
                _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        elif (
            row.match_method is not None
            or row.bond_id is not None
            or row.normalized_only_candidate_bond_id is not None
            or row.is_ofz is not None
        ):
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
        bridge_rows[row.source_uid] = row

    if set(bridge_rows) != set(source_by_uid):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    if tuple(row.source_uid for row in bridge.bridge_rows) != tuple(sorted(bridge_rows)):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
    expected_unmatched = tuple(
        uid
        for uid in sorted(bridge_rows)
        if bridge_rows[uid].match_state is not TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
    )
    if type(bridge.unmatched_rows) is not tuple or any(
        type(item) is not TInvestUnmatchedBondEvidence for item in bridge.unmatched_rows
    ) or tuple(
        item.source_uid for item in bridge.unmatched_rows
    ) != expected_unmatched:
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
    for unmatched in bridge.unmatched_rows:
        row = bridge_rows[unmatched.source_uid]
        if (
            unmatched.source_isin != row.source_isin
            or unmatched.match_state is not row.match_state
            or unmatched.availability_classification is not row.availability_classification
            or unmatched.api_trade_available != row.api_trade_available
            or unmatched.buy_available != row.buy_available
            or unmatched.sell_available != row.sell_available
            or unmatched.for_qual_investor != row.for_qual_investor
            or unmatched.required_tests != row.required_tests
            or unmatched.required_tests_state != row.required_tests_state
        ):
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    if type(bridge.bond_aggregates) is not tuple or any(
        type(item) is not TInvestBondUidAggregate for item in bridge.bond_aggregates
    ) or tuple(item.bond_id for item in bridge.bond_aggregates) != tuple(
        sorted(expected_matched_by_bond)
    ):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)
    for aggregate in bridge.bond_aggregates:
        matched_rows = sorted(
            expected_matched_by_bond[aggregate.bond_id], key=lambda item: item.source_uid
        )
        projection = projections[aggregate.bond_id]
        all_uids = tuple(item.source_uid for item in matched_rows)
        buyable = tuple(
            item.source_uid
            for item in matched_rows
            if item.availability_classification is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
        )
        nonqual_buyable = tuple(
            item.source_uid
            for item in matched_rows
            if item.availability_classification is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            and item.for_qual_investor is False
        )
        qual_buyable = tuple(
            item.source_uid
            for item in matched_rows
            if item.availability_classification is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            and item.for_qual_investor is True
        )
        if (
            aggregate.bond_isin != projection.isin
            or aggregate.bond_secid != projection.secid
            or aggregate.is_ofz is not is_ofz_instrument(isin=projection.isin, secid=projection.secid)
            or aggregate.matched_uids != all_uids
            or aggregate.matched_uid_count != len(all_uids)
            or aggregate.api_buyable_uids != buyable
            or aggregate.api_buyable_uid_count != len(buyable)
            or aggregate.has_api_buyable_uid is not bool(buyable)
            or aggregate.api_buyable_nonqual_flag_false_uids != nonqual_buyable
            or aggregate.api_buyable_nonqual_flag_false_uid_count != len(nonqual_buyable)
            or aggregate.has_nonqual_flag_false_buyable_uid is not bool(nonqual_buyable)
            or aggregate.api_buyable_qual_restricted_uids != qual_buyable
            or aggregate.api_buyable_qual_restricted_uid_count != len(qual_buyable)
            or aggregate.has_qual_restricted_buyable_uid is not bool(qual_buyable)
        ):
            _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    expected_matched_uid_count = sum(len(rows) for rows in expected_matched_by_bond.values())
    matched_ids = set(expected_matched_by_bond)
    ofz_ids = {
        bond_id
        for bond_id in matched_ids
        if is_ofz_instrument(isin=projections[bond_id].isin, secid=projections[bond_id].secid)
    }
    buyable_ids = {
        bond_id
        for bond_id, matched in expected_matched_by_bond.items()
        if any(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            for row in matched
        )
    }
    nonqual_buyable_ids = {
        bond_id
        for bond_id, matched in expected_matched_by_bond.items()
        if any(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            and row.for_qual_investor is False
            for row in matched
        )
    }
    matched_core_ids = matched_ids & core_ids
    buyable_core_ids = buyable_ids & core_ids
    nonqual_buyable_core_ids = nonqual_buyable_ids & core_ids
    unmatched_rows = [
        row
        for row in bridge_rows.values()
        if row.match_state is not TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
    ]
    source_buyable_uid_count = sum(
        row.availability_classification
        is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
        for row in bridge_rows.values()
    )
    if (
        bridge.coverage.matched_source_uid_count != expected_matched_uid_count
        or bridge.coverage.matched_unique_bond_count != len(expected_matched_by_bond)
        or bridge.coverage.source_api_buyable_uid_count != source_buyable_uid_count
        or bridge.coverage.matched_ofz_unique_bond_count != len(ofz_ids)
        or bridge.coverage.matched_non_ofz_unique_bond_count != len(matched_ids - ofz_ids)
        or bridge.coverage.matched_api_buyable_unique_bond_count != len(buyable_ids)
        or bridge.coverage.matched_api_buyable_nonqual_flag_false_unique_bond_count
        != len(nonqual_buyable_ids)
        or bridge.coverage.matched_core_m3_complete_count != len(matched_core_ids)
        or bridge.coverage.matched_api_buyable_core_m3_complete_count
        != len(buyable_core_ids)
        or bridge.coverage.matched_api_buyable_nonqual_flag_false_core_m3_complete_count
        != len(nonqual_buyable_core_ids)
        or bridge.coverage.api_buyable_core_coverage_pct
        != _percentage(len(buyable_core_ids), len(buyable_ids))
        or bridge.coverage.api_buyable_nonqual_flag_false_core_coverage_pct
        != _percentage(len(nonqual_buyable_core_ids), len(nonqual_buyable_ids))
        or bridge.coverage.unmatched_uid_count != len(unmatched_rows)
        or bridge.coverage.unmatched_api_buyable_uid_count
        != sum(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            for row in unmatched_rows
        )
        or bridge.coverage.unmatched_api_buyable_nonqual_flag_false_uid_count
        != sum(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            and row.for_qual_investor is False
            for row in unmatched_rows
        )
        or bridge.coverage.unmatched_api_buyable_qual_restricted_uid_count
        != sum(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            and row.for_qual_investor is True
            for row in unmatched_rows
        )
        or bridge.coverage.unmatched_visible_not_buyable_uid_count
        != sum(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_VISIBLE_NOT_BUYABLE
            for row in unmatched_rows
        )
        or bridge.coverage.unmatched_api_trade_unavailable_uid_count
        != sum(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_TRADE_UNAVAILABLE
            for row in unmatched_rows
        )
        or bridge.coverage.unmatched_unknown_availability_uid_count
        != sum(
            row.availability_classification is TInvestBridgeAvailabilityClass.UNKNOWN
            for row in unmatched_rows
        )
    ):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    expected_duplicate_isins = {
        isin: count for isin, count in source_isin_group_sizes.items() if count > 1
    }
    if (
        bridge.provenance.source_duplicate_isin_group_count != len(expected_duplicate_isins)
        or bridge.provenance.source_duplicate_isin_row_count
        != sum(expected_duplicate_isins.values())
        or bridge.provenance.bridge_row_set_sha256
        != _hash([row.model_dump(mode="json") for row in bridge.bridge_rows])
        or bridge.provenance.unmatched_uid_set_sha256 != _hash(list(expected_unmatched))
        or bridge.provenance.matched_uid_set_sha256
        != _hash(
            [
                row.source_uid
                for row in bridge.bridge_rows
                if row.match_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
            ]
        )
        or bridge.provenance.matched_unique_bond_id_set_sha256
        != _hash(sorted(expected_matched_by_bond))
    ):
        _fail(TInvestAdmissionErrorCode.IDENTITY_BRIDGE_INVALID)

    resolution_by_isin: dict[str, MoexBondResolutionProjection] = {}
    for resolution in resolutions:
        if type(resolution) is not MoexBondResolutionProjection:
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        if (
            resolution.contract_version != "moex-bond-resolution-projection-v1"
            or type(resolution.source_isin) is not str
            or not resolution.source_isin.strip()
            or type(resolution.security_match_status) is not MoexSecurityMatchStatus
            or type(resolution.candidate_count) is not int
            or resolution.candidate_count < 0
            or type(resolution.matched_candidate_count) is not int
            or resolution.matched_candidate_count < 0
            or resolution.matched_candidate_count > resolution.candidate_count
        ):
            _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        for field in (
            "matched_secid",
            "matched_isin",
            "primary_board",
            "issuer_metadata_status",
            "issuer_id",
            "issuer_title",
            "issuer_inn",
            "issuer_okpo",
        ):
            if not _exact_optional_string(getattr(resolution, field)):
                _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        if resolution.source_isin in resolution_by_isin:
            _fail(TInvestAdmissionErrorCode.MOEX_RESOLUTION_CONFLICT)
        resolution_by_isin[resolution.source_isin] = resolution

    unmatched_isins = {
        row.source_isin
        for row in bridge_rows.values()
        if row.match_state is not TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
        and _nonblank(row.source_isin)
    }
    if set(resolution_by_isin) != unmatched_isins:
        _fail(
            TInvestAdmissionErrorCode.MOEX_RESOLUTION_MISSING
            if unmatched_isins - set(resolution_by_isin)
            else TInvestAdmissionErrorCode.INVALID_INPUT
        )

    for isin, resolution in resolution_by_isin.items():
        if resolution.security_match_status is MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED:
            if resolution.candidate_count == 0 or resolution.matched_candidate_count == 0:
                _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
        elif resolution.security_match_status is MoexSecurityMatchStatus.SECURITY_NOT_FOUND:
            if resolution.matched_candidate_count != 0:
                _fail(TInvestAdmissionErrorCode.INVALID_INPUT)
    return (
        tuple(source_rows),
        bridge,
        tuple(internal),
        tuple(resolutions),
        tuple(core_ids_input),
        projections,
        bridge_rows,
        resolution_by_isin,
        core_ids,
    )


def _resolution_identity_is_exact(
    resolution: MoexBondResolutionProjection,
) -> bool:
    return (
        resolution.security_match_status is MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED
        and resolution.matched_isin == resolution.source_isin
    )


def _moex_reason(
    status: MoexSecurityMatchStatus,
) -> TInvestAdmissionReason:
    return {
        MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED: TInvestAdmissionReason.MOEX_EXACT_ISIN_RECOVERED,
        MoexSecurityMatchStatus.SECURITY_NOT_FOUND: TInvestAdmissionReason.MOEX_NOT_FOUND,
        MoexSecurityMatchStatus.SECURITY_AMBIGUOUS: TInvestAdmissionReason.MOEX_AMBIGUOUS,
        MoexSecurityMatchStatus.SECURITY_IDENTIFIER_CONFLICT: TInvestAdmissionReason.MOEX_IDENTIFIER_CONFLICT,
        MoexSecurityMatchStatus.SOURCE_ERROR: TInvestAdmissionReason.MOEX_SOURCE_ERROR,
    }[status]


def _metadata_values_conflict(
    rows: Sequence[tuple[TInvestAdmissionMetadataState, str | None]],
) -> bool:
    known = {value for state, value in rows if state is TInvestAdmissionMetadataState.SOURCE_VALUE}
    return len(known) > 1


def _aggregate_metadata(
    rows: Sequence[tuple[TInvestAdmissionMetadataState, str | None]],
) -> tuple[TInvestAdmissionMetadataState, str | None]:
    values = {value for state, value in rows if state is TInvestAdmissionMetadataState.SOURCE_VALUE}
    if len(values) > 1:
        return TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE, None
    if any(state is TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE for state, _ in rows):
        return TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE, None
    if values:
        return TInvestAdmissionMetadataState.SOURCE_VALUE, next(iter(values))
    return TInvestAdmissionMetadataState.NOT_SUPPLIED, None


class TInvestBondAdmissionManifestService:
    """Build an offline manifest from frozen Task295/296 and MOEX evidence."""

    @staticmethod
    def build(
        *,
        source_bonds: Sequence[TInvestBondUniverseInstrument],
        identity_bridge: TInvestBondIdentityBridgeView,
        internal_bonds: Sequence[BondIdentityProjection],
        moex_resolutions: Sequence[MoexBondResolutionProjection],
        core_m3_complete_bond_ids: Sequence[int],
    ) -> TInvestBondAdmissionManifestView:
        (
            sources,
            bridge,
            _internal_rows,
            _resolution_rows,
            _core_rows,
            projection_by_id,
            bridge_by_uid,
            resolution_by_isin,
            core_ids,
        ) = _validate_inputs(
            source_bonds,
            identity_bridge,
            internal_bonds,
            moex_resolutions,
            core_m3_complete_bond_ids,
        )

        source_by_uid = {source.uid: source for source in sources}
        unmatched_groups: dict[str, list[str]] = {}
        matched_by_bond: dict[int, list[str]] = {}
        for uid, bridge_row in bridge_by_uid.items():
            if bridge_row.match_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN:
                assert bridge_row.bond_id is not None
                matched_by_bond.setdefault(bridge_row.bond_id, []).append(uid)
            elif _nonblank(bridge_row.source_isin):
                assert bridge_row.source_isin is not None
                unmatched_groups.setdefault(bridge_row.source_isin, []).append(uid)

        rows_data: dict[str, dict[str, Any]] = {}
        base_row_reasons: dict[str, set[TInvestAdmissionReason]] = {}
        base_row_states: dict[str, TInvestAdmissionState] = {}
        metadata_by_uid: dict[str, dict[str, tuple[TInvestAdmissionMetadataState, str | None]]] = {}

        for uid in sorted(source_by_uid):
            source = source_by_uid[uid]
            bridge_row = bridge_by_uid[uid]
            reasons: set[TInvestAdmissionReason] = set()
            is_existing = bridge_row.match_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
            if is_existing:
                reasons.add(TInvestAdmissionReason.ALREADY_PRESENT)
                state = TInvestAdmissionState.EXISTING_MATCHED
                canonical = bridge_row.is_ofz
                resolution = None
            elif not _nonblank(source.isin):
                reasons.add(TInvestAdmissionReason.SOURCE_ISIN_MISSING)
                state = TInvestAdmissionState.MOEX_NOT_RESOLVED
                canonical = None
                resolution = None
            else:
                assert source.isin is not None
                resolution = resolution_by_isin[source.isin]
                canonical = (
                    is_ofz_instrument(
                        isin=resolution.matched_isin,
                        secid=resolution.matched_secid,
                    )
                    if _resolution_identity_is_exact(resolution)
                    else None
                )
                reasons.add(_moex_reason(resolution.security_match_status))
                if resolution.security_match_status is MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED:
                    if not _resolution_identity_is_exact(resolution):
                        reasons.add(TInvestAdmissionReason.MOEX_IDENTIFIER_CONFLICT)
                        state = TInvestAdmissionState.IDENTITY_CONFLICT
                    elif bridge_row.match_state in (
                        TInvestBondBridgeMatchState.CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS,
                        TInvestBondBridgeMatchState.CONFLICT_NORMALIZED_ISIN_AMBIGUOUS,
                    ):
                        reasons.add(TInvestAdmissionReason.INTERNAL_IDENTITY_AMBIGUOUS)
                        state = TInvestAdmissionState.IDENTITY_CONFLICT
                    elif bridge_row.match_state is TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE:
                        reasons.add(TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE)
                        state = TInvestAdmissionState.REVIEW_REQUIRED
                    else:
                        state = TInvestAdmissionState.REVIEW_REQUIRED
                elif resolution.security_match_status in (
                    MoexSecurityMatchStatus.SECURITY_AMBIGUOUS,
                    MoexSecurityMatchStatus.SECURITY_IDENTIFIER_CONFLICT,
                ):
                    state = TInvestAdmissionState.IDENTITY_CONFLICT
                else:
                    state = TInvestAdmissionState.MOEX_NOT_RESOLVED

            metadata: dict[str, tuple[TInvestAdmissionMetadataState, str | None]] = {}
            for field, key, fallback in _METADATA_FIELDS:
                metadata[field] = _metadata(source, key, fallback)
            if resolution is not None:
                metadata["primary_board"] = _metadata_projection_string(resolution.primary_board)
            else:
                metadata["primary_board"] = (
                    TInvestAdmissionMetadataState.NOT_SUPPLIED,
                    None,
                )
            metadata_by_uid[uid] = metadata

            if not is_existing:
                TInvestBondAdmissionManifestService._add_source_reasons(
                    reasons,
                    source,
                    metadata,
                    canonical,
                    resolution,
                )
                if (
                    bridge_row.match_state
                    is TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE
                ):
                    reasons.add(TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE)

            if (
                not is_existing
                and metadata["countryOfRisk"][0]
                is TInvestAdmissionMetadataState.NOT_SUPPLIED
            ):
                reasons.add(TInvestAdmissionReason.SOURCE_COUNTRY_MISSING)

            if not is_existing and state not in (
                TInvestAdmissionState.IDENTITY_CONFLICT,
                TInvestAdmissionState.MOEX_NOT_RESOLVED,
            ):
                if reasons.intersection(
                    {
                        TInvestAdmissionReason.CANONICAL_OFZ,
                        TInvestAdmissionReason.SOURCE_SECTOR_GOVERNMENT,
                        TInvestAdmissionReason.SOURCE_SECTOR_MUNICIPAL,
                        TInvestAdmissionReason.API_NOT_BUYABLE,
                        TInvestAdmissionReason.API_AVAILABILITY_UNKNOWN,
                        TInvestAdmissionReason.QUAL_RESTRICTED,
                        TInvestAdmissionReason.QUAL_UNKNOWN,
                        TInvestAdmissionReason.CURRENCY_NOT_CURRENTLY_SUPPORTED,
                        TInvestAdmissionReason.PRIMARY_BOARD_NOT_CURRENTLY_SUPPORTED,
                        TInvestAdmissionReason.REPLACED_BOND_REVIEW,
                        TInvestAdmissionReason.SOURCE_SECTOR_MISSING,
                        TInvestAdmissionReason.SOURCE_BOND_TYPE_MISSING,
                        TInvestAdmissionReason.MOEX_PRIMARY_BOARD_MISSING,
                        TInvestAdmissionReason.MOEX_SECID_MISSING,
                        TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE,
                    }
                ):
                    state = TInvestAdmissionState.REVIEW_REQUIRED
                else:
                    state = TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE

            base_row_reasons[uid] = reasons
            base_row_states[uid] = state
            rows_data[uid] = {
                "source": source,
                "bridge": bridge_row,
                "resolution": resolution,
                "metadata": metadata,
                "canonical": canonical,
                "state": state,
                "reasons": reasons,
                "existing": is_existing,
            }

        isin_aggregates: list[TInvestBondAdmissionIsinAggregate] = []
        group_state: dict[str, TInvestAdmissionState] = {}

        for isin in sorted(unmatched_groups):
            uids = tuple(sorted(unmatched_groups[isin]))
            resolution = resolution_by_isin[isin]
            uid_reasons = set().union(*(base_row_reasons[uid] for uid in uids))
            critical_conflict = False
            for field in ("sector", "currency", "bondType"):
                values = [metadata_by_uid[uid][field] for uid in uids]
                critical_conflict = critical_conflict or _metadata_values_conflict(values)
            if critical_conflict:
                uid_reasons.add(TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT)

            bridge_states = {bridge_by_uid[uid].match_state for uid in uids}
            if bridge_states.intersection(
                {
                    TInvestBondBridgeMatchState.CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS,
                    TInvestBondBridgeMatchState.CONFLICT_NORMALIZED_ISIN_AMBIGUOUS,
                }
            ):
                final_state = TInvestAdmissionState.IDENTITY_CONFLICT
                uid_reasons.add(TInvestAdmissionReason.INTERNAL_IDENTITY_AMBIGUOUS)
            elif not _resolution_identity_is_exact(resolution) and resolution.security_match_status is MoexSecurityMatchStatus.EXACT_ISIN_RECOVERED:
                final_state = TInvestAdmissionState.IDENTITY_CONFLICT
                uid_reasons.add(TInvestAdmissionReason.MOEX_IDENTIFIER_CONFLICT)
            elif resolution.security_match_status in (
                MoexSecurityMatchStatus.SECURITY_AMBIGUOUS,
                MoexSecurityMatchStatus.SECURITY_IDENTIFIER_CONFLICT,
            ):
                final_state = TInvestAdmissionState.IDENTITY_CONFLICT
            elif resolution.security_match_status in (
                MoexSecurityMatchStatus.SECURITY_NOT_FOUND,
                MoexSecurityMatchStatus.SOURCE_ERROR,
            ):
                final_state = TInvestAdmissionState.MOEX_NOT_RESOLVED
            elif critical_conflict:
                final_state = TInvestAdmissionState.REVIEW_REQUIRED
            elif any(
                bridge_by_uid[uid].match_state
                is TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE
                for uid in uids
            ):
                final_state = TInvestAdmissionState.REVIEW_REQUIRED
                uid_reasons.add(TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE)
            elif any(
                base_row_states[uid]
                is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
                for uid in uids
            ):
                final_state = TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
            else:
                final_state = TInvestAdmissionState.REVIEW_REQUIRED

            if final_state in (
                TInvestAdmissionState.IDENTITY_CONFLICT,
                TInvestAdmissionState.REVIEW_REQUIRED,
            ):
                if critical_conflict:
                    uid_reasons.add(TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT)

            group_state[isin] = final_state
            canonical = (
                is_ofz_instrument(isin=resolution.matched_isin, secid=resolution.matched_secid)
                if _resolution_identity_is_exact(resolution)
                else None
            )
            for uid in uids:
                if critical_conflict:
                    base_row_reasons[uid].add(
                        TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT
                    )
                if final_state is TInvestAdmissionState.IDENTITY_CONFLICT:
                    base_row_states[uid] = TInvestAdmissionState.IDENTITY_CONFLICT
                elif final_state is TInvestAdmissionState.MOEX_NOT_RESOLVED:
                    base_row_states[uid] = TInvestAdmissionState.MOEX_NOT_RESOLVED
                elif final_state is TInvestAdmissionState.REVIEW_REQUIRED:
                    base_row_states[uid] = TInvestAdmissionState.REVIEW_REQUIRED

            sector_state, sector_value = _aggregate_metadata(
                [metadata_by_uid[uid]["sector"] for uid in uids]
            )
            currency_state, currency_value = _aggregate_metadata(
                [metadata_by_uid[uid]["currency"] for uid in uids]
            )
            bond_type_state, bond_type_value = _aggregate_metadata(
                [metadata_by_uid[uid]["bondType"] for uid in uids]
            )
            board_state, board_value = _metadata_projection_string(resolution.primary_board)
            isin_aggregates.append(
                TInvestBondAdmissionIsinAggregate(
                    isin=isin,
                    source_uids=uids,
                    uid_count=len(uids),
                    moex_security_match_status=resolution.security_match_status,
                    matched_secid=resolution.matched_secid,
                    matched_isin=resolution.matched_isin,
                    primary_board_state=board_state,
                    primary_board=board_value,
                    canonical_ofz=canonical,
                    source_sector_state=sector_state,
                    source_sector=sector_value,
                    currency_state=currency_state,
                    currency=currency_value,
                    bond_type_state=bond_type_state,
                    bond_type=bond_type_value,
                    any_api_buyable_uid=any(
                        bridge_by_uid[uid].availability_classification
                        is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                        for uid in uids
                    ),
                    any_nonqual_flag_false_buyable_uid=any(
                        bridge_by_uid[uid].availability_classification
                        is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                        and bridge_by_uid[uid].for_qual_investor is False
                        for uid in uids
                    ),
                    any_qual_restricted_buyable_uid=any(
                        bridge_by_uid[uid].availability_classification
                        is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                        and bridge_by_uid[uid].for_qual_investor is True
                        for uid in uids
                    ),
                    admission_state=final_state,
                    reason_codes=_reason_tuple(uid_reasons),
                )
            )

        # Existing Bond aggregates use Task295 class_code as the current board proxy.
        existing_aggregates: list[TInvestExistingBondAdmissionAggregate] = []
        existing_uid_no_government: dict[str, bool | None] = {}
        for bond_id in sorted(matched_by_bond):
            uids = tuple(sorted(matched_by_bond[bond_id]))
            projection = projection_by_id[bond_id]
            canonical = is_ofz_instrument(isin=projection.isin, secid=projection.secid)
            sectors = [metadata_by_uid[uid]["sector"] for uid in uids]
            has_gov = any(
                state is TInvestAdmissionMetadataState.SOURCE_VALUE and value == "government"
                for state, value in sectors
            )
            has_municipal = any(
                state is TInvestAdmissionMetadataState.SOURCE_VALUE and value == "municipal"
                for state, value in sectors
            )
            invalid_sector = any(
                state is TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE
                for state, _ in sectors
            )
            has_valid_non_gov_sector = any(
                state is TInvestAdmissionMetadataState.SOURCE_VALUE
                and value not in ("government", "municipal")
                for state, value in sectors
            )
            no_gov_evidence = (
                not canonical
                and not has_gov
                and not has_municipal
                and not invalid_sector
                and has_valid_non_gov_sector
            )
            for uid in uids:
                existing_uid_no_government[uid] = True if no_gov_evidence else None
            buyable_uids = tuple(
                uid
                for uid in uids
                if bridge_by_uid[uid].availability_classification
                is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            )
            non_gov_buyable = no_gov_evidence and any(
                uid in buyable_uids
                and metadata_by_uid[uid]["sector"][0]
                is TInvestAdmissionMetadataState.SOURCE_VALUE
                and metadata_by_uid[uid]["sector"][1]
                not in ("government", "municipal")
                for uid in uids
            )
            current_pipeline_compatible = no_gov_evidence and any(
                bridge_by_uid[uid].availability_classification
                is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                and _metadata_projection_string(source_by_uid[uid].currency)
                == (TInvestAdmissionMetadataState.SOURCE_VALUE, "rub")
                and _metadata_projection_string(source_by_uid[uid].class_code)
                == (TInvestAdmissionMetadataState.SOURCE_VALUE, "TQCB")
                and metadata_by_uid[uid]["sector"][0]
                is TInvestAdmissionMetadataState.SOURCE_VALUE
                and metadata_by_uid[uid]["sector"][1]
                not in ("government", "municipal")
                for uid in uids
            )
            pipeline_uids = tuple(
                uid
                for uid in uids
                if bridge_by_uid[uid].availability_classification
                is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                and _metadata_projection_string(source_by_uid[uid].currency)
                == (TInvestAdmissionMetadataState.SOURCE_VALUE, "rub")
                and _metadata_projection_string(source_by_uid[uid].class_code)
                == (TInvestAdmissionMetadataState.SOURCE_VALUE, "TQCB")
                and metadata_by_uid[uid]["sector"][0]
                is TInvestAdmissionMetadataState.SOURCE_VALUE
                and metadata_by_uid[uid]["sector"][1]
                not in ("government", "municipal")
            )
            missing_bond_type = current_pipeline_compatible and not any(
                metadata_by_uid[uid]["bondType"][0]
                is TInvestAdmissionMetadataState.SOURCE_VALUE
                for uid in pipeline_uids
            )
            pipeline_board_values = [
                _metadata_projection_string(source_by_uid[uid].class_code)
                for uid in uids
            ]
            pipeline_board_state, pipeline_board = _aggregate_metadata(pipeline_board_values)
            existing_aggregates.append(
                TInvestExistingBondAdmissionAggregate(
                    bond_id=bond_id,
                    isin=projection.isin,
                    secid=projection.secid,
                    pipeline_board_state=pipeline_board_state,
                    pipeline_board=pipeline_board,
                    matched_uids=uids,
                    core_m3_complete=bond_id in core_ids,
                    canonical_ofz=canonical,
                    has_government_evidence=has_gov,
                    has_municipal_evidence=has_municipal,
                    has_api_buyable_uid=bool(buyable_uids),
                    has_nonqual_flag_false_buyable_uid=any(
                        bridge_by_uid[uid].availability_classification
                        is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                        and bridge_by_uid[uid].for_qual_investor is False
                        for uid in uids
                    ),
                    api_buyable_non_government=non_gov_buyable,
                    current_pipeline_compatible=current_pipeline_compatible,
                    current_pipeline_missing_bond_type_evidence=missing_bond_type,
                )
            )

        uid_rows: list[TInvestAdmissionUidRow] = []
        for uid in sorted(source_by_uid):
            data = rows_data[uid]
            source = data["source"]
            bridge_row = data["bridge"]
            resolution = data["resolution"]
            metadata = data["metadata"]
            is_existing = data["existing"]
            canonical = data["canonical"]
            no_gov: bool | None
            if is_existing:
                no_gov = existing_uid_no_government.get(uid)
            elif canonical is False:
                sector_state, sector = metadata["sector"]
                critical_conflict = (
                    TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT
                    in base_row_reasons[uid]
                )
                no_gov = True if (
                    sector_state is TInvestAdmissionMetadataState.SOURCE_VALUE
                    and sector not in ("government", "municipal")
                    and not critical_conflict
                ) else None
            else:
                no_gov = None
            primary_state, primary_board = metadata["primary_board"]
            if is_existing:
                pipeline_state, pipeline_board = _metadata_projection_string(source.class_code)
                pipeline_source = TInvestAdmissionBoardSource.TASK295_CLASS_CODE
            else:
                pipeline_state, pipeline_board = metadata["primary_board"]
                pipeline_source = (
                    TInvestAdmissionBoardSource.MOEX_PRIMARY_BOARD
                    if resolution is not None
                    else None
                )
            uid_rows.append(
                TInvestAdmissionUidRow(
                    source_uid=uid,
                    source_isin=source.isin,
                    task296_match_state=bridge_row.match_state,
                    admission_state=base_row_states[uid],
                    reason_codes=_reason_tuple(base_row_reasons[uid]),
                    matched_bond_id=bridge_row.bond_id,
                    normalized_only_candidate_bond_id=bridge_row.normalized_only_candidate_bond_id,
                    core_m3_complete=bridge_row.bond_id in core_ids
                    if bridge_row.bond_id is not None
                    else False,
                    availability_classification=bridge_row.availability_classification,
                    api_trade_available=bridge_row.api_trade_available,
                    buy_available=bridge_row.buy_available,
                    sell_available=bridge_row.sell_available,
                    for_qual_investor=bridge_row.for_qual_investor,
                    required_tests=bridge_row.required_tests,
                    required_tests_state=bridge_row.required_tests_state,
                    moex_security_match_status=resolution.security_match_status
                    if resolution is not None
                    else None,
                    matched_secid=resolution.matched_secid if resolution is not None else None,
                    matched_isin=resolution.matched_isin if resolution is not None else None,
                    primary_board_state=primary_state,
                    primary_board=primary_board,
                    pipeline_board_source=pipeline_source,
                    pipeline_board_state=pipeline_state,
                    pipeline_board=pipeline_board,
                    issuer_metadata_status=resolution.issuer_metadata_status
                    if resolution is not None
                    else None,
                    issuer_id=resolution.issuer_id if resolution is not None else None,
                    issuer_title=resolution.issuer_title if resolution is not None else None,
                    issuer_inn=resolution.issuer_inn if resolution is not None else None,
                    issuer_okpo=resolution.issuer_okpo if resolution is not None else None,
                    canonical_ofz=canonical,
                    source_sector_state=metadata["sector"][0],
                    source_sector=metadata["sector"][1],
                    country_of_risk_state=metadata["countryOfRisk"][0],
                    country_of_risk=metadata["countryOfRisk"][1],
                    currency_state=metadata["currency"][0],
                    currency=metadata["currency"][1],
                    bond_type_state=metadata["bondType"][0],
                    bond_type=metadata["bondType"][1],
                    class_code_state=metadata["class_code"][0],
                    class_code=metadata["class_code"][1],
                    exchange_state=metadata["exchange"][0],
                    exchange=metadata["exchange"][1],
                    real_exchange_state=metadata["real_exchange"][0],
                    real_exchange=metadata["real_exchange"][1],
                    no_government_evidence=no_gov,
                )
            )

        # Refresh aggregate reasons with all per-UID details after shared conflict handling.
        final_aggregates: list[TInvestBondAdmissionIsinAggregate] = []
        for aggregate in isin_aggregates:
            reasons = set(aggregate.reason_codes)
            reasons.update(
                reason
                for uid in aggregate.source_uids
                for reason in base_row_reasons[uid]
            )
            final_state = group_state[aggregate.isin]
            final_aggregates.append(
                aggregate.model_copy(
                    update={"admission_state": final_state, "reason_codes": _reason_tuple(reasons)}
                )
            )
        isin_aggregates = final_aggregates

        # Copy group-level final states/reasons to UID rows without dropping their own evidence.
        aggregate_by_isin = {item.isin: item for item in isin_aggregates}
        finalized_uid_rows: list[TInvestAdmissionUidRow] = []
        for row in uid_rows:
            if row.source_isin is None or not row.source_isin.strip() or row.source_isin not in aggregate_by_isin:
                finalized_uid_rows.append(row)
                continue
            aggregate = aggregate_by_isin[row.source_isin]
            reasons = set(row.reason_codes)
            if TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT in aggregate.reason_codes:
                reasons.add(TInvestAdmissionReason.SOURCE_CLASSIFICATION_CONFLICT)
            if TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE in aggregate.reason_codes:
                reasons.add(TInvestAdmissionReason.INTERNAL_NORMALIZED_ONLY_ISIN_CANDIDATE)
            if TInvestAdmissionReason.INTERNAL_IDENTITY_AMBIGUOUS in aggregate.reason_codes:
                reasons.add(TInvestAdmissionReason.INTERNAL_IDENTITY_AMBIGUOUS)
            state = (
                row.admission_state
                if aggregate.admission_state
                is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
                else aggregate.admission_state
            )
            finalized_uid_rows.append(
                row.model_copy(update={"admission_state": state, "reason_codes": _reason_tuple(reasons)})
            )
        uid_rows = finalized_uid_rows

        import_manifest = tuple(
            aggregate
            for aggregate in isin_aggregates
            if aggregate.admission_state is TInvestAdmissionState.IMPORT_CANDIDATE_CURRENT_PIPELINE
        )
        review_manifest = tuple(
            aggregate
            for aggregate in isin_aggregates
            if aggregate.admission_state is TInvestAdmissionState.REVIEW_REQUIRED
        )
        unresolved_manifest = tuple(
            aggregate
            for aggregate in isin_aggregates
            if aggregate.admission_state is TInvestAdmissionState.MOEX_NOT_RESOLVED
        )
        conflict_manifest = tuple(
            aggregate
            for aggregate in isin_aggregates
            if aggregate.admission_state is TInvestAdmissionState.IDENTITY_CONFLICT
        )

        # Reason counters are UID-based; ISIN lists remain unique and sorted.
        reason_to_uids: dict[TInvestAdmissionReason, set[str]] = {}
        reason_to_isins: dict[TInvestAdmissionReason, set[str]] = {}
        for row in uid_rows:
            for reason in row.reason_codes:
                reason_to_uids.setdefault(reason, set()).add(row.source_uid)
                if _nonblank(row.source_isin):
                    assert row.source_isin is not None
                    reason_to_isins.setdefault(reason, set()).add(row.source_isin)
        reason_breakdown = tuple(
            TInvestAdmissionReasonBreakdown(
                reason_code=reason,
                count=len(reason_to_uids[reason]),
                source_uids=tuple(sorted(reason_to_uids[reason])),
                isins=tuple(sorted(reason_to_isins.get(reason, set()))),
            )
            for reason in sorted(reason_to_uids, key=lambda item: item.value)
        )

        metadata_breakdowns = _build_metadata_breakdowns(
            sources,
            metadata_by_uid,
        )

        existing_bond_count = len(existing_aggregates)
        api_buyable_non_gov = [item for item in existing_aggregates if item.api_buyable_non_government]
        pipeline_compatible = [item for item in existing_aggregates if item.current_pipeline_compatible]
        api_buyable_non_gov_core_count = sum(item.core_m3_complete for item in api_buyable_non_gov)
        pipeline_core_count = sum(item.core_m3_complete for item in pipeline_compatible)
        import_uid_count = sum(item.uid_count for item in import_manifest)
        unmatched_uid_count = sum(len(group) for group in unmatched_groups.values()) + sum(
            row.match_state is TInvestBondBridgeMatchState.UNRESOLVED_NO_SOURCE_ISIN
            for row in bridge_by_uid.values()
        )
        coverage = TInvestAdmissionCoverage(
            source_uid_count=len(sources),
            existing_matched_uid_count=sum(len(uids) for uids in matched_by_bond.values()),
            unmatched_uid_count=unmatched_uid_count,
            existing_unique_bond_count=existing_bond_count,
            import_candidate_unique_isin_count=len(import_manifest),
            import_candidate_uid_count=import_uid_count,
            review_required_unique_isin_count=len(review_manifest),
            moex_not_resolved_unique_isin_count=len(unresolved_manifest),
            identity_conflict_unique_isin_count=len(conflict_manifest),
            current_import_gap_unique_isin_count=len(import_manifest),
            post_import_addressable_universe_size=existing_bond_count + len(import_manifest),
            existing_api_buyable_non_government_unique_bond_count=len(api_buyable_non_gov),
            existing_api_buyable_non_government_core_m3_complete_count=api_buyable_non_gov_core_count,
            existing_api_buyable_non_government_core_coverage_pct=_percentage(
                api_buyable_non_gov_core_count, len(api_buyable_non_gov)
            ),
            existing_current_pipeline_compatible_unique_bond_count=len(pipeline_compatible),
            existing_current_pipeline_compatible_core_m3_complete_count=pipeline_core_count,
            existing_current_pipeline_compatible_core_coverage_pct=_percentage(
                pipeline_core_count, len(pipeline_compatible)
            ),
            existing_current_pipeline_missing_bond_type_evidence_count=sum(
                item.current_pipeline_missing_bond_type_evidence for item in existing_aggregates
            ),
        )

        ordered_rows = tuple(sorted(uid_rows, key=lambda item: item.source_uid))
        ordered_isins = tuple(sorted(isin_aggregates, key=lambda item: item.isin))
        import_isins = sorted(item.isin for item in import_manifest)
        review_isins = sorted(item.isin for item in review_manifest)
        unresolved_isins = sorted(item.isin for item in unresolved_manifest)
        unmatched_dump = [item.model_dump(mode="json") for item in ordered_isins]
        provenance = TInvestAdmissionManifestProvenance(
            source_contract_version=TINVEST_UNIVERSE_CONTRACT_VERSION,
            identity_bridge_contract_version=bridge.contract_version,
            internal_projection_contract_version=bridge.provenance.internal_projection_contract_version,
            source_uid_count=len(sources),
            internal_bond_count=len(projection_by_id),
            core_m3_complete_bond_id_count=len(core_ids),
            moex_resolution_count=len(resolution_by_isin),
            exact_moex_resolution_count=sum(
                _resolution_identity_is_exact(item) for item in resolution_by_isin.values()
            ),
            admission_row_set_sha256=_hash([item.model_dump(mode="json") for item in ordered_rows]),
            unmatched_isin_manifest_sha256=_hash(unmatched_dump),
            import_candidate_isin_set_sha256=_hash(import_isins),
            review_required_isin_set_sha256=_hash(review_isins),
            moex_not_resolved_isin_set_sha256=_hash(unresolved_isins),
        )
        return TInvestBondAdmissionManifestView(
            source_universe_contract_version=TINVEST_UNIVERSE_CONTRACT_VERSION,
            identity_bridge=bridge,
            uid_admissions=ordered_rows,
            unmatched_isin_aggregates=ordered_isins,
            existing_bond_aggregates=tuple(sorted(existing_aggregates, key=lambda item: item.bond_id)),
            import_candidate_manifest=tuple(sorted(import_manifest, key=lambda item: item.isin)),
            review_manifest=tuple(sorted(review_manifest, key=lambda item: item.isin)),
            moex_not_resolved_manifest=tuple(sorted(unresolved_manifest, key=lambda item: item.isin)),
            identity_conflict_manifest=tuple(sorted(conflict_manifest, key=lambda item: item.isin)),
            reason_breakdown=reason_breakdown,
            metadata_breakdowns=metadata_breakdowns,
            coverage=coverage,
            provenance=provenance,
        )

    @staticmethod
    def _add_source_reasons(
        reasons: set[TInvestAdmissionReason],
        source: TInvestBondUniverseInstrument,
        metadata: dict[str, tuple[TInvestAdmissionMetadataState, str | None]],
        canonical: bool | None,
        resolution: MoexBondResolutionProjection | None,
    ) -> None:
        if canonical is True:
            reasons.add(TInvestAdmissionReason.CANONICAL_OFZ)
        sector_state, sector = metadata["sector"]
        if sector_state is not TInvestAdmissionMetadataState.SOURCE_VALUE:
            reasons.add(TInvestAdmissionReason.SOURCE_SECTOR_MISSING)
        elif canonical is not True and sector == "government":
            reasons.add(TInvestAdmissionReason.SOURCE_SECTOR_GOVERNMENT)
        elif sector == "municipal":
            reasons.add(TInvestAdmissionReason.SOURCE_SECTOR_MUNICIPAL)

        if source.availability_classification is None or source.availability_classification is TInvestAvailabilityClass.API_TRADE_UNAVAILABLE:
            reasons.add(TInvestAdmissionReason.API_AVAILABILITY_UNKNOWN if source.availability_classification is None else TInvestAdmissionReason.API_NOT_BUYABLE)
        elif source.availability_classification is not TInvestAvailabilityClass.API_BUY_AVAILABLE:
            reasons.add(TInvestAdmissionReason.API_NOT_BUYABLE)
        if source.for_qual_investor is True:
            reasons.add(TInvestAdmissionReason.QUAL_RESTRICTED)
        elif source.for_qual_investor is None:
            reasons.add(TInvestAdmissionReason.QUAL_UNKNOWN)

        currency_state, currency = metadata["currency"]
        if currency_state is not TInvestAdmissionMetadataState.SOURCE_VALUE or currency != "rub":
            reasons.add(TInvestAdmissionReason.CURRENCY_NOT_CURRENTLY_SUPPORTED)
        if resolution is not None:
            board_state, board = metadata["primary_board"]
            if board_state is not TInvestAdmissionMetadataState.SOURCE_VALUE:
                reasons.add(TInvestAdmissionReason.MOEX_PRIMARY_BOARD_MISSING)
            elif board != "TQCB":
                reasons.add(TInvestAdmissionReason.PRIMARY_BOARD_NOT_CURRENTLY_SUPPORTED)
            if not _nonblank(resolution.matched_secid):
                reasons.add(TInvestAdmissionReason.MOEX_SECID_MISSING)

        bond_type_state, bond_type = metadata["bondType"]
        if bond_type_state is not TInvestAdmissionMetadataState.SOURCE_VALUE:
            reasons.add(TInvestAdmissionReason.SOURCE_BOND_TYPE_MISSING)
        elif bond_type == "BOND_TYPE_REPLACED":
            reasons.add(TInvestAdmissionReason.REPLACED_BOND_REVIEW)

def _metadata_projection_string(
    value: object,
) -> tuple[TInvestAdmissionMetadataState, str | None]:
    if value is None:
        return TInvestAdmissionMetadataState.NOT_SUPPLIED, None
    if type(value) is not str:
        return TInvestAdmissionMetadataState.INVALID_SOURCE_VALUE, None
    if not value.strip():
        return TInvestAdmissionMetadataState.NOT_SUPPLIED, None
    return TInvestAdmissionMetadataState.SOURCE_VALUE, value


def _build_metadata_breakdowns(
    sources: Sequence[TInvestBondUniverseInstrument],
    metadata_by_uid: dict[str, dict[str, tuple[TInvestAdmissionMetadataState, str | None]]],
) -> tuple[TInvestAdmissionMetadataBreakdown, ...]:
    result: list[TInvestAdmissionMetadataBreakdown] = []
    for output_name, source_key, fallback in _METADATA_FIELDS:
        display_name = {
            "real_exchange": "realExchange",
        }.get(output_name, output_name)
        grouped: dict[tuple[TInvestAdmissionMetadataState, str | None], list[str]] = {}
        for source in sources:
            grouped.setdefault(metadata_by_uid[source.uid][output_name], []).append(source.uid)
        result.append(
            TInvestAdmissionMetadataBreakdown(
                field=display_name,
                entries=tuple(
                    TInvestAdmissionMetadataValue(
                        state=state,
                        value=value,
                        count=len(uids),
                        source_uids=tuple(sorted(uids)),
                    )
                    for (state, value), uids in sorted(
                        grouped.items(), key=lambda item: (item[0][0].value, item[0][1] or "")
                    )
                ),
            )
        )
    grouped_board: dict[tuple[TInvestAdmissionMetadataState, str | None], list[str]] = {}
    for source in sources:
        grouped_board.setdefault(metadata_by_uid[source.uid]["primary_board"], []).append(source.uid)
    result.append(
        TInvestAdmissionMetadataBreakdown(
            field="primary_board",
            entries=tuple(
                TInvestAdmissionMetadataValue(
                    state=state,
                    value=value,
                    count=len(uids),
                    source_uids=tuple(sorted(uids)),
                )
                for (state, value), uids in sorted(
                    grouped_board.items(), key=lambda item: (item[0][0].value, item[0][1] or "")
                )
            ),
        )
    )
    return tuple(result)
