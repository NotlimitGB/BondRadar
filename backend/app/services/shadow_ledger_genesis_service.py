"""Explicitly authorized, atomic genesis from a frozen Task302 plan."""
from datetime import date, timedelta
from decimal import Decimal, localcontext
import re
from sqlalchemy import select
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.schemas.shadow_ledger import (
    ShadowGenesisRequestV1 as Request, ShadowGenesisPlanV1 as Plan,
    ShadowGenesisAuthorizationV1 as Authorization, ShadowGenesisApplyReceiptV1 as Receipt,
)
from app.services.shadow_execution_planner import validate_strategy, validate_terms, exact_lot_floor
from app.services import shadow_ledger_repository as repository


def validate_request(request):
    repository.strict(request, Request)
    repository.require(re.fullmatch(r"[0-9a-f]{40}", request.source_code_sha) is not None, "INPUT_INVALID")
    s = request.shadow_execution
    repository.require(type(s.as_of_date) is date and s.market_source == "moex" and s.pit_ready is False, "INPUT_INVALID")
    validate_strategy(s.source_strategy)
    validate_terms(s.source_strategy, s.execution_terms_batch)
    repository.require(s.status == "READY" and s.quality_flags == () and s.positions and
        s.post_rounding_risk_evaluation.status == "PASS" and not s.post_rounding_risk_evaluation.reasons and
        s.execution_terms_batch.unavailable_count == 0 and s.summary.zero_lot_position_count == 0, "GENESIS_NOT_READY")
    repository.require((s.as_of_date, s.market_source) == (s.source_strategy.as_of_date, s.source_strategy.market_source), "INPUT_INVALID")
    repository.require(len(s.positions) == len(s.source_strategy.positions) == s.summary.planned_position_count, "GENESIS_NOT_READY")
    risk = s.post_rounding_risk_evaluation
    repository.require(risk.source_risk_batch == s.source_strategy.source_risk_batch and
        risk.proposal.capital_rub == s.summary.capital_rub and
        (risk.as_of_date, risk.market_source) == (s.as_of_date, s.market_source), "INPUT_INVALID")
    repository.require(risk.policy == s.source_strategy.source_risk_batch.policy and risk.quality_flags == () and
        tuple(p.bond_id for p in risk.positions) == tuple(p.bond_id for p in risk.proposal.positions) and
        all(p.status == "PASS" and not p.reasons and p.candidate.status == "ELIGIBLE" and
            p.requested_weight == proposed.target_weight and p.bond_id == proposed.bond_id
            for p,proposed in zip(risk.positions,risk.proposal.positions)) and
        all(group.status == "PASS" for group in risk.issuer_concentrations) and
        risk.metrics.issuer_concentration_complete is True and risk.metrics.ungrouped_bond_ids == (), "INPUT_INVALID")
    with localcontext(repository.DECIMAL_CONTEXT):
        for p, source, terms in zip(s.positions, s.source_strategy.positions, s.execution_terms_batch.terms):
            d = source.source_evaluation.candidate.m3.dv01
            repository.require(p.source_strategy_position == source and p.execution_terms == terms and p.bond_id == source.bond_id and
                p.status == "PLANNED" and p.terms_status == "READY" and p.terms_blockers == () and
                terms.status == "READY" and p.trading_board == terms.trading_board == "TQCB" and
                type(p.planned_lot_count) is int and p.planned_lot_count > 0 and
                type(p.planned_bond_quantity) is int and p.planned_bond_quantity > 0 and
                type(p.lot_size) is int and p.lot_size == terms.lot_size and p.lot_size > 0, "GENESIS_NOT_READY")
            for name in ("dirty_value_currency", "clean_quote_pct", "nominal_value", "nkd_currency", "clean_value_currency",
                         "market_snapshot_id", "market_trade_date", "price_basis"):
                repository.require(getattr(p, name) == getattr(d, name), "INPUT_INVALID")
            repository.require(repository.finite(p.dirty_value_currency, positive=True) and repository.finite(p.planned_cash_cost_rub, positive=True), "GENESIS_NOT_READY")
            repository.require(p.security_master_profile_id == terms.loaded_security_master_profile_id and
                p.investment_rank == source.investment_rank and p.isin == source.isin and p.secid == source.secid and
                p.target_weight == source.target_weight and p.target_amount_rub == source.target_amount_rub, "INPUT_INVALID")
            repository.require(p.lot_dirty_value_rub == p.dirty_value_currency * p.lot_size and
                p.planned_lot_count == exact_lot_floor(p.target_amount_rub, p.lot_dirty_value_rub) and
                p.planned_bond_quantity == p.planned_lot_count * p.lot_size and
                p.planned_cash_cost_rub == p.planned_bond_quantity * p.dirty_value_currency and
                p.target_shortfall_rub == p.target_amount_rub - p.planned_cash_cost_rub and
                p.realized_shadow_weight == p.planned_cash_cost_rub / s.summary.capital_rub and
                p.target_weight_gap == p.target_weight - p.realized_shadow_weight, "GENESIS_RECONCILIATION_FAILED")
        capital = s.source_strategy.request.capital_rub
        invested = sum((p.planned_cash_cost_rub for p in s.positions), repository.ZERO)
        weight = sum((p.realized_shadow_weight for p in s.positions), repository.ZERO)
        gap = s.source_strategy.summary.invested_capital_rub - invested
        m = s.summary
        expected = dict(capital_rub=capital, strategy_selected_count=len(s.positions),
            strategy_target_invested_weight=s.source_strategy.summary.invested_weight,
            strategy_target_invested_rub=s.source_strategy.summary.invested_capital_rub,
            terms_ready_count=len(s.positions), terms_unavailable_count=0, planned_position_count=len(s.positions),
            zero_lot_position_count=0, planned_total_lot_count=sum(p.planned_lot_count for p in s.positions),
            planned_total_bond_quantity=sum(p.planned_bond_quantity for p in s.positions), planned_shadow_invested_rub=invested,
            shadow_cash_rub=capital-invested, realized_invested_weight=weight, realized_cash_weight=Decimal(1)-weight,
            execution_tracking_gap_rub=gap, execution_tracking_gap_weight=s.source_strategy.summary.invested_weight-weight,
            target_amount_shortfall_rub=gap, post_rounding_risk_status="PASS")
        repository.require(m.model_dump() == expected and capital == invested + m.shadow_cash_rub and m.shadow_cash_rub >= 0, "GENESIS_RECONCILIATION_FAILED")
        repository.require(tuple(p.bond_id for p in risk.proposal.positions) == tuple(sorted(p.bond_id for p in s.positions)) and
            {p.bond_id:p.target_weight for p in risk.proposal.positions} == {p.bond_id:p.realized_shadow_weight for p in s.positions}, "INPUT_INVALID")
    p = s.provenance
    repository.require(p.selected_bond_ids == tuple(x.bond_id for x in s.positions) and p.planned_bond_ids == p.selected_bond_ids and
        p.terms_ready_bond_ids == p.selected_bond_ids and p.zero_lot_bond_ids == () and
        p.market_snapshot_ids == tuple(x.market_snapshot_id for x in s.positions) and
        p.security_master_profile_ids == tuple(x.security_master_profile_id for x in s.positions) and
        (p.as_of_date,p.market_source,p.capital_rub) == (s.as_of_date,s.market_source,capital), "INPUT_INVALID")
    return s


def genesis_fields(plan, request):
    s = request.shadow_execution
    return dict(run_key_sha256=plan.run_key_sha256, status="ACTIVE", genesis_date=s.as_of_date,
        planned_end_date=plan.planned_end_date, horizon_days=90, initial_capital_rub=s.summary.capital_rub,
        base_currency="RUB", market_source="moex", source_code_sha=request.source_code_sha,
        source_universe_sha256=plan.source_universe_sha256, shadow_execution_sha256=plan.shadow_execution_sha256, genesis_plan_sha256=plan.plan_sha256,
        shadow_execution_contract_version=s.contract_version, strategy_contract_version=s.source_strategy.contract_version,
        strategy_policy_version=s.provenance.strategy_policy_version, risk_policy_version=s.provenance.risk_policy_version,
        execution_policy_version=s.policy.contract_version)


def original_genesis_plan(plan):
    """Recover the first authorized plan; repeat authorization has a new DB hash."""
    return repository.signed_plan(plan.model_copy(update={
        "status": "EXECUTABLE", "blockers": (),
        "current_shadow_db_state_sha256": repository.state_hash(
            dict(run=None, ledger=[], snapshots=[], positions=[])),
    }))


def build_genesis_plan_in_session(db, *, request):
    """Read-only Genesis builder in a caller-owned transaction; never closes it."""
    with db.no_autoflush:
        s = validate_request(request)
        universe = s.source_strategy.source_investment_batch.source_batch
        universe_hash = repository.digest({name:getattr(universe, name) for name in
            ("as_of_date", "market_source", "requested_bond_ids", "existing_bond_ids", "missing_bond_ids", "candidate_bond_ids")})
        execution_hash = repository.digest(s)
        key = repository.digest(dict(shadow_execution_sha256=execution_hash, source_universe_sha256=universe_hash,
            source_code_sha=request.source_code_sha, horizon_days=90))
        state = repository.read_state(db, key)
        base = dict(run_key_sha256=key, as_of_date=s.as_of_date, planned_end_date=s.as_of_date+timedelta(days=90),
            shadow_execution_sha256=execution_hash, source_universe_sha256=universe_hash, source_code_sha=request.source_code_sha,
            current_shadow_db_state_sha256=repository.state_hash(state), input_state_sha256=execution_hash)
        # Physical references must exist; do not create or repair source evidence.
        with db.no_autoflush:
            for p in s.positions:
                bond = db.execute(select(Bond.id, Bond.isin, Bond.secid).where(Bond.id == p.bond_id)).one_or_none()
                market = db.execute(select(BondMarketSnapshot.bond_id, BondMarketSnapshot.trade_date, BondMarketSnapshot.source).where(BondMarketSnapshot.id == p.market_snapshot_id)).one_or_none()
                profile = db.execute(select(BondSecurityMasterProfile.bond_id).where(BondSecurityMasterProfile.id == p.security_master_profile_id)).scalar_one_or_none()
                repository.require(bond is not None and (bond.isin,bond.secid) == (p.isin,p.secid) and
                    market == (p.bond_id,p.market_trade_date,"moex") and profile == p.bond_id, "SOURCE_REFERENCE_MISSING")
        events = [repository.event(key, event_date=s.as_of_date, sequence_number=1, event_type="INITIAL_CAPITAL", bond_id=None,
            quantity_delta=0, cash_delta_rub=s.summary.capital_rub, unit_amount_rub=None,
            source_contract_version=s.contract_version, source_fingerprint_sha256=execution_hash)]
        positions = []
        with localcontext(repository.DECIMAL_CONTEXT):
            for n, p in enumerate(sorted(s.positions, key=lambda p:p.bond_id), 2):
                events.append(repository.event(key, event_date=s.as_of_date, sequence_number=n, event_type="GENESIS_PURCHASE",
                    bond_id=p.bond_id, quantity_delta=p.planned_bond_quantity, cash_delta_rub=-p.planned_cash_cost_rub,
                    unit_amount_rub=p.dirty_value_currency, source_market_snapshot_id=p.market_snapshot_id,
                    source_contract_version=s.contract_version, source_fingerprint_sha256=repository.digest(p)))
                positions.append(repository.position(bond_id=p.bond_id, position_status="ACTIVE", quantity=p.planned_bond_quantity,
                    market_snapshot_id=p.market_snapshot_id, market_trade_date=p.market_trade_date,
                    market_age_days=(s.as_of_date-p.market_trade_date).days, price_basis=p.price_basis,
                    dirty_value_rub_per_bond=p.dirty_value_currency, market_value_rub=p.planned_cash_cost_rub,
                    cashflow_rub_on_date=repository.ZERO, source_dv01_contract_version="bond-dv01-v1", source_security_master_profile_id=p.security_master_profile_id))
        snap = repository.snapshot(as_of_date=s.as_of_date, previous_snapshot_sha256=None, cash_rub=s.summary.shadow_cash_rub,
            market_value_rub=s.summary.planned_shadow_invested_rub, nav_rub=s.summary.capital_rub, daily_return=repository.ZERO,
            cumulative_return=repository.ZERO, active_position_count=len(positions), redeemed_position_count=0,
            ledger_entry_count_to_date=len(events), applied_cashflow_count_for_day=0, input_state_sha256=execution_hash)
        status, blockers = "EXECUTABLE", ()
        if state["run"]:
            existing = next((x for x in state["snapshots"] if x["as_of_date"] == s.as_of_date), None)
            if existing and repository.stored_snapshot(existing) == snap and repository.audit_genesis(db, original_genesis_plan(
                    repository.signed_plan(Plan(**base, status="EXECUTABLE", events=tuple(events), positions=tuple(positions), snapshot=snap)))).status == "VERIFIED":
                status = "IDEMPOTENT_NOOP"
            else:
                status, blockers = "BLOCKED", ("HISTORICAL_SOURCE_DRIFT",)
        return repository.signed_plan(Plan(**base, status=status, events=tuple(events), positions=tuple(positions), snapshot=snap, blockers=blockers))


class ShadowLedgerGenesisService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def _plan(self, db, request):
        return build_genesis_plan_in_session(db, request=request)

    def plan(self, *, request):
        try:
            validate_request(request)
            db = repository.fresh(self.session_factory)
        except Exception as exc:
            return Plan(status="BLOCKED", blockers=(str(exc) if str(exc) in repository.Blocker.__args__ else "INPUT_INVALID",))
        try:
            return self._plan(db, request)
        except Exception as exc:
            return Plan(status="BLOCKED", blockers=(str(exc) if str(exc) in repository.Blocker.__args__ else "INPUT_INVALID",))
        finally:
            db.close()

    def apply(self, *, request, reviewed_plan, authorization):
        try:
            validate_request(request)
            repository.strict(reviewed_plan, Plan)
            repository.strict(authorization, Authorization)
        except Exception:
            return Receipt(status="BLOCKED", blockers=("INPUT_INVALID",))
        return repository.execute_apply(self.session_factory, reviewed_plan, authorization,
            lambda db:self._plan(db,request), Receipt, genesis_fields(reviewed_plan,request),
            genesis_audit=lambda db, plan:repository.audit_genesis(db, original_genesis_plan(plan)))
