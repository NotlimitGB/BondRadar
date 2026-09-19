"""Task286 efficient M3 audit snapshot orchestration acceptance."""

import ast
import importlib.util
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_DOWN, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.models.bond import Bond
from app.models.company import Company
from app.schemas.bond_liquidity_batch_features import (
    BondLiquidityBatchDiagnostics,
    BondLiquidityBatchFeatureItem,
    BondLiquidityBatchFeatureView,
)
from app.schemas.m3_audit_snapshot import M3AuditSnapshotView
from app.services.m3_audit_snapshot_service import M3AuditSnapshotService
import app.services.m3_audit_snapshot_service as runner_module
from app.services.bond_liquidity_relative_value_composer import (
    compose_bond_liquidity_aware_relative_value,
)
from app.services.bond_m3_feature_composer import compose_bond_m3_feature_view


DAY = date(2026, 9, 19)


def _task282_helpers():
    path = Path(__file__).with_name("test_bond_m3_feature_composer.py")
    spec = importlib.util.spec_from_file_location("task286_task282_helpers", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


H = _task282_helpers()


def _views(bond_id: int = 1):
    views = H._views()
    if bond_id != 1:
        for name in (
            "market",
            "relative_value",
            "credit",
            "credit_comparability",
            "liquidity",
            "modified_duration",
            "dv01",
        ):
            views[name] = views[name].model_copy(update={"bond_id": bond_id})
    return views


def _batch(
    existing_ids=(),
    *,
    requested_ids=None,
    liquidity_by_id=None,
):
    existing_ids = tuple(sorted(existing_ids))
    requested_ids = tuple(
        sorted(requested_ids if requested_ids is not None else existing_ids)
    )
    existing_set = set(existing_ids)
    missing_ids = tuple(
        bond_id for bond_id in requested_ids if bond_id not in existing_set
    )
    liquidity_by_id = liquidity_by_id or {
        bond_id: _views(bond_id)["liquidity"] for bond_id in existing_ids
    }
    items = [
        BondLiquidityBatchFeatureItem(
            bond_id=bond_id,
            build_status="BUILT" if bond_id in existing_set else "BOND_NOT_FOUND",
            feature=liquidity_by_id.get(bond_id),
        )
        for bond_id in requested_ids
    ]
    ready = sum(
        feature.score_status == "READY" for feature in liquidity_by_id.values()
    )
    status = (
        "NO_EXISTING_BONDS"
        if not existing_ids
        else "PARTIAL"
        if missing_ids
        else "COMPLETE"
    )
    shared = 1 if existing_ids else 0
    return BondLiquidityBatchFeatureView(
        as_of_date=DAY,
        market_source="moex",
        lookback_calendar_days=30,
        min_observation_days=5,
        status=status,
        requested_bond_ids=list(requested_ids),
        existing_bond_ids=list(existing_ids),
        missing_bond_ids=list(missing_ids),
        requested_bond_count=len(requested_ids),
        existing_bond_count=len(existing_ids),
        missing_bond_count=len(missing_ids),
        built_feature_count=len(existing_ids),
        ready_feature_count=ready,
        unavailable_feature_count=len(existing_ids) - ready,
        items=items,
        diagnostics=BondLiquidityBatchDiagnostics(
            requested_identity_query_count=1,
            market_window_query_count=shared,
            universe_evaluation_count=shared,
            universe_candidate_count=10 if existing_ids else 0,
            score_eligible_universe_count=10 if existing_ids else 0,
            feature_projection_count=len(existing_ids),
        ),
    )


def _install(monkeypatch, batch, *, views_by_id=None, fail_market=False):
    calls = Counter()
    views_by_id = views_by_id or {
        bond_id: _views(bond_id) for bond_id in batch.existing_bond_ids
    }
    curve = H._relative().curve
    original_liquidity_composer = (
        runner_module.compose_bond_liquidity_aware_relative_value
    )
    original_m3_composer = runner_module.compose_bond_m3_feature_view
    original_reducer = runner_module.M3CoverageAuditReducer.build

    def liquidity_batch(self, bond_ids, as_of_date, **kwargs):
        calls["liquidity_batch"] += 1
        assert tuple(bond_ids) == tuple(batch.requested_bond_ids)
        assert as_of_date == DAY
        assert kwargs == {
            "market_source": "moex",
            "lookback_calendar_days": 30,
            "min_observation_days": 5,
        }
        return batch

    def curve_build(self, as_of_date, **kwargs):
        calls["curve"] += 1
        assert as_of_date == DAY
        assert kwargs == {"market_source": "moex", "max_curve_age_days": 7}
        return curve

    def market(self, bond_id, as_of_date, **kwargs):
        calls["market"] += 1
        if fail_market:
            raise RuntimeError("dependency failed")
        assert kwargs == {"market_source": "moex", "max_market_age_days": 7}
        return views_by_id[bond_id]["market"]

    def relative(target, shared_curve, **kwargs):
        calls["relative"] += 1
        assert shared_curve is curve
        assert kwargs == {"as_of_date": DAY, "market_source": "moex"}
        return views_by_id[target.bond_id]["relative_value"]

    def credit(self, bond_id, as_of_date):
        calls["credit"] += 1
        return views_by_id[bond_id]["credit"]

    def comparison(self, bond_id, as_of_date):
        calls["comparison"] += 1
        return views_by_id[bond_id]["credit_comparability"]

    def duration(self, bond_id, as_of_date, **kwargs):
        calls["duration"] += 1
        assert kwargs == {"market_source": "moex", "max_market_age_days": 7}
        return views_by_id[bond_id]["modified_duration"]

    def dv01(self, bond_id, as_of_date, **kwargs):
        calls["dv01"] += 1
        assert kwargs == {"market_source": "moex", "max_market_age_days": 7}
        return views_by_id[bond_id]["dv01"]

    def liquidity_composer(relative_value, liquidity, **kwargs):
        calls["liquidity_composer"] += 1
        return original_liquidity_composer(relative_value, liquidity, **kwargs)

    def m3_composer(**kwargs):
        calls["m3_composer"] += 1
        assert kwargs["peer_distribution"] is None
        return original_m3_composer(**kwargs)

    def reducer(composites):
        calls["reducer"] += 1
        return original_reducer(composites)

    monkeypatch.setattr(
        runner_module.BondLiquidityBatchFeatureService,
        "build_for_bonds",
        liquidity_batch,
    )
    monkeypatch.setattr(
        runner_module.OfzReferenceCurveService, "build_curve", curve_build
    )
    monkeypatch.setattr(
        runner_module.BondMarketFeatureService, "build_for_bond", market
    )
    monkeypatch.setattr(
        runner_module, "evaluate_market_against_ofz_curve", relative
    )
    monkeypatch.setattr(
        runner_module.BondCreditFeatureService, "build_for_bond", credit
    )
    monkeypatch.setattr(
        runner_module.BondCreditComparabilityService,
        "build_for_bond",
        comparison,
    )
    monkeypatch.setattr(
        runner_module.BondModifiedDurationService, "build_for_bond", duration
    )
    monkeypatch.setattr(runner_module.BondDv01Service, "build_for_bond", dv01)
    monkeypatch.setattr(
        runner_module,
        "compose_bond_liquidity_aware_relative_value",
        liquidity_composer,
    )
    monkeypatch.setattr(runner_module, "compose_bond_m3_feature_view", m3_composer)
    monkeypatch.setattr(runner_module.M3CoverageAuditReducer, "build", reducer)
    return calls, views_by_id, curve


def _build(db_session, ids):
    return M3AuditSnapshotService(db_session).build(ids, DAY)


@pytest.mark.parametrize("count", [1, 2, 10])
def test_shared_work_and_direct_call_counts(db_session, monkeypatch, count):
    ids = tuple(range(1, count + 1))
    batch = _batch(ids)
    calls, _, _ = _install(monkeypatch, batch)
    result = _build(db_session, list(reversed(ids)))
    assert result.status == "COMPLETE"
    assert result.existing_bond_ids == list(ids)
    assert result.composite_count == count
    assert result.coverage_audit.universe_size == count
    assert result.coverage_audit.bond_ids == list(ids)
    assert calls == Counter(
        liquidity_batch=1,
        curve=1,
        market=count,
        relative=count,
        credit=count,
        comparison=count,
        duration=count,
        dv01=count,
        liquidity_composer=count,
        m3_composer=count,
        reducer=1,
    )
    diagnostics = result.diagnostics
    assert diagnostics.liquidity_batch_call_count == 1
    assert diagnostics.liquidity_market_window_query_count == 1
    assert diagnostics.liquidity_universe_evaluation_count == 1
    assert diagnostics.ofz_curve_build_count == 1
    assert diagnostics.market_feature_call_count == count
    assert diagnostics.relative_evaluator_call_count == count
    assert diagnostics.credit_feature_call_count == count
    assert diagnostics.credit_comparability_call_count == count
    assert diagnostics.modified_duration_call_count == count
    assert diagnostics.dv01_call_count == count
    assert diagnostics.liquidity_relative_value_composer_call_count == count
    assert diagnostics.m3_composer_call_count == count
    assert diagnostics.coverage_reducer_call_count == 1


def test_manual_composite_and_audit_full_equivalence(db_session, monkeypatch):
    batch = _batch((1,))
    _, views_by_id, _ = _install(monkeypatch, batch)
    result = _build(db_session, [1])
    views = views_by_id[1]
    liquidity_relative = compose_bond_liquidity_aware_relative_value(
        views["relative_value"],
        views["liquidity"],
        bond_id=1,
        as_of_date=DAY,
        market_source="moex",
    )
    expected = compose_bond_m3_feature_view(
        bond_id=1,
        as_of_date=DAY,
        market_source="moex",
        market=views["market"],
        relative_value=views["relative_value"],
        credit=views["credit"],
        credit_comparability=views["credit_comparability"],
        liquidity=views["liquidity"],
        modified_duration=views["modified_duration"],
        dv01=views["dv01"],
        liquidity_relative_value=liquidity_relative,
        peer_distribution=None,
    )
    expected_audit = runner_module.M3CoverageAuditReducer.build([expected])
    assert result.items[0].composite.model_dump() == expected.model_dump()
    assert result.coverage_audit.model_dump() == expected_audit.model_dump()
    assert result.consistent_composite_count == 1
    assert result.invalid_composite_count == 0


def test_partial_and_all_missing_fast_path(db_session, monkeypatch):
    partial_batch = _batch((1,), requested_ids=(1, 999))
    _install(monkeypatch, partial_batch)
    partial = _build(db_session, [999, 1])
    assert partial.status == "PARTIAL"
    assert partial.requested_bond_ids == [1, 999]
    assert partial.existing_bond_ids == [1]
    assert partial.missing_bond_ids == [999]
    assert partial.coverage_audit.universe_size == 1
    assert [item.build_status for item in partial.items] == ["BUILT", "BOND_NOT_FOUND"]

    all_missing_batch = _batch((), requested_ids=(998, 999))
    calls, _, _ = _install(monkeypatch, all_missing_batch)
    missing = _build(db_session, [999, 998])
    assert missing.status == "NO_EXISTING_BONDS"
    assert missing.composite_count == 0
    assert missing.coverage_audit is None
    assert all(item.composite is None for item in missing.items)
    assert calls == Counter(liquidity_batch=1)
    assert missing.diagnostics.model_dump() == {
        name: (1 if name == "liquidity_batch_call_count" else 0)
        for name in type(missing.diagnostics).model_fields
    }
    assert missing.provenance.shared_ofz_curve_contract_version is None


def test_actual_task285_all_missing_path_avoids_downstream_work(
    db_session, monkeypatch
):
    def forbidden(*args, **kwargs):
        pytest.fail("all-missing Task285 result reached downstream work")

    monkeypatch.setattr(
        runner_module.OfzReferenceCurveService, "build_curve", forbidden
    )
    monkeypatch.setattr(
        runner_module.BondMarketFeatureService, "build_for_bond", forbidden
    )
    monkeypatch.setattr(runner_module.M3CoverageAuditReducer, "build", forbidden)
    result = _build(db_session, [999998, 999999])
    assert result.status == "NO_EXISTING_BONDS"
    assert result.coverage_audit is None
    assert result.diagnostics.liquidity_batch_call_count == 1
    assert result.diagnostics.liquidity_market_window_query_count == 0


def test_input_order_does_not_change_serialized_output(db_session, monkeypatch):
    batch = _batch((1, 2))
    _install(monkeypatch, batch)
    forward = _build(db_session, [1, 2])
    reverse = _build(db_session, [2, 1])
    assert forward.model_dump(mode="json") == reverse.model_dump(mode="json")


def test_evidence_invalid_is_retained_and_peer_context_is_not_built(
    db_session, monkeypatch
):
    batch = _batch((1,))
    views = _views(1)
    views["relative_value"] = views["relative_value"].model_copy(
        update={"bond_id": 999}
    )
    _install(monkeypatch, batch, views_by_id={1: views})
    result = _build(db_session, [1])
    composite = result.items[0].composite
    assert composite.status == "EVIDENCE_INVALID"
    assert result.invalid_composite_count == 1
    assert result.coverage_audit.invalid_composite_count == 1
    assert result.coverage_audit.peer_context_count == 0
    assert result.coverage_audit.extended_m3_complete_count == 0
    assert result.capabilities.peer_context_built is False
    assert result.provenance.peer_context_mode == "NOT_BUILT"


def test_coherent_unavailable_evidence_remains_consistent(db_session, monkeypatch):
    views = _views(1)
    views["liquidity"] = H._liquidity(
        score_status="INSUFFICIENT_UNIVERSE",
        liquidity_score_v1=None,
        score_components={
            "turnover_percentile": None,
            "trade_count_percentile": None,
            "recency_percentile": None,
        },
    )
    batch = _batch((1,), liquidity_by_id={1: views["liquidity"]})
    _install(monkeypatch, batch, views_by_id={1: views})
    result = _build(db_session, [1])
    composite = result.items[0].composite
    assert composite.status == "CONSISTENT"
    assert composite.availability.liquidity_score_ready is False
    assert result.coverage_audit.feature_coverage.liquidity_score_ready.available_count == 0
    assert result.status == "COMPLETE"


def test_unexpected_dependency_failure_propagates(db_session, monkeypatch):
    batch = _batch((1,))
    calls, _, _ = _install(monkeypatch, batch, fail_market=True)
    with pytest.raises(RuntimeError, match="dependency failed"):
        _build(db_session, [1])
    assert calls["liquidity_batch"] == 1
    assert calls["curve"] == 1
    assert calls["market"] == 1
    assert calls["reducer"] == 0


@pytest.mark.parametrize(
    "ids,kwargs",
    [
        ([], {}),
        ("1", {}),
        (b"1", {}),
        (bytearray(b"1"), {}),
        (memoryview(b"1"), {}),
        ({1: 2}, {}),
        ({1, 2}, {}),
        ((item for item in [1]), {}),
        ([True], {}),
        ([0], {}),
        (["1"], {}),
        ([1, 1], {}),
        ([1], {"as_of_date": datetime(2026, 9, 19)}),
        ([1], {"market_source": "manual"}),
        ([1], {"market_source": "MOEX"}),
        ([1], {"max_market_age_days": True}),
        ([1], {"max_market_age_days": -1}),
        ([1], {"max_curve_age_days": -1}),
        ([1], {"liquidity_lookback_calendar_days": 0}),
        ([1], {"liquidity_min_observation_days": 0}),
        ([1], {"liquidity_min_observation_days": 31}),
        ([1], {"as_of_date": date.min, "liquidity_lookback_calendar_days": 2}),
    ],
)
def test_validation_precedes_orchestration(db_session, monkeypatch, ids, kwargs):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid request reached Task285")

    monkeypatch.setattr(
        runner_module.BondLiquidityBatchFeatureService,
        "build_for_bonds",
        forbidden,
    )
    arguments = dict(
        bond_ids=ids,
        as_of_date=DAY,
        market_source="moex",
        max_market_age_days=7,
        max_curve_age_days=7,
        liquidity_lookback_calendar_days=30,
        liquidity_min_observation_days=5,
    )
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        M3AuditSnapshotService(db_session).build(**arguments)


def test_caller_state_decimal_context_and_dependency_objects_unchanged(
    db_session, monkeypatch
):
    company = Company(name="Task286 caller", ticker="TASK286")
    db_session.add(company)
    db_session.flush()
    bond = Bond(company_id=company.id, name="Task286 bond")
    doomed = Bond(company_id=company.id, name="Task286 doomed")
    db_session.add_all([bond, doomed])
    db_session.commit()
    bond_id = bond.id
    db_session.add(Company(name="Task286 pending", ticker="PENDING286"))
    bond.name = "caller dirty"
    db_session.delete(doomed)
    state = tuple(set(values) for values in (db_session.new, db_session.dirty, db_session.deleted))
    batch = _batch((1,))
    calls, views_by_id, _ = _install(monkeypatch, batch)
    before_batch = batch.model_dump_json()
    before_views = {
        name: value.model_dump_json()
        for name, value in views_by_id[1].items()
        if value is not None
    }

    def forbidden(*args, **kwargs):
        pytest.fail("runner attempted a session mutation")

    db_session.autoflush = True
    with monkeypatch.context() as patch:
        for name in ("add", "add_all", "delete", "flush", "commit", "merge"):
            patch.setattr(db_session, name, forbidden)
        with localcontext(Context(prec=4, rounding=ROUND_DOWN)) as context:
            result = _build(db_session, [1])
            assert context.prec == 4 and context.rounding == ROUND_DOWN
    assert result.composite_count == 1 and calls["reducer"] == 1
    assert batch.model_dump_json() == before_batch
    assert {
        name: value.model_dump_json()
        for name, value in views_by_id[1].items()
        if value is not None
    } == before_views
    assert tuple(set(values) for values in (db_session.new, db_session.dirty, db_session.deleted)) == state
    assert bond_id > 0


def test_schema_frozen_extra_forbidden_and_stable_empty_serialization(
    db_session, monkeypatch
):
    batch = _batch((), requested_ids=(998, 999))
    _install(monkeypatch, batch)
    first = _build(db_session, [999, 998])
    second = _build(db_session, [998, 999])
    assert first.model_dump_json() == second.model_dump_json()
    assert first.contract_version == "m3-audit-snapshot-v1"
    assert first.pit_ready is False
    for model in (
        first,
        first.items[0],
        first.diagnostics,
        first.provenance,
        first.capabilities,
    ):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unknown": 1})
    with pytest.raises(ValidationError):
        M3AuditSnapshotView.model_validate({**first.model_dump(), "pit_ready": True})


def test_static_safety_and_forbidden_orchestration_calls():
    path = Path(__file__).parents[1] / "app/services/m3_audit_snapshot_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_attributes = {
        "add",
        "add_all",
        "delete",
        "flush",
        "commit",
        "merge",
        "evaluate_bond",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in forbidden_attributes
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [name.name for name in node.names]
            )
            assert not any(
                token in module.lower()
                for module in modules
                for token in (
                    "broker",
                    "portfolio",
                    "strategy",
                    "risk_engine",
                    "router",
                    "requests",
                    "httpx",
                    "pathlib",
                )
            )
    source = ast.unparse(tree)
    assert "BondLiquidityFeatureService" not in source
    assert "BondLiquidityAwareRelativeValueService" not in source
    assert "CreditCohortBatchMemberService" not in source
    assert "CreditCohortPeerDistributionOrchestrator" not in source
