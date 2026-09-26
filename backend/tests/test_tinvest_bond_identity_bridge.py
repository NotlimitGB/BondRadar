from __future__ import annotations

import ast
from copy import deepcopy
from decimal import Decimal, ROUND_DOWN, getcontext
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.tinvest_bond_identity_bridge import (
    BondIdentityProjection,
    TInvestAuditValueState,
    TInvestBondIdentityBridgeError,
    TInvestBondIdentityBridgeErrorCode,
    TInvestBondBridgeMatchState,
    TInvestBridgeAvailabilityClass,
    TInvestUnmatchedAuditField,
    TInvestUnmatchedReviewPriority,
    TInvestBondIdentityBridgeView,
)
from app.schemas.tinvest_instrument_universe import (
    TInvestAvailabilityClass,
    TInvestBondUniverseInstrument,
)
from app.services.tinvest_bond_identity_bridge_service import (
    TInvestBondIdentityBridgeService,
)


def source_bond(
    uid: str,
    isin: str | None,
    *,
    availability: TInvestAvailabilityClass | None = TInvestAvailabilityClass.API_BUY_AVAILABLE,
    api_trade: bool | None = True,
    buy: bool | None = True,
    sell: bool | None = True,
    qualification: bool | None = False,
    ticker: str | None = "BOND-X",
    figi: str | None = "FIGI-X",
    name: str | None = "Source bond",
    class_code: str | None = "TQCB",
    currency: str | None = "rub",
    required_tests: tuple[str, ...] | None = None,
    required_tests_state: str = "NOT_SUPPLIED",
    source_fields: dict[str, Any] | None = None,
) -> TInvestBondUniverseInstrument:
    if source_fields is None:
        source_fields = {
            "uid": uid,
            "isin": isin,
            "ticker": ticker,
            "classCode": class_code,
            "currency": currency,
        }
    return TInvestBondUniverseInstrument(
        uid=uid,
        isin=isin,
        ticker=ticker,
        figi=figi,
        name=name,
        class_code=class_code,
        currency=currency,
        api_trade_available=api_trade,
        buy_available=buy,
        sell_available=sell,
        for_qual_investor=qualification,
        required_tests=required_tests,
        required_tests_state=required_tests_state,
        source_fields=source_fields,
        availability_classification=availability,
    )


def identity(
    bond_id: int,
    isin: str | None,
    secid: str | None = None,
) -> BondIdentityProjection:
    return BondIdentityProjection(bond_id=bond_id, isin=isin, secid=secid)


def build(
    source: list[TInvestBondUniverseInstrument],
    internal: list[BondIdentityProjection],
    core: list[int] | tuple[int, ...] = (),
) -> TInvestBondIdentityBridgeView:
    return TInvestBondIdentityBridgeService.build(source, internal, core)


def test_exact_isin_bridge_preserves_uid_and_source_identity_verbatim() -> None:
    source = source_bond("  UID-1  ", "RU000A000001", ticker="TICKER-1")
    result = build([source], [identity(41, "RU000A000001", "SU000A000041")], [41])

    row = result.bridge_rows[0]
    assert row.source_uid == "  UID-1  "
    assert row.source_isin == "RU000A000001"
    assert row.source_ticker == "TICKER-1"
    assert row.match_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
    assert row.match_method == "EXACT_ISIN"
    assert (row.bond_id, row.bond_isin, row.bond_secid) == (
        41,
        "RU000A000001",
        "SU000A000041",
    )
    assert row.is_ofz is True
    assert result.coverage.matched_source_uid_count == 1
    assert result.coverage.matched_unique_bond_count == 1
    assert result.bond_aggregates[0].matched_uids == ("  UID-1  ",)


def test_required_tests_presence_and_exact_isin_spacing_are_preserved() -> None:
    rows = [
        source_bond(
            "UID-VALUES",
            " RU000A000001 ",
            required_tests=(" Test A ", "Test-B"),
            required_tests_state="SOURCE_VALUES",
        ),
        source_bond(
            "UID-EMPTY",
            "RU000A000001",
            required_tests=(),
            required_tests_state="SOURCE_EMPTY",
        ),
    ]
    result = build(
        rows,
        [identity(41, " RU000A000001 "), identity(42, "RU000A000001")],
    )
    by_uid = {row.source_uid: row for row in result.bridge_rows}

    assert by_uid["UID-VALUES"].match_state is TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN
    assert by_uid["UID-VALUES"].source_isin == " RU000A000001 "
    assert by_uid["UID-VALUES"].required_tests == (" Test A ", "Test-B")
    assert by_uid["UID-VALUES"].required_tests_state == "SOURCE_VALUES"
    assert by_uid["UID-EMPTY"].required_tests == ()
    assert by_uid["UID-EMPTY"].required_tests_state == "SOURCE_EMPTY"


def test_duplicate_source_isin_is_allowed_and_all_uids_map_to_one_bond() -> None:
    result = build(
        [
            source_bond("UID-B", "RU-SHARED"),
            source_bond("UID-A", "RU-SHARED", qualification=True),
        ],
        [identity(7, "RU-SHARED")],
    )

    assert [row.source_uid for row in result.bridge_rows] == ["UID-A", "UID-B"]
    assert [row.bond_id for row in result.bridge_rows] == [7, 7]
    assert [row.source_isin_group_size for row in result.bridge_rows] == [2, 2]
    assert result.source_duplicate_isin_group_count == 1
    assert result.source_duplicate_isin_row_count == 2
    assert result.coverage.matched_source_uid_count == 2
    assert result.coverage.matched_unique_bond_count == 1
    assert result.bond_aggregates[0].matched_uid_count == 2
    assert result.bond_aggregates[0].matched_uids == ("UID-A", "UID-B")


def test_duplicate_uid_fails_whole_build_with_typed_code() -> None:
    rows = [source_bond("DUP", "RU-A"), source_bond("DUP", "RU-B")]

    with pytest.raises(TInvestBondIdentityBridgeError) as error:
        build(rows, [identity(1, "RU-A"), identity(2, "RU-B")])

    assert error.value.code is TInvestBondIdentityBridgeErrorCode.SOURCE_UID_CONFLICT


@pytest.mark.parametrize(
    ("source_isin", "internal", "expected", "candidate_id"),
    [
        (
            None,
            [identity(1, "RU-A")],
            TInvestBondBridgeMatchState.UNRESOLVED_NO_SOURCE_ISIN,
            None,
        ),
        (
            "RU-A",
            [identity(1, "RU-B")],
            TInvestBondBridgeMatchState.UNRESOLVED_NO_INTERNAL_EXACT_ISIN,
            None,
        ),
        (
            " RU-A ",
            [identity(1, "RU-A")],
            TInvestBondBridgeMatchState.UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE,
            1,
        ),
        (
            "ru-a",
            [identity(1, "RU-A"), identity(2, "RU-A ")],
            TInvestBondBridgeMatchState.CONFLICT_NORMALIZED_ISIN_AMBIGUOUS,
            None,
        ),
        (
            "RU-A",
            [identity(1, "RU-A"), identity(2, "RU-A")],
            TInvestBondBridgeMatchState.CONFLICT_INTERNAL_EXACT_ISIN_AMBIGUOUS,
            None,
        ),
    ],
)
def test_isin_unresolved_and_conflict_states_fail_closed(
    source_isin: str | None,
    internal: list[BondIdentityProjection],
    expected: TInvestBondBridgeMatchState,
    candidate_id: int | None,
) -> None:
    row = build([source_bond("UID-X", source_isin)], internal).bridge_rows[0]

    assert row.match_state is expected
    assert row.bond_id is None
    assert row.match_method is None
    assert row.is_ofz is None
    assert row.normalized_only_candidate_bond_id == candidate_id


def test_name_ticker_and_figi_never_bridge_or_classify_unmatched_source() -> None:
    unmatched = source_bond(
        "UID-NO-ISIN",
        None,
        name="ОФЗ-ПД",
        ticker="SU26238RMFS4",
        figi="FIGI-ONLY",
    )
    result = build([unmatched], [identity(9, "RU-OTHER", "SU-OTHER")])
    row = result.bridge_rows[0]

    assert row.match_state is TInvestBondBridgeMatchState.UNRESOLVED_NO_SOURCE_ISIN
    assert row.bond_id is None
    assert row.is_ofz is None
    assert row.normalized_only_candidate_bond_id is None


def test_many_uids_preserve_independent_availability_and_qualification() -> None:
    result = build(
        [
            source_bond("UID-TRUE", "RU-A", qualification=True),
            source_bond("UID-FALSE", "RU-A", qualification=False),
            source_bond("UID-UNKNOWN", "RU-A", qualification=None),
        ],
        [identity(1, "RU-A")],
    )
    aggregate = result.bond_aggregates[0]

    assert aggregate.matched_uid_count == 3
    assert aggregate.api_buyable_uid_count == 3
    assert aggregate.api_buyable_uids == ("UID-FALSE", "UID-TRUE", "UID-UNKNOWN")
    assert aggregate.has_api_buyable_uid is True
    assert aggregate.api_buyable_nonqual_flag_false_uids == ("UID-FALSE",)
    assert aggregate.has_nonqual_flag_false_buyable_uid is True
    assert aggregate.api_buyable_qual_restricted_uids == ("UID-TRUE",)
    assert aggregate.has_qual_restricted_buyable_uid is True
    rows = {row.source_uid: row for row in result.bridge_rows}
    assert rows["UID-TRUE"].for_qual_investor is True
    assert rows["UID-FALSE"].for_qual_investor is False
    assert rows["UID-UNKNOWN"].for_qual_investor is None


def test_coverage_uses_unique_bonds_and_task283_decimal_operation_order() -> None:
    source = [
        source_bond("UID-1A", "RU-1"),
        source_bond("UID-1B", "RU-1"),
        source_bond("UID-2", "RU-2", qualification=True),
        source_bond("UID-3", "RU-3", qualification=False),
        source_bond(
            "UID-4-NOT-BUYABLE",
            "RU-4",
            availability=TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE,
            api_trade=True,
            buy=False,
            qualification=False,
        ),
    ]
    internal = [identity(1, "RU-1"), identity(2, "RU-2"), identity(3, "RU-3"), identity(4, "RU-4")]
    result = build(source, internal, [1])
    coverage = result.coverage

    assert coverage.source_bond_uid_count == 5
    assert coverage.source_api_buyable_uid_count == 4
    assert coverage.matched_source_uid_count == 5
    assert coverage.matched_unique_bond_count == 4
    assert coverage.matched_api_buyable_unique_bond_count == 3
    assert coverage.matched_api_buyable_nonqual_flag_false_unique_bond_count == 2
    assert coverage.matched_core_m3_complete_count == 1
    assert coverage.matched_api_buyable_core_m3_complete_count == 1
    assert coverage.api_buyable_core_coverage_pct == Decimal(
        "33.33333333333333333333333333"
    )
    assert coverage.api_buyable_nonqual_flag_false_core_coverage_pct == Decimal(
        "50.0"
    )


def test_unknown_availability_is_explicit_and_qualification_is_not_inferred() -> None:
    unmatched = source_bond(
        "UID-UNKNOWN",
        "NO-MATCH",
        availability=None,
        api_trade=None,
        buy=None,
        qualification=None,
    )
    result = build([unmatched], [])
    row = result.unmatched_rows[0]

    assert row.availability_classification is TInvestBridgeAvailabilityClass.UNKNOWN
    assert row.for_qual_investor is None
    assert row.review_priority is TInvestUnmatchedReviewPriority.AVAILABILITY_UNKNOWN
    assert result.coverage.unmatched_unknown_availability_uid_count == 1

    buyable_unknown_qualification = build(
        [
            source_bond(
                "UID-BUYABLE-QUAL-UNKNOWN",
                "NO-MATCH-2",
                availability=TInvestAvailabilityClass.API_BUY_AVAILABLE,
                qualification=None,
            )
        ],
        [],
    )
    assert (
        buyable_unknown_qualification.unmatched_rows[0].review_priority
        is TInvestUnmatchedReviewPriority.BUYABLE_QUAL_UNKNOWN
    )


def _metadata_entry(result, field: TInvestUnmatchedAuditField, state: TInvestAuditValueState, value: Any = None):
    breakdown = next(item for item in result.unmatched_metadata_breakdowns if item.field is field)
    return next(entry for entry in breakdown.entries if entry.state is state and entry.value == value)


def test_unmatched_audit_breakdowns_preserve_values_and_mark_missing_or_invalid() -> None:
    rows = [
        source_bond(
            "UID-VALID",
            "NO-MATCH-1",
            availability=TInvestAvailabilityClass.API_BUY_AVAILABLE,
            qualification=False,
            required_tests=("A",),
            required_tests_state="SOURCE_VALUES",
            source_fields={
                "countryOfRisk": " RU ",
                "countryOfRiskName": "Russia",
                "sector": " financial ",
                "bondType": "bond",
                "exchange": "MOEX",
                "realExchange": "MOEX_RF",
                "otcFlag": False,
                "forIisFlag": True,
            },
        ),
        source_bond(
            "UID-MISSING",
            "NO-MATCH-2",
            availability=None,
            qualification=None,
            source_fields={"countryOfRisk": None},
        ),
        source_bond(
            "UID-INVALID",
            "NO-MATCH-3",
            availability=TInvestAvailabilityClass.API_TRADE_UNAVAILABLE,
            qualification=True,
            source_fields={
                "countryOfRisk": ["not", "a", "string"],
                "otcFlag": "false",
            },
        ),
    ]
    result = build(rows, [])

    country = _metadata_entry(
        result,
        TInvestUnmatchedAuditField.COUNTRY_OF_RISK,
        TInvestAuditValueState.SOURCE_VALUE,
        " RU ",
    )
    assert country.source_uids == ("UID-VALID",)
    missing = _metadata_entry(
        result,
        TInvestUnmatchedAuditField.COUNTRY_OF_RISK,
        TInvestAuditValueState.NOT_SUPPLIED,
    )
    assert missing.source_uids == ("UID-MISSING",)
    invalid = _metadata_entry(
        result,
        TInvestUnmatchedAuditField.COUNTRY_OF_RISK,
        TInvestAuditValueState.INVALID_SOURCE_VALUE,
    )
    assert invalid.source_uids == ("UID-INVALID",)
    assert _metadata_entry(
        result,
        TInvestUnmatchedAuditField.OTC_FLAG,
        TInvestAuditValueState.INVALID_SOURCE_VALUE,
    ).source_uids == ("UID-INVALID",)
    assert not any(entry.value == ["not", "a", "string"] for group in result.unmatched_metadata_breakdowns for entry in group.entries)
    assert _metadata_entry(
        result,
        TInvestUnmatchedAuditField.REQUIRED_TESTS_STATE,
        TInvestAuditValueState.SOURCE_VALUE,
        "SOURCE_VALUES",
    ).source_uids == ("UID-VALID",)


def test_all_optional_source_audit_dimensions_are_present_and_exact() -> None:
    row = source_bond(
        "UID-META",
        "NO-MATCH",
        class_code=" TQCB ",
        currency=" RUB ",
        source_fields={
            "countryOfRisk": " RU ",
            "countryOfRiskName": " Russia ",
            "sector": " Finance ",
            "bondType": " Bond ",
            "exchange": " MOEX ",
            "realExchange": " MOEX_RF ",
            "otcFlag": False,
            "forIisFlag": True,
        },
    )
    result = build([row], [])
    expected = {
        TInvestUnmatchedAuditField.AVAILABILITY_CLASSIFICATION: "API_BUY_AVAILABLE",
        TInvestUnmatchedAuditField.FOR_QUAL_INVESTOR: False,
        TInvestUnmatchedAuditField.REQUIRED_TESTS_STATE: "NOT_SUPPLIED",
        TInvestUnmatchedAuditField.CURRENCY: " RUB ",
        TInvestUnmatchedAuditField.CLASS_CODE: " TQCB ",
        TInvestUnmatchedAuditField.COUNTRY_OF_RISK: " RU ",
        TInvestUnmatchedAuditField.COUNTRY_OF_RISK_NAME: " Russia ",
        TInvestUnmatchedAuditField.SECTOR: " Finance ",
        TInvestUnmatchedAuditField.BOND_TYPE: " Bond ",
        TInvestUnmatchedAuditField.EXCHANGE: " MOEX ",
        TInvestUnmatchedAuditField.REAL_EXCHANGE: " MOEX_RF ",
        TInvestUnmatchedAuditField.OTC_FLAG: False,
        TInvestUnmatchedAuditField.FOR_IIS_FLAG: True,
    }
    assert {group.field for group in result.unmatched_metadata_breakdowns} == set(expected)
    for field, value in expected.items():
        entry = _metadata_entry(
            result,
            field,
            TInvestAuditValueState.SOURCE_VALUE,
            value,
        )
        assert entry.count == 1
        assert entry.source_uids == ("UID-META",)


def test_malformed_normalized_currency_and_class_code_are_audited_without_failure() -> None:
    payload = source_bond("UID-BAD-OPTIONAL", "NO-MATCH").model_dump()
    payload["currency"] = 7
    payload["class_code"] = ["malformed"]
    payload["source_fields"] = {
        **payload["source_fields"],
        "currency": 7,
        "classCode": ["malformed"],
    }
    malformed = TInvestBondUniverseInstrument.model_construct(**payload)

    result = build([malformed], [])

    assert result.unmatched_rows[0].source_class_code is None
    assert _metadata_entry(
        result,
        TInvestUnmatchedAuditField.CURRENCY,
        TInvestAuditValueState.INVALID_SOURCE_VALUE,
    ).source_uids == ("UID-BAD-OPTIONAL",)
    assert _metadata_entry(
        result,
        TInvestUnmatchedAuditField.CLASS_CODE,
        TInvestAuditValueState.INVALID_SOURCE_VALUE,
    ).source_uids == ("UID-BAD-OPTIONAL",)


def test_unmatched_review_and_reason_counts_are_complete_and_sorted() -> None:
    result = build(
        [
            source_bond("U5", "N5", availability=None, api_trade=None, buy=None),
            source_bond(
                "U4",
                "N4",
                availability=TInvestAvailabilityClass.API_TRADE_UNAVAILABLE,
                api_trade=False,
                buy=True,
            ),
            source_bond(
                "U3",
                "N3",
                availability=TInvestAvailabilityClass.API_VISIBLE_NOT_BUYABLE,
                api_trade=True,
                buy=False,
                qualification=True,
            ),
            source_bond("U2", None, availability=TInvestAvailabilityClass.API_BUY_AVAILABLE, qualification=True),
            source_bond("U1", "N1", availability=TInvestAvailabilityClass.API_BUY_AVAILABLE, qualification=False),
        ],
        [],
    )

    assert [row.source_uid for row in result.unmatched_rows] == ["U1", "U2", "U3", "U4", "U5"]
    assert result.coverage.unmatched_uid_count == 5
    assert result.coverage.unmatched_api_buyable_uid_count == 2
    assert result.coverage.unmatched_api_buyable_nonqual_flag_false_uid_count == 1
    assert result.coverage.unmatched_api_buyable_qual_restricted_uid_count == 1
    assert result.coverage.unmatched_visible_not_buyable_uid_count == 1
    assert result.coverage.unmatched_api_trade_unavailable_uid_count == 1
    assert result.coverage.unmatched_unknown_availability_uid_count == 1
    match_counts = {item.match_state: item for item in result.unmatched_match_state_counts}
    assert match_counts[TInvestBondBridgeMatchState.UNRESOLVED_NO_SOURCE_ISIN].source_uids == ("U2",)
    assert match_counts[TInvestBondBridgeMatchState.UNRESOLVED_NO_INTERNAL_EXACT_ISIN].source_uids == ("U1", "U3", "U4", "U5")
    priorities = {item.review_priority: item for item in result.unmatched_review_priority_counts}
    assert priorities[TInvestUnmatchedReviewPriority.BUYABLE_NONQUAL_FLAG_FALSE].source_uids == ("U1",)
    assert priorities[TInvestUnmatchedReviewPriority.BUYABLE_QUAL_RESTRICTED].source_uids == ("U2",)
    assert priorities[TInvestUnmatchedReviewPriority.NOT_CURRENTLY_BUYABLE].source_uids == ("U3", "U4")


def test_zero_denominators_return_decimal_zero_and_empty_inputs_are_supported() -> None:
    result = build([], [], [])

    assert result.bridge_rows == ()
    assert result.unmatched_rows == ()
    assert result.coverage.api_buyable_core_coverage_pct == Decimal("0")
    assert result.coverage.api_buyable_nonqual_flag_false_core_coverage_pct == Decimal("0")


@pytest.mark.parametrize(
    ("source", "internal", "core", "code"),
    [
        ([source_bond("U", "RU-A")], [identity(1, "RU-A"), identity(1, "RU-B")], [], TInvestBondIdentityBridgeErrorCode.INTERNAL_BOND_ID_CONFLICT),
        ([source_bond("U", "RU-A")], [identity(1, "RU-A")], [True], TInvestBondIdentityBridgeErrorCode.INVALID_INPUT),
        ([source_bond("U", "RU-A")], [identity(1, "RU-A")], [1, 1], TInvestBondIdentityBridgeErrorCode.CORE_BOND_ID_CONFLICT),
        ([source_bond("U", "RU-A")], [identity(1, "RU-A")], [2], TInvestBondIdentityBridgeErrorCode.CORE_BOND_ID_UNKNOWN),
    ],
)
def test_invalid_internal_and_core_ids_fail_closed(source, internal, core, code) -> None:
    with pytest.raises(TInvestBondIdentityBridgeError) as error:
        build(source, internal, core)
    assert error.value.code is code


@pytest.mark.parametrize("bad", ["x", b"x", bytearray(b"x"), {1, 2}, {"a": 1}, iter([1])])
def test_nondeterministic_or_nonsequence_inputs_are_rejected(bad: Any) -> None:
    with pytest.raises(TInvestBondIdentityBridgeError):
        TInvestBondIdentityBridgeService.build(bad, [], [])  # type: ignore[arg-type]


def test_input_permutations_produce_identical_serialization_and_hashes() -> None:
    source = [
        source_bond("UID-B", "RU-B"),
        source_bond("UID-A", "RU-A"),
        source_bond("UID-X", "NO-MATCH", source_fields={"sector": "Other"}),
        source_bond("UID-Y", "NO-MATCH-2", source_fields={"sector": "A"}),
    ]
    internal = [identity(2, "RU-B"), identity(1, "RU-A"), identity(3, "RU-C")]
    left = build(source, internal, [2, 1])
    right = build(list(reversed(source)), list(reversed(internal)), [1, 2])

    assert left.model_dump(mode="json") == right.model_dump(mode="json")
    assert left.provenance.bridge_row_set_sha256 == right.provenance.bridge_row_set_sha256
    assert left.provenance.unmatched_uid_set_sha256 == right.provenance.unmatched_uid_set_sha256
    assert left.provenance.matched_uid_set_sha256 == right.provenance.matched_uid_set_sha256
    assert left.provenance.matched_unique_bond_id_set_sha256 == right.provenance.matched_unique_bond_id_set_sha256
    assert len(left.provenance.bridge_row_set_sha256) == 64
    sector = next(
        item
        for item in left.unmatched_metadata_breakdowns
        if item.field is TInvestUnmatchedAuditField.SECTOR
    )
    assert [entry.value for entry in sector.entries] == ["A", "Other"]


def test_decimal_context_and_input_objects_remain_unchanged() -> None:
    source = source_bond("UID-A", "RU-A", source_fields={"sector": "Utilities"})
    before_fields = deepcopy(source.source_fields)
    context = getcontext()
    old_prec, old_rounding = context.prec, context.rounding
    context.prec = 6
    context.rounding = ROUND_DOWN
    try:
        result = build([source], [identity(1, "RU-A")], [1])
        assert result.coverage.api_buyable_core_coverage_pct == Decimal("100")
        assert context.prec == 6
        assert context.rounding == ROUND_DOWN
    finally:
        context.prec = old_prec
        context.rounding = old_rounding
    assert source.source_fields == before_fields
    assert source.isin == "RU-A"


def test_frozen_extra_forbid_and_stable_serialization() -> None:
    result = build([source_bond("UID-X", "NO-MATCH")], [])
    first = result.model_dump_json()
    second = build([source_bond("UID-X", "NO-MATCH")], []).model_dump_json()
    assert first == second
    with pytest.raises(ValidationError):
        TInvestBondIdentityBridgeView.model_validate({**result.model_dump(), "unknown": 1})
    with pytest.raises(ValidationError):
        result.coverage.source_bond_uid_count = 2  # type: ignore[misc]


def test_ofz_identity_is_applied_only_to_exactly_matched_internal_projection() -> None:
    rows = [
        source_bond("UID-MATCH", "RU-OFZ", name="ОФЗ", ticker="SU-TICKER"),
        source_bond("UID-UNMATCHED", "NO-SUCH-ISIN", name="ОФЗ", ticker="SU26238RMFS4"),
    ]
    result = build(
        rows,
        [identity(10, "RU-OFZ", " SU26238RMFS4 "), identity(11, "RU-OTHER", "RU26238")],
    )

    by_uid = {row.source_uid: row for row in result.bridge_rows}
    assert by_uid["UID-MATCH"].is_ofz is True
    assert by_uid["UID-UNMATCHED"].bond_id is None
    assert by_uid["UID-UNMATCHED"].is_ofz is None
    assert result.coverage.matched_ofz_unique_bond_count == 1
    assert result.coverage.matched_non_ofz_unique_bond_count == 0


def test_static_service_boundary_has_no_database_network_or_feature_dependencies() -> None:
    path = Path(__file__).parents[1] / "app" / "services" / "tinvest_bond_identity_bridge_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imports |= {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden = (
        "sqlalchemy",
        "httpx",
        "requests",
        "urllib",
        "os",
        "pathlib",
        "app.services.tinvest_instrument_universe_client",
        "app.services.m3_audit_snapshot_service",
        "app.services.m3_coverage_audit_reducer",
        "app.services.bond_market_feature_service",
        "app.services.bond_modified_duration_service",
        "app.services.bond_dv01_service",
        "app.services.ofz_reference_curve_service",
    )
    assert not any(any(item in module for item in forbidden) for module in imports)
    assert "app.services.ofz_identity" in imports
    forbidden_calls = {"open", "urlopen", "commit", "flush", "add", "delete", "execute"}
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls.intersection(forbidden_calls)
