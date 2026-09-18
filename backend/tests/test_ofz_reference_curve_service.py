"""Task268 acceptance on disposable SQLite; no production/source access."""

import ast
from datetime import date, datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.schemas.ofz_reference_curve import BondRelativeValueView, OfzReferenceCurveView
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.ofz_reference_curve_service import OfzReferenceCurveService, _finite

DAY = date(2026, 9, 17)
D = Decimal


@pytest.fixture
def factory(db_session):
    company = Company(name="Task268 fixture issuer", ticker="TASK268")
    db_session.add(company)
    db_session.commit()
    sequence = 0

    def make(*, name="ОФЗ-ПД", profile=True, terms=None, market=True,
             duration="2", ytm="12", when=DAY, source="moex", isin=None):
        nonlocal sequence
        sequence += 1
        bond = Bond(company_id=company.id, name=name, secid=f"CURVE{sequence}",
                    isin=isin, maturity_date=DAY + timedelta(days=100),
                    is_floating_coupon=False, is_perpetual=False, amortization=False,
                    yield_to_maturity=D("99"), duration_years=D("99"))
        db_session.add(bond)
        db_session.flush()
        if profile:
            values = dict(currency_state="verified", currency_code="RUB",
                          coupon_structure="fixed", amortization_structure="bullet",
                          perpetual_structure="dated", maturity_state="verified",
                          maturity_date=DAY + timedelta(days=100))
            values.update(terms or {})
            db_session.add(BondSecurityMasterProfile(bond_id=bond.id, **values))
        if market:
            db_session.add(BondMarketSnapshot(
                bond_id=bond.id, trade_date=when, source=source,
                duration_years=D(duration) if duration is not None else None,
                yield_to_maturity=D(ytm) if ytm is not None else None,
                spread_to_ofz=D("999.999"),
            ))
        db_session.commit()
        return bond
    return make


def add_market(db, bond, when=DAY, source="moex", duration="2", ytm="12"):
    row = BondMarketSnapshot(bond_id=bond.id, trade_date=when, source=source,
                             duration_years=D(duration) if duration is not None else None,
                             yield_to_maturity=D(ytm) if ytm is not None else None,
                             spread_to_ofz=D("-777"))
    db.add(row)
    db.commit()
    return row


def curve(db, **kwargs):
    return OfzReferenceCurveService(db).build_curve(DAY, **kwargs)


def evaluate(db, bond, **kwargs):
    return OfzReferenceCurveService(db).evaluate_bond(bond.id, DAY, **kwargs)


@pytest.fixture
def ready(factory):
    return factory(duration="2", ytm="12"), factory(duration="4", ytm="14")


def assert_unavailable(result, status):
    assert result.status == status
    assert status in result.quality_flags
    assert result.reference_ofz_yield_pct is None
    assert result.spread_to_ofz_pp is None
    assert result.spread_to_ofz_bps is None
    assert result.interpolation_method is None
    assert not result.pit_ready and not result.curve.pit_ready
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert BondRelativeValueView.model_validate_json(result.model_dump_json()) == result


def assert_counts(view):
    c = view.diagnostics
    assert c.ofz_identity_count == c.security_master_eligible_count + c.excluded_security_master_count
    assert c.security_master_eligible_count == c.with_market_snapshot_count + c.excluded_missing_snapshot_count
    assert c.with_market_snapshot_count == (c.with_valid_yield_duration_count
        + c.excluded_missing_yield_count + c.excluded_invalid_yield_count
        + c.excluded_missing_duration_count + c.excluded_invalid_duration_count
        + c.excluded_nonpositive_duration_count)
    assert c.with_valid_yield_duration_count == c.fresh_market_count + c.excluded_stale_count
    assert c.fresh_market_count == c.curve_date_member_count + c.excluded_curve_date_mismatch_count
    assert c.distinct_duration_node_count == view.node_count == len(view.nodes)


@pytest.mark.parametrize("name,isin", [("ОФЗ 26200", None), ("ofz 26200", None),
    ("Federal Loan Bond", None), ("Government instrument", "SU0000000001")])
def test_identity_compatible_boundary(db_session, factory, name, isin):
    factory(name=name, isin=isin)
    factory(name="Corporate issuer")
    result = curve(db_session)
    assert result.diagnostics.ofz_identity_count == 1
    assert result.node_count == 1
    assert_counts(result)


@pytest.mark.parametrize("terms", [
    {"coupon_structure": "floating"}, {"coupon_structure": "unknown"},
    {"coupon_structure": "conflict"}, {"amortization_structure": "amortizing"},
    {"amortization_structure": "unknown"}, {"amortization_structure": "conflict"},
    {"perpetual_structure": "perpetual"}, {"perpetual_structure": "unknown"},
    {"perpetual_structure": "conflict"},
    {"currency_code": "USD"}, {"currency_state": "conflict", "currency_code": None},
    {"currency_state": "unknown", "currency_code": None},
    {"maturity_state": "unknown", "maturity_date": None},
    {"maturity_state": "conflict", "maturity_date": None},
    {"maturity_date": DAY - timedelta(days=1)},
])
def test_security_master_authority_no_legacy_fallback(db_session, factory, terms):
    factory(terms=terms)
    result = curve(db_session)
    assert result.status == "NO_ELIGIBLE_OFZ"
    assert result.diagnostics.excluded_security_master_count == 1
    assert_counts(result)


def test_missing_profile_and_maturity_on_boundary(db_session, factory):
    factory(profile=False)
    included = factory(terms={"maturity_date": DAY})
    result = curve(db_session)
    assert result.nodes[0].component_bond_ids == [included.id]
    assert result.diagnostics.excluded_security_master_count == 1
    assert_counts(result)


@pytest.mark.parametrize("marker", ["ОФЗ-ИН", "OFZ-IN", "ОФЗ-ПК", "OFZ-PK", "ОФЗ-АД", "OFZ-AD"])
def test_explicit_family_excluded_even_with_eligible_profile(db_session, factory, marker):
    factory(name=marker)
    result = curve(db_session)
    assert result.status == "NO_ELIGIBLE_OFZ"
    assert_counts(result)


def test_exact_source_latest_future_exclusion(db_session, factory):
    bond = factory(when=DAY - timedelta(days=3), ytm="1")
    selected = add_market(db_session, bond, when=DAY - timedelta(days=1), ytm="12")
    manual = add_market(db_session, bond, source="manual", ytm="88")
    add_market(db_session, bond, when=DAY + timedelta(days=1), ytm="99")
    result = curve(db_session)
    assert result.nodes[0].component_snapshot_ids == [selected.id]
    assert result.nodes[0].yield_to_maturity_pct == D("12")
    assert result.curve_trade_date == DAY - timedelta(days=1)
    assert curve(db_session, market_source="manual").nodes[0].component_snapshot_ids == [manual.id]
    assert curve(db_session, market_source="missing").status == "NO_FRESH_MARKET_DATA"
    assert_counts(result)


def test_latest_invalid_snapshot_not_replaced_by_older_valid(db_session, factory):
    bond = factory(when=DAY - timedelta(days=1))
    add_market(db_session, bond, ytm=None)
    result = curve(db_session)
    assert result.status == "NO_FRESH_MARKET_DATA"
    assert result.diagnostics.excluded_missing_yield_count == 1


@pytest.mark.parametrize("age,threshold,included", [(7, 7, True), (8, 7, False),
    (0, 0, True), (1, 0, False)])
def test_curve_freshness_boundary(db_session, factory, age, threshold, included):
    factory(when=DAY - timedelta(days=age))
    result = curve(db_session, max_curve_age_days=threshold)
    assert result.node_count == int(included)
    assert result.diagnostics.excluded_stale_count == int(not included)
    assert_counts(result)


def test_single_date_and_latest_valid_fresh_date(db_session, factory):
    factory(duration="1", when=DAY - timedelta(days=1))
    newest = factory(duration="2")
    factory(duration="3", ytm=None)
    result = curve(db_session)
    assert result.curve_trade_date == DAY
    assert result.status == "INSUFFICIENT_DISTINCT_DURATIONS"
    assert result.nodes[0].component_bond_ids == [newest.id]
    assert result.diagnostics.excluded_curve_date_mismatch_count == 1
    assert_counts(result)


def test_invalid_newer_date_does_not_set_curve_date(db_session, factory):
    factory(duration="1", when=DAY - timedelta(days=1))
    factory(duration="3", when=DAY - timedelta(days=1))
    factory(duration="5", ytm=None)
    result = curve(db_session)
    assert result.status == "READY"
    assert result.curve_trade_date == DAY - timedelta(days=1)


@pytest.mark.parametrize("ytm,duration,reason", [(None, "2", "missing_yield"),
    ("12", None, "missing_duration"), ("12", "0", "nonpositive_duration"),
    ("12", "-2", "nonpositive_duration"), (None, None, "missing_yield")])
def test_invalid_numbers_exclusive_diagnostics(db_session, factory, ytm, duration, reason):
    factory(ytm=ytm, duration=duration)
    result = curve(db_session)
    assert result.status == "NO_FRESH_MARKET_DATA"
    assert getattr(result.diagnostics, f"excluded_{reason}_count") == 1
    assert_counts(result)


@pytest.mark.parametrize("raw", [D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"),
    1.0, True, "12", None])
def test_numeric_fail_closed(raw):
    assert _finite(raw) is None


@pytest.mark.parametrize("column,reason", [("yield_to_maturity", "invalid_yield"),
    ("duration_years", "invalid_duration")])
@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity"])
def test_curve_nonfinite_source_diagnostics(db_session, factory, monkeypatch, column, reason, number):
    # SQLite cannot preserve PostgreSQL Numeric NaN. Substitute only the narrow
    # query result, keeping actual candidate/profile selection and SQL execution.
    factory()
    original = db_session.execute
    def execute(statement, *args, **kwargs):
        result = original(statement, *args, **kwargs)
        if len(statement.column_descriptions) == 4:
            row = result.first()
            values = dict(row._mapping)
            values[column] = D(number)
            return SimpleNamespace(first=lambda: SimpleNamespace(**values))
        return result
    monkeypatch.setattr(db_session, "execute", execute)
    result = curve(db_session)
    assert result.status == "NO_FRESH_MARKET_DATA"
    assert getattr(result.diagnostics, f"excluded_{reason}_count") == 1
    assert_counts(result)


@pytest.mark.parametrize("field,status", [("yield_to_maturity_pct", "TARGET_YIELD_MISSING"),
    ("duration_years", "TARGET_DURATION_MISSING")])
def test_target_nonfinite_defensive_gate(db_session, factory, ready, monkeypatch, field, status):
    target = factory(name="Corporate")
    original = BondMarketFeatureService.build_for_bond
    def build(self, *args, **kwargs):
        return original(self, *args, **kwargs).model_copy(update={field: D("NaN")})
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", build)
    result = evaluate(db_session, target)
    assert_unavailable(result, status)


def test_target_corrupt_input_rejected_by_task267_schema(db_session, factory, ready):
    target = factory(name="Corporate")
    market = db_session.query(BondMarketSnapshot).filter_by(bond_id=target.id).one()
    market.yield_to_maturity = D("NaN")
    # Identity-map corruption never reaches a spread; do not persist NaN to SQLite.
    with pytest.raises(ValidationError):
        evaluate(db_session, target)


def test_interpolation_local_decimal_context(db_session, factory):
    factory(duration="1", ytm="12")
    factory(duration="4", ytm="13")
    target = factory(name="Corporate", duration="2", ytm="15")
    expected = evaluate(db_session, target)
    assert expected.reference_ofz_yield_pct == D("12.33333333333333333333333333")
    with localcontext() as context:
        context.prec = 2
        assert evaluate(db_session, target).model_dump_json() == expected.model_dump_json()


@pytest.mark.parametrize("values,expected", [(["15", "11", "13"], "13"),
    (["12", "13"], "12.5"), (["18", "12", "13", "14"], "13.5")])
def test_duplicate_median_and_all_provenance(db_session, factory, values, expected):
    bonds = [factory(duration="2", ytm=y) for y in values]
    factory(duration="4", ytm="14")
    result = curve(db_session)
    node = result.nodes[0]
    assert node.duration_years == D("2")
    assert node.yield_to_maturity_pct == D(expected)
    assert node.aggregation_method == "MEDIAN"
    assert node.component_bond_ids == [b.id for b in bonds]
    assert node.component_yields_pct == [D(y) for y in values]
    assert len(node.component_snapshot_ids) == len(values)
    assert node.component_secids == [b.secid for b in bonds]
    assert node.component_isins == [None] * len(values)
    assert_counts(result)
    target = factory(name="Corporate", duration="2", ytm="15")
    spread = evaluate(db_session, target)
    assert "CURVE_DUPLICATE_DURATION_AGGREGATED" in spread.quality_flags
    assert spread.provenance.lower_component_snapshot_ids == node.component_snapshot_ids
    assert spread.provenance.upper_component_snapshot_ids == node.component_snapshot_ids


def test_sorted_nodes_deterministic_serialization_and_context(db_session, factory):
    for duration, ytm in [("4", "14"), ("2", "12"), ("2", "13")]:
        factory(duration=duration, ytm=ytm)
    expected = curve(db_session)
    assert [n.duration_years for n in expected.nodes] == [D("2"), D("4")]
    with localcontext() as context:
        context.prec = 2
        assert curve(db_session).model_dump_json() == expected.model_dump_json()
    assert curve(db_session).model_dump_json() == expected.model_dump_json()
    assert OfzReferenceCurveView.model_validate_json(expected.model_dump_json()) == expected


def test_zero_candidates_and_missing_snapshot(db_session, factory):
    empty = curve(db_session)
    assert empty.status == "NO_ELIGIBLE_OFZ"
    assert empty.node_count == 0 and empty.curve_trade_date is None
    assert empty.min_duration_years is None and empty.max_duration_years is None
    factory(market=False)
    absent = curve(db_session)
    assert absent.status == "NO_FRESH_MARKET_DATA"
    assert absent.diagnostics.excluded_missing_snapshot_count == 1
    assert_counts(absent)


@pytest.mark.parametrize("duration,reference,method", [("2", "12", "EXACT_NODE"),
    ("4", "14", "EXACT_NODE"), ("3", "13", "LINEAR_INTERPOLATION")])
def test_spread_arithmetic_and_provenance(db_session, factory, ready, duration, reference, method):
    target = factory(name="Corporate", profile=False, duration=duration, ytm="15")
    target.yield_to_maturity = D("80")
    target.duration_years = D("80")
    db_session.commit()
    result = evaluate(db_session, target)
    assert result.status == "READY"
    assert result.reference_ofz_yield_pct == D(reference)
    assert result.spread_to_ofz_pp == D("15") - D(reference)
    assert result.spread_to_ofz_bps == (D("15") - D(reference)) * D("100")
    assert type(result.spread_to_ofz_pp) is Decimal
    assert result.interpolation_method == method
    assert result.provenance.target_market_snapshot_id is not None
    assert result.provenance.target_market_trade_date == result.provenance.curve_trade_date == DAY
    assert result.provenance.lower_component_snapshot_ids
    assert result.provenance.upper_component_snapshot_ids
    if method == "EXACT_NODE":
        assert result.provenance.lower_curve_duration_years == result.provenance.upper_curve_duration_years
    else:
        assert result.provenance.lower_curve_duration_years == D("2")
        assert result.provenance.upper_curve_duration_years == D("4")
    assert BondRelativeValueView.model_validate_json(result.model_dump_json()) == result
    assert not result.pit_ready and not result.curve.pit_ready


@pytest.mark.parametrize("yield_value,pp", [("11", "-2"), ("13", "0"), ("15.4", "2.4")])
def test_signed_spreads_no_semantic_promotion(db_session, factory, ready, yield_value, pp):
    result = evaluate(db_session, factory(name="Corporate", duration="3", ytm=yield_value))
    assert result.spread_to_ofz_pp == D(pp)
    assert result.spread_to_ofz_bps == D(pp) * D("100")
    assert result.quality_flags == []


def test_negative_ofz_yield_allowed(db_session, factory):
    factory(duration="2", ytm="-2")
    factory(duration="4", ytm="-1")
    result = evaluate(db_session, factory(name="Corporate", duration="3", ytm="0"))
    assert result.reference_ofz_yield_pct == D("-1.5")
    assert result.spread_to_ofz_bps == D("150")


@pytest.mark.parametrize("duration", ["1", "5"])
def test_no_extrapolation(db_session, factory, ready, duration):
    assert_unavailable(evaluate(db_session, factory(name="Corporate", duration=duration)),
                       "TARGET_DURATION_OUTSIDE_CURVE")


@pytest.mark.parametrize("args,status", [({"market": False}, "TARGET_MARKET_MISSING"),
    ({"when": DAY - timedelta(days=8)}, "TARGET_MARKET_STALE"),
    ({"ytm": None}, "TARGET_YIELD_MISSING"),
    ({"duration": None}, "TARGET_DURATION_MISSING"),
    ({"duration": "0"}, "TARGET_DURATION_MISSING"),
    ({"duration": "-1"}, "TARGET_DURATION_MISSING"),
    ({"when": DAY - timedelta(days=1)}, "TARGET_CURVE_DATE_MISMATCH")])
def test_target_unavailable_states(db_session, factory, ready, args, status):
    target = factory(name="Corporate", **args)
    result = evaluate(db_session, target)
    assert_unavailable(result, status)
    assert result.model_dump_json() == evaluate(db_session, target).model_dump_json()
    if args.get("duration") in ("0", "-1"):
        assert "TARGET_DURATION_NONPOSITIVE" in result.quality_flags


def test_curve_unavailable_and_gate_priority_all_flags(db_session, factory):
    target = factory(name="Corporate", ytm=None, duration="0", when=DAY - timedelta(days=8))
    result = evaluate(db_session, target)
    assert_unavailable(result, "TARGET_MARKET_STALE")
    assert set(result.quality_flags) == {"TARGET_MARKET_STALE", "TARGET_YIELD_MISSING",
        "TARGET_DURATION_MISSING", "TARGET_DURATION_NONPOSITIVE", "CURVE_NOT_READY",
        "CURVE_NO_ELIGIBLE_OFZ"}
    target2 = factory(name="Corporate")
    assert_unavailable(evaluate(db_session, target2), "CURVE_NOT_READY")


def test_target_task267_reused_source_future_and_config(db_session, factory, ready, monkeypatch):
    target = factory(name="Corporate", duration="3", ytm="15")
    manual = add_market(db_session, target, source="manual", duration="3", ytm="88")
    add_market(db_session, target, when=DAY + timedelta(days=1), duration="3", ytm="99")
    original = BondMarketFeatureService.build_for_bond
    calls = []
    def spy(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", spy)
    result = evaluate(db_session, target, max_market_age_days=3, max_curve_age_days=2)
    assert result.spread_to_ofz_pp == D("2")
    assert calls == [((target.id, DAY), {"market_source": "moex", "max_market_age_days": 3})]
    manual_result = evaluate(db_session, target, market_source="manual")
    assert manual_result.provenance.target_market_snapshot_id == manual.id
    assert manual_result.target_yield_to_maturity_pct == D("88")
    assert_unavailable(manual_result, "CURVE_NOT_READY")


def test_no_mutation_select_only_and_preserved_pending_state(db_session, factory, ready, monkeypatch):
    target = factory(name="Corporate", duration="3", ytm="15")
    target_id = target.id
    deletion = Company(name="Caller-owned deletion", ticker="DELETE268")
    db_session.add(deletion)
    db_session.commit()
    db_session.refresh(target)
    pending = Company(name="Caller-owned pending insert", ticker="PENDING268")
    db_session.add(pending)
    db_session.delete(deletion)
    target.name = "Caller-owned pending edit"
    db_session.autoflush = True
    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        raise AssertionError("Service must not mutate or flush")
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            for operation in ("flush", "commit", "add", "add_all", "delete"):
                patch.setattr(db_session, operation, forbidden)
            assert curve(db_session).status == "READY"
            result = OfzReferenceCurveService(db_session).evaluate_bond(target_id, DAY)
            assert result.spread_to_ofz_pp == D("2")
        assert before == (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
        assert pending.id is None
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
        db_session.rollback()


def test_clean_session_and_legacy_spread_unchanged(db_session, factory, ready):
    target = factory(name="Corporate", duration="3", ytm="15")
    before = db_session.query(BondMarketSnapshot).all()
    stored = [(r.id, r.spread_to_ofz) for r in before]
    curve(db_session)
    assert evaluate(db_session, target).spread_to_ofz_pp == D("2")
    assert not db_session.new and not db_session.dirty and not db_session.deleted
    db_session.expire_all()
    assert [(r.id, r.spread_to_ofz) for r in db_session.query(BondMarketSnapshot).all()] == stored


@pytest.mark.parametrize("kwargs", [{"as_of_date": datetime(2026, 9, 17)},
    {"as_of_date": "2026-09-17"}, {"market_source": ""}, {"market_source": " "},
    {"market_source": None}, {"max_curve_age_days": True}, {"max_curve_age_days": -1},
    {"max_curve_age_days": 1.0}])
def test_curve_invalid_arguments(db_session, kwargs):
    with pytest.raises(ValueError):
        OfzReferenceCurveService(db_session).build_curve(**{"as_of_date": DAY, **kwargs})


@pytest.mark.parametrize("kwargs", [{"bond_id": True}, {"bond_id": 0}, {"bond_id": -1},
    {"bond_id": 1.0}, {"as_of_date": datetime(2026, 9, 17)}, {"market_source": " "},
    {"max_market_age_days": True}, {"max_market_age_days": -1},
    {"max_market_age_days": 1.0}, {"max_curve_age_days": True}, {"max_curve_age_days": -1}])
def test_target_invalid_arguments(db_session, kwargs):
    with pytest.raises(ValueError):
        OfzReferenceCurveService(db_session).evaluate_bond(**{"bond_id": 1, "as_of_date": DAY, **kwargs})


def test_missing_bond_preserves_404(db_session):
    with pytest.raises(HTTPException) as exc:
        OfzReferenceCurveService(db_session).evaluate_bond(99999, DAY)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Bond not found"


def test_contracts_frozen_extra_forbidden_pit_false(db_session, factory, ready):
    result = evaluate(db_session, factory(name="Corporate"))
    for view in (result, result.curve):
        assert view.pit_ready is False
        with pytest.raises(ValidationError):
            view.pit_ready = True
        with pytest.raises(ValidationError):
            type(view).model_validate({**view.model_dump(), "pit_ready": True})
        with pytest.raises(ValidationError):
            type(view).model_validate({**view.model_dump(), "unexpected": 1})
    assert result.contract_version == "bond-relative-value-v1"
    assert result.curve.contract_version == "ofz-reference-curve-v1"


def test_production_surface_no_writes_network_or_prohibited_fields(db_session, factory, ready):
    import app.services.ofz_reference_curve_service as module
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    prohibited_calls = {"commit", "flush", "add", "add_all", "delete", "update", "insert"}
    db_calls = {node.func.attr for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and ast.unparse(node.func.value) == "self.db"}
    assert not db_calls & prohibited_calls
    names = {node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not names & {"insert", "update", "delete", "float"}
    imports = " ".join(ast.unparse(n) for n in ast.walk(tree)
                       if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert not any(name in imports for name in ("requests", "httpx", "urllib", "clients", "subprocess"))
    assert "spread_to_ofz" not in {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    forbidden_fields = {"buy", "sell", "hold", "best", "worst", "attractive", "unattractive",
        "undervalued", "overvalued", "expected_return", "alpha", "signal", "rank", "score",
        "recommendation", "portfolio_weight"}
    result = evaluate(db_session, factory(name="Corporate"))
    def check(value):
        if isinstance(value, dict):
            assert not forbidden_fields & value.keys()
            for member in value.values():
                check(member)
        elif isinstance(value, list):
            for member in value:
                check(member)
    check(result.model_dump())
