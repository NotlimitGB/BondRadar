"""Pure floor-to-target Shadow representation and narrow execution evidence gates."""

from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from app.schemas.portfolio_strategy import PortfolioStrategyView
from app.schemas.risk_engine import ProposedRiskPortfolio, ProposedRiskPosition
from app.schemas.shadow_execution import (
    ShadowExecutionTermsView, ShadowExecutionTermsBatchView, ShadowExecutionPositionView,
    ShadowExecutionSummary, ShadowExecutionProvenance, ShadowExecutionPlanView,
)
from app.services.risk_candidate_reducer import _validated, _require, _finite, validate_risk_batch
from app.services.portfolio_risk_evaluator import PortfolioRiskEvaluator


def validate_strategy(strategy):
    """Validate supplied envelopes and trace; never re-run selection or upstream risk."""
    _validated(strategy, PortfolioStrategyView)
    risk = strategy.source_risk_batch
    validate_risk_batch(risk)
    _require(strategy.source_investment_batch == risk.source_batch)
    _require((strategy.as_of_date, strategy.market_source) == (risk.as_of_date, risk.market_source))
    capital, policy = strategy.request.capital_rub, strategy.policy
    candidates = {c.bond_id:c for c in risk.candidates}
    ranked = strategy.source_investment_batch.ranked_bond_ids
    selected = tuple(p.bond_id for p in strategy.positions)
    _require(len(set(selected)) == len(selected) and selected == tuple(i for i in ranked if i in selected))
    _require(len(selected) <= policy.max_positions)
    final = strategy.final_risk_evaluation
    _require(final.status == "PASS" and final.reasons == () and final.source_risk_batch == risk and final.policy == risk.policy)
    _require((final.as_of_date, final.market_source) == (strategy.as_of_date, strategy.market_source))
    _require(final.proposal.capital_rub == capital)
    final_positions = {p.bond_id:p for p in final.positions}
    _require(len(final_positions) == len(final.positions) and set(final_positions) == set(selected))
    _require(tuple(p.bond_id for p in final.proposal.positions) == tuple(sorted(selected)))
    _require(tuple(p.bond_id for p in final.positions) == tuple(sorted(selected)))
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        weights, top_ups, last_seeds = {}, {}, {}
        last_order = (0, 0, 0)
        for number, t in enumerate(strategy.allocation_attempts,1):
            _require(t.sequence_number == number and 1 <= t.round_number <= strategy.summary.round_count)
            _require(t.bond_id in candidates and candidates[t.bond_id].status == "ELIGIBLE")
            _require(t.investment_rank == candidates[t.bond_id].source_evaluation.rank)
            order = (t.round_number, 0 if t.phase == "SEED" else 1, t.investment_rank)
            _require(order > last_order); last_order = order
            previous = weights.get(t.bond_id, Decimal("0"))
            total = sum((weights[i] for i in sorted(weights)), Decimal("0"))
            _require(t.previous_weight == previous and t.previous_invested_weight == total)
            increment = policy.min_initial_position_weight if t.phase == "SEED" else policy.allocation_increment
            _require((previous == 0) if t.phase == "SEED" else (previous > 0))
            _require(t.attempted_weight == previous+increment and t.attempted_invested_weight == total+increment)
            _require(t.attempted_invested_weight <= policy.target_invested_weight)
            _require(t.accepted is (t.risk_status == "PASS") and t.risk_reasons == tuple(sorted(set(t.risk_reasons))))
            _require(not t.accepted or not t.risk_reasons)
            if t.phase == "SEED": last_seeds[t.bond_id] = t.risk_reasons
            if t.accepted:
                weights[t.bond_id] = t.attempted_weight
                if t.phase == "TOP_UP": top_ups[t.bond_id] = top_ups.get(t.bond_id,0)+1
        _require(weights == {p.bond_id:p.target_weight for p in strategy.positions})
        _require(1 <= strategy.summary.round_count <= 101)
        _require(strategy.summary.round_count in (last_order[0], last_order[0]+1) if strategy.allocation_attempts else strategy.summary.round_count == 1)
        _require(final.proposal == ProposedRiskPortfolio(capital_rub=capital, positions=tuple(
            ProposedRiskPosition(bond_id=i, target_weight=weights[i]) for i in sorted(weights))))
        for p in strategy.positions:
            c = candidates[p.bond_id]; e = c.source_evaluation; f = e.candidate.features
            _require(c.status == "ELIGIBLE" and e.status == "READY" and p.source_risk_candidate == c and p.source_evaluation == e)
            _require((p.isin,p.secid,p.investment_rank,p.investment_score_v1,p.legal_issuer_id) ==
                     (e.isin,e.secid,e.rank,e.investment_score_v1,c.legal_issuer_id))
            _require(p.initial_seed_weight == policy.min_initial_position_weight and p.accepted_top_up_count == top_ups.get(p.bond_id,0))
            _require(p.target_weight >= policy.min_initial_position_weight and p.target_weight % policy.allocation_increment == 0)
            _require(p.target_amount_rub == capital*p.target_weight)
            r = final_positions[p.bond_id]
            _require(r.status == "PASS" and r.reasons == () and r.candidate == c and r.legal_issuer_id == c.legal_issuer_id)
            _require((r.requested_weight,r.position_amount_rub,r.position_dv01_rub_per_1bp,r.liquidity_capacity_amount_rub) ==
                     (p.target_weight,p.target_amount_rub,p.position_dv01_rub_per_1bp,p.liquidity_capacity_amount_rub))
            _require((p.modified_duration_years,p.relative_price_sensitivity_per_1bp,p.liquidity_capacity_amount_rub) ==
                     (c.modified_duration_years,c.relative_price_sensitivity_per_1bp,c.liquidity_capacity_amount_rub))
            d = e.candidate.m3.dv01
            _require(d.status == "READY" and d.contract_version == "bond-dv01-v1")
            _require((d.bond_id,d.isin,d.secid,d.as_of_date,d.market_source,d.market_snapshot_id,d.market_trade_date) ==
                     (p.bond_id,p.isin,p.secid,strategy.as_of_date,strategy.market_source,f.market_snapshot_id,f.market_trade_date))
            _require(d.provenance.security_master_profile_id == e.candidate.provenance.security_master_profile_id)
            _require(type(e.candidate.provenance.security_master_profile_id) is int and e.candidate.provenance.security_master_profile_id > 0)
            _require((d.nominal_value,d.currency_code,d.relative_price_sensitivity_per_1bp,d.dv01_currency_per_bond) ==
                     (f.nominal_value,f.currency_code,f.relative_price_sensitivity_per_1bp,f.dv01_currency_per_bond))
            for value in (d.dirty_value_currency,d.clean_value_currency,d.clean_quote_pct):
                _finite(value); _require(value > 0)
            _finite(d.nkd_currency,Decimal("0"))
            _require(d.price_basis in ("CLEAN_PRICE","PRICE_FALLBACK"))
        non_selected = strategy.non_selections
        _require(len({n.bond_id for n in non_selected}) == len(non_selected))
        expected_non = sorted((c for c in risk.candidates if c.bond_id not in selected),
            key=lambda c:(c.source_evaluation.rank is None,c.source_evaluation.rank or 0,c.bond_id))
        _require(tuple(n.bond_id for n in non_selected) == tuple(c.bond_id for c in expected_non))
        m = final.metrics; s = strategy.summary
        for n,c in zip(non_selected,expected_non):
            _require((n.investment_rank,n.investment_status,n.risk_candidate_status,n.risk_reasons,n.final_attempt_risk_reasons) ==
                     (c.source_evaluation.rank,c.source_evaluation.status,c.status,c.reasons,last_seeds.get(c.bond_id,())))
            reason = ("INVESTMENT_MODEL_NOT_READY" if c.source_evaluation.status != "READY" else
                "RISK_CANDIDATE_BLOCKED" if c.status != "ELIGIBLE" else
                "TARGET_INVESTED_WEIGHT_REACHED" if m.invested_weight == policy.target_invested_weight else
                "MAX_POSITIONS_REACHED" if len(selected) == policy.max_positions else
                "MIN_INITIAL_POSITION_NOT_ADMISSIBLE" if last_seeds.get(c.bond_id) else "NOT_SELECTED_AFTER_CONVERGENCE")
            _require(n.reason == reason)
        expected_counts = dict(candidate_count=risk.candidate_count,investment_ready_count=risk.source_batch.ready_count,
            risk_eligible_count=risk.eligible_count,selected_count=len(selected),non_selected_count=len(non_selected),
            allocation_attempt_count=len(strategy.allocation_attempts),accepted_attempt_count=sum(t.accepted for t in strategy.allocation_attempts),
            rejected_attempt_count=sum(not t.accepted for t in strategy.allocation_attempts))
        _require(all(getattr(s,name) == value for name,value in expected_counts.items()))
        _require(m.position_count == len(selected) and m.issuer_concentration_complete and m.ungrouped_bond_ids == ())
        issuer_ids = tuple(sorted({candidates[i].legal_issuer_id for i in selected}))
        _require(tuple(group.legal_issuer_id for group in final.issuer_concentrations) == issuer_ids and m.issuer_count == len(issuer_ids))
        for group in final.issuer_concentrations:
            _require(group.status == "PASS" and group.bond_ids == tuple(sorted(i for i in selected if candidates[i].legal_issuer_id == group.legal_issuer_id)))
            _finite(group.headroom,Decimal("0")); _finite(group.total_weight,Decimal("0"),Decimal("1"))
        _require(final.provenance == risk.provenance.model_copy(update={"proposed_bond_ids":tuple(sorted(selected)),"legal_issuer_ids":issuer_ids}))
        for name in ("invested_weight","cash_weight","invested_capital_rub","cash_rub","max_position_weight_observed",
                     "max_issuer_weight_observed","capital_weighted_modified_duration_years","portfolio_dv01_rub_per_1bp",
                     "portfolio_relative_dv01_per_1bp","portfolio_dv01_per_100k_rub"):
            _finite(getattr(m,name),Decimal("0"))
        if selected: _finite(m.invested_weighted_modified_duration_years,Decimal("0"))
        else: _require(m.invested_weighted_modified_duration_years is None)
        _require(m.invested_weight == sum((weights[i] for i in sorted(weights)),Decimal("0")))
        _require(m.capital_rub == capital and m.invested_capital_rub == capital*m.invested_weight and
                 m.cash_weight == Decimal("1")-m.invested_weight and m.cash_rub == capital*m.cash_weight)
        _require((s.capital_rub,s.invested_weight,s.cash_weight,s.invested_capital_rub,s.cash_rub,
            s.final_max_position_weight,s.final_max_issuer_weight,s.final_invested_weighted_modified_duration_years,
            s.final_portfolio_relative_dv01_per_1bp,s.final_portfolio_dv01_per_100k_rub) ==
            (m.capital_rub,m.invested_weight,m.cash_weight,m.invested_capital_rub,m.cash_rub,
             m.max_position_weight_observed,m.max_issuer_weight_observed,m.invested_weighted_modified_duration_years,
             m.portfolio_relative_dv01_per_1bp,m.portfolio_dv01_per_100k_rub))
        expected_status = "FULLY_INVESTED" if m.invested_weight == policy.target_invested_weight else "PARTIALLY_INVESTED" if m.invested_weight else "EMPTY"
        _require(strategy.status == expected_status)
        p = strategy.provenance
        _require((p.as_of_date,p.market_source,p.capital_rub,p.policy,p.candidate_bond_ids,p.ranked_bond_ids,
                  p.risk_eligible_bond_ids,p.selected_bond_ids) ==
                 (strategy.as_of_date,strategy.market_source,capital,policy,risk.candidate_bond_ids,ranked,
                  tuple(i for i in ranked if candidates[i].status == "ELIGIBLE"),selected))


def make_terms(position, row=None):
    """Canonical terms classification shared by loader and supplied-batch validation."""
    expected = position.source_evaluation.candidate.provenance.security_master_profile_id
    identity = dict(bond_id=position.bond_id,isin=position.isin,secid=position.secid,
                    expected_security_master_profile_id=expected)
    fields = ("currency_state","currency_code","nominal_state","nominal_value","lot_size_state",
              "lot_size","trading_board_state","trading_board")
    if row is None:
        return ShadowExecutionTermsView(**identity,loaded_security_master_profile_id=None,
            security_master_contract_version=None,**{name:None for name in fields},status="UNAVAILABLE",
            blockers=("SECURITY_MASTER_PROFILE_MISSING",))
    _require(type(row["id"]) is int and row["id"] > 0 and row["bond_id"] == position.bond_id and type(row["bond_id"]) is int)
    _require(type(row["contract_version"]) is str and bool(row["contract_version"].strip()))
    for state,field,kind in (("currency_state","currency_code",str),("nominal_state","nominal_value",Decimal),
                             ("lot_size_state","lot_size",int),("trading_board_state","trading_board",str)):
        _require(row[state] in ("unknown","verified","conflict") and type(row[state]) is str)
        value = row[field]
        if row[state] != "verified": _require(value is None)
        else:
            _require(type(value) is kind)
            if kind is Decimal: _finite(value); _require(value > 0)
            elif kind is int: _require(value > 0)
            else: _require(bool(value.strip()))
    blockers = []
    f = position.source_evaluation.candidate.features
    if row["id"] != expected: blockers.append("SECURITY_MASTER_PROFILE_ID_MISMATCH")
    if row["contract_version"] != "bond-security-master-v2": blockers.append("SECURITY_MASTER_CONTRACT_MISMATCH")
    if row["currency_state"] != "verified": blockers.append("CURRENCY_NOT_VERIFIED")
    elif row["currency_code"] != "RUB" or row["currency_code"] != f.currency_code: blockers.append("CURRENCY_MISMATCH")
    if row["nominal_state"] != "verified": blockers.append("NOMINAL_NOT_VERIFIED")
    elif row["nominal_value"] != f.nominal_value: blockers.append("NOMINAL_MISMATCH")
    if row["lot_size_state"] != "verified": blockers.append("LOT_SIZE_NOT_VERIFIED")
    if row["trading_board_state"] != "verified": blockers.append("TRADING_BOARD_NOT_VERIFIED")
    if any(b in blockers for b in ("SECURITY_MASTER_PROFILE_ID_MISMATCH","SECURITY_MASTER_CONTRACT_MISMATCH","CURRENCY_MISMATCH","NOMINAL_MISMATCH")):
        blockers.append("SECURITY_MASTER_PROFILE_DRIFT")
    return ShadowExecutionTermsView(**identity,loaded_security_master_profile_id=row["id"],
        security_master_contract_version=row["contract_version"],**{name:row[name] for name in fields},
        status="UNAVAILABLE" if blockers else "READY",blockers=tuple(sorted(set(blockers))))


def make_terms_batch(strategy, terms):
    return ShadowExecutionTermsBatchView(as_of_date=strategy.as_of_date,market_source=strategy.market_source,
        strategy_selected_bond_ids=tuple(p.bond_id for p in strategy.positions),
        ready_bond_ids=tuple(t.bond_id for t in terms if t.status == "READY"),
        unavailable_bond_ids=tuple(t.bond_id for t in terms if t.status == "UNAVAILABLE"),
        selected_count=len(terms),ready_count=sum(t.status == "READY" for t in terms),
        unavailable_count=sum(t.status == "UNAVAILABLE" for t in terms),terms=tuple(terms))


def validate_terms(strategy, batch):
    _validated(batch,ShadowExecutionTermsBatchView)
    _require(len(batch.terms) == len(strategy.positions))
    for p,t in zip(strategy.positions,batch.terms):
        row = None if t.loaded_security_master_profile_id is None else dict(id=t.loaded_security_master_profile_id,
            bond_id=t.bond_id,contract_version=t.security_master_contract_version,currency_state=t.currency_state,
            currency_code=t.currency_code,nominal_state=t.nominal_state,nominal_value=t.nominal_value,
            lot_size_state=t.lot_size_state,lot_size=t.lot_size,trading_board_state=t.trading_board_state,trading_board=t.trading_board)
        _require(t == make_terms(p,row))
    _require(batch == make_terms_batch(strategy,batch.terms))


def exact_lot_floor(target, lot_value):
    _finite(target,Decimal("0")); _finite(lot_value); _require(lot_value > 0)
    tn,td = target.as_integer_ratio(); ln,ld = lot_value.as_integer_ratio()
    return (tn*ld)//(td*ln)


class ShadowExecutionPlanner:
    @staticmethod
    def build(strategy, terms_batch):
        validate_strategy(strategy)
        validate_terms(strategy,terms_batch)
        capital = strategy.request.capital_rub
        positions, flags = [], set()
        with localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
            for p,t in zip(strategy.positions,terms_batch.terms):
                d = p.source_evaluation.candidate.m3.dv01
                lot_value = d.dirty_value_currency*t.lot_size if t.status == "READY" else None
                lots = exact_lot_floor(p.target_amount_rub,lot_value) if lot_value is not None else 0
                quantity = lots*t.lot_size if lots else 0
                cost = lots*lot_value if lots else Decimal("0")
                realized = cost/capital
                shortfall = p.target_amount_rub-cost
                _require(cost <= p.target_amount_rub and shortfall >= 0 and realized <= p.target_weight)
                status = "TERMS_UNAVAILABLE" if t.status != "READY" else "PLANNED" if lots else "TARGET_BELOW_ONE_LOT"
                if status == "TERMS_UNAVAILABLE": flags.add("EXECUTION_TERMS_UNAVAILABLE")
                if status == "TARGET_BELOW_ONE_LOT": flags.add(status)
                positions.append(ShadowExecutionPositionView(bond_id=p.bond_id,isin=p.isin,secid=p.secid,
                    investment_rank=p.investment_rank,target_weight=p.target_weight,target_amount_rub=p.target_amount_rub,
                    terms_status=t.status,terms_blockers=t.blockers,security_master_profile_id=t.loaded_security_master_profile_id,
                    lot_size=t.lot_size,trading_board=t.trading_board,market_snapshot_id=d.market_snapshot_id,
                    market_trade_date=d.market_trade_date,price_basis=d.price_basis,clean_quote_pct=d.clean_quote_pct,
                    nominal_value=d.nominal_value,nkd_currency=d.nkd_currency,clean_value_currency=d.clean_value_currency,
                    dirty_value_currency=d.dirty_value_currency,lot_dirty_value_rub=lot_value,planned_lot_count=lots,
                    planned_bond_quantity=quantity,planned_cash_cost_rub=cost,target_shortfall_rub=shortfall,
                    realized_shadow_weight=realized,target_weight_gap=p.target_weight-realized,status=status,
                    source_strategy_position=p,execution_terms=t))
            invested = sum((p.planned_cash_cost_rub for p in positions),Decimal("0"))
            weight = sum((p.realized_shadow_weight for p in positions),Decimal("0"))
            gap = strategy.summary.invested_capital_rub-invested
            cash = strategy.summary.cash_rub+gap
            _require(cash == capital-invested and cash >= 0 and gap >= 0)
            _require(sum((p.target_shortfall_rub for p in positions),Decimal("0")) == gap)
            _require(weight <= strategy.summary.invested_weight)
            planned = tuple(p for p in positions if p.status == "PLANNED")
            proposal = ProposedRiskPortfolio(capital_rub=capital,positions=tuple(
                ProposedRiskPosition(bond_id=p.bond_id,target_weight=p.realized_shadow_weight) for p in sorted(planned,key=lambda p:p.bond_id)))
            risk = PortfolioRiskEvaluator.build(strategy.source_risk_batch,proposal)
            _require(risk.proposal == proposal and risk.source_risk_batch == strategy.source_risk_batch)
            _require(risk.status in ("PASS","BLOCKED") and (bool(planned) or risk.status == "PASS"))
            if risk.status == "BLOCKED": status = "RISK_BLOCKED"; flags.add("POST_ROUNDING_RISK_BLOCKED")
            elif not positions: status = "EMPTY"
            elif not planned: status = "UNEXECUTABLE"
            elif len(planned) < len(positions): status = "PARTIAL"
            else: status = "READY"
            summary = ShadowExecutionSummary(capital_rub=capital,strategy_selected_count=len(positions),
                strategy_target_invested_weight=strategy.summary.invested_weight,strategy_target_invested_rub=strategy.summary.invested_capital_rub,
                terms_ready_count=terms_batch.ready_count,terms_unavailable_count=terms_batch.unavailable_count,
                planned_position_count=len(planned),zero_lot_position_count=len(positions)-len(planned),
                planned_total_lot_count=sum(p.planned_lot_count for p in positions),
                planned_total_bond_quantity=sum(p.planned_bond_quantity for p in positions),
                planned_shadow_invested_rub=invested,shadow_cash_rub=cash,realized_invested_weight=weight,
                realized_cash_weight=Decimal("1")-weight,execution_tracking_gap_rub=gap,
                execution_tracking_gap_weight=strategy.summary.invested_weight-weight,target_amount_shortfall_rub=gap,
                post_rounding_risk_status=risk.status)
        return ShadowExecutionPlanView(status=status,as_of_date=strategy.as_of_date,market_source=strategy.market_source,
            positions=tuple(positions),summary=summary,execution_terms_batch=terms_batch,source_strategy=strategy,
            post_rounding_risk_evaluation=risk,quality_flags=tuple(sorted(flags)),
            provenance=ShadowExecutionProvenance(selected_bond_ids=terms_batch.strategy_selected_bond_ids,
                terms_ready_bond_ids=terms_batch.ready_bond_ids,planned_bond_ids=tuple(p.bond_id for p in planned),
                zero_lot_bond_ids=tuple(p.bond_id for p in positions if p.status != "PLANNED"),
                market_snapshot_ids=tuple(p.market_snapshot_id for p in positions),
                security_master_profile_ids=tuple(p.security_master_profile_id for p in planned),
                dirty_value_formula_versions=tuple(p.source_strategy_position.source_evaluation.candidate.m3.dv01.provenance.dv01_formula_version for p in positions),
                as_of_date=strategy.as_of_date,market_source=strategy.market_source,capital_rub=capital))
