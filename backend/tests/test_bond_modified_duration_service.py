"""Task272 acceptance on the existing disposable SQLite fixture."""

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
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.schemas.bond_modified_duration import BondModifiedDurationView
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.bond_modified_duration_service import BondModifiedDurationService

DAY = date(2026, 9, 17)
D = Decimal


@pytest.fixture
def seed(db_session):
    company = Company(name="Task272 issuer", ticker="TASK272")
    db_session.add(company)
    db_session.flush()
    bond = Bond(company_id=company.id, name="Modified duration bond", isin="RU000A123472", secid="TASK272",
                duration_years=D(99), yield_to_maturity=D(99), coupon_rate=D(99),
                maturity_date=DAY + timedelta(days=365), liquidity_score=99)
    db_session.add(bond)
    db_session.commit()
    def profile(**fields):
        values = dict(coupon_structure="fixed", perpetual_structure="dated",
                      coupon_frequency_state="verified", coupon_frequency_per_year=1)
        values.update(fields)
        row = BondSecurityMasterProfile(bond_id=bond.id, **values)
        db_session.add(row)
        db_session.commit()
        return row
    def market(*, days=730, ytm="10", stored="2", age=0, source="moex", payload=None):
        raw = payload if payload is not None else {"moex": {"DURATION": days}}
        row = BondMarketSnapshot(bond_id=bond.id, trade_date=DAY - timedelta(days=age), source=source,
                                 duration_years=D(stored) if stored is not None else None,
                                 yield_to_maturity=D(ytm) if ytm is not None else None,
                                 raw_payload=raw, price=D(100), liquidity_score=88, spread_to_ofz=D(77))
        db_session.add(row)
        db_session.commit()
        return row
    return SimpleNamespace(bond=bond, company=company, profile=profile, market=market)


def build(db, seed, **kwargs):
    return BondModifiedDurationService(db).build_for_bond(seed.bond.id, DAY, **kwargs)


def unavailable(result, status):
    assert result.status == status
    assert result.modified_duration_years is None
    assert not result.availability.has_modified_duration
    assert result.capabilities.modified_duration_ready is True
    assert result.pit_ready is False


@pytest.mark.parametrize("frequency", [1, 2, 4, 12, 7, 100])
@pytest.mark.parametrize("ytm", ["10", "0", "-5", "12.5"])
def test_formula_percentage_conversion_and_arbitrary_frequency(db_session, seed, frequency, ytm):
    profile = seed.profile(coupon_frequency_per_year=frequency)
    snapshot = seed.market(ytm=ytm)
    result = build(db_session, seed)
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        converted = D(ytm) / D(100)
        denominator = D(1) + converted / D(frequency)
        assert result.modified_duration_years == D(2) / denominator
    assert result.status == "READY"
    assert result.yield_decimal == converted and result.modified_duration_denominator == denominator
    assert result.coupon_frequency_per_year == frequency
    assert ("NEGATIVE_YIELD" in result.quality_flags) is (D(ytm) < 0)
    assert type(result.modified_duration_years) is Decimal
    assert all(result.availability.model_dump().values())
    assert result.provenance.security_master_profile_id == profile.id
    assert result.provenance.security_master_contract_version == profile.contract_version
    assert result.provenance.market_snapshot_id == snapshot.id
    assert result.provenance.formula_version == "modified-duration-v1"


@pytest.mark.parametrize("ytm,frequency", [("-100", 1), ("-200", 2), ("-101", 1), ("-201", 2)])
def test_nonpositive_denominator_fail_closed(db_session, seed, ytm, frequency):
    seed.profile(coupon_frequency_per_year=frequency)
    seed.market(ytm=ytm)
    result = build(db_session, seed)
    unavailable(result, "INVALID_MODIFIED_DURATION_DENOMINATOR")
    assert result.modified_duration_denominator <= 0
    assert result.yield_decimal == D(ytm) / D(100)
    assert "NEGATIVE_YIELD" in result.quality_flags
    assert not result.availability.has_valid_denominator


def test_zero_duration_valid_and_short_raw_duration_repair_inherited(db_session, seed):
    seed.profile()
    row = seed.market(days=0, stored="99")
    result = build(db_session, seed)
    assert result.status == "READY" and result.modified_duration_years == 0
    assert result.availability.has_macaulay_duration
    assert "STORED_DURATION_MISMATCH" in result.quality_flags
    row.raw_payload = {"moex": {"DURATION": 30}}
    row.duration_years = D(30)
    db_session.commit()
    result = build(db_session, seed)
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        assert result.modified_duration_years == (D(30)/D(365))/D("1.1")
    assert result.macaulay_duration_years == D(30)/D(365)
    assert row.duration_years == 30


@pytest.mark.parametrize("age,threshold,status", [(0, 0, "READY"), (7, 7, "READY"),
    (8, 7, "MARKET_DATA_STALE"), (1, 0, "MARKET_DATA_STALE")])
def test_freshness_boundary_and_stale_diagnostics(db_session, seed, age, threshold, status):
    seed.profile()
    row = seed.market(age=age)
    result = build(db_session, seed, max_market_age_days=threshold)
    assert result.status == status and result.market_age_days == age
    assert result.market_snapshot_id == row.id
    assert result.macaulay_duration_years == 2 and result.yield_decimal == D("0.1")
    assert result.modified_duration_denominator == D("1.1")
    assert result.availability.has_valid_denominator
    if status != "READY":
        unavailable(result, status)


def test_missing_market_no_legacy_rescue(db_session, seed):
    seed.profile()
    seed.market(age=-1)
    result = build(db_session, seed)
    unavailable(result, "MARKET_DATA_MISSING")
    assert result.market_snapshot_id is None
    assert result.macaulay_duration_years is result.yield_to_maturity_pct is result.yield_decimal is None
    assert result.modified_duration_denominator is None


@pytest.mark.parametrize("payload,flag", [({}, "DURATION_RAW_MISSING"),
    ({"moex": {"DURATION": "NaN"}}, "MALFORMED_RAW_DURATION"),
    ({"moex": {"DURATION": 30}, "canonical": {"duration": 50}}, "CONFLICTING_RAW_DURATION")])
def test_task271_unavailable_raw_never_bypassed(db_session, seed, payload, flag):
    seed.profile()
    row = seed.market(stored="30", payload=payload)
    result = build(db_session, seed)
    unavailable(result, "MACAULAY_DURATION_MISSING")
    assert flag in result.quality_flags and "DURATION_MISSING" in result.quality_flags
    assert row.duration_years == 30


def test_missing_yield_preserved(db_session, seed):
    seed.profile()
    seed.market(ytm=None)
    result = build(db_session, seed)
    unavailable(result, "YIELD_TO_MATURITY_MISSING")
    assert result.macaulay_duration_years == 2
    assert result.yield_decimal is result.modified_duration_denominator is None


@pytest.mark.parametrize("field,status", [("yield_to_maturity_pct", "YIELD_TO_MATURITY_INVALID"),
                                        ("duration_years", "MACAULAY_DURATION_INVALID")])
@pytest.mark.parametrize("bad", [D("NaN"), D("sNaN"), D("Infinity"), D("-Infinity"), True, 2.0, "2"])
def test_synthetic_invalid_market_numbers_fail_closed(db_session, seed, monkeypatch, field, status, bad):
    seed.profile()
    seed.market()
    original = BondMarketFeatureService.build_for_bond
    def synthetic(self, *args, **kwargs):
        return original(self, *args, **kwargs).model_copy(update={field: bad})
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", synthetic)
    result = build(db_session, seed)
    unavailable(result, status)
    assert result.yield_to_maturity_pct is None if field == "yield_to_maturity_pct" else result.macaulay_duration_years is None
    assert BondModifiedDurationView.model_validate_json(result.model_dump_json()) == result


def test_negative_duration_defensive_gate_and_manual_source(db_session, seed):
    seed.profile()
    row = seed.market(source="manual", stored="-2", payload={"moex": {"DURATION": 730}})
    result = build(db_session, seed, market_source="manual")
    unavailable(result, "MACAULAY_DURATION_INVALID")
    assert result.macaulay_duration_years == -2
    row.duration_years = D(2)
    db_session.commit()
    result = build(db_session, seed, market_source="manual")
    assert result.status == "READY" and result.macaulay_duration_years == 2


@pytest.mark.parametrize("changes,status,flag", [
    ({"coupon_structure": "floating"}, "COUPON_STRUCTURE_NOT_FIXED", None),
    ({"coupon_structure": "unknown"}, "COUPON_STRUCTURE_NOT_FIXED", "COUPON_STRUCTURE_UNKNOWN"),
    ({"coupon_structure": "conflict"}, "COUPON_STRUCTURE_NOT_FIXED", "COUPON_STRUCTURE_CONFLICT"),
    ({"coupon_frequency_state": "unknown", "coupon_frequency_per_year": None}, "COUPON_FREQUENCY_NOT_VERIFIED", "COUPON_FREQUENCY_MISSING"),
    ({"coupon_frequency_state": "conflict", "coupon_frequency_per_year": None}, "COUPON_FREQUENCY_NOT_VERIFIED", "COUPON_FREQUENCY_MISSING"),
    ({"perpetual_structure": "perpetual"}, "PERPETUAL_STRUCTURE_NOT_DATED", None),
    ({"perpetual_structure": "unknown"}, "PERPETUAL_STRUCTURE_NOT_DATED", None),
    ({"perpetual_structure": "conflict"}, "PERPETUAL_STRUCTURE_NOT_DATED", None),
])
def test_security_master_structural_gates(db_session, seed, changes, status, flag):
    seed.profile(**changes)
    seed.market()
    result = build(db_session, seed)
    unavailable(result, status)
    if flag:
        assert flag in result.quality_flags
    assert result.yield_decimal == D("0.1")


def test_exact_profile_join_missing_profile_and_no_frequency_inference(db_session, seed):
    unrelated = Bond(company_id=seed.company.id, name="Unrelated canonical terms")
    db_session.add(unrelated)
    db_session.flush()
    db_session.add(BondSecurityMasterProfile(bond_id=unrelated.id, coupon_structure="fixed",
        coupon_frequency_state="verified", coupon_frequency_per_year=2, perpetual_structure="dated"))
    for days in (30, 60, 90):
        db_session.add(BondCashflowEvent(bond_id=seed.bond.id, source="moex", event_type="coupon",
                                        event_date=DAY + timedelta(days=days)))
    db_session.commit()
    seed.market()
    result = build(db_session, seed)
    unavailable(result, "SECURITY_MASTER_MISSING")
    assert result.coupon_frequency_per_year is None
    assert result.provenance.security_master_profile_id is None
    assert result.modified_duration_denominator is None
    seed.profile(coupon_frequency_state="unknown", coupon_frequency_per_year=None)
    unavailable(build(db_session, seed), "COUPON_FREQUENCY_NOT_VERIFIED")


@pytest.mark.parametrize("bad,status", [(None, "COUPON_FREQUENCY_MISSING"),
    (0, "COUPON_FREQUENCY_INVALID"), (-1, "COUPON_FREQUENCY_INVALID"),
    (True, "COUPON_FREQUENCY_INVALID"), (2.0, "COUPON_FREQUENCY_INVALID"),
    (D(2), "COUPON_FREQUENCY_INVALID"), ("2", "COUPON_FREQUENCY_INVALID")])
def test_synthetic_frequency_validation_preserves_constraints(db_session, seed, monkeypatch, bad, status):
    seed.profile()
    seed.market()
    original = db_session.execute
    def synthetic(statement, *args, **kwargs):
        result = original(statement, *args, **kwargs)
        if "bond_security_master_profiles.coupon_frequency_per_year" in str(statement):
            row = dict(result.one()._mapping)
            row["coupon_frequency_per_year"] = bad
            return SimpleNamespace(one_or_none=lambda: SimpleNamespace(**row))
        return result
    monkeypatch.setattr(db_session, "execute", synthetic)
    result = build(db_session, seed)
    unavailable(result, status)
    assert result.modified_duration_denominator is None
    assert not result.availability.has_verified_coupon_frequency


@pytest.mark.parametrize("fields", [
    {"offer_structure": "present"}, {"amortization_structure": "amortizing"},
    {"subordination_structure": "subordinated"}, {"currency_state": "verified", "currency_code": "USD"},
    {"offer_structure": "present", "amortization_structure": "amortizing", "subordination_structure": "subordinated"},
])
def test_offer_amortization_subordination_and_currency_do_not_block(db_session, seed, fields):
    seed.profile(**fields)
    seed.market()
    seed.bond.currency = "USD"
    seed.bond.is_perpetual = seed.bond.is_floating_coupon = seed.bond.is_subordinated = True
    seed.bond.maturity_date = DAY - timedelta(days=100)
    db_session.commit()
    result = build(db_session, seed)
    assert result.status == "READY"
    assert result.modified_duration_years == D(2) / D("1.1")
    assert not set(result.quality_flags) & {"PERPETUAL_BOND", "FLOATING_COUPON", "MATURED_BOND"}


def test_status_precedence_all_flags_and_diagnostic_denominator(db_session, seed):
    seed.profile(coupon_structure="conflict", perpetual_structure="perpetual")
    seed.market(age=8, ytm="-100")
    result = build(db_session, seed)
    unavailable(result, "MARKET_DATA_STALE")
    assert {"MARKET_DATA_STALE", "COUPON_STRUCTURE_NOT_FIXED", "COUPON_STRUCTURE_CONFLICT",
            "PERPETUAL_STRUCTURE_NOT_DATED", "NEGATIVE_YIELD", "INVALID_MODIFIED_DURATION_DENOMINATOR"} <= set(result.quality_flags)
    assert result.modified_duration_denominator == 0
    # With stale gate removed, structural evidence still precedes denominator.
    unavailable(build(db_session, seed, max_market_age_days=8), "COUPON_STRUCTURE_NOT_FIXED")


def test_task267_called_exactly_and_input_view_unmodified(db_session, seed, monkeypatch):
    profile = seed.profile()
    seed.market(source="MOEX")
    original = BondMarketFeatureService.build_for_bond
    calls, views, copies = [], [], []
    def capture(self, *args, **kwargs):
        calls.append((args, kwargs))
        view = original(self, *args, **kwargs)
        views.append(view)
        copies.append(deepcopy(view.model_dump()))
        return view
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", capture)
    identity = seed.bond.id
    result = build(db_session, seed, market_source="MOEX", max_market_age_days=3)
    assert calls == [((identity, DAY), {"market_source": "MOEX", "max_market_age_days": 3})]
    assert views[0].model_dump() == copies[0]
    assert result.market_snapshot_id == views[0].market_snapshot_id
    assert result.market_trade_date == views[0].market_trade_date
    assert result.provenance.market_contract_version == views[0].contract_version
    assert result.provenance.security_master_profile_id == profile.id


def test_repeat_serialization_context_isolation_and_unavailable_stability(db_session, seed):
    seed.profile(coupon_frequency_per_year=7)
    seed.market(days=30, stored="30", payload={"moex": {"DURATION": 30},
        "mapping_notes": ["DURATION looked like days and was divided by 365"]})
    result = build(db_session, seed)
    with localcontext(Context(prec=3, rounding=ROUND_DOWN)) as context:
        repeated = build(db_session, seed)
        assert context.prec == 3 and context.rounding == ROUND_DOWN
    assert result.model_dump_json() == repeated.model_dump_json()
    assert "DURATION_LEGACY_DAY_NORMALIZATION" in result.quality_flags
    assert result.quality_flags == sorted(set(result.quality_flags))
    stale = BondModifiedDurationService(db_session).build_for_bond(seed.bond.id, DAY + timedelta(days=8))
    unavailable(stale, "MARKET_DATA_STALE")
    assert BondModifiedDurationView.model_validate_json(stale.model_dump_json()) == stale


@pytest.mark.parametrize("pending", [False, True])
def test_select_only_no_persistence_and_preserved_pending_state(db_session, seed, monkeypatch, pending):
    profile = seed.profile()
    snapshot = seed.market(days=30, stored="30")
    doomed = Company(name="Caller deletion", ticker="DELETE272")
    db_session.add(doomed)
    db_session.commit()
    identity = seed.bond.id
    stored = deepcopy((snapshot.raw_payload, snapshot.duration_years, snapshot.liquidity_score,
                       snapshot.spread_to_ofz, seed.bond.duration_years, seed.bond.yield_to_maturity,
                       seed.bond.liquidity_score, profile.coupon_frequency_per_year))
    if pending:
        db_session.add(Company(name="Caller pending", ticker="PENDING272"))
        seed.bond.name = "Caller edit"
        profile.coupon_frequency_per_year = 99
        db_session.delete(doomed)
    state = tuple(set(rows) for rows in (db_session.new, db_session.dirty, db_session.deleted))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        pytest.fail("Read-time service attempted session mutation")
    db_session.autoflush = True
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            for name in ("add", "add_all", "flush", "commit", "delete"):
                patch.setattr(db_session, name, forbidden)
            result = BondModifiedDurationService(db_session).build_for_bond(identity, DAY)
        assert result.status == "READY"
        assert result.coupon_frequency_per_year == 1
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert tuple(set(rows) for rows in (db_session.new, db_session.dirty, db_session.deleted)) == state
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert (snapshot.raw_payload, snapshot.duration_years, snapshot.liquidity_score,
            snapshot.spread_to_ofz, seed.bond.duration_years, seed.bond.yield_to_maturity,
            seed.bond.liquidity_score) == stored[:-1]
    assert profile.coupon_frequency_per_year == (99 if pending else stored[-1])


@pytest.mark.parametrize("changes", [
    {"bond_id": True}, {"bond_id": 0}, {"bond_id": -1}, {"bond_id": 1.0}, {"bond_id": "1"},
    {"as_of_date": datetime(2026, 9, 17)}, {"as_of_date": "2026-09-17"}, {"as_of_date": None},
    {"market_source": ""}, {"market_source": " "}, {"market_source": None}, {"market_source": 1},
    {"max_market_age_days": True}, {"max_market_age_days": -1}, {"max_market_age_days": 1.0},
    {"max_market_age_days": "7"},
])
def test_invalid_arguments_rejected_before_market_calls(db_session, seed, monkeypatch, changes):
    args = {"bond_id": seed.bond.id, "as_of_date": DAY, **changes}
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments reached market service")
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", forbidden)
    with pytest.raises(ValueError):
        BondModifiedDurationService(db_session).build_for_bond(**args)


def test_missing_bond_preserves_task267_http_404(db_session):
    with pytest.raises(HTTPException) as error:
        BondModifiedDurationService(db_session).build_for_bond(999999, DAY)
    assert error.value.status_code == 404 and error.value.detail == "Bond not found"


def test_frozen_forbidden_extra_capabilities_and_no_feature_labels(db_session, seed):
    seed.profile()
    seed.market()
    result = build(db_session, seed)
    assert result.contract_version == "bond-modified-duration-v1" and result.pit_ready is False
    for model in (result, result.availability, result.provenance, result.capabilities):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unknown": 1})
    with pytest.raises(ValidationError):
        BondModifiedDurationView.model_validate({**result.model_dump(), "pit_ready": True})
    for name, value in result.capabilities.model_dump().items():
        assert value is (name in {"macaulay_duration_input_ready", "modified_duration_ready"})
    forbidden = {"dv01", "convexity", "price_shock", "recommendation", "score", "portfolio_weight", "signal", "label"}
    assert not forbidden & set(result.model_dump())


def test_narrow_static_safety_no_legacy_or_reparsed_duration():
    path = Path(__file__).parents[1] / "app/services/bond_modified_duration_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = " ".join(ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert not any(word in imports for word in ("moex_iss", "moex_duration", "bond_cashflow", "bond_market_snapshot",
        "broker", "strategy", "risk_engine", "requests", "httpx", "urllib", "models.bond import"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and ast.unparse(node.func.value) == "self.db":
                assert node.func.attr not in {"add", "add_all", "flush", "commit", "delete"}
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"float", "insert", "update", "delete", "create_engine"}
        assert not isinstance(node, ast.Constant) or not isinstance(node.value, float)
