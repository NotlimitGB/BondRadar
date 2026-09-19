"""Task270 acceptance using the existing disposable SQLite fixture only."""

import ast
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Context, Decimal, ROUND_DOWN, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
from app.services.bond_liquidity_feature_service import (
    BondLiquidityFeatureService, _midrank_percentile, _select_daily, _summarize,
)
import app.services.bond_liquidity_feature_service as liquidity_service_module

DAY = date(2026, 9, 17)
D = Decimal


@pytest.fixture
def seed(db_session):
    company = Company(name="Liquidity issuer", ticker="TASK270")
    db_session.add(company)
    db_session.flush()
    def bond(**fields):
        row = Bond(company_id=company.id, name="Liquidity bond", **fields)
        db_session.add(row)
        db_session.flush()
        return row
    target = bond(isin="RU000A123450", secid="TASK270")
    def snapshot(row=target, *, age=0, source="moex", payload=None, **fields):
        observation = BondMarketSnapshot(bond_id=row.id, trade_date=DAY - timedelta(days=age),
                                         source=source, raw_payload=payload, **fields)
        db_session.add(observation)
        db_session.flush()
        return observation
    def days(row=target, *, value=100, trades=10, volume=5, count=5, age=0):
        return [snapshot(row, age=age + i, payload={"moex": {
            "VALUE": str(value), "NUMTRADES": trades, "VOLUME": str(volume)}}) for i in range(count)]
    def universe(count=10, *, tied=False):
        rows = [target] + [bond() for _ in range(count - 1)]
        for index, row in enumerate(rows):
            days(row, value=100 if tied else (index + 1) * 100,
                 trades=10 if tied else index + 1, age=0 if tied else index)
        db_session.commit()
        return rows
    db_session.commit()
    return SimpleNamespace(target=target, company=company, bond=bond, snapshot=snapshot,
                           days=days, universe=universe)


def build(db, seed, **kwargs):
    return BondLiquidityFeatureService(db).build_for_bond(seed.target.id, DAY, **kwargs)


def assert_unavailable(result, status):
    assert result.score_status == status
    assert result.liquidity_score_v1 is None
    assert result.score_components.model_dump() == {
        "turnover_percentile": None, "trade_count_percentile": None, "recency_percentile": None}
    assert not result.availability.has_liquidity_score
    assert result.pit_ready is False


def test_exact_source_inclusive_window_and_no_future(db_session, seed):
    before = seed.snapshot(age=30, payload={"value": "999", "num_trades": 99})
    lower = seed.snapshot(age=29, payload={"value": "10", "num_trades": 1})
    upper = seed.snapshot(age=0, payload={"value": "20", "num_trades": 2})
    seed.snapshot(age=-1, payload={"value": "999", "num_trades": 99})
    other = seed.snapshot(source="MOEX", payload={"value": "999", "num_trades": 99})
    seed.snapshot(source=" moex ", payload={"value": "777", "num_trades": 77})
    db_session.commit()
    result = build(db_session, seed)
    assert result.window_start_date == DAY - timedelta(days=29)
    assert result.calendar_window_days == result.lookback_calendar_days == 30
    assert result.snapshot_observation_days == 2
    assert result.total_turnover_value == D(30)
    assert result.provenance.selected_market_snapshot_ids == [lower.id, upper.id]
    assert before.id not in result.provenance.selected_market_snapshot_ids
    assert result.latest_observation_age_days == 0
    assert build(db_session, seed, market_source="MOEX").provenance.selected_market_snapshot_ids == [other.id]
    assert build(db_session, seed, market_source=" moex ").total_turnover_value == D(777)
    assert_unavailable(build(db_session, seed, market_source="manual"), "NO_MARKET_DATA")


def test_same_day_synthetic_query_rows_choose_max_id_no_fallback(db_session, seed, monkeypatch):
    rows = [SimpleNamespace(id=i, bond_id=seed.target.id, trade_date=DAY,
                            raw_payload=payload) for i, payload in (
        (9, {"value": "12", "num_trades": 5}), (11, {"value": "broken"}),
        (10, {"value": "24", "num_trades": 8}))]
    execute = db_session.execute
    def synthetic(statement, *args, **kwargs):
        if "bond_market_snapshots.raw_payload" in str(statement):
            return SimpleNamespace(all=lambda: rows)
        return execute(statement, *args, **kwargs)
    monkeypatch.setattr(db_session, "execute", synthetic)
    first = build(db_session, seed, min_observation_days=1)
    rows.reverse()
    second = build(db_session, seed, min_observation_days=1)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.provenance.selected_market_snapshot_ids == [11]
    assert first.snapshot_observation_days == 1
    assert first.total_turnover_value is None
    assert "MALFORMED_RAW_VALUE" in first.quality_flags
    assert_unavailable(first, "MISSING_SCORE_COMPONENT")
    assert any(c.name == "bond_market_snapshots_bond_date_source_unique"
               for c in BondMarketSnapshot.__table__.constraints)


@pytest.mark.parametrize("payload", [
    {"moex": {"VOLUME": "12.5", "VALUE": "100,25", "NUMTRADES": "4"}},
    {"canonical": {"volume": "12.5", "value": "100.25", "num_trades": 4}},
    {"moex": {"volume": 12.5, "value": "100.25", "numtrades": 4},
     "canonical": {"volume": "12.50", "value": "100.250", "num_trades": "4"}},
])
def test_raw_components_decimal_copying(db_session, seed, payload):
    seed.snapshot(payload=payload)
    db_session.commit()
    result = build(db_session, seed)
    assert result.total_trade_volume == D("12.5")
    assert result.total_turnover_value == D("100.25")
    assert result.total_num_trades == 4 and type(result.total_num_trades) is int
    assert result.median_daily_num_trades == D(4)
    assert all(getattr(result, field) == 1 for field in (
        "trade_volume_observation_days", "turnover_value_observation_days", "num_trades_observation_days"))


def test_legacy_value_fallback_not_promoted_and_actual_volume_preserved(db_session, seed):
    seed.snapshot(age=1, volume=D(1000), liquidity_score=99,
                  payload={"moex": {"VALUE": "1000", "NUMTRADES": 2},
                           "mapping_notes": ["VALUE was used as volume fallback"]})
    seed.snapshot(payload={"moex": {"VOLUME": "3", "VALUE": "20", "NUMTRADES": 1},
                           "mapping_notes": ["VALUE was used as volume fallback"]})
    db_session.commit()
    result = build(db_session, seed)
    assert result.trade_volume_observation_days == 1 and result.total_trade_volume == D(3)
    assert result.total_turnover_value == D(1020)
    assert "VOLUME_VALUE_FALLBACK_NOT_PROMOTED" in result.quality_flags


@pytest.mark.parametrize("bad", [True, -1, "NaN", "Infinity", "-Infinity", "broken", [], {}])
@pytest.mark.parametrize("key,field,flag", [
    ("VOLUME", "total_trade_volume", "MALFORMED_RAW_VOLUME"),
    ("VALUE", "total_turnover_value", "MALFORMED_RAW_VALUE"),
    ("NUMTRADES", "total_num_trades", "MALFORMED_RAW_NUMTRADES"),
])
def test_malformed_optional_components(db_session, seed, bad, key, field, flag):
    seed.snapshot(payload={"moex": {"VOLUME": 5, "VALUE": 100, "NUMTRADES": 4, key: bad}})
    db_session.commit()
    result = build(db_session, seed)
    assert getattr(result, field) is None
    assert flag in result.quality_flags


@pytest.mark.parametrize("key,canonical,field,flag", [
    ("VOLUME", "volume", "total_trade_volume", "CONFLICTING_RAW_VOLUME"),
    ("VALUE", "value", "total_turnover_value", "CONFLICTING_RAW_VALUE"),
    ("NUMTRADES", "num_trades", "total_num_trades", "CONFLICTING_RAW_NUMTRADES"),
])
def test_conflicting_representations(db_session, seed, key, canonical, field, flag):
    seed.snapshot(payload={"moex": {key: 2}, "canonical": {canonical: 3}})
    db_session.commit()
    result = build(db_session, seed)
    assert getattr(result, field) is None and flag in result.quality_flags


def test_fractional_trade_count_invalid(db_session, seed):
    seed.snapshot(payload={"num_trades": "1.5"})
    db_session.commit()
    result = build(db_session, seed)
    assert result.total_num_trades is None and "MALFORMED_RAW_NUMTRADES" in result.quality_flags


@pytest.mark.parametrize("payload", [None, {}, {"moex": {"VALUE": None}}, [], "broken"])
def test_missing_metrics_null_not_zero(db_session, seed, payload):
    seed.snapshot(payload=payload)
    db_session.commit()
    result = build(db_session, seed)
    for field in ("total_trade_volume", "total_turnover_value", "total_num_trades",
                  "median_daily_turnover_value", "mean_daily_num_trades",
                  "positive_turnover_share_of_turnover_observations",
                  "positive_trade_count_share_of_trade_count_observations"):
        assert getattr(result, field) is None
    assert result.latest_trade_date == DAY and result.latest_observation_age_days == 0
    assert result.snapshot_observation_days == 1


def test_observed_zero_is_eligible(db_session, seed):
    seed.universe(tied=True)
    snapshots = db_session.query(BondMarketSnapshot).all()
    for row in snapshots:
        row.raw_payload = {"moex": {"VOLUME": 0, "VALUE": 0, "NUMTRADES": 0}}
    db_session.commit()
    result = build(db_session, seed)
    assert result.score_status == "READY" and result.liquidity_score_v1 == D(50)
    assert result.total_trade_volume == result.total_turnover_value == result.total_num_trades == 0
    assert result.days_with_positive_turnover == result.days_with_positive_trade_volume == result.days_with_positive_num_trades == 0
    assert result.positive_turnover_share_of_turnover_observations == 0
    assert result.positive_trade_count_share_of_trade_count_observations == 0
    assert result.availability.has_turnover_value and result.availability.has_num_trades


def test_metric_specific_denominators_odd_even_medians_and_totals(db_session, seed):
    for age, payload in enumerate([
        {"moex": {"VOLUME": "1.25", "VALUE": "0", "NUMTRADES": 0}},
        {"moex": {"VOLUME": "3.75", "VALUE": "20", "NUMTRADES": 2}},
        {"moex": {"VALUE": "10", "NUMTRADES": 4}},
        {"moex": {"NUMTRADES": 8}}, {},
    ]):
        seed.snapshot(age=age, payload=payload)
    db_session.commit()
    result = build(db_session, seed)
    assert result.snapshot_observation_days == 5
    assert (result.trade_volume_observation_days, result.turnover_value_observation_days,
            result.num_trades_observation_days) == (2, 3, 4)
    assert result.median_daily_trade_volume == result.mean_daily_trade_volume == D("2.50")
    assert result.total_trade_volume == D(5)
    assert result.median_daily_turnover_value == result.mean_daily_turnover_value == D(10)
    assert result.total_turnover_value == D(30)
    assert result.median_daily_num_trades == D(3) and result.mean_daily_num_trades == D("3.5")
    assert result.total_num_trades == 14
    assert (result.days_with_positive_turnover, result.days_with_positive_trade_volume,
            result.days_with_positive_num_trades) == (2, 2, 3)
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        assert result.positive_turnover_share_of_turnover_observations == D(2) / D(3)
    assert result.positive_trade_count_share_of_trade_count_observations == D("0.75")
    assert all(f"PARTIAL_{name}_COVERAGE" in result.quality_flags
               for name in ("TRADE_VOLUME", "TURNOVER_VALUE", "NUM_TRADES"))


def test_recency_uses_latest_snapshot_even_without_usable_values(db_session, seed):
    seed.days(age=5)
    newest = seed.snapshot(payload={"value": "NaN"})
    db_session.commit()
    result = build(db_session, seed)
    assert result.latest_trade_date == DAY and result.latest_observation_age_days == 0
    assert result.provenance.selected_market_snapshot_ids[-1] == newest.id
    assert result.turnover_value_observation_days == 5


@pytest.mark.parametrize("count,missing,status", [
    (0, None, "NO_MARKET_DATA"), (4, None, "INSUFFICIENT_TARGET_EVIDENCE"),
    (4, "VALUE", "INSUFFICIENT_TARGET_EVIDENCE"),
    (5, "VALUE", "MISSING_SCORE_COMPONENT"), (5, "NUMTRADES", "MISSING_SCORE_COMPONENT"),
    (5, None, "INSUFFICIENT_UNIVERSE"),
])
def test_status_precedence_and_stable_unavailable_serialization(db_session, seed, count, missing, status):
    for age in range(count):
        raw = {"VALUE": 100, "NUMTRADES": 2}
        if missing:
            raw.pop(missing)
        seed.snapshot(age=age, payload={"moex": raw})
    db_session.commit()
    result = build(db_session, seed)
    assert_unavailable(result, status)
    assert result.snapshot_observation_days == count
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert result.model_dump_json() == build(db_session, seed).model_dump_json()
    assert BondLiquidityFeatureView.model_validate_json(result.model_dump_json()) == result
    assert result.capabilities.liquidity_score_v1_ready is True
    assert not result.capabilities.transaction_cost_model_ready


def test_universe_candidates_source_window_evidence_and_peer_flags(db_session, seed):
    seed.days()
    for source, age in (("manual", 0), ("MOEX", 0), ("moex", 30), ("moex", -1)):
        seed.snapshot(seed.bond(), source=source, age=age, payload={"value": 100, "num_trades": 2})
    seed.days(seed.bond(), count=4)
    seed.days(seed.bond(), count=5, value="broken")
    seed.days(seed.bond(), count=5, trades="broken")
    seed.days(seed.bond(), count=5)
    db_session.commit()
    result = build(db_session, seed)
    assert result.provenance.universe_candidate_count == 5
    assert result.provenance.score_eligible_universe_count == 2
    assert not any(flag.startswith(("MALFORMED", "CONFLICTING")) for flag in result.quality_flags)
    assert_unavailable(result, "INSUFFICIENT_UNIVERSE")


@pytest.mark.parametrize("count,status", [(9, "INSUFFICIENT_UNIVERSE"), (10, "READY")])
def test_universe_minimum_9_10_and_target_included(db_session, seed, count, status):
    seed.universe(count=count)
    result = build(db_session, seed)
    assert result.score_status == status
    assert result.provenance.universe_candidate_count == result.provenance.score_eligible_universe_count == count
    if count == 9:
        assert_unavailable(result, status)


def test_one_usable_component_day_enough_and_volume_not_mandatory(db_session, seed):
    rows = [seed.target] + [seed.bond() for _ in range(9)]
    for row in rows:
        for age in range(5):
            seed.snapshot(row, age=age, payload={"value": 10, "num_trades": 2} if age == 0 else {})
    db_session.commit()
    result = build(db_session, seed)
    assert result.score_status == "READY" and result.liquidity_score_v1 == D(50)
    assert result.turnover_value_observation_days == result.num_trades_observation_days == 1
    assert not result.availability.has_trade_volume
    assert result.score_components.model_dump() == dict.fromkeys(
        ["turnover_percentile", "trade_count_percentile", "recency_percentile"], D(50))


def test_percentile_directions_weighted_score_and_range(db_session, seed):
    rows = seed.universe()
    results = [BondLiquidityFeatureService(db_session).build_for_bond(row.id, DAY) for row in rows]
    assert results[0].score_components.turnover_percentile == results[0].score_components.trade_count_percentile == 0
    assert results[0].score_components.recency_percentile == 100
    assert results[-1].score_components.turnover_percentile == results[-1].score_components.trade_count_percentile == 100
    assert results[-1].score_components.recency_percentile == 0
    for index, result in enumerate(results):
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            rank = D(100) * D(index) / D(9)
            assert result.score_components.turnover_percentile == rank
            assert result.score_components.trade_count_percentile == rank
            assert result.score_components.recency_percentile == D(100) * D(9-index) / D(9)
            assert result.liquidity_score_v1 == (
                D("0.50") * result.score_components.turnover_percentile
                + D("0.35") * result.score_components.trade_count_percentile
                + D("0.15") * result.score_components.recency_percentile)
        assert D(0) <= result.liquidity_score_v1 <= D(100)
        assert type(result.liquidity_score_v1) is Decimal
        assert not any("label" in field or "signal" in field for field in result.model_dump())


def test_tied_extrema_and_fully_tied_midrank(db_session, seed):
    seed.universe(tied=True)
    assert build(db_session, seed).liquidity_score_v1 == D(50)
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        values = [D(0), D(0), D(1), D(2), D(3), D(4), D(5), D(6), D(9), D(9)]
        assert _midrank_percentile(D(0), values) == D(100) * D("0.5") / D(9)
        assert _midrank_percentile(D(9), values) == D(100) * D("8.5") / D(9)
        assert _midrank_percentile(D(0), values, lower_is_better=True) == D(100) * D("8.5") / D(9)
        assert _midrank_percentile(D(1), [D(1)] * 10) == 50


def test_insertion_order_and_daily_order_independent():
    rows = [SimpleNamespace(id=bond_id * 10 + age, bond_id=bond_id,
                            trade_date=DAY-timedelta(days=age),
                            raw_payload={"value": str(bond_id*100+age), "num_trades": bond_id})
            for bond_id in range(1, 11) for age in range(5)]
    def calculate(rows):
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            stats = {key: _summarize(value, DAY, 30) for key, value in _select_daily(rows).items()}
            values = [row.statistics.median_daily_turnover_value for row in stats.values()]
            return {key: (row.statistics.model_dump(), _midrank_percentile(
                row.statistics.median_daily_turnover_value, values)) for key, row in stats.items()}
    assert calculate(rows) == calculate(list(reversed(rows))) == calculate(rows[::2] + rows[1::2])


def test_decimal_context_is_local_and_presentation_not_rounded(db_session, seed):
    seed.universe()
    normal = build(db_session, seed)
    with localcontext(Context(prec=4, rounding=ROUND_DOWN)) as context:
        result = build(db_session, seed)
        assert context.prec == 4 and context.rounding == ROUND_DOWN
    assert result.model_dump_json() == normal.model_dump_json()
    with localcontext(Context(prec=3, rounding=ROUND_DOWN)):
        middle = BondLiquidityFeatureService(db_session).build_for_bond(seed.target.id + 1, DAY)
    assert middle.score_components.turnover_percentile == D("11.11111111111111111111111111")


def test_no_legacy_inputs_raw_payload_mutation_or_persistence(db_session, seed):
    rows = seed.universe()
    before = build(db_session, seed)
    seed.target.volume, seed.target.liquidity_score = D(9999), 99
    seed.target.currency, seed.target.is_perpetual = "USD", True
    seed.target.is_floating_coupon, seed.target.is_subordinated = True, True
    seed.target.maturity_date = DAY - timedelta(days=300)
    snapshots = db_session.query(BondMarketSnapshot).all()
    for row in snapshots:
        row.volume, row.liquidity_score, row.spread_to_ofz = D(999), 1, D("123.456")
    db_session.commit()
    payloads = [deepcopy(row.raw_payload) for row in snapshots]
    result = build(db_session, seed)
    assert result.model_dump_json() == before.model_dump_json()
    assert seed.target.liquidity_score == 99
    assert all(row.liquidity_score == 1 and row.spread_to_ofz == D("123.456") for row in snapshots)
    assert [row.raw_payload for row in snapshots] == payloads
    assert len(rows) == 10


def test_rating_and_bank_evidence_changes_do_not_change_score(db_session, seed):
    # Reuse only the existing offline evidence factory, without running Task269's suite.
    from test_bond_credit_feature_service import seed as credit_factory
    seed.universe(tied=True)
    before = build(db_session, seed).model_dump_json()
    evidence = credit_factory.__wrapped__(db_session)
    issuer = evidence.issuer()
    evidence.mapping(issuer)
    rating = evidence.rating(target=seed.target, value="AAA(RU)")
    bank = evidence.subject(issuer)
    metric = evidence.metric(bank)
    db_session.commit()
    assert build(db_session, seed).model_dump_json() == before
    rating.rating_value_raw = "BBB(RU)"
    metric.metric.metric_value = D("1.125")
    db_session.commit()
    assert build(db_session, seed).model_dump_json() == before


@pytest.mark.parametrize("pending", [False, True])
def test_select_only_preserves_caller_state_with_autoflush(db_session, seed, monkeypatch, pending):
    seed.universe(tied=True)
    identity = seed.target.id
    doomed = seed.bond()
    db_session.commit()
    if pending:
        db_session.add(Company(name="Caller pending", ticker="PENDING270"))
        seed.target.name = "Caller dirty name"
        db_session.delete(doomed)
    state = tuple(set(items) for items in (db_session.new, db_session.dirty, db_session.deleted))
    statements = []
    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        pytest.fail("Service attempted a session mutation")
    db_session.autoflush = True
    with monkeypatch.context() as patch:
        for name in ("commit", "flush", "add", "add_all", "delete"):
            patch.setattr(db_session, name, forbidden)
        event.listen(db_session.bind, "before_cursor_execute", capture)
        try:
            result = BondLiquidityFeatureService(db_session).build_for_bond(identity, DAY)
        finally:
            event.remove(db_session.bind, "before_cursor_execute", capture)
    assert result.score_status == "READY"
    assert len(statements) == 2
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert "raw_payload" in statements[1] and "liquidity_score" not in statements[1]
    assert tuple(set(items) for items in (db_session.new, db_session.dirty, db_session.deleted)) == state


def test_delegates_shared_evaluator_exactly_once(db_session, seed, monkeypatch):
    seed.universe(tied=True)
    original = liquidity_service_module.evaluate_liquidity_features
    calls = []

    def capture(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(
        liquidity_service_module, "evaluate_liquidity_features", capture
    )
    result = build(db_session, seed)
    assert result.score_status == "READY"
    assert len(calls) == 1
    assert len(calls[0][0][0]) == 1


@pytest.mark.parametrize("kwargs", [
    {"bond_id": True}, {"bond_id": 0}, {"bond_id": -1}, {"bond_id": "1"}, {"bond_id": 1.0},
    {"as_of_date": None}, {"as_of_date": "2026-09-17"}, {"as_of_date": datetime(2026, 9, 17)},
    {"market_source": None}, {"market_source": ""}, {"market_source": "  "}, {"market_source": 1},
    {"lookback_calendar_days": True}, {"lookback_calendar_days": 0}, {"lookback_calendar_days": -1},
    {"lookback_calendar_days": 1.5}, {"lookback_calendar_days": "30"},
    {"min_observation_days": True}, {"min_observation_days": 0}, {"min_observation_days": -1},
    {"min_observation_days": 31}, {"min_observation_days": "5"}, {"min_observation_days": 1.5},
    {"as_of_date": date.min}, {"lookback_calendar_days": 10**30},
])
def test_argument_validation_before_queries(db_session, seed, kwargs, monkeypatch):
    args = {"bond_id": seed.target.id, "as_of_date": DAY, **kwargs}
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments reached SQL")
    monkeypatch.setattr(db_session, "execute", forbidden)
    with pytest.raises(ValueError):
        BondLiquidityFeatureService(db_session).build_for_bond(**args)


def test_one_day_window_and_missing_bond(db_session, seed):
    seed.snapshot(payload={"value": 1, "num_trades": 1})
    db_session.commit()
    result = build(db_session, seed, lookback_calendar_days=1, min_observation_days=1)
    assert result.window_start_date == DAY and result.snapshot_observation_days == 1
    with pytest.raises(HTTPException) as error:
        BondLiquidityFeatureService(db_session).build_for_bond(999999, DAY)
    assert error.value.status_code == 404 and error.value.detail == "Bond not found"
    result = BondLiquidityFeatureService(db_session).build_for_bond(
        seed.target.id, date.min, lookback_calendar_days=1, min_observation_days=1)
    assert result.window_start_date == date.min


def test_frozen_extra_forbidden_contract_and_false_pit(db_session, seed):
    result = build(db_session, seed)
    assert result.contract_version == "bond-liquidity-feature-v1"
    for model in (result, result.availability, result.provenance, result.score_components, result.capabilities):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unknown": 1})
    with pytest.raises(ValidationError):
        BondLiquidityFeatureView.model_validate({**result.model_dump(), "pit_ready": True})


def test_narrow_static_safety_and_direct_task267_reuse():
    path = Path(__file__).parents[1] / "app/services/bond_liquidity_feature_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mutations = {"commit", "flush", "add", "add_all", "delete"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "db":
                assert node.func.attr not in mutations
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else [n.name for n in node.names]
            assert not any(word in module.lower() for module in modules for word in (
                "client", "broker", "strategy", "risk_engine", "scoring", "credit", "ofz", "requests", "httpx"))
        assert not isinstance(node, ast.Constant) or not isinstance(node.value, float)
    assert any(isinstance(node, ast.ImportFrom)
               and node.module == "app.services.bond_market_feature_service"
               and any(alias.name == "_liquidity" for alias in node.names) for node in ast.walk(tree))
