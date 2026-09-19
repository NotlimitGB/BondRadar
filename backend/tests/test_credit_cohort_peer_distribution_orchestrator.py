"""Task281 pure Task280-to-Task277 orchestration acceptance."""

import ast
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN, getcontext, setcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.bond_credit_cohort_relative_value import (
    BondCreditCohortRelativeValueMemberView,
)
from app.schemas.credit_cohort_batch_members import CreditCohortBatchMemberView
from app.schemas.credit_cohort_peer_distribution_orchestration import (
    CREDIT_COHORT_PEER_DISTRIBUTION_ORCHESTRATION_CONTRACT_VERSION,
    CreditCohortPeerDistributionOrchestrationView,
)
from app.schemas.credit_cohort_peer_spread_distribution import (
    CreditCohortPeerSpreadDistributionView,
)
from app.services.credit_cohort_peer_distribution_orchestrator import (
    CreditCohortPeerDistributionOrchestrator,
)
from app.services.credit_cohort_peer_spread_distribution_service import (
    CreditCohortPeerSpreadDistributionService,
)


DAY = date(2026, 9, 19)
D = Decimal


def _key(value="AA"):
    return dict(
        target_kind="BOND", rating_agency="ACRA", source_provider="provider",
        rating_scale_raw="national", rating_value_raw=value,
    )


def _member(bond_id=1, spread="10", **updates):
    ready = updates.get("status", "READY") == "READY"
    data = dict(
        bond_id=bond_id, isin=f"RU{bond_id:010d}", secid=f"B{bond_id}",
        as_of_date=DAY, market_source="moex", status="READY",
        requested_target_kind="BOND", requested_rating_agency="ACRA",
        credit_comparability_status="READY", relative_value_status="READY",
        cohort_key=_key(), rating_event_id=100 + bond_id,
        rating_event_date=DAY, rating_source_provider="provider",
        rating_scale_raw="national", rating_value_raw="AA",
        target_yield_to_maturity_pct=D("12"), target_duration_years=D("2"),
        reference_ofz_yield_pct=D("11.9"), spread_to_ofz_pp=D("0.1"),
        spread_to_ofz_bps=D(spread), interpolation_method="EXACT_NODE",
        market_snapshot_id=200 + bond_id, market_trade_date=DAY,
        curve_trade_date=DAY,
        availability=dict(
            has_credit_comparability=True, has_selected_rating_entry=True,
            has_selected_cohort_key=ready, has_relative_value=True,
            has_spread_to_ofz=True,
            has_credit_cohort_relative_value_member=ready,
        ),
        quality_flags=[],
        provenance=dict(
            credit_comparability_contract_version="bond-credit-comparability-v1",
            credit_identity=dict(bond_id=bond_id, as_of_date=DAY),
            relative_value_identity=dict(
                bond_id=bond_id, as_of_date=DAY, market_source="moex"
            ),
            requested_target_kind="BOND", requested_rating_agency="ACRA",
            selected_entry_count=1, selected_entry_status="READY",
            selected_rating_event_id=100 + bond_id,
            selected_rating_event_date=DAY, selected_source_provider="provider",
            selected_rating_scale_raw="national", selected_rating_value_raw="AA",
            bond_legal_issuer_profile_id=None, legal_issuer_id=None,
            relative_value_contract_version="bond-relative-value-v1",
            ofz_curve_contract_version="ofz-reference-curve-v1",
            target_market_snapshot_id=200 + bond_id,
            target_market_trade_date=DAY, curve_trade_date=DAY,
            market_source="moex", as_of_date=DAY,
        ),
    )
    data.update(updates)
    return BondCreditCohortRelativeValueMemberView(**data)


def _curve(**updates):
    data = dict(
        as_of_date=DAY, market_source="moex", status="READY",
        curve_trade_date=DAY, node_count=0, min_duration_years=None,
        max_duration_years=None, nodes=[], diagnostics={},
    )
    data.update(updates)
    from app.schemas.ofz_reference_curve import OfzReferenceCurveView
    return OfzReferenceCurveView(**data)


def _batch(
    *, existing=(1, 2, 3), missing=(), members=None, status=None,
):
    existing = tuple(existing)
    missing = tuple(missing)
    requested = tuple(sorted((*existing, *missing)))
    members = members or {bond_id: _member(bond_id, str(bond_id * 10)) for bond_id in existing}
    items = [
        dict(
            bond_id=bond_id,
            build_status="BUILT" if bond_id in existing else "BOND_NOT_FOUND",
            member=members.get(bond_id),
        )
        for bond_id in requested
    ]
    ready = sum(members[bond_id].status == "READY" for bond_id in existing)
    batch_status = status or (
        "NO_EXISTING_BONDS" if not existing else "PARTIAL" if missing else "COMPLETE"
    )
    curve = _curve() if existing else None
    count = len(existing)
    return CreditCohortBatchMemberView(
        as_of_date=DAY, market_source="moex", requested_target_kind="BOND",
        requested_rating_agency="ACRA", status=batch_status,
        requested_bond_ids=list(requested), existing_bond_ids=list(existing),
        missing_bond_ids=list(missing), requested_bond_count=len(requested),
        existing_bond_count=count, missing_bond_count=len(missing),
        built_member_count=count, ready_member_count=ready,
        unavailable_member_count=count - ready, curve_built=bool(existing),
        items=items, shared_curve=curve,
        provenance=dict(
            as_of_date=DAY, market_source="moex", target_kind="BOND",
            rating_agency="ACRA", max_market_age_days=7,
            max_curve_age_days=7, requested_bond_ids=list(requested),
            existing_bond_ids=list(existing), missing_bond_ids=list(missing),
            shared_curve_contract_version=curve.contract_version if curve else None,
            shared_curve_status=curve.status if curve else None,
            shared_curve_trade_date=curve.curve_trade_date if curve else None,
            shared_curve_node_count=curve.node_count if curve else None,
            curve_build_count=1 if existing else 0,
            market_feature_call_count=count,
            credit_comparability_call_count=count,
            relative_evaluator_call_count=count,
            member_composer_call_count=count,
        ),
    )


def _distribution(status="READY", target_id=1):
    ready = status == "READY"
    return CreditCohortPeerSpreadDistributionView(
        target_bond_id=target_id, as_of_date=DAY, market_source="moex",
        cohort_key=_key(), target_spread_to_ofz_bps=D("10"), status=status,
        candidate_count=3, eligible_peer_count=1 if ready else 0,
        min_peer_count=1, peer_min_spread_bps=D("20") if ready else None,
        peer_median_spread_bps=D("20") if ready else None,
        peer_mean_spread_bps=D("20") if ready else None,
        peer_max_spread_bps=D("20") if ready else None,
        spread_minus_peer_median_bps=D("-10") if ready else None,
        target_spread_percentile=D("0") if ready else None,
        eligible_peers=(
            [dict(bond_id=2, isin="RU0000000002", secid="B2",
                  spread_to_ofz_bps=D("20"), rating_event_id=102,
                  market_snapshot_id=202)] if ready else []
        ),
        exclusions=dict(
            excluded_target_self_count=1, excluded_not_ready_count=0,
            invalid_candidate_count=0, excluded_as_of_mismatch_count=0,
            excluded_market_source_mismatch_count=0,
            excluded_cohort_mismatch_count=0, unprocessed_candidate_count=0,
            duplicate_peer_bond_ids=[],
        ),
        availability=dict(
            has_valid_target=status != "TARGET_MEMBER_INVALID",
            has_eligible_peers=ready, has_minimum_peer_count=ready,
            has_peer_distribution=ready, has_peer_median=ready,
            has_target_spread_percentile=ready,
            has_spread_vs_peer_median=ready,
            has_ready_peer_distribution=ready,
        ),
        quality_flags=[] if ready else [status],
        provenance=dict(
            target_member_contract_version="bond-credit-cohort-relative-value-member-v1",
            target_rating_event_id=101, target_market_snapshot_id=201,
            candidate_count=3, eligible_peer_bond_ids=[2] if ready else [],
            min_peer_count=1,
        ),
    )


def _build(batch, target=1, minimum=1):
    return CreditCohortPeerDistributionOrchestrator.build(
        batch, target, min_peer_count=minimum,
    )


def _spy_reducer(monkeypatch, result=None):
    calls = []
    output = result or _distribution()

    def build(target, candidates, **kwargs):
        calls.append((target, candidates, kwargs))
        return output

    monkeypatch.setattr(CreditCohortPeerSpreadDistributionService, "build", build)
    return output, calls


def test_a_complete_batch_calls_reducer_and_preserves_exact_output(monkeypatch):
    batch = _batch()
    output, calls = _spy_reducer(monkeypatch)
    result = _build(batch, minimum=2)
    assert result.status == "READY" and result.distribution is output
    assert len(calls) == 1 and calls[0][0] is batch.items[0].member
    assert calls[0][1] == tuple(item.member for item in batch.items)
    assert all(
        supplied is item.member for supplied, item in zip(calls[0][1], batch.items)
    )
    assert calls[0][2] == {"min_peer_count": 2}
    assert result.provenance.reducer_call_count == 1
    assert result.provenance.candidate_member_bond_ids == [1, 2, 3]
    assert result.availability.model_dump() == dict(
        has_valid_batch=True, target_requested=True, target_exists=True,
        has_target_member=True, has_candidate_members=True,
        has_distribution_result=True, has_ready_distribution=True,
    )


def test_b_an_partial_batch_existing_target_is_usable_and_missing_excluded(monkeypatch):
    batch = _batch(existing=(1, 2), missing=(99,))
    output, calls = _spy_reducer(monkeypatch)
    result = _build(batch)
    assert result.status == "READY" and result.batch_status == "PARTIAL"
    assert result.distribution is output and len(calls) == 1
    assert [member.bond_id for member in calls[0][1]] == [1, 2]
    assert result.candidate_member_count == 2
    assert result.provenance.missing_bond_ids == [99]


def test_c_x_no_existing_batch_target_missing_without_reducer(monkeypatch):
    batch = _batch(existing=(), missing=(1, 2))
    _, calls = _spy_reducer(monkeypatch)
    result = _build(batch, 1)
    assert result.status == "TARGET_BOND_NOT_FOUND"
    assert result.distribution is None and calls == []
    assert result.candidate_member_count == 0
    assert result.availability.target_requested
    assert not result.availability.target_exists
    assert result.provenance.target_item_build_status == "BOND_NOT_FOUND"


def _corrupt(batch, case):
    if case == "version":
        return batch.model_copy(update={"contract_version": "wrong"})
    if case == "pit":
        return batch.model_copy(update={"pit_ready": True})
    if case == "batch_date":
        return batch.model_copy(update={"as_of_date": datetime(2026, 9, 19)})
    if case == "batch_source":
        return batch.model_copy(update={"market_source": " "})
    if case == "batch_target":
        return batch.model_copy(update={"requested_target_kind": "ISSUER"})
    if case == "batch_agency":
        return batch.model_copy(update={"requested_rating_agency": "OTHER"})
    if case == "requested_unsorted":
        return batch.model_copy(update={"requested_bond_ids": [2, 1, 3]})
    if case == "requested_duplicate":
        return batch.model_copy(update={"requested_bond_ids": [1, 1, 3]})
    if case == "partition_overlap":
        return batch.model_copy(update={"missing_bond_ids": [2]})
    if case == "partition_incomplete":
        return batch.model_copy(update={"existing_bond_ids": [1, 2]})
    if case == "count":
        return batch.model_copy(update={"existing_bond_count": 2})
    if case == "status":
        return batch.model_copy(update={"status": "PARTIAL"})
    if case == "items_order":
        return batch.model_copy(update={"items": list(reversed(batch.items))})
    if case == "built_null":
        items = list(batch.items)
        items[0] = items[0].model_copy(update={"member": None})
        return batch.model_copy(update={"items": items})
    if case == "member_bond":
        items = list(batch.items)
        items[0] = items[0].model_copy(
            update={"member": items[0].member.model_copy(update={"bond_id": 9})}
        )
        return batch.model_copy(update={"items": items})
    if case == "member_date":
        items = list(batch.items)
        items[0] = items[0].model_copy(update={
            "member": items[0].member.model_copy(update={"as_of_date": DAY.replace(day=18)})
        })
        return batch.model_copy(update={"items": items})
    if case == "member_source":
        items = list(batch.items)
        items[0] = items[0].model_copy(update={
            "member": items[0].member.model_copy(update={"market_source": "MOEX"})
        })
        return batch.model_copy(update={"items": items})
    if case == "member_selector":
        items = list(batch.items)
        items[0] = items[0].model_copy(update={
            "member": items[0].member.model_copy(update={"requested_rating_agency": "NRA"})
        })
        return batch.model_copy(update={"items": items})
    if case == "member_version":
        items = list(batch.items)
        items[0] = items[0].model_copy(update={
            "member": items[0].member.model_copy(update={"contract_version": "wrong"})
        })
        return batch.model_copy(update={"items": items})
    if case == "member_pit":
        items = list(batch.items)
        items[0] = items[0].model_copy(update={
            "member": items[0].member.model_copy(update={"pit_ready": True})
        })
        return batch.model_copy(update={"items": items})
    if case == "ready_count":
        return batch.model_copy(update={"ready_member_count": 2, "unavailable_member_count": 1})
    if case == "curve_missing":
        return batch.model_copy(update={"shared_curve": None})
    if case == "curve_date":
        return batch.model_copy(update={
            "shared_curve": batch.shared_curve.model_copy(update={"as_of_date": DAY.replace(day=18)})
        })
    if case == "curve_source":
        return batch.model_copy(update={
            "shared_curve": batch.shared_curve.model_copy(update={"market_source": "other"})
        })
    if case == "curve_version":
        return batch.model_copy(update={
            "shared_curve": batch.shared_curve.model_copy(update={"contract_version": "wrong"})
        })
    if case == "curve_pit":
        return batch.model_copy(update={
            "shared_curve": batch.shared_curve.model_copy(update={"pit_ready": True})
        })
    if case == "provenance_ids":
        return batch.model_copy(update={
            "provenance": batch.provenance.model_copy(update={"existing_bond_ids": [1, 2]})
        })
    if case == "provenance_calls":
        return batch.model_copy(update={
            "provenance": batch.provenance.model_copy(update={"curve_build_count": 2})
        })
    if case == "provenance_bool_calls":
        return batch.model_copy(update={
            "provenance": batch.provenance.model_copy(update={"curve_build_count": True})
        })
    if case == "provenance_curve":
        return batch.model_copy(update={
            "provenance": batch.provenance.model_copy(update={"shared_curve_node_count": 9})
        })
    raise AssertionError(case)


@pytest.mark.parametrize("case", [
    "version", "pit", "batch_date", "batch_source", "batch_target",
    "batch_agency", "requested_unsorted", "requested_duplicate",
    "partition_overlap", "partition_incomplete", "count", "status",
    "items_order", "built_null", "member_bond", "member_date",
    "member_source", "member_selector", "member_version", "member_pit",
    "ready_count", "curve_missing", "curve_date", "curve_source",
    "curve_version", "curve_pit", "provenance_ids", "provenance_calls",
    "provenance_bool_calls", "provenance_curve",
])
def test_d_u_batch_contradictions_fail_closed_without_reducer(monkeypatch, case):
    invalid = _corrupt(_batch(), case)
    _, calls = _spy_reducer(monkeypatch)
    result = _build(invalid)
    assert result.status == "BATCH_EVIDENCE_INVALID"
    assert result.distribution is None and calls == []
    assert result.candidate_member_count == 0
    assert not any(result.availability.model_dump().values())
    assert result.quality_flags == ["BATCH_EVIDENCE_INVALID"]
    assert result.provenance.reducer_call_count == 0
    assert CreditCohortPeerDistributionOrchestrationView.model_validate_json(
        result.model_dump_json()
    ) == result


def test_n_bond_not_found_item_with_member_is_invalid(monkeypatch):
    batch = _batch(existing=(1,), missing=(2,))
    items = list(batch.items)
    items[1] = items[1].model_copy(update={"member": _member(2)})
    invalid = batch.model_copy(update={"items": items})
    _, calls = _spy_reducer(monkeypatch)
    assert _build(invalid).status == "BATCH_EVIDENCE_INVALID"
    assert calls == []


def test_s_no_existing_batch_with_curve_is_invalid(monkeypatch):
    batch = _batch(existing=(), missing=(1,))
    invalid = batch.model_copy(update={"shared_curve": _curve(), "curve_built": True})
    _, calls = _spy_reducer(monkeypatch)
    assert _build(invalid).status == "BATCH_EVIDENCE_INVALID"
    assert calls == []


def test_x_partial_batch_missing_target_preserves_built_candidates(monkeypatch):
    batch = _batch(existing=(1, 2), missing=(99,))
    _, calls = _spy_reducer(monkeypatch)
    result = _build(batch, target=99)
    assert result.status == "TARGET_BOND_NOT_FOUND"
    assert result.distribution is None and calls == []
    assert result.candidate_member_count == 2
    assert result.provenance.candidate_member_bond_ids == [1, 2]
    assert result.provenance.target_item_build_status == "BOND_NOT_FOUND"


def test_w_target_not_requested_without_fallback_or_reducer(monkeypatch):
    batch = _batch()
    _, calls = _spy_reducer(monkeypatch)
    result = _build(batch, target=99)
    assert result.status == "TARGET_NOT_REQUESTED"
    assert result.distribution is None and calls == []
    assert result.candidate_member_count == 3
    assert result.availability.has_valid_batch
    assert not result.availability.target_requested
    assert not result.availability.has_target_member
    assert result.provenance.target_item_build_status is None


@pytest.mark.parametrize("target", [True, 0, -1, 1.0, "1", None])
def test_y_z_invalid_target_argument_before_reducer(monkeypatch, target):
    _, calls = _spy_reducer(monkeypatch)
    with pytest.raises(ValueError):
        _build(_batch(), target=target)
    assert calls == []


@pytest.mark.parametrize("minimum", [True, 0, -1, 1.0, "1", None])
def test_invalid_minimum_argument_before_reducer(monkeypatch, minimum):
    _, calls = _spy_reducer(monkeypatch)
    with pytest.raises(ValueError):
        _build(_batch(), minimum=minimum)
    assert calls == []


@pytest.mark.parametrize("value", [None, {}, [], "batch", object()])
def test_wrong_batch_type_is_value_error(value):
    with pytest.raises(ValueError):
        _build(value)


@pytest.mark.parametrize("status", [
    "READY", "TARGET_MEMBER_INVALID", "PEER_INPUT_INVALID",
    "NO_ELIGIBLE_PEERS", "INSUFFICIENT_PEERS",
])
def test_ai_am_reducer_status_and_exact_result_are_propagated(monkeypatch, status):
    output = _distribution(status)
    expected, calls = _spy_reducer(monkeypatch, output)
    result = _build(_batch())
    assert len(calls) == 1
    assert result.status == status and result.distribution is expected
    assert result.quality_flags == ([] if status == "READY" else [status])
    assert result.availability.has_distribution_result
    assert result.availability.has_ready_distribution is (status == "READY")
    assert result.provenance.reducer_contract_version == output.contract_version
    assert result.provenance.reducer_status == status


def test_aj_nonready_target_is_passed_to_real_reducer():
    unavailable = _member(
        1, status="SELECTED_COHORT_MISSING", cohort_key=None,
        spread_to_ofz_bps=None,
        availability=dict(
            has_credit_comparability=True, has_selected_rating_entry=False,
            has_selected_cohort_key=False, has_relative_value=True,
            has_spread_to_ofz=True,
            has_credit_cohort_relative_value_member=False,
        ),
    )
    batch = _batch(members={1: unavailable, 2: _member(2), 3: _member(3)})
    result = _build(batch)
    assert result.status == "TARGET_MEMBER_INVALID"
    assert result.distribution.status == "TARGET_MEMBER_INVALID"
    assert result.provenance.reducer_call_count == 1


def test_ap_aq_all_built_members_passed_without_cohort_or_readiness_filter(monkeypatch):
    other = _member(2).model_copy(update={"cohort_key": _key("BBB")})
    unavailable = _member(3, status="RELATIVE_VALUE_UNAVAILABLE")
    batch = _batch(members={1: _member(1), 2: other, 3: unavailable})
    _, calls = _spy_reducer(monkeypatch)
    _build(batch)
    assert len(calls) == 1
    assert calls[0][1] == (
        batch.items[0].member, batch.items[1].member, batch.items[2].member,
    )


def test_ay_to_bd_immutability_serialization_schema_and_decimal_context(monkeypatch):
    batch = _batch()
    output = _distribution()
    _spy_reducer(monkeypatch, output)
    before_batch = deepcopy(batch.model_dump())
    before_output = deepcopy(output.model_dump())
    original_context = getcontext().copy()
    getcontext().prec = 9
    getcontext().rounding = ROUND_DOWN
    try:
        result = _build(batch)
        assert getcontext().prec == 9 and getcontext().rounding == ROUND_DOWN
    finally:
        setcontext(original_context)
    assert batch.model_dump() == before_batch
    assert output.model_dump() == before_output
    assert result.distribution is output
    assert result.contract_version == (
        CREDIT_COHORT_PEER_DISTRIBUTION_ORCHESTRATION_CONTRACT_VERSION
    )
    assert result.pit_ready is False and result.capabilities.pit_ready is False
    assert result.capabilities.peer_distribution_orchestration_ready is True
    assert result.capabilities.batch_member_build_ready is False
    assert CreditCohortPeerDistributionOrchestrationView.model_validate_json(
        result.model_dump_json()
    ) == result
    with pytest.raises(ValidationError):
        CreditCohortPeerDistributionOrchestrationView.model_validate(
            {**result.model_dump(), "extra": True}
        )
    with pytest.raises(ValidationError):
        result.status = "NO_ELIGIBLE_PEERS"


def test_static_pure_safety_and_single_reducer_call_site():
    import app.services.credit_cohort_peer_distribution_orchestrator as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    forbidden_import_parts = (
        "sqlalchemy", "app.models", "credit_cohort_batch_member_service",
        "bond_credit_cohort_relative_value_service", "bond_market_feature_service",
        "bond_credit_comparability_service", "ofz_reference_curve_service",
    )
    assert not any(
        any(part in imported for part in forbidden_import_parts)
        for imported in imports
    )
    assert "Session" not in source and "Decimal" not in source
    assert "spread_to_ofz" not in source and "cohort_key" not in source
    assert "percentile" not in source.lower() and "median" not in source.lower()
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert sum(
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "CreditCohortPeerSpreadDistributionService"
        and node.func.attr == "build"
        for node in calls
    ) == 1
    forbidden_calls = {
        "add", "add_all", "flush", "commit", "delete", "execute",
        "get", "post", "put", "patch",
    }
    assert not any(
        isinstance(node.func, ast.Attribute) and node.func.attr in forbidden_calls
        for node in calls
    )
    assert not any(
        isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub)
        for node in ast.walk(tree)
    )
