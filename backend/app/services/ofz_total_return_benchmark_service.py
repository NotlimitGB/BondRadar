"""Persisted-source reference index; no fractional quantities or source refresh."""
from datetime import date, timedelta
from decimal import localcontext
from sqlalchemy import select
from app.models.bond_cashflow_event import BondCashflowEvent
from app.schemas.shadow_execution import ShadowExecutionPlanView
from app.schemas.shadow_ledger import ShadowGenesisRequestV1
from app.schemas.ofz_reference_curve import OfzReferenceCurveView
from app.schemas.bond_modified_duration import BondModifiedDurationView
from app.schemas.bond_dv01 import BondDv01View
from app.schemas.shadow_experiment import (
    ShadowExperimentPolicyV1, OfzRepresentativeV1, OfzBenchmarkComponentV1,
    OfzBenchmarkGenesisViewV1, BenchmarkCashflowV1, BenchmarkComponentDailyV1,
    OfzBenchmarkDailyViewV1, Blocker,
)
from app.services.ofz_reference_curve_service import OfzReferenceCurveService
from app.services.bond_modified_duration_service import BondModifiedDurationService
from app.services.bond_dv01_service import BondDv01Service
from app.services.shadow_daily_cycle_service import validate_mark
from app.services.shadow_ledger_genesis_service import validate_request
from app.services.shadow_experiment_evaluator import (
    DECIMAL_CONTEXT, ZERO, ONE, require, finite, strict, digest, signed, validate_day, validate_benchmark_genesis,
)


def evidence_code(exc, fallback):
    return str(exc) if str(exc) in Blocker.__args__ else fallback


def safe_evidence(value, cls):
    """Retain serializable typed evidence; never repair malformed source values."""
    try:
        strict(value, cls)
        digest(value)
        return value
    except ValueError:
        return None


def validate_shadow(source):
    strict(source, ShadowExecutionPlanView)
    # Task303's pure validator owns the complete source envelope. The code SHA
    # here is only a valid validator context, never persisted or attributed.
    validate_request(ShadowGenesisRequestV1(shadow_execution=source, source_code_sha="0"*40))


def curve_representatives(curve, day):
    strict(curve, OfzReferenceCurveView)
    require(curve.status == "READY", "BENCHMARK_CURVE_NOT_READY")
    require(curve.as_of_date == day and curve.market_source == "moex" and
        type(curve.node_count) is int and curve.node_count == len(curve.nodes) >= 2 and
        type(curve.curve_trade_date) is date and 0 <= (day-curve.curve_trade_date).days <= 7,
        "BENCHMARK_CURVE_EVIDENCE_INVALID")
    previous = ZERO; seen_bonds = set(); representatives = []
    with localcontext(DECIMAL_CONTEXT):
        for node in curve.nodes:
            arrays = (node.component_bond_ids,node.component_snapshot_ids,node.component_secids,
                      node.component_isins,node.component_yields_pct)
            require(finite(node.duration_years, positive=True) and node.duration_years > previous and
                finite(node.yield_to_maturity_pct) and len(arrays[0]) > 0 and
                all(len(values) == len(arrays[0]) for values in arrays), "BENCHMARK_CURVE_EVIDENCE_INVALID")
            previous = node.duration_years
            components = []
            for bond, snapshot, secid, isin, ytm in zip(*arrays):
                require(type(bond) is int and bond > 0 and bond not in seen_bonds and
                    type(snapshot) is int and snapshot > 0 and finite(ytm) and
                    (secid is None or type(secid) is str) and (isin is None or type(isin) is str),
                    "BENCHMARK_CURVE_EVIDENCE_INVALID")
                seen_bonds.add(bond)
                components.append((bond,snapshot,secid,isin,ytm))
            chosen = min(components, key=lambda row:(abs(row[4]-node.yield_to_maturity_pct),row[0],row[1]))
            representatives.append((node,chosen))
    require((curve.min_duration_years,curve.max_duration_years) ==
        (curve.nodes[0].duration_years,curve.nodes[-1].duration_years), "BENCHMARK_CURVE_EVIDENCE_INVALID")
    return representatives


def modified_representative(view, node, component, day, trade_date):
    strict(view, BondModifiedDurationView)
    require(view.status == "READY" and view.availability.has_modified_duration is True and
        finite(view.modified_duration_years, positive=True), "BENCHMARK_MODIFIED_DURATION_UNAVAILABLE")
    bond,snapshot,secid,isin,ytm = component
    p = view.provenance
    require((view.bond_id,view.market_snapshot_id,view.secid,view.isin,view.market_trade_date,
             view.as_of_date,view.market_source) == (bond,snapshot,secid,isin,trade_date,day,"moex") and
        view.market_status == "FRESH" and type(view.market_age_days) is int and
        view.market_age_days == (day-trade_date).days and 0 <= view.market_age_days <= 7 and
        view.macaulay_duration_years == node.duration_years and view.yield_to_maturity_pct == ytm and
        view.coupon_structure == "fixed" and view.perpetual_structure == "dated" and
        view.coupon_frequency_state == p.coupon_frequency_state == "verified" and
        type(view.coupon_frequency_per_year) is int and view.coupon_frequency_per_year > 0 and
        p.coupon_frequency_per_year == view.coupon_frequency_per_year and finite(view.modified_duration_denominator, positive=True) and
        all(getattr(view.availability,name) is True for name in type(view.availability).model_fields) and
        (p.market_snapshot_id,p.market_trade_date,p.market_source,p.as_of_date,p.max_market_age_days) ==
        (snapshot,trade_date,"moex",day,7) and p.market_contract_version == "bond-market-feature-v1" and
        p.security_master_contract_version == "bond-security-master-v2" and
        type(p.security_master_profile_id) is int and p.security_master_profile_id > 0,
        "BENCHMARK_MODIFIED_DURATION_EVIDENCE_MISMATCH")
    return OfzRepresentativeV1(bond_id=bond,snapshot_id=snapshot,isin=isin,secid=secid,
        source_node_macaulay_duration_years=node.duration_years,source_node_yield_pct=node.yield_to_maturity_pct,
        component_yield_pct=ytm,modified_duration_years=view.modified_duration_years,modified_duration_evidence=view)


def duration_weights(representatives, target):
    require(finite(target, positive=True), "TARGET_DURATION_INVALID")
    ordered = sorted(representatives,key=lambda r:(r.modified_duration_years,r.bond_id,r.snapshot_id))
    exact = next((r for r in ordered if r.modified_duration_years == target),None)
    if exact: return "EXACT_DURATION_NODE", ((exact,ONE),)
    lower = [r for r in ordered if r.modified_duration_years < target]
    upper = [r for r in ordered if r.modified_duration_years > target]
    require(lower and upper, "TARGET_DURATION_OUTSIDE_OFZ_CURVE")
    lower_duration = lower[-1].modified_duration_years
    low = next(r for r in lower if r.modified_duration_years == lower_duration)
    high = upper[0]
    with localcontext(DECIMAL_CONTEXT):
        dl,du = low.modified_duration_years,high.modified_duration_years
        wl,wu = (du-target)/(du-dl),(target-dl)/(du-dl)
        require(wl > ZERO and wu > ZERO and wl+wu == ONE and wl*dl+wu*du == target,
            "BENCHMARK_DECIMAL_INVARIANTS_FAILED")
    return "LINEAR_DURATION_MATCH", ((low,wl),(high,wu))


class OfzTotalReturnBenchmarkService:
    def __init__(self, db):
        self.db = db

    def build_genesis(self, *, shadow_execution, policy):
        strict(shadow_execution, ShadowExecutionPlanView); strict(policy, ShadowExperimentPolicyV1)
        s = shadow_execution
        day = s.as_of_date
        require(type(day) is date)
        target = s.post_rounding_risk_evaluation.metrics.invested_weighted_modified_duration_years
        base = dict(genesis_date=day,planned_end_date=day+timedelta(days=90),initial_capital_rub=s.summary.capital_rub,
            target_duration_years=target,policy=policy,experiment_policy_sha256=digest(policy),shadow_execution_sha256=digest(s))
        curve = None; representatives = []; modified_inputs = []; components = []; calls = 0; curve_calls = 0; modified_calls = 0
        try:
            validate_shadow(s)
            require(finite(target, positive=True), "TARGET_DURATION_INVALID")
            with self.db.no_autoflush:
                curve_calls = 1
                supplied_curve = OfzReferenceCurveService(self.db).build_curve(day,market_source="moex",max_curve_age_days=7)
                curve = safe_evidence(supplied_curve,OfzReferenceCurveView)
                inputs = curve_representatives(supplied_curve,day)
                errors = set()
                for node, component in inputs:
                    modified_calls += 1
                    view = BondModifiedDurationService(self.db).build_for_bond(component[0],day,market_source="moex",max_market_age_days=7)
                    captured = safe_evidence(view,BondModifiedDurationView)
                    if captured is not None: modified_inputs.append(captured)
                    try: representatives.append(modified_representative(view,node,component,day,curve.curve_trade_date))
                    except ValueError as exc: errors.add(evidence_code(exc,"BENCHMARK_MODIFIED_DURATION_EVIDENCE_MISMATCH"))
                if errors:
                    return signed(OfzBenchmarkGenesisViewV1(**base,status="BLOCKED",curve=curve,
                        representatives=tuple(sorted(representatives,key=lambda r:(r.modified_duration_years,r.bond_id,r.snapshot_id))),
                        modified_duration_inputs=tuple(modified_inputs),curve_build_count=curve_calls,
                        modified_duration_call_count=modified_calls,blockers=tuple(sorted(errors))),"benchmark_genesis_sha256")
                representatives.sort(key=lambda r:(r.modified_duration_years,r.bond_id,r.snapshot_id))
                mode, chosen = duration_weights(representatives,target)
                require(all(p.market_trade_date == curve.curve_trade_date for p in s.positions), "GENESIS_MARKET_DATE_MISMATCH")
                for representative, weight in chosen:
                    calls += 1
                    view = BondDv01Service(self.db).build_for_bond(representative.bond_id,day,market_source="moex",max_market_age_days=7)
                    try: validate_mark(view,representative.bond_id,day)
                    except ValueError: raise ValueError("BENCHMARK_MARKET_VALUE_UNAVAILABLE") from None
                    require((view.market_snapshot_id,view.market_trade_date,view.isin,view.secid,view.provenance.security_master_profile_id) ==
                        (representative.snapshot_id,curve.curve_trade_date,representative.isin,representative.secid,
                         representative.modified_duration_evidence.provenance.security_master_profile_id), "BENCHMARK_MARKET_EVIDENCE_MISMATCH")
                    components.append(OfzBenchmarkComponentV1(representative=representative,weight=weight,
                        genesis_dirty_value_rub=view.dirty_value_currency,genesis_market_evidence=view))
            with localcontext(DECIMAL_CONTEXT):
                reconstructed = sum((c.weight*c.representative.modified_duration_years for c in components),ZERO)
                nav = s.summary.capital_rub*sum((c.weight for c in components),ZERO)
                require(nav == s.summary.capital_rub and reconstructed == target, "BENCHMARK_DECIMAL_INVARIANTS_FAILED")
            return signed(OfzBenchmarkGenesisViewV1(**base,status="READY",common_market_trade_date=curve.curve_trade_date,
                matching_mode=mode,reconstructed_duration_years=reconstructed,genesis_nav_rub=nav,
                curve=curve,representatives=tuple(representatives),modified_duration_inputs=tuple(modified_inputs),
                components=tuple(components),curve_build_count=curve_calls,modified_duration_call_count=modified_calls,
                dirty_value_call_count=calls),"benchmark_genesis_sha256")
        except ValueError as exc:
            return signed(OfzBenchmarkGenesisViewV1(**base,status="BLOCKED",curve=curve,
                representatives=tuple(representatives),modified_duration_inputs=tuple(modified_inputs),components=tuple(components),
                curve_build_count=curve_calls,modified_duration_call_count=modified_calls,dirty_value_call_count=calls,
                blockers=(evidence_code(exc,"INPUT_EVIDENCE_INVALID"),)),"benchmark_genesis_sha256")

    def build_daily(self, *, genesis, as_of_date):
        validate_benchmark_genesis(genesis)
        validate_day(as_of_date,genesis.genesis_date,genesis.planned_end_date)
        base = dict(as_of_date=as_of_date,genesis_date=genesis.genesis_date,planned_end_date=genesis.planned_end_date,
            initial_capital_rub=genesis.initial_capital_rub,benchmark_genesis_sha256=genesis.benchmark_genesis_sha256,
            experiment_policy_sha256=genesis.experiment_policy_sha256)
        flows = (); query_count = 0; calls = []; diagnostic = set(); current = previous = ()
        order = {"coupon":0,"amortization":1,"redemption":2,"offer_redemption":3}
        try:
            with self.db.no_autoflush:
                if as_of_date > genesis.genesis_date:
                    query_count = 1
                    ids = [c.representative.bond_id for c in genesis.components]
                    columns = (BondCashflowEvent.id,BondCashflowEvent.bond_id,BondCashflowEvent.event_date,
                        BondCashflowEvent.event_type,BondCashflowEvent.source,BondCashflowEvent.currency,BondCashflowEvent.amount)
                    rows = self.db.execute(select(*columns).where(BondCashflowEvent.bond_id.in_(ids),
                        BondCashflowEvent.source == "moex",BondCashflowEvent.event_date > genesis.genesis_date,
                        BondCashflowEvent.event_date <= as_of_date)).mappings()
                    flows = tuple(sorted((BenchmarkCashflowV1(source_event_id=r["id"],**{k:v for k,v in r.items() if k != "id"})
                        for r in rows),key=lambda f:(f.event_date,order.get(f.event_type,4),f.bond_id,f.source_event_id)))
                current, nav = self._value(genesis,as_of_date,flows,calls,diagnostic)
                if as_of_date == genesis.genesis_date: previous, previous_nav = current,nav
                else: previous, previous_nav = self._value(genesis,as_of_date-timedelta(days=1),flows,calls,diagnostic)
            require(previous_nav > ZERO,"BENCHMARK_PREVIOUS_NAV_ZERO")
            with localcontext(DECIMAL_CONTEXT):
                daily = ZERO if as_of_date == genesis.genesis_date else nav/previous_nav-ONE
                cumulative = nav/genesis.initial_capital_rub-ONE
            result = OfzBenchmarkDailyViewV1(**base,status="READY",benchmark_nav_rub=nav,previous_nav_rub=previous_nav,
                daily_return=daily,cumulative_return=cumulative,components=current,previous_components=previous)
        except ValueError as exc:
            result = OfzBenchmarkDailyViewV1(**base,status="UNAVAILABLE",blockers=(evidence_code(exc,"INPUT_EVIDENCE_INVALID"),))
        inputs_hash = digest(dict(genesis=genesis.benchmark_genesis_sha256,as_of_date=as_of_date,
            cashflows=flows,market_evidence=calls))
        result = result.model_copy(update=dict(cashflow_evidence=flows,cashflow_query_count=query_count,
            dirty_value_call_count=len(calls),diagnostics=tuple(sorted(diagnostic)),benchmark_daily_input_sha256=inputs_hash))
        return signed(result,"benchmark_daily_sha256")

    def _value(self, genesis, day, flows, calls, diagnostics):
        positions = []; weighted = ZERO
        selected_ids = {c.representative.bond_id for c in genesis.components}
        relevant = [f for f in flows if f.event_date <= day]
        keys = [(f.bond_id,f.event_date,f.event_type) for f in relevant]
        require(len(keys) == len(set(keys)),"BENCHMARK_CASHFLOW_DUPLICATE")
        with localcontext(DECIMAL_CONTEXT):
            for c in genesis.components:
                bond_id = c.representative.bond_id
                cash = ZERO; redeemed = False
                for f in (f for f in relevant if f.bond_id == bond_id):
                    require(type(f.source_event_id) is int and f.source_event_id > 0 and
                        f.bond_id in selected_ids and f.source == "moex" and
                        genesis.genesis_date < f.event_date <= day,"BENCHMARK_CASHFLOW_EVIDENCE_INVALID")
                    if f.event_type == "offer_redemption":
                        diagnostics.add("OFFER_REDEMPTION_NOT_AUTO_EXERCISED"); continue
                    require(f.event_type in ("coupon","amortization","redemption"),"BENCHMARK_UNKNOWN_CASHFLOW_TYPE")
                    require(not redeemed,"BENCHMARK_REDEMPTION_CONFLICT")
                    require(f.amount is not None,"BENCHMARK_CASHFLOW_AMOUNT_MISSING")
                    require(f.currency == "RUB" and finite(f.amount) and f.amount >= ZERO,"BENCHMARK_CASHFLOW_EVIDENCE_INVALID")
                    cash += f.amount
                    if f.event_type == "redemption": redeemed = True
                view = None
                if redeemed: dirty = ZERO
                elif day == genesis.genesis_date: dirty = c.genesis_dirty_value_rub; view = c.genesis_market_evidence
                else:
                    view = BondDv01Service(self.db).build_for_bond(bond_id,day,market_source="moex",max_market_age_days=7)
                    captured = safe_evidence(view,BondDv01View)
                    calls.append(captured if captured is not None else dict(bond_id=bond_id,as_of_date=day,status="INVALID_EVIDENCE"))
                    try: validate_mark(view,bond_id,day)
                    except ValueError: raise ValueError("BENCHMARK_MARKET_VALUE_UNAVAILABLE") from None
                    require((view.isin,view.secid) == (c.representative.isin,c.representative.secid),"BENCHMARK_MARKET_EVIDENCE_MISMATCH")
                    dirty = view.dirty_value_currency
                total = dirty+cash
                factor = ONE if day == genesis.genesis_date else total/c.genesis_dirty_value_rub
                weighted += c.weight*factor
                positions.append(BenchmarkComponentDailyV1(bond_id=bond_id,status="REDEEMED" if redeemed else "ACTIVE",
                    weight=c.weight,cash_per_original_bond_rub=cash,dirty_value_rub=dirty,
                    total_value_per_original_bond_rub=total,total_return_factor=factor,market_evidence=view))
            nav = genesis.initial_capital_rub*weighted
            require(finite(nav) and nav >= ZERO,"BENCHMARK_DECIMAL_INVARIANTS_FAILED")
            if day == genesis.genesis_date: require(nav == genesis.initial_capital_rub,"BENCHMARK_DECIMAL_INVARIANTS_FAILED")
        return tuple(positions),nav
