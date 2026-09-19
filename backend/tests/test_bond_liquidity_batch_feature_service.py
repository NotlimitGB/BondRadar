"""Task285 acceptance for the efficient Task270 batch builder."""

import ast
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_DOWN, localcontext
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.schemas.bond_liquidity_batch_features import (
    BondLiquidityBatchFeatureView,
)
from app.services.bond_liquidity_batch_feature_service import (
    BondLiquidityBatchFeatureService,
)
import app.services.bond_liquidity_batch_feature_service as batch_service_module
from app.services.bond_liquidity_feature_service import BondLiquidityFeatureService


DAY = date(2026, 9, 19)
D = Decimal


@pytest.fixture
def seed(db_session):
    company = Company(name="Task285 issuer", ticker="TASK285")
    db_session.add(company)
    db_session.flush()

    def bond(index: int):
        row = Bond(
            company_id=company.id,
            name=f"Liquidity batch bond {index}",
            isin=f"RU000A285{index:03d}",
            secid=f"T285{index:03d}",
        )
        db_session.add(row)
        db_session.flush()
        return row

    def days(row, *, count=5, value=100, trades=10, volume=5, start_age=0):
        observations = []
        for offset in range(count):
            observation = BondMarketSnapshot(
                bond_id=row.id,
                trade_date=DAY - timedelta(days=start_age + offset),
                source="moex",
                raw_payload={
                    "moex": {
                        "VALUE": str(value),
                        "NUMTRADES": trades,
                        "VOLUME": str(volume),
                    }
                },
            )
            db_session.add(observation)
            observations.append(observation)
        db_session.flush()
        return observations

    def universe(count=10, *, tied=False):
        bonds = [bond(index) for index in range(count)]
        for index, row in enumerate(bonds):
            days(
                row,
                value=100 if tied else (index + 1) * 100,
                trades=10 if tied else index + 1,
                start_age=0 if tied else index,
            )
        db_session.commit()
        return bonds

    db_session.commit()
    return {"bond": bond, "days": days, "universe": universe}


def build(db_session, ids, **kwargs):
    return BondLiquidityBatchFeatureService(db_session).build_for_bonds(
        ids, DAY, **kwargs
    )


@pytest.mark.parametrize("count", [2, 10])
def test_complete_batch_is_sorted_and_exactly_matches_task270(
    db_session, seed, count
):
    bonds = seed["universe"](10)
    requested = [row.id for row in reversed(bonds[:count])]
    result = build(db_session, requested)
    assert result.status == "COMPLETE"
    assert result.requested_bond_ids == sorted(requested)
    assert result.existing_bond_ids == sorted(requested)
    assert result.missing_bond_ids == []
    assert [item.bond_id for item in result.items] == sorted(requested)
    assert all(item.build_status == "BUILT" for item in result.items)
    assert result.built_feature_count == result.ready_feature_count == count
    assert result.unavailable_feature_count == 0
    assert result.diagnostics.model_dump() == {
        "requested_identity_query_count": 1,
        "market_window_query_count": 1,
        "universe_evaluation_count": 1,
        "universe_candidate_count": 10,
        "score_eligible_universe_count": 10,
        "feature_projection_count": count,
    }
    direct = BondLiquidityFeatureService(db_session)
    for item in result.items:
        expected = direct.build_for_bond(item.bond_id, DAY)
        assert item.feature.model_dump() == expected.model_dump()


def test_unrequested_bonds_remain_in_global_benchmark_universe(db_session, seed):
    bonds = seed["universe"](10)
    result = build(db_session, [bonds[0].id])
    feature = result.items[0].feature
    assert feature.score_status == "READY"
    assert feature.provenance.universe_candidate_count == 10
    assert feature.provenance.score_eligible_universe_count == 10
    assert result.diagnostics.feature_projection_count == 1


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("NO_MARKET_DATA", "NO_MARKET_DATA"),
        ("INSUFFICIENT_TARGET_EVIDENCE", "INSUFFICIENT_TARGET_EVIDENCE"),
        ("MISSING_SCORE_COMPONENT", "MISSING_SCORE_COMPONENT"),
        ("INSUFFICIENT_UNIVERSE", "INSUFFICIENT_UNIVERSE"),
    ],
)
def test_unavailable_feature_full_task270_equivalence(db_session, seed, kind, expected):
    target = seed["bond"](0)
    if kind == "INSUFFICIENT_TARGET_EVIDENCE":
        seed["days"](target, count=4)
    elif kind == "MISSING_SCORE_COMPONENT":
        for offset in range(5):
            db_session.add(
                BondMarketSnapshot(
                    bond_id=target.id,
                    trade_date=DAY - timedelta(days=offset),
                    source="moex",
                    raw_payload={"moex": {"NUMTRADES": 2}},
                )
            )
    elif kind == "INSUFFICIENT_UNIVERSE":
        seed["days"](target)
    db_session.commit()

    batch_feature = build(db_session, [target.id]).items[0].feature
    direct = BondLiquidityFeatureService(db_session).build_for_bond(target.id, DAY)
    assert batch_feature.score_status == expected
    assert batch_feature.model_dump() == direct.model_dump()


def test_partial_and_all_missing_paths(db_session, seed):
    existing = seed["bond"](0)
    db_session.commit()
    missing = existing.id + 100000
    partial = build(db_session, [missing, existing.id])
    assert partial.status == "PARTIAL"
    assert partial.existing_bond_ids == [existing.id]
    assert partial.missing_bond_ids == [missing]
    assert [item.build_status for item in partial.items] == ["BUILT", "BOND_NOT_FOUND"]
    assert partial.items[1].feature is None
    assert partial.diagnostics.market_window_query_count == 1
    assert partial.diagnostics.universe_evaluation_count == 1

    all_missing = build(db_session, [missing, missing + 1])
    assert all_missing.status == "NO_EXISTING_BONDS"
    assert all_missing.existing_bond_ids == []
    assert all(item.feature is None for item in all_missing.items)
    assert all_missing.diagnostics.model_dump() == {
        "requested_identity_query_count": 1,
        "market_window_query_count": 0,
        "universe_evaluation_count": 0,
        "universe_candidate_count": 0,
        "score_eligible_universe_count": 0,
        "feature_projection_count": 0,
    }


@pytest.mark.parametrize("count", [1, 2, 10])
def test_exactly_two_selects_for_existing_output_universe(db_session, seed, count):
    bonds = seed["universe"](10, tied=True)
    requested_ids = [row.id for row in bonds[:count]]
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        result = build(db_session, requested_ids)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert result.built_feature_count == count
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert "bonds.id IN" in statements[0]
    assert "raw_payload" in statements[1]
    assert "bond_market_snapshots.bond_id IN" not in statements[1]
    assert "liquidity_score" not in statements[1]


def test_all_missing_uses_only_identity_query(db_session, seed):
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        result = build(db_session, [999991, 999992])
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert result.status == "NO_EXISTING_BONDS"
    assert len(statements) == 1
    assert statements[0].lstrip().upper().startswith("SELECT")


def test_evaluator_called_once_and_not_called_for_all_missing(
    db_session, seed, monkeypatch
):
    bonds = seed["universe"](10, tied=True)
    requested = [bonds[0].id, bonds[1].id]
    original = batch_service_module.evaluate_liquidity_features
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(batch_service_module, "evaluate_liquidity_features", capture)
    result = build(db_session, requested)
    assert result.diagnostics.universe_evaluation_count == 1
    assert len(calls) == 1
    assert len(calls[0][0][0]) == 2

    calls.clear()
    missing = build(db_session, [999991, 999992])
    assert missing.status == "NO_EXISTING_BONDS"
    assert calls == []


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
        ([-1], {}),
        (["1"], {}),
        ([1, 1], {}),
        ([1], {"as_of_date": datetime(2026, 9, 19)}),
        ([1], {"market_source": ""}),
        ([1], {"market_source": 1}),
        ([1], {"lookback_calendar_days": True}),
        ([1], {"lookback_calendar_days": 0}),
        ([1], {"min_observation_days": 0}),
        ([1], {"min_observation_days": 31}),
        ([1], {"as_of_date": date.min, "lookback_calendar_days": 2}),
    ],
)
def test_argument_validation_precedes_sql(db_session, ids, kwargs, monkeypatch):
    args = {
        "bond_ids": ids,
        "as_of_date": DAY,
        "market_source": "moex",
        "lookback_calendar_days": 30,
        "min_observation_days": 5,
        **kwargs,
    }

    def forbidden(*args, **kwargs):
        pytest.fail("invalid request reached SQL")

    monkeypatch.setattr(db_session, "execute", forbidden)
    with pytest.raises(ValueError):
        BondLiquidityBatchFeatureService(db_session).build_for_bonds(**args)


def test_select_only_preserves_pending_state_and_decimal_context(
    db_session, seed, monkeypatch
):
    bonds = seed["universe"](10, tied=True)
    doomed = seed["bond"](99)
    db_session.commit()
    target_id = bonds[0].id
    db_session.add(Company(name="Caller pending 285", ticker="PENDING285"))
    bonds[0].name = "Caller dirty"
    db_session.delete(doomed)
    state = tuple(set(items) for items in (db_session.new, db_session.dirty, db_session.deleted))

    def forbidden(*args, **kwargs):
        pytest.fail("batch attempted a session mutation")

    db_session.autoflush = True
    with monkeypatch.context() as patch:
        for name in ("commit", "flush", "add", "add_all", "delete"):
            patch.setattr(db_session, name, forbidden)
        with localcontext(Context(prec=4, rounding=ROUND_DOWN)) as context:
            result = build(db_session, [target_id])
            assert context.prec == 4 and context.rounding == ROUND_DOWN
    assert result.items[0].feature.liquidity_score_v1 == D(50)
    assert tuple(set(items) for items in (db_session.new, db_session.dirty, db_session.deleted)) == state


def test_contract_is_frozen_extra_forbidden_and_deterministic(db_session, seed):
    bonds = seed["universe"](10, tied=True)
    first = build(db_session, [bonds[1].id, bonds[0].id])
    second = build(db_session, [bonds[0].id, bonds[1].id])
    assert first.model_dump_json() == second.model_dump_json()
    assert first.contract_version == "bond-liquidity-batch-feature-v1"
    assert first.pit_ready is False
    for model in (first, first.items[0], first.diagnostics, first.capabilities):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unknown": 1})
    with pytest.raises(ValidationError):
        BondLiquidityBatchFeatureView.model_validate(
            {**first.model_dump(), "pit_ready": True}
        )


def test_static_safety_and_shared_evaluator_boundary():
    root = Path(__file__).parents[1] / "app/services"
    evaluator = ast.parse(
        (root / "bond_liquidity_feature_evaluator.py").read_text(encoding="utf-8")
    )
    batch = ast.parse(
        (root / "bond_liquidity_batch_feature_service.py").read_text(
            encoding="utf-8"
        )
    )
    for node in ast.walk(evaluator):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [name.name for name in node.names]
            )
            assert not any(
                token in module.lower()
                for module in modules
                for token in ("sqlalchemy", "models", "services", "requests", "httpx")
            )
        assert not isinstance(node, ast.Constant) or not isinstance(node.value, float)
    source = ast.unparse(batch)
    for forbidden in (
        "bond_liquidity_relative_value",
        "bond_m3_feature",
        "m3_coverage",
        "Bond.volume",
        "Bond.liquidity_score",
        "BondMarketSnapshot.liquidity_score",
    ):
        assert forbidden not in source
    assert not any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_for_bond"
        for node in ast.walk(batch)
    )
    mutations = {"commit", "flush", "add", "add_all", "delete"}
    for node in ast.walk(batch):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in mutations
