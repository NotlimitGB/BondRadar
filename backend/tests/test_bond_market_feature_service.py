from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.schemas.bond_market_features import BondMarketFeatureView
from app.services.bond_market_feature_service import BondMarketFeatureService, _raw_number

DAY = date(2026, 9, 17)


@pytest.fixture
def bond(db_session):
    company = Company(name="Feature test issuer", ticker="FEATURE")
    db_session.add(company)
    db_session.flush()
    row = Bond(company_id=company.id, name="Feature bond", isin="RU000A000001",
               secid="FEATURE", currency="RUB", nominal_value=Decimal("1000"),
               coupon_rate=Decimal("9.125"), maturity_date=DAY + timedelta(days=20),
               offer_date=DAY - timedelta(days=3), amortization=False)
    db_session.add(row)
    db_session.commit()
    return row


def snapshot(db, bond, *, when=DAY, source="moex", **fields):
    row = BondMarketSnapshot(bond_id=bond.id, trade_date=when, source=source, **fields)
    db.add(row)
    db.commit()
    return row


def build(db, bond, **kwargs):
    return BondMarketFeatureService(db).build_for_bond(bond.id, DAY, **kwargs)


def test_exact_selection_future_exclusion_and_explicit_source(db_session, bond):
    other_bond = Bond(company_id=bond.company_id, name="Other")
    db_session.add(other_bond)
    db_session.commit()
    snapshot(db_session, other_bond, price=Decimal("99"))
    snapshot(db_session, bond, when=DAY - timedelta(days=3), price=Decimal("80"))
    selected = snapshot(db_session, bond, when=DAY - timedelta(days=1), price=Decimal("91"))
    manual = snapshot(db_session, bond, source="manual", price=Decimal("92"))
    snapshot(db_session, bond, when=DAY + timedelta(days=1), price=Decimal("93"))
    result = build(db_session, bond)
    assert result.market_snapshot_id == selected.id
    assert result.price == Decimal("91")
    assert result.market_age_days == 1
    assert result.provenance.market_source == "moex"
    assert build(db_session, bond, market_source="manual").market_snapshot_id == manual.id
    assert build(db_session, bond, market_source="missing").market_status == "MISSING"


def test_no_snapshot_or_legacy_market_fallback(db_session, bond):
    bond.current_price = Decimal("100")
    bond.yield_to_maturity = Decimal("11")
    bond.duration_years = Decimal("2")
    bond.volume = Decimal("400")
    bond.liquidity_score = 98
    db_session.commit()
    snapshot(db_session, bond, when=DAY + timedelta(days=1), price=Decimal("101"))
    result = build(db_session, bond)
    assert result.market_status == "MISSING"
    assert result.market_age_days is None
    for field in ("price", "clean_price", "nkd", "yield_to_maturity_pct", "duration_years",
                  "trade_volume", "turnover_value", "num_trades"):
        assert getattr(result, field) is None
    assert not result.availability.has_market_snapshot
    assert result.provenance.market_snapshot_id is None
    assert "MARKET_DATA_MISSING" in result.quality_flags


@pytest.mark.parametrize("age,threshold,status", [(7, 7, "FRESH"), (8, 7, "STALE"),
                                                  (0, 0, "FRESH"), (1, 0, "STALE")])
def test_freshness_keeps_values(db_session, bond, age, threshold, status):
    snapshot(db_session, bond, when=DAY - timedelta(days=age), price=Decimal("90.5"),
             yield_to_maturity=Decimal("11.250"), duration_years=Decimal("2.125"))
    result = build(db_session, bond, max_market_age_days=threshold)
    assert result.market_status == status
    assert result.market_age_days == age
    assert result.price == Decimal("90.5")
    assert result.yield_to_maturity_pct == Decimal("11.250")
    assert result.duration_years == Decimal("2.125")
    assert ("MARKET_DATA_STALE" in result.quality_flags) == (status == "STALE")


@pytest.mark.parametrize("payload", [
    {"moex": {"VOLUME": "12.5", "VALUE": "100,25", "NUMTRADES": "4"}},
    {"moex": {"volume": 12.5, "value": "100.25", "numtrades": 4},
     "canonical": {"volume": "12.50", "value": "100.25", "num_trades": 4},
     "value": "100.25", "num_trades": 4},
    {"canonical": {"volume": "12.5"}, "value": "100.25", "num_trades": 4},
])
def test_existing_raw_shapes_exact_liquidity_and_no_payload_mutation(db_session, bond, payload):
    row = snapshot(db_session, bond, volume=Decimal("999"), raw_payload=payload)
    before = deepcopy(row.raw_payload)
    result = build(db_session, bond)
    assert result.trade_volume == Decimal("12.5")
    assert result.turnover_value == Decimal("100.25")
    assert result.num_trades == 4
    assert type(result.trade_volume) is Decimal
    assert type(result.num_trades) is int
    assert row.raw_payload == before
    assert not db_session.dirty


def test_volume_fallback_is_not_promoted_and_duration_lineage(db_session, bond):
    row = snapshot(db_session, bond, volume=Decimal("1000"), duration_years=Decimal("2"),
                   liquidity_score=99, spread_to_ofz=Decimal("5"),
                   raw_payload={"moex": {"VALUE": "1000"}, "mapping_notes": [
                       "VALUE was used as volume fallback",
                       "DURATION looked like days and was divided by 365"]})
    result = build(db_session, bond)
    assert result.trade_volume is None
    assert result.turnover_value == Decimal("1000")
    assert result.duration_years == Decimal("2")
    assert result.spread_to_ofz is None
    assert "VOLUME_VALUE_FALLBACK_NOT_PROMOTED" in result.quality_flags
    assert "DURATION_LEGACY_DAY_NORMALIZATION" in result.quality_flags
    row.raw_payload = {**row.raw_payload, "moex": {"VOLUME": 0, "VALUE": "1000"}}
    db_session.commit()
    assert build(db_session, bond).trade_volume == Decimal("0")


def test_unproven_snapshot_volume_is_missing(db_session, bond):
    snapshot(db_session, bond, volume=Decimal("300"))
    assert build(db_session, bond).trade_volume is None


@pytest.mark.parametrize("raw,expected", [(None, None), ("", None), ("  ", None),
    (Decimal("12.500"), Decimal("12.500")), (2, Decimal("2")),
    (0.1, Decimal("0.1")), (" 1,25 ", Decimal("1.25")), ("1e2", Decimal("100"))])
def test_decimal_parser(raw, expected):
    value = _raw_number(raw)
    assert value == expected
    if isinstance(raw, Decimal):
        assert value is raw


@pytest.mark.parametrize("raw", [True, False, -1, "-0.01", "NaN", "Infinity", "-Infinity",
                                    float("nan"), float("inf"), "bad", {}, [], object()])
def test_bad_decimal_parser(raw):
    with pytest.raises(ValueError):
        _raw_number(raw)


@pytest.mark.parametrize("raw,valid", [("2.000", True), (0, True), (2.0, True),
    (Decimal("3"), True), ("2.5", False), (True, False), ("-1", False)])
def test_trade_count_integer_contract(raw, valid):
    if valid:
        assert type(_raw_number(raw, integer=True)) is int
    else:
        with pytest.raises(ValueError):
            _raw_number(raw, integer=True)


@pytest.mark.parametrize("key,field,flag", [("VOLUME", "trade_volume", "VOLUME"),
    ("VALUE", "turnover_value", "VALUE"), ("NUMTRADES", "num_trades", "NUMTRADES")])
@pytest.mark.parametrize("raw", ["bad", -2, True, "NaN", {"x": 1}])
def test_optional_malformed_fields_are_isolated(db_session, bond, key, field, flag, raw):
    payload = {"moex": {"VOLUME": "2", "VALUE": "100", "NUMTRADES": 3}}
    payload["moex"][key] = raw
    snapshot(db_session, bond, raw_payload=payload)
    result = build(db_session, bond)
    assert getattr(result, field) is None
    assert f"MALFORMED_RAW_{flag}" in result.quality_flags
    for other in {"trade_volume", "turnover_value", "num_trades"} - {field}:
        assert getattr(result, other) is not None


@pytest.mark.parametrize("raw_key,canonical_key,field", [
    ("VOLUME", "volume", "trade_volume"), ("VALUE", "value", "turnover_value"),
    ("NUMTRADES", "num_trades", "num_trades")])
def test_conflicts_fail_closed(db_session, bond, raw_key, canonical_key, field):
    snapshot(db_session, bond, raw_payload={"moex": {raw_key: "2"},
                                          "canonical": {canonical_key: "3"}})
    result = build(db_session, bond)
    assert getattr(result, field) is None
    assert f"CONFLICTING_RAW_{raw_key}" in result.quality_flags


def test_malformed_representation_blocks_other_valid_representation(db_session, bond):
    snapshot(db_session, bond, raw_payload={"moex": {"VALUE": "bad", "VOLUME": ""},
                                          "canonical": {"value": "2", "volume": "3"}})
    result = build(db_session, bond)
    assert result.turnover_value is None
    assert result.trade_volume == Decimal("3")


def test_zero_availability_and_deterministic_contract(db_session, bond):
    snapshot(db_session, bond, price=Decimal("0"), clean_price=Decimal("0"), nkd=Decimal("0"),
             yield_to_maturity=Decimal("0"), duration_years=Decimal("0"),
             raw_payload={"moex": {"VOLUME": 0, "VALUE": 0, "NUMTRADES": 0}})
    result = build(db_session, bond)
    for name, available in result.availability.model_dump().items():
        if name != "has_cashflow_schedule":
            assert available
    assert result.contract_version == "bond-market-feature-v1"
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert result.model_dump_json() == build(db_session, bond).model_dump_json()
    assert BondMarketFeatureView.model_validate_json(result.model_dump_json()) == result
    assert result.capabilities.liquidity_raw_inputs_ready
    for key, value in result.capabilities.model_dump().items():
        if key != "liquidity_raw_inputs_ready":
            assert value is False
    assert result.pit_ready is False
    with pytest.raises(ValidationError):
        BondMarketFeatureView.model_validate({**result.model_dump(), "pit_ready": True})


@pytest.mark.parametrize("maturity,perpetual,days,matured", [
    (DAY + timedelta(days=20), False, 20, False), (DAY, False, 0, False),
    (DAY - timedelta(days=1), False, -1, True), (None, False, None, False),
    (DAY - timedelta(days=1), True, None, False)])
def test_structural_dates_and_perpetual(db_session, bond, maturity, perpetual, days, matured):
    bond.maturity_date = maturity
    bond.is_perpetual = perpetual
    bond.is_floating_coupon = True
    bond.is_subordinated = True
    bond.amortization = None
    db_session.commit()
    result = build(db_session, bond)
    assert result.days_to_maturity == days
    assert result.is_matured is matured
    assert result.days_to_offer == -3
    assert result.metadata_has_amortization is None
    assert result.nominal_value == Decimal("1000")
    assert result.coupon_rate == Decimal("9.125")
    assert result.currency == "RUB"
    assert "FLOATING_COUPON" in result.quality_flags
    assert "SUBORDINATED_BOND" in result.quality_flags
    assert ("PERPETUAL_BOND" in result.quality_flags) == perpetual
    assert ("MATURED_BOND" in result.quality_flags) == matured


def test_cashflow_selection_counts_and_metadata_independence(db_session, bond):
    rows = []
    for offset, kind, source in [(2, "coupon", "moex"), (0, "amortization", "moex"),
        (0, "coupon", "moex"), (4, "redemption", "moex"),
        (3, "offer_redemption", "moex"), (1, "other", "moex"),
        (-1, "coupon", "moex"), (0, "redemption", "manual")]:
        row = BondCashflowEvent(bond_id=bond.id, event_date=DAY + timedelta(days=offset),
                                event_type=kind, source=source)
        rows.append(row)
        db_session.add(row)
    other_bond = Bond(company_id=bond.company_id, name="Other cashflows")
    db_session.add(other_bond)
    db_session.flush()
    db_session.add(BondCashflowEvent(bond_id=other_bond.id, event_date=DAY, event_type="coupon",
                                   source="moex"))
    db_session.commit()
    result = build(db_session, bond)
    assert result.future_cashflow_event_count == 6
    assert result.future_coupon_count == 2
    assert result.future_amortization_count == 1
    assert result.next_coupon_date == result.next_amortization_date == DAY
    assert result.next_redemption_date == DAY + timedelta(days=4)
    assert result.next_offer_redemption_date == DAY + timedelta(days=3)
    assert all([result.has_future_coupon, result.has_future_amortization,
                result.has_future_redemption, result.has_future_offer_redemption])
    assert result.metadata_has_amortization is False
    assert result.has_future_amortization
    assert "AMORTIZATION_METADATA_SCHEDULE_MISMATCH" in result.quality_flags
    expected = sorted(rows[:6], key=lambda row: (row.event_date, row.id))
    assert result.provenance.selected_cashflow_event_ids == [row.id for row in expected]
    assert result.provenance.cashflow_source == "moex"
    assert result.availability.has_cashflow_schedule
    assert result.offer_date == DAY - timedelta(days=3)


def test_absent_terms_and_schedule_do_not_infer_mismatch(db_session, bond):
    bond.maturity_date = None
    bond.offer_date = None
    bond.amortization = True
    db_session.commit()
    result = build(db_session, bond)
    assert result.days_to_offer is None
    assert result.days_to_maturity is None
    assert not result.availability.has_maturity
    assert not result.availability.has_offer
    assert result.future_cashflow_event_count == 0
    assert not result.availability.has_cashflow_schedule
    assert result.provenance.selected_cashflow_event_ids == []
    assert "CASHFLOW_SCHEDULE_MISSING" in result.quality_flags
    assert "AMORTIZATION_METADATA_SCHEDULE_MISMATCH" not in result.quality_flags


def test_select_only_and_no_autoflush_with_pending_changes(db_session, bond, monkeypatch):
    snapshot(db_session, bond, price=Decimal("91"))
    # Preserve all caller-owned pending state, even with autoflush enabled.
    deletion = Company(name="Pending caller deletion", ticker="DELETE")
    db_session.add(deletion)
    db_session.flush()
    db_session.commit()
    db_session.refresh(bond)
    bond_id = bond.id
    bond.name = "Another pending edit"
    pending = Company(name="Not flushed", ticker="PENDING")
    db_session.add(pending)
    db_session.delete(deletion)
    db_session.autoflush = True
    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        raise AssertionError("Service must not flush or commit")
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(db_session, "flush", forbidden)
            patch.setattr(db_session, "commit", forbidden)
            result = BondMarketFeatureService(db_session).build_for_bond(bond_id, DAY)
            assert result.price == Decimal("91")
            with pytest.raises(HTTPException):
                BondMarketFeatureService(db_session).build_for_bond(999999, DAY)
        assert before == (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert pending.id is None
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
        db_session.rollback()


@pytest.mark.parametrize("kwargs", [{"bond_id": True}, {"bond_id": 0}, {"bond_id": 1.0},
    {"as_of_date": datetime(2026, 9, 17)}, {"as_of_date": "2026-09-17"},
    {"market_source": ""}, {"market_source": " "}, {"market_source": None},
    {"max_market_age_days": True}, {"max_market_age_days": -1},
    {"max_market_age_days": 1.0}])
def test_invalid_arguments(db_session, bond, kwargs):
    arguments = {"bond_id": bond.id, "as_of_date": DAY, **kwargs}
    with pytest.raises(ValueError):
        BondMarketFeatureService(db_session).build_for_bond(**arguments)


def test_missing_bond(db_session):
    with pytest.raises(HTTPException) as exc:
        BondMarketFeatureService(db_session).build_for_bond(999999, DAY)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Bond not found"


def test_production_service_has_no_unsafe_dependencies_or_write_surface():
    import ast
    import app.services.bond_market_feature_service as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    prohibited = {"commit", "flush", "add", "add_all", "delete", "update", "insert"}
    db_calls = {node.func.attr for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and ast.unparse(node.func.value) == "self.db"}
    assert not db_calls & prohibited
    assert not {node.func.id for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)} & {
                    "insert", "update", "delete", "create_engine"
                }
    imports = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any(any(token in name for token in
                      ("score", "financial_report", "feature_snapshot", "moex_iss", "httpx",
                       "credit_risk", "cbr_bank")) for name in imports)
