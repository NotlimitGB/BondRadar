"""Task280 explicit-universe batch orchestration and safety acceptance."""

import ast
import inspect
from datetime import date, datetime
from decimal import Decimal, ROUND_DOWN, getcontext, setcontext
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import event

from app.models.bond import Bond
from app.models.company import Company
from app.schemas.bond_credit_comparability import (
    BondCreditComparabilityView,
    RatingComparabilityEntry,
)
from app.schemas.bond_market_features import BondMarketFeatureView
from app.schemas.credit_cohort_batch_members import (
    CREDIT_COHORT_BATCH_MEMBERS_CONTRACT_VERSION,
    CreditCohortBatchMemberItem,
    CreditCohortBatchMemberView,
)
from app.schemas.ofz_reference_curve import OfzReferenceCurveView
from app.services.bond_credit_cohort_relative_value_service import (
    BondCreditCohortRelativeValueMemberService,
)
from app.services.bond_credit_comparability_service import (
    BondCreditComparabilityService,
)
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.credit_cohort_batch_member_service import (
    CreditCohortBatchMemberService,
)
from app.services.ofz_reference_curve_service import OfzReferenceCurveService


DAY = date(2026, 9, 19)
D = Decimal


def _seed_bonds(db, count=2):
    company = Company(name=f"Task280 issuer {count}", ticker=f"TASK280{count}")
    db.add(company)
    db.flush()
    bonds = [
        Bond(company_id=company.id, name=f"Corporate {index}", secid=f"T280{count}{index}")
        for index in range(1, count + 1)
    ]
    db.add_all(bonds)
    db.commit()
    return bonds


def _curve(*, status="READY"):
    ready = status == "READY"
    nodes = [
        dict(
            duration_years=D("1"), yield_to_maturity_pct=D("10"),
            aggregation_method="SINGLE", component_bond_ids=[901],
            component_snapshot_ids=[1901], component_secids=["OFZ1"],
            component_isins=["SU0000000001"], component_yields_pct=[D("10")],
        ),
        dict(
            duration_years=D("3"), yield_to_maturity_pct=D("12"),
            aggregation_method="SINGLE", component_bond_ids=[902],
            component_snapshot_ids=[1902], component_secids=["OFZ2"],
            component_isins=["SU0000000002"], component_yields_pct=[D("12")],
        ),
    ] if ready else []
    return OfzReferenceCurveView(
        as_of_date=DAY, market_source="moex", status=status,
        curve_trade_date=DAY if ready else None, node_count=len(nodes),
        min_duration_years=D("1") if ready else None,
        max_duration_years=D("3") if ready else None,
        nodes=nodes, diagnostics={},
    )


def _market(bond_id, *, status="FRESH"):
    fresh = status == "FRESH"
    return BondMarketFeatureView(
        bond_id=bond_id, isin=f"RU{bond_id:010d}", secid=f"B{bond_id}",
        as_of_date=DAY, market_snapshot_id=1000 + bond_id if fresh else None,
        market_trade_date=DAY if fresh else None, market_source="moex",
        market_age_days=0 if fresh else None, market_status=status,
        price=None, clean_price=None, nkd=None,
        yield_to_maturity_pct=D("14") if fresh else None,
        duration_years=D("2") if fresh else None,
        trade_volume=None, turnover_value=None, num_trades=None,
        currency="RUB", nominal_value=None, coupon_rate=None,
        maturity_date=None, offer_date=None, days_to_maturity=None,
        days_to_offer=None, is_matured=False, is_floating_coupon=False,
        is_subordinated=False, is_perpetual=False,
        metadata_has_amortization=None, future_cashflow_event_count=0,
        future_coupon_count=0, future_amortization_count=0,
        next_coupon_date=None, next_amortization_date=None,
        next_redemption_date=None, next_offer_redemption_date=None,
        has_future_coupon=False, has_future_amortization=False,
        has_future_redemption=False, has_future_offer_redemption=False,
        availability=dict(
            has_market_snapshot=fresh, has_price=False, has_clean_price=False,
            has_nkd=False, has_yield_to_maturity=fresh, has_duration=fresh,
            has_trade_volume=False, has_turnover_value=False,
            has_num_trades=False, has_cashflow_schedule=False,
            has_maturity=False, has_offer=False,
        ),
        quality_flags=[],
        provenance=dict(
            market_snapshot_id=1000 + bond_id if fresh else None,
            market_source="moex", market_trade_date=DAY if fresh else None,
            selected_cashflow_event_ids=[],
        ),
    )


def _entry(*, ready=True):
    return RatingComparabilityEntry(
        target_kind="BOND", rating_agency="ACRA",
        status="READY" if ready else "RATING_VALUE_MISSING",
        latest_event_date=DAY, event_count=1, event_ids=[71],
        selected_event_id=71 if ready else None,
        source_provider="CBR_RATINGS" if ready else None,
        rating_scale_raw=None, rating_value_raw="AA(RU)" if ready else None,
        has_declared_rating_scale=False, has_rating_value=ready,
        cohort_key=(
            dict(target_kind="BOND", rating_agency="ACRA",
                 source_provider="CBR_RATINGS", rating_scale_raw=None,
                 rating_value_raw="AA(RU)")
            if ready else None
        ),
        selected_event=None, quality_flags=[] if ready else ["RATING_VALUE_MISSING"],
    )


def _credit(bond_id, *, cohort=True):
    entries = [_entry()] if cohort else []
    return BondCreditComparabilityView(
        bond_id=bond_id, isin=f"RU{bond_id:010d}", secid=f"B{bond_id}",
        as_of_date=DAY, status="READY" if cohort else "NO_COMPARABLE_RATING",
        bond_rating_entries=entries, issuer_rating_entries=[],
        availability=dict(
            has_bond_rating_evidence=cohort, has_issuer_rating_evidence=False,
            has_bond_comparable_cohort=cohort,
            has_issuer_comparable_cohort=False,
            bond_comparable_agencies=["ACRA"] if cohort else [],
            issuer_comparable_agencies=[], has_any_comparable_cohort=cohort,
        ),
        provenance=dict(
            credit_feature_contract_version="bond-credit-feature-v1",
            credit_feature_bond_id=bond_id, credit_feature_as_of_date=DAY,
            as_of_date=DAY, bond_legal_issuer_profile_id=40 + bond_id,
            legal_issuer_id=50 + bond_id, issuer_link_status="VERIFIED",
            bond_rating_event_ids=[71] if cohort else [], issuer_rating_event_ids=[],
            selected_bond_rating_event_ids=[71] if cohort else [],
            selected_issuer_rating_event_ids=[],
            bond_rating_agencies=["ACRA"] if cohort else [],
            issuer_rating_agencies=[],
        ),
        quality_flags=[],
    )


def _install_dependencies(
    monkeypatch, *, curve=None, market_states=None, credit_states=None,
):
    import app.services.credit_cohort_batch_member_service as module

    shared_curve = curve or _curve()
    market_states = market_states or {}
    credit_states = credit_states or {}
    calls = []
    curve_objects = []
    original_evaluator = module.evaluate_market_against_ofz_curve
    original_composer = module.compose_credit_cohort_relative_value_member

    def build_curve(service, *args, **kwargs):
        assert service.db.autoflush is False
        calls.append(("CURVE", args, kwargs))
        return shared_curve

    def build_market(service, bond_id, *args, **kwargs):
        assert service.db.autoflush is False
        calls.append(("MARKET", (bond_id, *args), kwargs))
        return _market(bond_id, status=market_states.get(bond_id, "FRESH"))

    def build_credit(service, bond_id, *args, **kwargs):
        assert service.db.autoflush is False
        calls.append(("CREDIT", (bond_id, *args), kwargs))
        return _credit(bond_id, cohort=credit_states.get(bond_id, True))

    def evaluate(market, supplied_curve, **kwargs):
        calls.append(("EVALUATOR", (market.bond_id,), kwargs))
        curve_objects.append(supplied_curve)
        return original_evaluator(market, supplied_curve, **kwargs)

    def compose(credit, relative, **kwargs):
        calls.append(("COMPOSER", (credit.bond_id,), kwargs))
        return original_composer(credit, relative, **kwargs)

    monkeypatch.setattr(OfzReferenceCurveService, "build_curve", build_curve)
    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", build_market)
    monkeypatch.setattr(BondCreditComparabilityService, "build_for_bond", build_credit)
    monkeypatch.setattr(module, "evaluate_market_against_ofz_curve", evaluate)
    monkeypatch.setattr(module, "compose_credit_cohort_relative_value_member", compose)
    return shared_curve, calls, curve_objects


def _build(db, ids, **kwargs):
    arguments = dict(target_kind="BOND", rating_agency="ACRA")
    arguments.update(kwargs)
    return CreditCohortBatchMemberService(db).build_for_bonds(ids, DAY, **arguments)


@pytest.mark.parametrize("count", [1, 2, 10])
def test_a_b_m_n_explicit_universe_shared_curve_and_exact_call_counts(
    db_session, monkeypatch, count,
):
    bonds = _seed_bonds(db_session, count)
    shared, calls, curve_objects = _install_dependencies(monkeypatch)
    result = _build(db_session, [bond.id for bond in reversed(bonds)])
    ids = sorted(bond.id for bond in bonds)
    assert result.status == "COMPLETE"
    assert result.requested_bond_ids == result.existing_bond_ids == ids
    assert result.missing_bond_ids == []
    assert [item.bond_id for item in result.items] == ids
    assert all(item.build_status == "BUILT" and item.member for item in result.items)
    assert result.shared_curve is shared
    assert len(curve_objects) == count and all(value is shared for value in curve_objects)
    assert [name for name, _, _ in calls].count("CURVE") == 1
    for name in ("MARKET", "CREDIT", "EVALUATOR", "COMPOSER"):
        assert [kind for kind, _, _ in calls].count(name) == count
    assert result.provenance.curve_build_count == 1
    assert result.provenance.market_feature_call_count == count
    assert result.provenance.credit_comparability_call_count == count
    assert result.provenance.relative_evaluator_call_count == count
    assert result.provenance.member_composer_call_count == count
    assert result.built_member_count == count
    assert result.ready_member_count == count
    assert result.unavailable_member_count == 0


@pytest.mark.parametrize("value", [[], (), "1", b"1", bytearray(b"1"), {1: 2}, {1}, iter([1])])
def test_c_h_invalid_outer_universe_fails_before_db(value):
    class NoDb:
        @property
        def no_autoflush(self):
            raise AssertionError("DB accessed")

    with pytest.raises(ValueError):
        _build(NoDb(), value)


@pytest.mark.parametrize("ids", [[1, 1], [True], [0], [-1], [1.0], ["1"], [None]])
def test_d_g_invalid_ids_fail_before_db(ids):
    class NoDb:
        @property
        def no_autoflush(self):
            raise AssertionError("DB accessed")

    with pytest.raises(ValueError):
        _build(NoDb(), ids)


@pytest.mark.parametrize(
    "field,value",
    [
        ("as_of_date", datetime(2026, 9, 19)),
        ("target_kind", "ISSUER"),
        ("rating_agency", "UNKNOWN"),
        ("market_source", ""),
        ("market_source", "  "),
        ("max_market_age_days", True),
        ("max_market_age_days", -1),
        ("max_curve_age_days", 1.5),
    ],
)
def test_j_l_invalid_request_fields_fail_before_db(field, value):
    class NoDb:
        @property
        def no_autoflush(self):
            raise AssertionError("DB accessed")

    kwargs = dict(target_kind="BOND", rating_agency="ACRA")
    when = kwargs.pop("as_of_date", DAY)
    if field == "as_of_date":
        when = value
    else:
        kwargs[field] = value
    with pytest.raises(ValueError):
        CreditCohortBatchMemberService(NoDb()).build_for_bonds([1], when, **kwargs)


def test_i_input_order_is_normalized_and_serialization_is_stable(db_session, monkeypatch):
    bonds = _seed_bonds(db_session, 3)
    _install_dependencies(monkeypatch)
    ids = [bond.id for bond in bonds]
    supplied = [ids[2], ids[0], ids[1]]
    before = list(supplied)
    first = _build(db_session, supplied)
    second = _build(db_session, ids)
    assert supplied == before
    assert first.model_dump() == second.model_dump()
    assert first.model_dump_json() == second.model_dump_json()


def test_other_deterministic_sequence_and_exact_argument_forwarding(db_session, monkeypatch):
    bond = _seed_bonds(db_session, 1)[0]
    bond_id = bond.id
    _, calls, _ = _install_dependencies(monkeypatch)
    result = _build(
        db_session, range(bond_id, bond_id + 1), target_kind="LEGAL_ISSUER",
        rating_agency="NKR", max_market_age_days=3, max_curve_age_days=4,
    )
    assert result.status == "COMPLETE"
    assert result.requested_target_kind == "LEGAL_ISSUER"
    assert result.requested_rating_agency == "NKR"
    assert calls == [
        ("CURVE", (DAY,), {"market_source": "moex", "max_curve_age_days": 4}),
        ("MARKET", (bond_id, DAY), {"market_source": "moex", "max_market_age_days": 3}),
        ("CREDIT", (bond_id, DAY), {}),
        ("EVALUATOR", (bond_id,), {"as_of_date": DAY, "market_source": "moex"}),
        ("COMPOSER", (bond_id,), {
            "bond_id": bond_id, "as_of_date": DAY, "target_kind": "LEGAL_ISSUER",
            "rating_agency": "NKR", "market_source": "moex",
        }),
    ]


def test_o_ac_to_ag_partial_and_all_missing_semantics(db_session, monkeypatch):
    bond = _seed_bonds(db_session, 1)[0]
    _, calls, _ = _install_dependencies(monkeypatch)
    partial = _build(db_session, [999999, bond.id])
    assert partial.status == "PARTIAL"
    assert partial.existing_bond_ids == [bond.id]
    assert partial.missing_bond_ids == [999999]
    assert [item.build_status for item in partial.items] == ["BUILT", "BOND_NOT_FOUND"]
    assert partial.items[1].member is None
    before = len(calls)
    missing = _build(db_session, [888888, 999999])
    assert missing.status == "NO_EXISTING_BONDS"
    assert missing.shared_curve is None and not missing.curve_built
    assert missing.built_member_count == missing.ready_member_count == 0
    assert missing.unavailable_member_count == 0
    assert all(item.member is None for item in missing.items)
    assert len(calls) == before
    assert missing.provenance.curve_build_count == 0


def test_q_y_to_ab_nonready_curve_and_unavailable_members_are_preserved(
    db_session, monkeypatch,
):
    bonds = _seed_bonds(db_session, 3)
    ids = [bond.id for bond in bonds]
    shared, calls, objects = _install_dependencies(
        monkeypatch,
        curve=_curve(status="NO_ELIGIBLE_OFZ"),
        market_states={ids[1]: "MISSING"},
        credit_states={ids[2]: False},
    )
    result = _build(db_session, ids)
    assert result.status == "COMPLETE"
    assert result.shared_curve is shared
    assert len(objects) == 3 and all(item is shared for item in objects)
    assert result.ready_member_count == 0 and result.unavailable_member_count == 3
    assert [item.member.status for item in result.items] == [
        "RELATIVE_VALUE_UNAVAILABLE",
        "RELATIVE_VALUE_UNAVAILABLE",
        "SELECTED_COHORT_MISSING",
    ]
    assert [name for name, _, _ in calls].count("CURVE") == 1


@pytest.mark.parametrize("state", ["READY", "COHORT_MISSING", "RELATIVE_UNAVAILABLE"])
def test_task276_full_model_equivalence(db_session, monkeypatch, state):
    import app.services.credit_cohort_batch_member_service as batch_module

    bonds = _seed_bonds(db_session, 2)
    ids = [bond.id for bond in bonds]
    shared = _curve(status="NO_ELIGIBLE_OFZ" if state == "RELATIVE_UNAVAILABLE" else "READY")

    def market(service, bond_id, *args, **kwargs):
        return _market(bond_id)

    def credit(service, bond_id, *args, **kwargs):
        return _credit(bond_id, cohort=not (state == "COHORT_MISSING" and bond_id == ids[1]))

    def curve(service, *args, **kwargs):
        return shared

    def relative(service, bond_id, as_of_date, **kwargs):
        return batch_module.evaluate_market_against_ofz_curve(
            _market(bond_id), shared, as_of_date=as_of_date,
            market_source=kwargs.get("market_source", "moex"),
        )

    monkeypatch.setattr(BondMarketFeatureService, "build_for_bond", market)
    monkeypatch.setattr(BondCreditComparabilityService, "build_for_bond", credit)
    monkeypatch.setattr(OfzReferenceCurveService, "build_curve", curve)
    monkeypatch.setattr(OfzReferenceCurveService, "evaluate_bond", relative)
    direct = {
        bond_id: BondCreditCohortRelativeValueMemberService(db_session).build_for_bond(
            bond_id, DAY, target_kind="BOND", rating_agency="ACRA",
        )
        for bond_id in ids
    }
    batch = _build(db_session, ids)
    assert {
        item.bond_id: item.member.model_dump() for item in batch.items
    } == {bond_id: view.model_dump() for bond_id, view in direct.items()}


def test_ah_to_aj_one_narrow_existence_query(db_session, monkeypatch):
    bond = _seed_bonds(db_session, 1)[0]
    bond_id = bond.id
    _install_dependencies(monkeypatch)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        result = _build(db_session, [999999, bond_id])
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    assert result.status == "PARTIAL"
    assert len(statements) == 1
    sql = " ".join(statements[0][0].upper().split())
    assert sql.startswith("SELECT BONDS.ID")
    assert "WHERE BONDS.ID IN" in sql and "ORDER BY BONDS.ID" in sql
    assert set(statements[0][1]) == {bond_id, 999999}


def test_ao_to_aq_read_only_preserves_pending_state_with_autoflush(
    db_session, monkeypatch,
):
    bonds = _seed_bonds(db_session, 3)
    _install_dependencies(monkeypatch)
    db_session.autoflush = True
    bonds[0].name = "caller dirty"
    db_session.delete(bonds[1])
    pending = Bond(company_id=bonds[2].company_id, name="caller pending", secid="T280P")
    db_session.add(pending)
    before = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    verbs = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        verbs.append(statement.lstrip().split(None, 1)[0].upper())

    event.listen(db_session.bind, "before_cursor_execute", capture)
    try:
        result = _build(db_session, [bonds[2].id])
    finally:
        event.remove(db_session.bind, "before_cursor_execute", capture)
    after = (set(db_session.new), set(db_session.dirty), set(db_session.deleted))
    assert result.status == "COMPLETE"
    assert before == after
    assert verbs and set(verbs) == {"SELECT"}


def test_ar_as_at_contract_freezing_serialization_and_decimal_context(
    db_session, monkeypatch,
):
    bond = _seed_bonds(db_session, 1)[0]
    shared, _, _ = _install_dependencies(monkeypatch)
    original_context = getcontext().copy()
    getcontext().prec = 9
    getcontext().rounding = ROUND_DOWN
    before_curve = shared.model_dump()
    try:
        result = _build(db_session, [bond.id])
        assert getcontext().prec == 9 and getcontext().rounding == ROUND_DOWN
    finally:
        setcontext(original_context)
    assert shared.model_dump() == before_curve
    assert result.contract_version == CREDIT_COHORT_BATCH_MEMBERS_CONTRACT_VERSION
    assert result.pit_ready is False and result.capabilities.pit_ready is False
    assert result.capabilities.batch_member_build_ready is True
    assert result.capabilities.peer_universe_discovery_ready is False
    assert CreditCohortBatchMemberView.model_validate_json(result.model_dump_json()) == result
    with pytest.raises(ValidationError):
        CreditCohortBatchMemberView.model_validate({**result.model_dump(), "extra": True})
    with pytest.raises(ValidationError):
        result.status = "PARTIAL"
    with pytest.raises(ValidationError):
        CreditCohortBatchMemberItem(bond_id=1, build_status="UNKNOWN", member=None)


def test_static_safety_and_no_duplicate_financial_or_member_logic():
    import app.services.credit_cohort_batch_member_service as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    assert "app.models.bond_market_snapshot" not in imports
    assert "app.models.credit_risk_evidence" not in imports
    assert "app.services.bond_credit_cohort_relative_value_service" not in imports
    assert "app.services.credit_cohort_peer_spread_distribution_service" not in imports
    assert "evaluate_bond" not in source
    assert "EXACT_NODE" not in source and "LINEAR_INTERPOLATION" not in source
    assert "spread_to_ofz_pp" not in source and "cohort_key" not in source
    forbidden = {"add", "add_all", "flush", "commit", "delete"}
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    assert not any(
        isinstance(node.func, ast.Attribute) and node.func.attr in forbidden
        for node in calls
    )
    assert sum(
        isinstance(node.func, ast.Attribute) and node.func.attr == "build_curve"
        for node in calls
    ) == 1
    assert sum(
        isinstance(node.func, ast.Name)
        and node.func.id == "evaluate_market_against_ofz_curve"
        for node in calls
    ) == 1
    assert sum(
        isinstance(node.func, ast.Name)
        and node.func.id == "compose_credit_cohort_relative_value_member"
        for node in calls
    ) == 1
    bond_fields = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "Bond"
    }
    assert bond_fields == {"id"}
    assert "requests" not in source and "httpx" not in source
    assert "rating_map" not in source.lower() and "agency_preference" not in source.lower()
    signature = inspect.signature(CreditCohortBatchMemberService.build_for_bonds)
    assert signature.parameters["market_source"].default == "moex"
    assert signature.parameters["max_market_age_days"].default == 7
    assert signature.parameters["max_curve_age_days"].default == 7
