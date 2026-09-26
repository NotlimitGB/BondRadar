"""Pure exact-ISIN bridge and actionable coverage reducer for Task296."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence, Set
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any

from app.schemas.tinvest_bond_identity_bridge import (
    BondIdentityProjection,
    TInvestAuditValueState,
    TInvestBondBridgeRow,
    TInvestBondIdentityBridgeCoverage,
    TInvestBondIdentityBridgeError,
    TInvestBondIdentityBridgeErrorCode,
    TInvestBondIdentityBridgeProvenance,
    TInvestBondIdentityBridgeView,
    TInvestBondUidAggregate,
    TInvestBridgeAvailabilityClass,
    TInvestBridgeMatchStateCount,
    TInvestBridgeReviewPriorityCount,
    TInvestBondBridgeMatchState,
    TInvestUnmatchedAuditBreakdown,
    TInvestUnmatchedAuditField,
    TInvestUnmatchedBondEvidence,
    TInvestUnmatchedMetadataValue,
    TInvestUnmatchedReviewPriority,
)
from app.schemas.tinvest_instrument_universe import (
    CONTRACT_VERSION as TINVEST_UNIVERSE_CONTRACT_VERSION,
    TInvestAvailabilityClass,
    TInvestBondUniverseInstrument,
)
from app.services.ofz_identity import is_ofz_instrument


_AUDIT_FIELDS: tuple[tuple[TInvestUnmatchedAuditField, str, str], ...] = (
    (TInvestUnmatchedAuditField.AVAILABILITY_CLASSIFICATION, "availability", "enum"),
    (TInvestUnmatchedAuditField.FOR_QUAL_INVESTOR, "qualification", "bool"),
    (TInvestUnmatchedAuditField.REQUIRED_TESTS_STATE, "required_tests_state", "str"),
    (TInvestUnmatchedAuditField.CURRENCY, "currency", "str"),
    (TInvestUnmatchedAuditField.CLASS_CODE, "class_code", "str"),
    (TInvestUnmatchedAuditField.COUNTRY_OF_RISK, "countryOfRisk", "str"),
    (TInvestUnmatchedAuditField.COUNTRY_OF_RISK_NAME, "countryOfRiskName", "str"),
    (TInvestUnmatchedAuditField.SECTOR, "sector", "str"),
    (TInvestUnmatchedAuditField.BOND_TYPE, "bondType", "str"),
    (TInvestUnmatchedAuditField.EXCHANGE, "exchange", "str"),
    (TInvestUnmatchedAuditField.REAL_EXCHANGE, "realExchange", "str"),
    (TInvestUnmatchedAuditField.OTC_FLAG, "otcFlag", "bool"),
    (TInvestUnmatchedAuditField.FOR_IIS_FLAG, "forIisFlag", "bool"),
)

_UNMATCHED_STATES = tuple(
    state
    for state in TInvestBondBridgeMatchState
    if state is not TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
)


def _invalid_input(_message: str) -> None:
    raise TInvestBondIdentityBridgeError(
        TInvestBondIdentityBridgeErrorCode.INVALID_INPUT
    )


def _snapshot_sequence(value: object, name: str) -> tuple[Any, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping, Set))
        or not isinstance(value, Sequence)
    ):
        _invalid_input(f"{name} must be a deterministic sequence")
    return tuple(value)


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        _invalid_input(f"{name} must be a string or None")
    return value


def _nonblank_isin(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _normalized_isin(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    return normalized or None


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _audit_sort_key(
    item: tuple[tuple[TInvestAuditValueState, str | bool | None], list[str]],
) -> tuple[int, str | bool, str]:
    (state, value), _uids = item
    if type(value) is str:
        return (0, value, state.value)
    if type(value) is bool:
        return (1, value, state.value)
    return (2, "", state.value)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _percentage(count: int, denominator: int) -> Decimal:
    if denominator == 0:
        return Decimal("0")
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return Decimal(count) * Decimal("100") / Decimal(denominator)


def _availability(
    value: TInvestAvailabilityClass | None,
) -> TInvestBridgeAvailabilityClass:
    if value is None:
        return TInvestBridgeAvailabilityClass.UNKNOWN
    return TInvestBridgeAvailabilityClass(value.value)


def _review_priority(
    availability: TInvestBridgeAvailabilityClass,
    qualification: bool | None,
) -> TInvestUnmatchedReviewPriority:
    if availability is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE:
        if qualification is False:
            return TInvestUnmatchedReviewPriority.BUYABLE_NONQUAL_FLAG_FALSE
        if qualification is True:
            return TInvestUnmatchedReviewPriority.BUYABLE_QUAL_RESTRICTED
        return TInvestUnmatchedReviewPriority.BUYABLE_QUAL_UNKNOWN
    if availability in (
        TInvestBridgeAvailabilityClass.API_VISIBLE_NOT_BUYABLE,
        TInvestBridgeAvailabilityClass.API_TRADE_UNAVAILABLE,
    ):
        return TInvestUnmatchedReviewPriority.NOT_CURRENTLY_BUYABLE
    return TInvestUnmatchedReviewPriority.AVAILABILITY_UNKNOWN


def _audit_value(
    source: TInvestBondUniverseInstrument,
    field: TInvestUnmatchedAuditField,
    source_key: str,
    expected_kind: str,
) -> tuple[TInvestAuditValueState, str | bool | None]:
    if field is TInvestUnmatchedAuditField.AVAILABILITY_CLASSIFICATION:
        return TInvestAuditValueState.SOURCE_VALUE, _availability(
            source.availability_classification
        ).value
    if field is TInvestUnmatchedAuditField.FOR_QUAL_INVESTOR:
        value = source.for_qual_investor
        if value is None:
            return TInvestAuditValueState.NOT_SUPPLIED, None
        if type(value) is bool:
            return TInvestAuditValueState.SOURCE_VALUE, value
        return TInvestAuditValueState.INVALID_SOURCE_VALUE, None
    if field is TInvestUnmatchedAuditField.REQUIRED_TESTS_STATE:
        value = source.required_tests_state
        if type(value) is str and value in ("NOT_SUPPLIED", "SOURCE_EMPTY", "SOURCE_VALUES"):
            return TInvestAuditValueState.SOURCE_VALUE, value
        return TInvestAuditValueState.INVALID_SOURCE_VALUE, None

    source_fields = source.source_fields
    if type(source_fields) is not dict:
        return TInvestAuditValueState.INVALID_SOURCE_VALUE, None
    if source_key in source_fields:
        raw: object = source_fields[source_key]
    elif field is TInvestUnmatchedAuditField.CURRENCY:
        raw = source.currency
    elif field is TInvestUnmatchedAuditField.CLASS_CODE:
        raw = source.class_code
    else:
        return TInvestAuditValueState.NOT_SUPPLIED, None

    if raw is None:
        return TInvestAuditValueState.NOT_SUPPLIED, None
    if expected_kind == "str" and type(raw) is str:
        return TInvestAuditValueState.SOURCE_VALUE, raw
    if expected_kind == "bool" and type(raw) is bool:
        return TInvestAuditValueState.SOURCE_VALUE, raw
    return TInvestAuditValueState.INVALID_SOURCE_VALUE, None


class TInvestBondIdentityBridgeService:
    """Build a reproducible bridge from already-normalized Task295 evidence."""

    @staticmethod
    def build(
        source_bonds: Sequence[TInvestBondUniverseInstrument],
        internal_bonds: Sequence[BondIdentityProjection],
        core_m3_complete_bond_ids: Sequence[int],
    ) -> TInvestBondIdentityBridgeView:
        source_rows = _snapshot_sequence(source_bonds, "source_bonds")
        internal_rows = _snapshot_sequence(internal_bonds, "internal_bonds")
        core_rows = _snapshot_sequence(
            core_m3_complete_bond_ids, "core_m3_complete_bond_ids"
        )

        seen_uids: set[str] = set()
        for source in source_rows:
            if type(source) is not TInvestBondUniverseInstrument:
                _invalid_input("source rows must be Task295 bond instruments")
            if type(source.uid) is not str or not source.uid.strip():
                _invalid_input("source UID must be a nonblank string")
            if source.uid in seen_uids:
                raise TInvestBondIdentityBridgeError(
                    TInvestBondIdentityBridgeErrorCode.SOURCE_UID_CONFLICT
                )
            seen_uids.add(source.uid)

        for source in source_rows:
            if source.contract_version != TINVEST_UNIVERSE_CONTRACT_VERSION:
                _invalid_input("unsupported Task295 contract version")
            for name in ("isin", "figi", "ticker"):
                _optional_string(getattr(source, name), f"source.{name}")
            if (
                source.source_universe != "BASE"
                or source.instrument_classification != "TINVEST_BASE_BOND"
                or source.listed_in_source_universe is not True
            ):
                _invalid_input("source row is not a Task295 BASE bond")
            for name in (
                "api_trade_available",
                "buy_available",
                "sell_available",
                "for_qual_investor",
            ):
                value = getattr(source, name)
                if value is not None and type(value) is not bool:
                    _invalid_input(f"source.{name} must be boolean or None")
            if source.required_tests is not None and (
                type(source.required_tests) is not tuple
                or any(type(item) is not str for item in source.required_tests)
            ):
                _invalid_input("source.required_tests must be a string tuple or None")
            if source.required_tests_state not in (
                "NOT_SUPPLIED",
                "SOURCE_EMPTY",
                "SOURCE_VALUES",
            ):
                _invalid_input("invalid Task295 required-tests state")
            if source.availability_classification is not None and type(
                source.availability_classification
            ) is not TInvestAvailabilityClass:
                _invalid_input("invalid Task295 availability classification")

        identities: dict[int, BondIdentityProjection] = {}
        exact_isin_index: dict[str, list[BondIdentityProjection]] = {}
        normalized_isin_index: dict[str, list[BondIdentityProjection]] = {}
        for projection in internal_rows:
            if type(projection) is not BondIdentityProjection:
                _invalid_input("internal rows must be BondIdentityProjection")
            if projection.contract_version != "bond-identity-projection-v1":
                _invalid_input("unsupported internal identity projection version")
            if type(projection.bond_id) is not int or projection.bond_id <= 0:
                _invalid_input("Bond ID must be an exact positive integer")
            isin = _optional_string(projection.isin, "projection.isin")
            _optional_string(projection.secid, "projection.secid")
            if projection.bond_id in identities:
                raise TInvestBondIdentityBridgeError(
                    TInvestBondIdentityBridgeErrorCode.INTERNAL_BOND_ID_CONFLICT
                )
            identities[projection.bond_id] = projection
            usable_isin = _nonblank_isin(isin)
            if usable_isin is None:
                continue
            exact_isin_index.setdefault(usable_isin, []).append(projection)
            normalized_isin_index.setdefault(
                usable_isin.strip().upper(), []
            ).append(projection)

        core_ids: set[int] = set()
        for bond_id in core_rows:
            if type(bond_id) is not int or bond_id <= 0:
                raise TInvestBondIdentityBridgeError(
                    TInvestBondIdentityBridgeErrorCode.INVALID_INPUT
                )
            if bond_id in core_ids:
                raise TInvestBondIdentityBridgeError(
                    TInvestBondIdentityBridgeErrorCode.CORE_BOND_ID_CONFLICT
                )
            if bond_id not in identities:
                raise TInvestBondIdentityBridgeError(
                    TInvestBondIdentityBridgeErrorCode.CORE_BOND_ID_UNKNOWN
                )
            core_ids.add(bond_id)

        source_isin_groups: dict[str, list[str]] = {}
        for source in source_rows:
            isin = _nonblank_isin(source.isin)
            if isin is not None:
                source_isin_groups.setdefault(isin, []).append(source.uid)
        source_isin_group_sizes = {
            isin: len(uids) for isin, uids in source_isin_groups.items()
        }
        duplicate_isin_groups = {
            isin: uids
            for isin, uids in source_isin_groups.items()
            if len(uids) > 1
        }
        duplicate_isin_group_count = len(duplicate_isin_groups)
        duplicate_isin_row_count = sum(
            len(uids) for uids in duplicate_isin_groups.values()
        )

        ordered_sources = tuple(sorted(source_rows, key=lambda row: row.uid))
        bridge_rows: list[TInvestBondBridgeRow] = []
        unmatched: list[TInvestUnmatchedBondEvidence] = []
        matched_by_bond: dict[int, list[TInvestBondBridgeRow]] = {}

        for source in ordered_sources:
            source_isin = _nonblank_isin(source.isin)
            group_size = (
                source_isin_group_sizes.get(source_isin)
                if source_isin is not None
                else None
            )
            match_state: TInvestBondBridgeMatchState
            match_method: str | None = None
            matched_projection: BondIdentityProjection | None = None
            normalized_candidate_id: int | None = None

            if source_isin is None:
                match_state = TInvestBondBridgeMatchState.UNRESOLVED_NO_SOURCE_ISIN
            else:
                exact_candidates = exact_isin_index.get(source_isin, [])
                if len(exact_candidates) == 1:
                    matched_projection = exact_candidates[0]
                    match_state = TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
                    match_method = "EXACT_ISIN"
                elif len(exact_candidates) > 1:
                    match_state = (
                        TInvestBondBridgeMatchState.CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS
                    )
                else:
                    normalized = _normalized_isin(source_isin)
                    normalized_candidates = (
                        normalized_isin_index.get(normalized, [])
                        if normalized is not None
                        else []
                    )
                    if len(normalized_candidates) == 1:
                        match_state = (
                            TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE
                        )
                        normalized_candidate_id = normalized_candidates[0].bond_id
                    elif len(normalized_candidates) > 1:
                        match_state = (
                            TInvestBondBridgeMatchState.CONFLICT_NORMALIZED_ISIN_AMBIGUOUS
                        )
                    else:
                        match_state = (
                            TInvestBondBridgeMatchState.UNRESOLVED_NO_INTERNAL_EXACT_ISIN
                        )

            availability = _availability(source.availability_classification)
            source_values = {
                "contract_version": "tinvest-bond-identity-bridge-v1",
                "source_uid": source.uid,
                "source_isin": source.isin,
                "source_figi": source.figi,
                "source_ticker": source.ticker,
                "source_class_code": (
                    source.class_code if type(source.class_code) is str else None
                ),
                "source_isin_group_size": group_size,
                "availability_classification": availability,
                "api_trade_available": source.api_trade_available,
                "buy_available": source.buy_available,
                "sell_available": source.sell_available,
                "for_qual_investor": source.for_qual_investor,
                "required_tests": source.required_tests,
                "required_tests_state": source.required_tests_state,
                "match_state": match_state,
                "match_method": match_method,
                "bond_id": matched_projection.bond_id if matched_projection else None,
                "bond_isin": matched_projection.isin if matched_projection else None,
                "bond_secid": matched_projection.secid if matched_projection else None,
                "is_ofz": (
                    is_ofz_instrument(
                        isin=matched_projection.isin,
                        secid=matched_projection.secid,
                    )
                    if matched_projection
                    else None
                ),
                "normalized_only_candidate_bond_id": normalized_candidate_id,
            }
            bridge_row = TInvestBondBridgeRow(**source_values)
            bridge_rows.append(bridge_row)

            if matched_projection is not None:
                matched_by_bond.setdefault(matched_projection.bond_id, []).append(
                    bridge_row
                )
            else:
                priority = _review_priority(availability, source.for_qual_investor)
                unmatched.append(
                    TInvestUnmatchedBondEvidence(
                        source_uid=source.uid,
                        source_isin=source.isin,
                        source_figi=source.figi,
                        source_ticker=source.ticker,
                        source_class_code=(
                            source.class_code
                            if type(source.class_code) is str
                            else None
                        ),
                        source_isin_group_size=group_size,
                        match_state=match_state,
                        review_priority=priority,
                        availability_classification=availability,
                        api_trade_available=source.api_trade_available,
                        buy_available=source.buy_available,
                        sell_available=source.sell_available,
                        for_qual_investor=source.for_qual_investor,
                        required_tests=source.required_tests,
                        required_tests_state=source.required_tests_state,
                    )
                )

        bond_aggregates: list[TInvestBondUidAggregate] = []
        for bond_id in sorted(matched_by_bond):
            projection = identities[bond_id]
            rows = sorted(matched_by_bond[bond_id], key=lambda row: row.source_uid)
            all_uids = tuple(row.source_uid for row in rows)
            buyable_uids = tuple(
                row.source_uid
                for row in rows
                if row.availability_classification
                is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            )
            nonqual_buyable_uids = tuple(
                row.source_uid
                for row in rows
                if row.availability_classification
                is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                and row.for_qual_investor is False
            )
            qual_restricted_buyable_uids = tuple(
                row.source_uid
                for row in rows
                if row.availability_classification
                is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
                and row.for_qual_investor is True
            )
            bond_aggregates.append(
                TInvestBondUidAggregate(
                    bond_id=bond_id,
                    bond_isin=projection.isin,
                    bond_secid=projection.secid,
                    is_ofz=is_ofz_instrument(isin=projection.isin, secid=projection.secid),
                    matched_uid_count=len(all_uids),
                    matched_uids=all_uids,
                    api_buyable_uid_count=len(buyable_uids),
                    api_buyable_uids=buyable_uids,
                    has_api_buyable_uid=bool(buyable_uids),
                    api_buyable_nonqual_flag_false_uid_count=len(nonqual_buyable_uids),
                    api_buyable_nonqual_flag_false_uids=nonqual_buyable_uids,
                    has_nonqual_flag_false_buyable_uid=bool(nonqual_buyable_uids),
                    api_buyable_qual_restricted_uid_count=len(qual_restricted_buyable_uids),
                    api_buyable_qual_restricted_uids=qual_restricted_buyable_uids,
                    has_qual_restricted_buyable_uid=bool(qual_restricted_buyable_uids),
                )
            )

        unmatched_state_counts = []
        for state in sorted(_UNMATCHED_STATES, key=lambda item: item.value):
            uids = tuple(
                row.source_uid for row in unmatched if row.match_state is state
            )
            unmatched_state_counts.append(
                TInvestBridgeMatchStateCount(
                    match_state=state,
                    count=len(uids),
                    source_uids=uids,
                )
            )

        unmatched_priority_counts = []
        for priority in sorted(TInvestUnmatchedReviewPriority, key=lambda item: item.value):
            uids = tuple(
                row.source_uid for row in unmatched if row.review_priority is priority
            )
            unmatched_priority_counts.append(
                TInvestBridgeReviewPriorityCount(
                    review_priority=priority,
                    count=len(uids),
                    source_uids=uids,
                )
            )

        source_by_uid = {row.uid: row for row in ordered_sources}
        unmatched_metadata_breakdowns = []
        for field, source_key, expected_kind in _AUDIT_FIELDS:
            grouped: dict[
                tuple[TInvestAuditValueState, str | bool | None], list[str]
            ] = {}
            for unmatched_row in unmatched:
                state, value = _audit_value(
                    source_by_uid[unmatched_row.source_uid],
                    field,
                    source_key,
                    expected_kind,
                )
                grouped.setdefault((state, value), []).append(unmatched_row.source_uid)
            entries = tuple(
                TInvestUnmatchedMetadataValue(
                    state=state,
                    value=value,
                    count=len(uids),
                    source_uids=tuple(sorted(uids)),
                )
                for (state, value), uids in sorted(
                    grouped.items(),
                    key=_audit_sort_key,
                )
            )
            unmatched_metadata_breakdowns.append(
                TInvestUnmatchedAuditBreakdown(field=field, entries=entries)
            )

        matched_rows = tuple(
            row
            for row in bridge_rows
            if row.match_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
        )
        matched_uids = tuple(row.source_uid for row in matched_rows)
        unmatched_uids = tuple(row.source_uid for row in unmatched)
        matched_bond_ids = tuple(sorted(matched_by_bond))
        matched_bond_id_set = set(matched_bond_ids)
        matched_ofz_ids = {
            bond_id
            for bond_id in matched_bond_ids
            if is_ofz_instrument(
                isin=identities[bond_id].isin,
                secid=identities[bond_id].secid,
            )
        }
        matched_buyable_bond_ids = {
            aggregate.bond_id
            for aggregate in bond_aggregates
            if aggregate.has_api_buyable_uid
        }
        matched_nonqual_buyable_bond_ids = {
            aggregate.bond_id
            for aggregate in bond_aggregates
            if aggregate.has_nonqual_flag_false_buyable_uid
        }
        matched_core_ids = matched_bond_id_set & core_ids
        matched_buyable_core_ids = matched_buyable_bond_ids & core_ids
        matched_nonqual_buyable_core_ids = matched_nonqual_buyable_bond_ids & core_ids
        source_buyable_uid_count = sum(
            row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
            for row in bridge_rows
        )
        unmatched_buyable = [
            row
            for row in unmatched
            if row.availability_classification
            is TInvestBridgeAvailabilityClass.API_BUY_AVAILABLE
        ]

        coverage = TInvestBondIdentityBridgeCoverage(
            source_bond_uid_count=len(bridge_rows),
            source_api_buyable_uid_count=source_buyable_uid_count,
            matched_source_uid_count=len(matched_rows),
            matched_unique_bond_count=len(matched_bond_ids),
            matched_non_ofz_unique_bond_count=len(
                matched_bond_id_set - matched_ofz_ids
            ),
            matched_ofz_unique_bond_count=len(matched_ofz_ids),
            matched_api_buyable_unique_bond_count=len(matched_buyable_bond_ids),
            matched_api_buyable_nonqual_flag_false_unique_bond_count=len(
                matched_nonqual_buyable_bond_ids
            ),
            matched_core_m3_complete_count=len(matched_core_ids),
            matched_api_buyable_core_m3_complete_count=len(matched_buyable_core_ids),
            matched_api_buyable_nonqual_flag_false_core_m3_complete_count=len(
                matched_nonqual_buyable_core_ids
            ),
            api_buyable_core_coverage_pct=_percentage(
                len(matched_buyable_core_ids), len(matched_buyable_bond_ids)
            ),
            api_buyable_nonqual_flag_false_core_coverage_pct=_percentage(
                len(matched_nonqual_buyable_core_ids),
                len(matched_nonqual_buyable_bond_ids),
            ),
            unmatched_uid_count=len(unmatched),
            unmatched_api_buyable_uid_count=len(unmatched_buyable),
            unmatched_api_buyable_nonqual_flag_false_uid_count=sum(
                row.for_qual_investor is False for row in unmatched_buyable
            ),
            unmatched_api_buyable_qual_restricted_uid_count=sum(
                row.for_qual_investor is True for row in unmatched_buyable
            ),
            unmatched_visible_not_buyable_uid_count=sum(
                row.availability_classification
                is TInvestBridgeAvailabilityClass.API_VISIBLE_NOT_BUYABLE
                for row in unmatched
            ),
            unmatched_api_trade_unavailable_uid_count=sum(
                row.availability_classification
                is TInvestBridgeAvailabilityClass.API_TRADE_UNAVAILABLE
                for row in unmatched
            ),
            unmatched_unknown_availability_uid_count=sum(
                row.availability_classification
                is TInvestBridgeAvailabilityClass.UNKNOWN
                for row in unmatched
            ),
        )

        provenance = TInvestBondIdentityBridgeProvenance(
            source_uid_count=len(bridge_rows),
            internal_bond_count=len(identities),
            core_m3_complete_bond_id_count=len(core_ids),
            source_duplicate_isin_group_count=duplicate_isin_group_count,
            source_duplicate_isin_row_count=duplicate_isin_row_count,
            bridge_row_set_sha256=_sha256_json(
                [row.model_dump(mode="json") for row in bridge_rows]
            ),
            unmatched_uid_set_sha256=_sha256_json(list(unmatched_uids)),
            matched_uid_set_sha256=_sha256_json(list(matched_uids)),
            matched_unique_bond_id_set_sha256=_sha256_json(
                list(matched_bond_ids)
            ),
        )

        return TInvestBondIdentityBridgeView(
            bridge_rows=tuple(bridge_rows),
            bond_aggregates=tuple(bond_aggregates),
            unmatched_rows=tuple(unmatched),
            unmatched_match_state_counts=tuple(unmatched_state_counts),
            unmatched_review_priority_counts=tuple(unmatched_priority_counts),
            unmatched_metadata_breakdowns=tuple(unmatched_metadata_breakdowns),
            source_duplicate_isin_group_count=duplicate_isin_group_count,
            source_duplicate_isin_row_count=duplicate_isin_row_count,
            coverage=coverage,
            provenance=provenance,
        )
