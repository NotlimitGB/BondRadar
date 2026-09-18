"""Task277 A–BE: synthetic schema inputs, without a database fixture."""

import ast
import inspect
import itertools
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, Inexact, ROUND_DOWN, ROUND_HALF_EVEN, localcontext

import pytest
from pydantic import ValidationError

from app.schemas.bond_credit_cohort_relative_value import BondCreditCohortRelativeValueMemberView
from app.schemas.bond_credit_comparability import SourceNativeRatingCohortKey
from app.schemas.credit_cohort_peer_spread_distribution import CreditCohortPeerSpreadDistributionView
from app.services.credit_cohort_peer_spread_distribution_service import CreditCohortPeerSpreadDistributionService

D = Decimal
DAY = date(2026, 9, 18)
build = CreditCohortPeerSpreadDistributionService.build


def key(**updates):
    return SourceNativeRatingCohortKey(**{
        **dict(target_kind="BOND", rating_agency="ACRA", source_provider="provider",
               rating_scale_raw="national", rating_value_raw="AA"), **updates,
    })


def member(fixture_bond_id=1, spread="10", **updates):
    bond_id = fixture_bond_id
    data = dict(
        bond_id=bond_id, isin=f"ISIN{bond_id}", secid=f"SEC{bond_id}", as_of_date=DAY,
        market_source="moex", requested_target_kind="BOND", requested_rating_agency="ACRA",
        status="READY", credit_comparability_status="READY", relative_value_status="READY",
        cohort_key=key(), rating_event_id=100 + bond_id, rating_event_date=DAY,
        rating_source_provider="provider", rating_scale_raw="national", rating_value_raw="AA",
        target_yield_to_maturity_pct=D("12"), target_duration_years=D("2"),
        reference_ofz_yield_pct=D("11.9"), spread_to_ofz_pp=D("0.1"),
        spread_to_ofz_bps=D(spread), interpolation_method="EXACT_NODE",
        market_snapshot_id=200 + bond_id, market_trade_date=DAY, curve_trade_date=DAY,
        availability=dict(has_credit_comparability=True, has_selected_rating_entry=True,
                          has_selected_cohort_key=True, has_relative_value=True,
                          has_spread_to_ofz=True, has_credit_cohort_relative_value_member=True),
        quality_flags=[], provenance=dict(
            credit_comparability_contract_version="bond-credit-comparability-v1",
            credit_identity=dict(bond_id=bond_id, as_of_date=DAY),
            relative_value_identity=dict(bond_id=bond_id, as_of_date=DAY, market_source="moex"),
            requested_target_kind="BOND", requested_rating_agency="ACRA",
            selected_entry_count=1, selected_entry_status="READY", selected_rating_event_id=100 + bond_id,
            selected_rating_event_date=DAY, selected_source_provider="provider",
            selected_rating_scale_raw="national", selected_rating_value_raw="AA",
            bond_legal_issuer_profile_id=None, legal_issuer_id=None,
            relative_value_contract_version="bond-relative-value-v1",
            ofz_curve_contract_version="ofz-reference-curve-v1", target_market_snapshot_id=200 + bond_id,
            target_market_trade_date=DAY, curve_trade_date=DAY, market_source="moex", as_of_date=DAY,
        ),
    )
    # Validate ordinary fixtures; contradictions are injected without schema coercion.
    return BondCreditCohortRelativeValueMemberView(**data).model_copy(update=updates)


def changed_cohort(row, **updates):
    cohort = key(**updates)
    return row.model_copy(update=dict(cohort_key=cohort, requested_target_kind=cohort.target_kind,
                                      requested_rating_agency=cohort.rating_agency))


def assert_null_math(result):
    for field in ("peer_min_spread_bps", "peer_median_spread_bps", "peer_mean_spread_bps",
                  "peer_max_spread_bps", "spread_minus_peer_median_bps", "target_spread_percentile"):
        assert getattr(result, field) is None
    for field in ("has_minimum_peer_count", "has_peer_distribution", "has_peer_median",
                  "has_target_spread_percentile", "has_spread_vs_peer_median", "has_ready_peer_distribution"):
        assert getattr(result.availability, field) is False


def assert_partition(result):
    counts = result.exclusions.model_dump(exclude={"duplicate_peer_bond_ids"})
    assert sum(counts.values()) + result.eligible_peer_count == result.candidate_count
    assert result.quality_flags == sorted(set(result.quality_flags))


INVALID_MEMBER_UPDATES = [
    {"status": "RELATIVE_VALUE_UNAVAILABLE"}, {"cohort_key": None},
    {"spread_to_ofz_bps": None}, {"spread_to_ofz_bps": D("NaN")},
    {"spread_to_ofz_bps": D("sNaN")}, {"spread_to_ofz_bps": D("Infinity")},
    {"spread_to_ofz_bps": D("-Infinity")}, {"spread_to_ofz_bps": 10},
    {"spread_to_ofz_bps": 10.0}, {"spread_to_ofz_bps": "10"},
    {"requested_target_kind": "LEGAL_ISSUER"}, {"requested_rating_agency": "NRA"},
    {"contract_version": "wrong"}, {"pit_ready": True}, {"pit_ready": 0},
    {"bond_id": True}, {"bond_id": 0}, {"bond_id": -1}, {"bond_id": "1"}, {"bond_id": 1.0},
    {"as_of_date": datetime(2026, 9, 18)}, {"as_of_date": "2026-09-18"},
    {"market_source": " "}, {"market_source": None}, {"market_source": 1},
    {"cohort_key": key().model_copy(update={"source_provider": None})},
    {"cohort_key": key().model_copy(update={"source_provider": " "})},
    {"cohort_key": key().model_copy(update={"rating_value_raw": ""})},
    {"cohort_key": key().model_copy(update={"rating_value_raw": 3})},
    {"cohort_key": key().model_copy(update={"rating_scale_raw": 3})},
    {"cohort_key": key().model_copy(update={"rating_agency": "OTHER"})},
    {"cohort_key": key().model_copy(update={"target_kind": "ISSUER"})},
    {"cohort_key": key().model_dump()},
]


@pytest.mark.parametrize("updates", INVALID_MEMBER_UPDATES)
def test_invalid_target_no_peer_processing_and_nullable_contract(updates):
    target = member(**updates)
    candidates = [member(2), member(2), member(3, spread_to_ofz_bps=D("NaN"))]
    result = build(target, candidates)
    assert result.status == "TARGET_MEMBER_INVALID"
    assert result.candidate_count == 3 and result.eligible_peer_count == 0
    assert result.exclusions.unprocessed_candidate_count == 3
    assert result.eligible_peers == [] and result.exclusions.duplicate_peer_bond_ids == []
    assert result.availability.has_valid_target is False
    assert_null_math(result)
    assert_partition(result)
    assert CreditCohortPeerSpreadDistributionView.model_validate_json(result.model_dump_json()) == result


def test_invalid_target_retains_usable_metadata_and_sanitizes_wrong_types():
    result = build(member(status="RELATIVE_VALUE_UNAVAILABLE"), [])
    assert result.target_bond_id == 1 and result.as_of_date == DAY and result.market_source == "moex"
    assert result.target_spread_to_ofz_bps == D("10") and result.cohort_key == key()
    result = build(member(bond_id=True, as_of_date=datetime(2026, 9, 18), market_source=" ",
                          spread_to_ofz_bps=D("NaN"), cohort_key={}), [])
    assert result.target_bond_id is result.as_of_date is result.market_source is None
    assert result.target_spread_to_ofz_bps is result.cohort_key is None


@pytest.mark.parametrize("updates", [u for u in INVALID_MEMBER_UPDATES if "status" not in u])
def test_structurally_invalid_ready_candidate_is_fatal_even_outside_comparison(updates):
    row = member(3, **updates)
    # Ensure an invalid ID does not accidentally exercise target self-exclusion.
    target = member(99)
    result = build(target, [member(2), row])
    assert result.status == "PEER_INPUT_INVALID"
    assert result.exclusions.invalid_candidate_count == 1
    assert result.eligible_peer_count == 1 and result.availability.has_eligible_peers
    assert_null_math(result)
    assert_partition(result)


@pytest.mark.parametrize("field,value", [
    ("rating_value_raw", "AA+"), ("rating_agency", "EXPERT_RA"),
    ("source_provider", "other"), ("rating_scale_raw", "international"),
    ("target_kind", "LEGAL_ISSUER"), ("rating_value_raw", "aa"),
    ("rating_value_raw", " AA "), ("source_provider", "Provider"),
    ("rating_scale_raw", None), ("rating_scale_raw", ""),
])
def test_each_exact_key_field_is_required(field, value):
    result = build(member(), [changed_cohort(member(2), **{field: value})])
    assert result.status == "NO_ELIGIBLE_PEERS"
    assert result.exclusions.excluded_cohort_mismatch_count == 1
    assert_partition(result)


@pytest.mark.parametrize("scale", [None, "", " ", "  National  "])
def test_null_and_blank_scale_exact_strings_preserved(scale):
    target = changed_cohort(member(), rating_scale_raw=scale, source_provider=" Provider ", rating_value_raw=" AA(rU) ")
    peer = changed_cohort(member(2), rating_scale_raw=scale, source_provider=" Provider ", rating_value_raw=" AA(rU) ")
    result = build(target, [peer])
    assert result.status == "READY" and result.cohort_key == target.cohort_key
    assert result.cohort_key.model_dump() == target.cohort_key.model_dump()
    assert result.cohort_key is not target.cohort_key


@pytest.mark.parametrize("updates,count", [
    ({"as_of_date": DAY - timedelta(days=1)}, "excluded_as_of_mismatch_count"),
    ({"market_source": "MOEX"}, "excluded_market_source_mismatch_count"),
    ({"market_source": " moex "}, "excluded_market_source_mismatch_count"),
    ({"status": "RELATIVE_VALUE_UNAVAILABLE", "cohort_key": None}, "excluded_not_ready_count"),
])
def test_date_source_and_non_ready_exclusions(updates, count):
    result = build(member(), [member(2, **updates)])
    assert result.status == "NO_ELIGIBLE_PEERS" and getattr(result.exclusions, count) == 1
    assert_null_math(result)
    assert_partition(result)


def test_primary_reason_precedence_and_every_candidate_accounted_once():
    target = member()
    rows = [
        member(1, status="RELATIVE_VALUE_UNAVAILABLE", cohort_key=None),
        member(1, spread_to_ofz_bps=D("NaN")),
        member(2, status="RELATIVE_VALUE_UNAVAILABLE", pit_ready=True),
        changed_cohort(member(3, spread_to_ofz_bps=D("NaN"), as_of_date=DAY - timedelta(days=1),
                              market_source="other"), rating_value_raw="BBB"),
        changed_cohort(member(4, as_of_date=DAY - timedelta(days=1), market_source="other"), rating_value_raw="BBB"),
        changed_cohort(member(5, market_source="other"), rating_value_raw="BBB"),
        changed_cohort(member(6), rating_value_raw="BBB"), member(7),
    ]
    result = build(target, rows)
    assert result.status == "PEER_INPUT_INVALID"
    assert result.exclusions.model_dump() == dict(
        excluded_target_self_count=2, excluded_not_ready_count=1, invalid_candidate_count=1,
        excluded_as_of_mismatch_count=1, excluded_market_source_mismatch_count=1,
        excluded_cohort_mismatch_count=1, unprocessed_candidate_count=0, duplicate_peer_bond_ids=[],
    )
    assert result.eligible_peer_count == 1
    assert_partition(result)


def test_self_exclusion_precedes_validation_but_bool_id_is_not_self():
    result = build(member(), [member(), member(), member(2, "20")])
    assert result.status == "READY" and result.exclusions.excluded_target_self_count == 2
    assert result.peer_mean_spread_bps == D("20") and result.target_spread_percentile == 0
    result = build(member(), [member(2, bond_id=True)])
    assert result.status == "PEER_INPUT_INVALID" and result.exclusions.excluded_target_self_count == 0


def test_duplicate_eligible_rows_retained_without_statistics_or_row_invalid_count():
    rows = [member(2, "20", market_snapshot_id=999), member(2, "30"), member(3, "40"), member(3, "40")]
    result = build(member(), rows)
    assert result.status == "PEER_INPUT_INVALID" and result.eligible_peer_count == 4
    assert result.exclusions.duplicate_peer_bond_ids == [2, 3]
    assert result.exclusions.invalid_candidate_count == 0
    assert [p.bond_id for p in result.eligible_peers] == [2, 2, 3, 3]
    assert result.provenance.eligible_peer_bond_ids == [2, 2, 3, 3]
    assert result.availability.has_eligible_peers
    assert_null_math(result)
    assert_partition(result)
    assert build(member(), list(reversed(rows))).model_dump_json() == result.model_dump_json()


def test_duplicates_only_count_if_eligible_and_repeated_self_is_not_error():
    rows = [member(), member(), changed_cohort(member(2), rating_value_raw="BBB")]
    rows += [rows[-1], member(2), member(3, status="RELATIVE_VALUE_UNAVAILABLE")]
    rows += [rows[-1], member(4, market_source="other"), member(4, market_source="other")]
    result = build(member(), rows)
    assert result.status == "READY" and result.eligible_peer_count == 1
    assert result.exclusions.duplicate_peer_bond_ids == []
    assert_partition(result)


@pytest.mark.parametrize("values,minimum,median,mean,maximum", [
    (["20"], "20", "20", "20", "20"),
    (["3", "1", "2"], "1", "2", "2", "3"),
    (["1", "2", "3", "8"], "1", "2.5", "3.5", "8"),
    (["-30", "-20", "-10"], "-30", "-20", "-20", "-10"),
    (["0", "0"], "0", "0", "0", "0"),
    (["-10", "0", "20", "30"], "-10", "10", "10", "30"),
    (["0.00000000000000000000000001", "0.00000000000000000000000003"],
     "0.00000000000000000000000001", "0.00000000000000000000000002",
     "0.00000000000000000000000002", "0.00000000000000000000000003"),
])
def test_peer_only_decimal_statistics(values, minimum, median, mean, maximum):
    result = build(member(spread="1000"), [member(i + 2, value) for i, value in enumerate(values)])
    assert result.status == "READY"
    assert (result.peer_min_spread_bps, result.peer_median_spread_bps,
            result.peer_mean_spread_bps, result.peer_max_spread_bps) == tuple(map(D, (minimum, median, mean, maximum)))
    assert result.spread_minus_peer_median_bps == D("1000") - D(median)
    assert all(isinstance(getattr(result, name), D) for name in (
        "peer_min_spread_bps", "peer_median_spread_bps", "peer_mean_spread_bps",
        "peer_max_spread_bps", "spread_minus_peer_median_bps", "target_spread_percentile"))
    assert_partition(result)


@pytest.mark.parametrize("target,values,expected", [
    ("0", ["10", "20"], "0"), ("30", ["10", "20"], "100"),
    ("10", ["10", "10", "10"], "50"),
    ("10", ["0", "10", "10", "20"], "50"),
    ("10", ["0", "20"], "50"), ("10", ["0", "10", "10", "10"], "62.5"),
    ("10", ["0"], "100"), ("10", ["20"], "0"), ("10", ["10"], "50"),
    ("10", ["10", "10", "20", "20"], "25"),
])
def test_target_vs_peers_percentile_midrank(target, values, expected):
    result = build(member(spread=target), [member(i + 2, value) for i, value in enumerate(values)])
    assert result.target_spread_percentile == D(expected)
    assert 0 <= result.target_spread_percentile <= 100


@pytest.mark.parametrize("target,expected", [("0", "-10"), ("10", "0"), ("30", "20")])
def test_signed_descriptive_difference(target, expected):
    assert build(member(spread=target), [member(2)]).spread_minus_peer_median_bps == D(expected)


@pytest.mark.parametrize("rows", [[], [changed_cohort(member(2), rating_value_raw="BBB")]])
def test_no_eligible_peers(rows):
    result = build(member(), rows)
    assert result.status == "NO_ELIGIBLE_PEERS" and result.eligible_peer_count == 0
    assert result.availability.has_valid_target and not result.availability.has_eligible_peers
    assert_null_math(result)
    assert_partition(result)


@pytest.mark.parametrize("minimum,status", [(1, "READY"), (2, "READY"), (3, "INSUFFICIENT_PEERS")])
def test_minimum_boundary_statistics_available_percentile_only_when_ready(minimum, status):
    result = build(member(), [member(2, "20"), member(3, "40")], min_peer_count=minimum)
    assert result.status == status and result.peer_median_spread_bps == 30
    assert result.spread_minus_peer_median_bps == -20
    assert result.availability.has_peer_distribution and result.availability.has_spread_vs_peer_median
    assert result.availability.has_minimum_peer_count == (status == "READY")
    assert result.availability.has_ready_peer_distribution == (status == "READY")
    assert result.target_spread_percentile == (D(0) if status == "READY" else None)


@pytest.mark.parametrize("minimum", [True, False, 0, -1, 1.0, "1", D(1), None])
def test_invalid_minimum_before_any_argument_processing(minimum):
    class Bomb:
        def __iter__(self):
            raise AssertionError("must not be inspected")
    with pytest.raises(ValueError, match="min_peer_count"):
        build(Bomb(), Bomb(), min_peer_count=minimum)


@pytest.mark.parametrize("target,candidates", [
    (None, []), ({}, []), ("target", []), (member(), None), (member(), [None]),
    (member(), [{}]), (member(), "abc"), (member(), [member(2), 1]),
    (member(), ""), (member(), b""), (member(), bytearray()), (member(), {}),
])
def test_wrong_outer_types_raise_value_error(target, candidates):
    with pytest.raises(ValueError):
        build(target, candidates)


def test_candidate_iterable_snapshotted_once():
    class Once:
        def __init__(self):
            self.calls = 0
        def __iter__(self):
            self.calls += 1
            assert self.calls == 1
            yield member(2)
            yield member(3)
    rows = Once()
    assert build(member(), rows).candidate_count == 2 and rows.calls == 1


@pytest.mark.parametrize("mode", ["ready", "fatal", "insufficient", "empty", "invalid_target"])
def test_shuffle_stable_serialization_and_input_immutability(mode):
    target = member(status="RELATIVE_VALUE_UNAVAILABLE") if mode == "invalid_target" else member()
    rows = [member(3, "10000000000000000000000000001"), member(2, "-10000000000000000000000000000"), member(4, "0.1")]
    if mode == "fatal":
        rows.append(member(2, "22"))
    if mode == "empty":
        rows = []
    minimum = 10 if mode == "insufficient" else 1
    original = [row.model_dump_json() for row in [target, *rows]]
    identities = [id(row) for row in rows]
    expected = build(target, rows, min_peer_count=minimum).model_dump_json()
    for permutation in itertools.permutations(rows):
        assert build(target, permutation, min_peer_count=minimum).model_dump_json() == expected
    assert original == [row.model_dump_json() for row in [target, *rows]]
    assert identities == [id(row) for row in rows]
    result = CreditCohortPeerSpreadDistributionView.model_validate_json(expected)
    assert result.model_dump_json() == expected
    assert result.pit_ready is False
    assert_partition(result)


def test_fresh_decimal_context_independent_of_precision_rounding_traps_and_flags():
    target, rows = member(spread="0.12345678901234567890123456789"), [member(2, "1"), member(3, "2"), member(4, "4")]
    reference = build(target, rows).model_dump_json()
    with localcontext(Context(prec=4, rounding=ROUND_DOWN, Emax=5, Emin=-5)) as caller:
        caller.traps[Inexact] = True
        caller.flags[Inexact] = True
        before = caller.copy()
        result = build(target, rows)
        assert result.model_dump_json() == reference
        assert caller.prec == before.prec and caller.rounding == before.rounding
        assert caller.Emax == before.Emax and caller.Emin == before.Emin
        assert caller.traps == before.traps and caller.flags == before.flags
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        assert result.peer_mean_spread_bps == D(7) / D(3)
        assert result.spread_minus_peer_median_bps == target.spread_to_ofz_bps - D(2)


def test_compact_evidence_and_provenance_copy_decimal_and_ids_exactly():
    target = member(rating_event_id=None, market_snapshot_id=None)
    peer = member(2, "-0.0001234500", rating_event_id=None, market_snapshot_id=None)
    result = build(target, [peer])
    assert result.eligible_peers[0].spread_to_ofz_bps.as_tuple() == peer.spread_to_ofz_bps.as_tuple()
    assert result.provenance.model_dump() == dict(
        target_member_contract_version=target.contract_version, target_rating_event_id=None,
        target_market_snapshot_id=None, candidate_count=1, eligible_peer_bond_ids=[2], min_peer_count=1,
        percentile_method="TARGET_VS_PEERS_MIDRANK_V1", median_method="DECIMAL_STANDARD_MEDIAN",
        mean_method="DECIMAL_ARITHMETIC_MEAN",
    )
    assert set(result.eligible_peers[0].model_dump()) == {
        "bond_id", "isin", "secid", "spread_to_ofz_bps", "rating_event_id", "market_snapshot_id"}
    assert result.quality_flags == []
    # Mutating an output list cannot alter any input list.
    result.quality_flags.append("output only")
    assert target.quality_flags == peer.quality_flags == []


def test_optional_malformed_metadata_is_null_without_coercion_or_extra_flags():
    target = member(rating_event_id=True, market_snapshot_id="5", quality_flags=["BANK_FLAG"])
    peer = member(2, isin=3, secid=False, rating_event_id=-1, market_snapshot_id=True,
                  quality_flags=["LIQUIDITY_FLAG"])
    result = build(target, [peer])
    assert result.status == "READY" and result.quality_flags == []
    assert result.provenance.target_rating_event_id is result.provenance.target_market_snapshot_id is None
    evidence = result.eligible_peers[0]
    assert evidence.isin is evidence.secid is evidence.rating_event_id is evidence.market_snapshot_id is None


def test_duplicate_tie_break_uses_all_compact_evidence_without_selection():
    rows = [member(2, isin="Z", secid="A"), member(2, isin="A", secid="Z"),
            member(2, "10.00", rating_event_id=None)]
    result = build(member(), rows)
    assert result.status == "PEER_INPUT_INVALID" and len(result.eligible_peers) == 3
    assert build(member(), rows[::-1]).model_dump_json() == result.model_dump_json()
    assert sorted(p.model_dump_json() for p in result.eligible_peers) == [p.model_dump_json() for p in result.eligible_peers]


def test_candidate_invalid_structure_precedes_mismatched_date_source_and_cohort():
    row = changed_cohort(member(2, contract_version="wrong", as_of_date=DAY - timedelta(days=1),
                                market_source="other"), rating_value_raw="BBB")
    result = build(member(), [row], min_peer_count=100)
    assert result.status == "PEER_INPUT_INVALID" and result.exclusions.invalid_candidate_count == 1
    assert result.exclusions.excluded_as_of_mismatch_count == result.exclusions.excluded_market_source_mismatch_count == 0
    assert result.exclusions.excluded_cohort_mismatch_count == 0
    assert_null_math(result)
    assert_partition(result)


def test_frozen_extra_forbidden_all_output_models_and_capabilities():
    result = build(member(), [member(2)])
    for obj in (result, result.cohort_key, result.eligible_peers[0], result.exclusions,
                result.availability, result.provenance, result.capabilities):
        assert obj.model_config["frozen"] and obj.model_config["extra"] == "forbid"
        with pytest.raises(ValidationError):
            type(obj).model_validate({**obj.model_dump(), "unexpected": 1})
    with pytest.raises(ValidationError):
        result.status = "NO_ELIGIBLE_PEERS"
    capabilities = result.capabilities.model_dump()
    ready = {"credit_cohort_relative_value_member_input_ready", "credit_cohort_peer_distribution_ready",
             "credit_cohort_spread_percentile_ready", "credit_cohort_spread_vs_median_ready"}
    assert {name for name, value in capabilities.items() if value} == ready
    assert result.contract_version == "credit-cohort-peer-spread-distribution-v1"


def test_ast_pure_imports_calls_no_maps_labels_or_evidence_recalculation():
    import app.services.credit_cohort_peer_spread_distribution_service as module
    source = inspect.getsource(module)
    tree = ast.parse(source)
    allowed = {
        "collections", "collections.abc", "datetime", "decimal", "typing", "app.schemas.bond_credit_comparability",
        "app.schemas.bond_credit_features", "app.schemas.bond_credit_cohort_relative_value",
        "app.schemas.credit_cohort_peer_spread_distribution",
    }
    imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert imports <= allowed
    assert not any(isinstance(n, ast.Import) for n in ast.walk(tree))
    forbidden_calls = {"execute", "query", "select", "add", "add_all", "delete", "flush", "commit",
                       "rollback", "get", "post", "urlopen", "build_for_bond", "evaluate_bond", "float"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            if name == "add" and isinstance(node.func, ast.Attribute):
                # Local diagnostic set mutation is distinct from a persistence call.
                assert isinstance(node.func.value, ast.Name) and node.func.value.id == "flags"
                continue
            assert name not in forbidden_calls
        if isinstance(node, ast.Dict):
            assert not node.keys  # No grade, agency, or preference dictionaries.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert node.value.upper() not in {
                "CHEAP", "EXPENSIVE", "ATTRACTIVE", "UNATTRACTIVE", "BUY", "SELL", "OVERVALUED", "UNDERVALUED"}
    assert "Session" not in source and "sqlalchemy" not in source
    assert "app.models" not in source and "app.services" not in source
    assert "yield_to_maturity" not in source and "interpolation" not in source
