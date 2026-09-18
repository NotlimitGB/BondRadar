"""Task273 A–AP acceptance using the disposable SQLite fixture and synthetic read results."""

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
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.company import Company
from app.schemas.bond_dv01 import BondDv01View
from app.services.bond_dv01_service import BondDv01Service, ONE_BP
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.bond_modified_duration_service import BondModifiedDurationService

DAY = date(2026, 9, 18)
D = Decimal


@pytest.fixture
def seed(db_session):
    company = Company(name="Task273 issuer", ticker="TASK273")
    db_session.add(company)
    db_session.flush()
    bond = Bond(company_id=company.id, name="DV01 bond", isin="RU000A123473", secid="TASK273",
                nominal_value=D(9999), currency="USD", current_price=D(777),
                duration_years=D(99), yield_to_maturity=D(88), liquidity_score=77)
    db_session.add(bond)
    db_session.commit()
    def profile(**fields):
        values = dict(coupon_structure="fixed", perpetual_structure="dated",
                      coupon_frequency_state="verified", coupon_frequency_per_year=2,
                      currency_state="verified", currency_code="RUB",
                      nominal_state="verified", nominal_value=D(1000),
                      outstanding_nominal_state="verified", outstanding_nominal=D(900))
        values.update(fields)
        row = BondSecurityMasterProfile(bond_id=bond.id, **values)
        db_session.add(row)
        db_session.commit()
        return row
    def market(*, days=730, ytm="0", age=0, source="moex", **fields):
        values = dict(raw_payload={"moex": {"DURATION": days}}, duration_years=D(2),
                      yield_to_maturity=D(ytm) if ytm is not None else None,
                      clean_price=D(100), price=D(95), nkd=D(10), dirty_price=D(5555),
                      liquidity_score=88, spread_to_ofz=D(66))
        values.update(fields)
        row = BondMarketSnapshot(bond_id=bond.id, trade_date=DAY - timedelta(days=age),
                                 source=source, **values)
        db_session.add(row)
        db_session.commit()
        return row
    return SimpleNamespace(bond=bond, company=company, profile=profile, market=market)


@pytest.fixture
def ready(seed):
    return SimpleNamespace(seed=seed, profile=seed.profile(), snapshot=seed.market())


def build(db, seed, **kwargs):
    return BondDv01Service(db).build_for_bond(seed.bond.id, DAY, **kwargs)


def unavailable(result, status):
    assert result.status == status
    assert result.dv01_currency_per_bond is None and not result.availability.has_dv01
    assert result.capabilities.dv01_ready is True and result.pit_ready is False
    assert result.quality_flags == sorted(set(result.quality_flags))
    assert BondDv01View.model_validate_json(result.model_dump_json()) == result


def patch_market(monkeypatch, **fields):
    original = BondMarketFeatureService.build_for_bond
    def read(self, *args, **kwargs):
        return original(self, *args, **kwargs).model_copy(update=fields)
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", read)


def patch_profile(db, monkeypatch, profile, **fields):
    values = {key: getattr(profile, key) for key in (
        "id", "contract_version", "currency_state", "currency_code", "nominal_state", "nominal_value")}
    values.update(fields)
    original = db.execute
    def read(statement, *args, **kwargs):
        if "nominal_value" in statement.selected_columns.keys():
            return SimpleNamespace(one_or_none=lambda: SimpleNamespace(**values))
        return original(statement, *args, **kwargs)
    monkeypatch.setattr(db, "execute", read)


@pytest.mark.parametrize("quote,nkd,days", [
    ("100", "10", 730), ("80", "10", 730), ("120", "10", 730),
    ("100", "0", 730), ("100", "10", 0), ("87.654321", "12.345678", 30),
])
def test_financial_examples_decimal_magnitude_and_zero(db_session, seed, quote, nkd, days):
    seed.profile()
    seed.market(days=days, clean_price=D(quote), nkd=D(nkd))
    result = build(db_session, seed)
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        md = D(days) / D(365)
        clean = D(1000) * D(quote) / D(100)
        dirty = clean + D(nkd)
        assert result.modified_duration_years == md
        assert result.clean_value_currency == clean and result.dirty_value_currency == dirty
        assert result.relative_price_sensitivity_per_1bp == md * D("0.0001")
        assert result.dv01_currency_per_bond == dirty * md * D("0.0001")
    assert result.status == "READY" and result.dv01_currency_per_bond >= 0
    assert all(result.availability.model_dump().values())
    for name in ("modified_duration_years", "nominal_value", "clean_quote_pct", "nkd_currency",
                 "clean_value_currency", "dirty_value_currency", "relative_price_sensitivity_per_1bp",
                 "dv01_currency_per_bond"):
        assert type(getattr(result, name)) is Decimal
    if quote == "100" and nkd == "10" and days == 730:
        assert result.dv01_currency_per_bond == D("0.202")
        assert result.relative_price_sensitivity_per_1bp == D("0.0002")


@pytest.mark.parametrize("clean,price,basis,expected", [
    ("100", "95", "CLEAN_PRICE", "0.202"),
    ("100", None, "CLEAN_PRICE", "0.202"),
    (None, "95", "PRICE_FALLBACK", "0.192"),
])
def test_quote_priority_fallback_and_provenance(db_session, seed, clean, price, basis, expected):
    profile = seed.profile()
    snapshot = seed.market(clean_price=D(clean) if clean is not None else None,
                           price=D(price) if price is not None else None)
    result = build(db_session, seed)
    assert result.status == "READY" and result.dv01_currency_per_bond == D(expected)
    assert result.price_basis == result.provenance.price_basis == basis
    assert ("MARKET_PRICE_FALLBACK_USED" in result.quality_flags) is (basis == "PRICE_FALLBACK")
    p = result.provenance
    assert p.market_snapshot_id == snapshot.id and p.market_trade_date == DAY and p.market_source == "moex"
    assert p.market_identity == p.modified_duration_market_identity
    assert p.security_master_profile_id == profile.id
    assert p.security_master_contract_version == profile.contract_version
    assert p.nominal_state == p.currency_state == "verified"
    assert p.modified_duration_contract_version == "bond-modified-duration-v1"
    assert p.modified_duration_formula_version == "modified-duration-v1"
    assert p.market_contract_version == "bond-market-feature-v1" and p.dv01_formula_version == "dv01-v1"


@pytest.mark.parametrize("value", [D(0), D(-1), D("NaN"), D("Infinity"), D("-Infinity"), 100, 1.5, True, "100"])
@pytest.mark.parametrize("field,status", [("clean_price", "CLEAN_PRICE_INVALID"), ("price", "MARKET_PRICE_INVALID")])
def test_invalid_quote_no_hidden_fallback(db_session, ready, monkeypatch, field, status, value):
    patch_market(monkeypatch, **{field: value, **({"clean_price": None} if field == "price" else {})})
    result = build(db_session, ready.seed)
    unavailable(result, status)
    assert result.clean_value_currency is None and result.dirty_value_currency is None
    assert result.relative_price_sensitivity_per_1bp == D("0.0002")
    assert result.clean_quote_pct == (value if isinstance(value, Decimal) and value.is_finite() else None)


def test_both_quotes_missing_preserves_other_inputs(db_session, ready, monkeypatch):
    patch_market(monkeypatch, clean_price=None, price=None)
    result = build(db_session, ready.seed)
    unavailable(result, "MARKET_PRICE_MISSING")
    assert result.price_basis is None and result.clean_quote_pct is None
    assert result.nkd_currency == 10 and result.nominal_value == 1000
    assert result.relative_price_sensitivity_per_1bp == D("0.0002")


@pytest.mark.parametrize("value,status", [(None, "NKD_MISSING"), (D(-1), "NKD_INVALID"),
    (D("NaN"), "NKD_INVALID"), (D("Infinity"), "NKD_INVALID"), (0, "NKD_INVALID"),
    (True, "NKD_INVALID"), ("10", "NKD_INVALID")])
def test_nkd_missing_invalid_preserves_clean_value(db_session, ready, monkeypatch, value, status):
    patch_market(monkeypatch, nkd=value)
    result = build(db_session, ready.seed)
    unavailable(result, status)
    assert result.clean_value_currency == 1000 and result.dirty_value_currency is None
    assert result.relative_price_sensitivity_per_1bp == D("0.0002")
    assert not result.availability.has_nkd


@pytest.mark.parametrize("state", ["unknown", "conflict"])
@pytest.mark.parametrize("field,status", [("currency", "CURRENCY_NOT_VERIFIED"), ("nominal", "NOMINAL_NOT_VERIFIED")])
def test_unverified_canonical_fields_no_legacy_substitution(db_session, seed, state, field, status):
    seed.profile(**{field + "_state": state, "currency_code" if field == "currency" else "nominal_value": None})
    seed.market()
    result = build(db_session, seed)
    unavailable(result, status)
    assert result.relative_price_sensitivity_per_1bp == D("0.0002")
    assert result.clean_value_currency is result.dirty_value_currency is None


@pytest.mark.parametrize("code,status", [("USD", "UNSUPPORTED_CURRENCY"), ("EUR", "UNSUPPORTED_CURRENCY"),
    ("rub", "UNSUPPORTED_CURRENCY"),
    (None, "CURRENCY_MISSING")])
def test_verified_currency_scope_and_missing_synthetic(db_session, ready, monkeypatch, code, status):
    patch_profile(db_session, monkeypatch, ready.profile, currency_code=code)
    result = build(db_session, ready.seed)
    unavailable(result, status)
    assert not result.availability.is_supported_currency and result.clean_value_currency is None


@pytest.mark.parametrize("value,status", [(None, "NOMINAL_MISSING"), (D(0), "NOMINAL_INVALID"),
    (D(-1), "NOMINAL_INVALID"), (D("NaN"), "NOMINAL_INVALID"), (D("Infinity"), "NOMINAL_INVALID"),
    (1000, "NOMINAL_INVALID"), (1000.0, "NOMINAL_INVALID"), (True, "NOMINAL_INVALID"), ("1000", "NOMINAL_INVALID")])
def test_nominal_invalid_synthetic_no_constraint_changes(db_session, ready, monkeypatch, value, status):
    patch_profile(db_session, monkeypatch, ready.profile, nominal_value=value)
    result = build(db_session, ready.seed)
    unavailable(result, status)
    assert result.nominal_value == (value if isinstance(value, Decimal) and value.is_finite() else None)
    assert not result.availability.has_verified_nominal


def test_exact_security_master_join_unrelated_profile_cannot_fill_gap(db_session, seed):
    other = Bond(company_id=seed.company.id, name="Unrelated bond")
    db_session.add(other)
    db_session.flush()
    db_session.add(BondSecurityMasterProfile(bond_id=other.id, currency_state="verified", currency_code="RUB",
                                           nominal_state="verified", nominal_value=D(1000)))
    db_session.commit()
    seed.market()
    result = build(db_session, seed)
    unavailable(result, "MODIFIED_DURATION_UNAVAILABLE")
    assert "SECURITY_MASTER_MISSING" in result.quality_flags
    assert result.provenance.security_master_profile_id is None and not result.availability.has_security_master


def test_security_master_missing_after_ready_md(db_session, ready, monkeypatch):
    original = db_session.execute
    def read(statement, *args, **kwargs):
        if "nominal_value" in statement.selected_columns.keys():
            return SimpleNamespace(one_or_none=lambda: None)
        return original(statement, *args, **kwargs)
    monkeypatch.setattr(db_session, "execute", read)
    result = build(db_session, ready.seed)
    unavailable(result, "SECURITY_MASTER_MISSING")
    assert result.relative_price_sensitivity_per_1bp == D("0.0002")


@pytest.mark.parametrize("profile_fields,market_fields,md_status", [
    ({"coupon_structure": "floating"}, {}, "COUPON_STRUCTURE_NOT_FIXED"),
    ({"coupon_frequency_state": "unknown", "coupon_frequency_per_year": None}, {}, "COUPON_FREQUENCY_NOT_VERIFIED"),
    ({"perpetual_structure": "perpetual"}, {}, "PERPETUAL_STRUCTURE_NOT_DATED"),
    ({}, {"ytm": None}, "YIELD_TO_MATURITY_MISSING"),
    ({}, {"raw_payload": {}}, "MACAULAY_DURATION_MISSING"),
])
def test_task272_unavailable_never_reconstructed(db_session, seed, profile_fields, market_fields, md_status):
    seed.profile(**profile_fields)
    seed.market(**market_fields)
    result = build(db_session, seed)
    unavailable(result, "MODIFIED_DURATION_UNAVAILABLE")
    assert result.modified_duration_status == md_status
    assert result.modified_duration_years is result.relative_price_sensitivity_per_1bp is None
    assert result.dirty_value_currency == 1010


@pytest.mark.parametrize("age,threshold,status", [(0, 0, "READY"), (7, 7, "READY"),
    (8, 7, "MODIFIED_DURATION_UNAVAILABLE"), (1, 0, "MODIFIED_DURATION_UNAVAILABLE")])
def test_task272_freshness_gates_forwarded(db_session, seed, age, threshold, status):
    seed.profile()
    seed.market(age=age)
    result = build(db_session, seed, max_market_age_days=threshold)
    assert result.status == status and result.market_age_days == age
    assert result.provenance.max_market_age_days == threshold
    if status != "READY":
        unavailable(result, status)
        assert result.modified_duration_status == "MARKET_DATA_STALE"


def test_absent_snapshot_not_artificial_mismatch_and_no_future_rescue(db_session, seed):
    seed.profile()
    seed.market(age=-1)
    seed.market(source="manual")
    result = build(db_session, seed)
    unavailable(result, "MODIFIED_DURATION_UNAVAILABLE")
    assert result.modified_duration_status == "MARKET_DATA_MISSING"
    assert "MARKET_CONTRACT_MISMATCH" not in result.quality_flags
    assert not result.availability.has_matching_market_snapshot


@pytest.mark.parametrize("changes", [
    {"market_snapshot_id": 99999}, {"market_trade_date": DAY - timedelta(days=1)},
    {"market_source": "manual"}, {"bond_id": 99999},
])
@pytest.mark.parametrize("md_unavailable", [False, True])
def test_mismatch_highest_priority_blocks_all_derived_values(db_session, ready, monkeypatch, changes, md_unavailable):
    md = BondModifiedDurationService(db_session).build_for_bond(ready.seed.bond.id, DAY)
    updates = {**changes, **({"status": "MARKET_DATA_STALE", "modified_duration_years": None} if md_unavailable else {})}
    changed = md.model_copy(update=updates)
    monkeypatch.setattr(BondModifiedDurationService, "build_for_bond", lambda *args, **kwargs: changed)
    result = build(db_session, ready.seed)
    unavailable(result, "MARKET_CONTRACT_MISMATCH")
    assert result.market_snapshot_id is result.market_trade_date is result.market_age_days is None
    assert result.relative_price_sensitivity_per_1bp is result.clean_value_currency is result.dirty_value_currency is None
    assert not result.availability.has_matching_market_snapshot
    assert result.provenance.market_identity != result.provenance.modified_duration_market_identity
    for name, value in changes.items():
        assert getattr(result.provenance.modified_duration_market_identity, name) == value
    assert ("MODIFIED_DURATION_UNAVAILABLE" in result.quality_flags) is md_unavailable


def test_both_wrong_identity_cannot_agree_into_ready(db_session, ready, monkeypatch):
    patch_market(monkeypatch, bond_id=99999, market_source="manual")
    result = build(db_session, ready.seed)
    unavailable(result, "MARKET_CONTRACT_MISMATCH")
    assert result.bond_id == ready.seed.bond.id and result.market_source == "moex"


@pytest.mark.parametrize("value", [None, D(-1), D("NaN"), D("Infinity"), 2, 2.0, True, "2"])
def test_corrupt_ready_md_fail_closed(db_session, ready, monkeypatch, value):
    original = BondModifiedDurationService.build_for_bond
    monkeypatch.setattr(BondModifiedDurationService, "build_for_bond",
        lambda self, *args, **kwargs: original(self, *args, **kwargs).model_copy(update={"modified_duration_years": value}))
    result = build(db_session, ready.seed)
    unavailable(result, "MODIFIED_DURATION_UNAVAILABLE")
    assert result.relative_price_sensitivity_per_1bp is None


def test_unavailable_task272_status_blocks_even_present_md(db_session, ready, monkeypatch):
    original = BondModifiedDurationService.build_for_bond
    monkeypatch.setattr(BondModifiedDurationService, "build_for_bond",
        lambda self, *args, **kwargs: original(self, *args, **kwargs).model_copy(update={"status": "MARKET_DATA_STALE"}))
    result = build(db_session, ready.seed)
    unavailable(result, "MODIFIED_DURATION_UNAVAILABLE")
    assert result.modified_duration_years == D(2)
    assert result.relative_price_sensitivity_per_1bp is None and result.dirty_value_currency == D(1010)
    assert not result.availability.has_modified_duration


def test_empty_unavailable_output_has_stable_serialization(db_session, seed):
    first = build(db_session, seed)
    unavailable(first, "MODIFIED_DURATION_UNAVAILABLE")
    assert first.model_dump_json() == build(db_session, seed).model_dump_json()
    assert first.market_snapshot_id is first.nominal_value is first.clean_quote_pct is first.nkd_currency is None
    assert first.relative_price_sensitivity_per_1bp is first.dv01_currency_per_bond is None
    assert not any(first.availability.model_dump().values())


def test_exact_md_reuse_argument_forwarding_and_views_unchanged(db_session, ready, monkeypatch):
    identity = ready.seed.bond.id
    md = BondModifiedDurationService(db_session).build_for_bond(identity, DAY)
    md = md.model_copy(update={"modified_duration_years": D("2.123456789123456789123456789")})
    market = BondMarketFeatureService(db_session).build_for_bond(identity, DAY)
    copies = deepcopy((md.model_dump(), market.model_dump()))
    calls = []
    def read_md(self, *args, **kwargs):
        calls.append(("MD", args, kwargs))
        return md
    def read_market(self, *args, **kwargs):
        calls.append(("MARKET", args, kwargs))
        return market
    monkeypatch.setattr(BondModifiedDurationService, "build_for_bond", read_md)
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", read_market)
    result = build(db_session, ready.seed, max_market_age_days=3)
    expected_args = ((identity, DAY), {"market_source": "moex", "max_market_age_days": 3})
    assert calls == [("MD", *expected_args), ("MARKET", *expected_args)]
    assert result.modified_duration_years == md.modified_duration_years
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        assert result.dv01_currency_per_bond == D(1010) * md.modified_duration_years * ONE_BP
    assert (md.model_dump(), market.model_dump()) == copies


def test_negative_yield_valid_md_duration_flags_narrow_and_context_isolation(db_session, seed):
    seed.profile(coupon_frequency_per_year=7)
    seed.market(days=30, ytm="-5", raw_payload={"moex": {"DURATION": 30},
        "mapping_notes": ["DURATION looked like days and was divided by 365"]})
    result = build(db_session, seed)
    assert result.status == "READY" and result.dv01_currency_per_bond > 0
    assert {"NEGATIVE_YIELD", "STORED_DURATION_MISMATCH", "DURATION_LEGACY_DAY_NORMALIZATION"} <= set(result.quality_flags)
    assert not any("LIQUIDITY" in flag or "CASHFLOW" in flag or "TRADES" in flag for flag in result.quality_flags)
    with localcontext(Context(prec=3, rounding=ROUND_DOWN)) as context:
        repeated = build(db_session, seed)
        assert context.prec == 3 and context.rounding == ROUND_DOWN
    assert result.model_dump_json() == repeated.model_dump_json()


@pytest.mark.parametrize("payload,flag", [({}, "DURATION_RAW_MISSING"),
    ({"moex": {"DURATION": "bad"}}, "MALFORMED_RAW_DURATION"),
    ({"moex": {"DURATION": 30}, "canonical": {"duration": 50}}, "CONFLICTING_RAW_DURATION")])
def test_repaired_duration_diagnostics_propagate(db_session, seed, payload, flag):
    seed.profile()
    seed.market(raw_payload=payload)
    result = build(db_session, seed)
    unavailable(result, "MODIFIED_DURATION_UNAVAILABLE")
    assert flag in result.quality_flags


@pytest.mark.parametrize("fields,status", [
    ({"currency_state": "unknown", "nominal_state": "unknown"}, "CURRENCY_NOT_VERIFIED"),
    ({"currency_code": None, "nominal_value": None}, "CURRENCY_MISSING"),
    ({"currency_code": "USD", "nominal_value": None}, "UNSUPPORTED_CURRENCY"),
    ({"nominal_state": "unknown", "nominal_value": None}, "NOMINAL_NOT_VERIFIED"),
    ({"nominal_value": None}, "NOMINAL_MISSING"),
    ({"nominal_value": D(0)}, "NOMINAL_INVALID"),
])
def test_primary_status_and_all_detected_limitations(db_session, ready, monkeypatch, fields, status):
    patch_profile(db_session, monkeypatch, ready.profile, **fields)
    patch_market(monkeypatch, clean_price=D(-1), nkd=None)
    result = build(db_session, ready.seed)
    unavailable(result, status)
    assert {"CLEAN_PRICE_INVALID", "NKD_MISSING", status} <= set(result.quality_flags)


def test_quote_before_nkd_status_and_dirty_arithmetic_guard(db_session, ready, monkeypatch):
    with monkeypatch.context() as patch:
        patch_market(patch, clean_price=D(-1), nkd=D(-1))
        result = build(db_session, ready.seed)
        unavailable(result, "CLEAN_PRICE_INVALID")
        assert "NKD_INVALID" in result.quality_flags
    patch_profile(db_session, monkeypatch, ready.profile, nominal_value=D("1e999999"))
    patch_market(monkeypatch, clean_price=D("1e999999"))
    result = build(db_session, ready.seed)
    unavailable(result, "DIRTY_VALUE_INVALID")
    assert result.clean_value_currency is result.dirty_value_currency is None


@pytest.mark.parametrize("pending", [False, True])
def test_select_only_pending_state_and_source_rows_preserved(db_session, ready, monkeypatch, pending):
    seed, profile, snapshot = ready.seed, ready.profile, ready.snapshot
    doomed = Company(name="Caller deletion", ticker="DELETE273")
    db_session.add(doomed)
    db_session.commit()
    identity = seed.bond.id
    source_values = deepcopy((snapshot.raw_payload, snapshot.duration_years, snapshot.dirty_price,
        snapshot.liquidity_score, snapshot.spread_to_ofz, seed.bond.nominal_value,
        seed.bond.currency, seed.bond.current_price, seed.bond.duration_years, seed.bond.liquidity_score,
        profile.outstanding_nominal))
    if pending:
        db_session.add(Company(name="Caller pending", ticker="PENDING273"))
        seed.bond.name = "Caller edit"
        profile.nominal_value = D(777)
        db_session.delete(doomed)
    state = tuple(set(rows) for rows in (db_session.new, db_session.dirty, db_session.deleted))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    def forbidden(*args, **kwargs):
        pytest.fail("Service attempted a mutation")
    db_session.autoflush = True
    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        with monkeypatch.context() as patch:
            for name in ("add", "add_all", "delete", "flush", "commit"):
                patch.setattr(db_session, name, forbidden)
            result = BondDv01Service(db_session).build_for_bond(identity, DAY)
        assert result.status == "READY" and result.nominal_value == D(1000)
        assert result.dv01_currency_per_bond == D("0.202")
        assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
        assert tuple(set(rows) for rows in (db_session.new, db_session.dirty, db_session.deleted)) == state
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert (snapshot.raw_payload, snapshot.duration_years, snapshot.dirty_price, snapshot.liquidity_score,
        snapshot.spread_to_ofz, seed.bond.nominal_value, seed.bond.currency, seed.bond.current_price,
        seed.bond.duration_years, seed.bond.liquidity_score, profile.outstanding_nominal) == source_values
    assert profile.nominal_value == (D(777) if pending else D(1000))


@pytest.mark.parametrize("changes", [
    {"bond_id": True}, {"bond_id": False}, {"bond_id": 0}, {"bond_id": -1}, {"bond_id": 1.0}, {"bond_id": "1"},
    {"as_of_date": datetime(2026, 9, 18)}, {"as_of_date": "2026-09-18"}, {"as_of_date": None},
    {"market_source": "MOEX"}, {"market_source": "manual"}, {"market_source": " moex"},
    {"market_source": "moex "}, {"market_source": ""}, {"market_source": None}, {"market_source": 1},
    {"max_market_age_days": True}, {"max_market_age_days": -1}, {"max_market_age_days": 1.0},
    {"max_market_age_days": "7"}, {"max_market_age_days": None},
])
def test_invalid_arguments_before_dependency_calls(db_session, seed, monkeypatch, changes):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid arguments reached a dependency")
    monkeypatch.setattr(BondModifiedDurationService, "build_for_bond", forbidden)
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", forbidden)
    with pytest.raises(ValueError):
        BondDv01Service(db_session).build_for_bond(**{"bond_id": seed.bond.id, "as_of_date": DAY, **changes})


def test_missing_bond_preserves_http_404(db_session):
    with pytest.raises(HTTPException) as error:
        BondDv01Service(db_session).build_for_bond(999999, DAY)
    assert error.value.status_code == 404 and error.value.detail == "Bond not found"


def test_frozen_forbidden_extra_and_capability_contract(db_session, ready):
    result = build(db_session, ready.seed)
    assert result.contract_version == "bond-dv01-v1" and result.pit_ready is False
    for model in (result, result.availability, result.provenance, result.capabilities,
                  result.provenance.market_identity, result.provenance.modified_duration_market_identity):
        field = next(iter(type(model).model_fields))
        with pytest.raises(ValidationError):
            setattr(model, field, getattr(model, field))
        with pytest.raises(ValidationError):
            type(model).model_validate({**model.model_dump(), "unknown": 1})
    for change in ({"pit_ready": True}, {"status": "HIGH"}, {"market_source": "manual"}):
        with pytest.raises(ValidationError):
            BondDv01View.model_validate({**result.model_dump(), **change})
    true_fields = {"modified_duration_input_ready", "relative_1bp_sensitivity_ready", "dv01_ready", "pvbp_ready"}
    for name, value in result.capabilities.model_dump().items():
        if name == "dv01_price_basis":
            assert value == "DIRTY_VALUE"
        elif name == "dv01_sign_convention":
            assert value == "MAGNITUDE"
        else:
            assert value is (name in true_fields)
    assert not {"score", "signal", "label", "recommendation", "portfolio_weight", "quantity", "weight",
                "position_dv01", "portfolio_dv01"} & set(result.model_dump())


def test_static_safety_no_direct_snapshot_legacy_or_network_inputs():
    path = Path(__file__).parents[1] / "app/services/bond_dv01_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = " ".join(ast.unparse(n) for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)))
    assert not any(word in imports for word in ("moex_iss", "moex_duration", "bond_cashflow", "bond_market_snapshot",
        "broker", "strategy", "risk_engine", "requests", "httpx", "urllib", "models.bond import"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert node.attr not in {"dirty_price", "duration_years", "coupon_frequency_per_year",
                                     "outstanding_nominal", "current_price", "quantity", "lot_size"}
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and ast.unparse(node.func.value) == "self.db":
                assert node.func.attr not in {"add", "add_all", "flush", "commit", "delete", "merge"}
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"float", "insert", "update", "delete", "create_engine"}
        assert not isinstance(node, ast.Constant) or not isinstance(node.value, float)
