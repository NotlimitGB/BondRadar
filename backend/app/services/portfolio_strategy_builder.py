"""Rank-ordered progressive target weights using the pure Risk Engine oracle."""

from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext

from app.schemas.investment_model import InvestmentEvaluationBatchView
from app.schemas.risk_engine import RiskCandidateBatchView, ProposedRiskPortfolio, ProposedRiskPosition
from app.schemas.portfolio_strategy import (
    PortfolioStrategyPolicyV1, PortfolioStrategyRequest, PortfolioStrategyPosition,
    PortfolioStrategyNonSelection, PortfolioStrategyAllocationAttempt, PortfolioStrategySummary,
    PortfolioStrategyProvenance, PortfolioStrategyView,
)
from app.services.risk_candidate_reducer import _validated, _require, validate_risk_batch
from app.services.portfolio_risk_evaluator import PortfolioRiskEvaluator


class PortfolioStrategyBuilder:
    @staticmethod
    def build(investment_batch: InvestmentEvaluationBatchView, risk_batch: RiskCandidateBatchView,
              request: PortfolioStrategyRequest) -> PortfolioStrategyView:
        _validated(request, PortfolioStrategyRequest)
        _validated(investment_batch, InvestmentEvaluationBatchView)
        validate_risk_batch(risk_batch)
        _require(investment_batch.model_dump() == risk_batch.source_batch.model_dump())
        policy = PortfolioStrategyPolicyV1()
        evaluations = {e.bond_id: e for e in investment_batch.evaluations}
        candidates = {c.bond_id: c for c in risk_batch.candidates}
        ordered = investment_batch.ranked_bond_ids
        eligible = tuple(i for i in ordered if candidates[i].status == "ELIGIBLE")
        weights, trace, last_seed_reasons, top_ups = {}, [], {}, {}
        rounds = 0

        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            def total():
                return sum((weights[i] for i in sorted(weights)), Decimal("0"))

            def proposal(values):
                return ProposedRiskPortfolio(capital_rub=request.capital_rub, positions=tuple(
                    ProposedRiskPosition(bond_id=i, target_weight=values[i]) for i in sorted(values)))

            def attempt(i, phase, new_weight):
                previous = weights.get(i, Decimal("0"))
                before_total = total()
                proposed = {**weights, i: new_weight}
                result = PortfolioRiskEvaluator.build(risk_batch, proposal(proposed))
                _require(result.status in ("PASS", "BLOCKED") and result.proposal == proposal(proposed))
                accepted = result.status == "PASS"
                trace.append(PortfolioStrategyAllocationAttempt(sequence_number=len(trace)+1,
                    round_number=rounds, phase=phase, bond_id=i, investment_rank=evaluations[i].rank,
                    previous_weight=previous, attempted_weight=new_weight,
                    previous_invested_weight=before_total,
                    attempted_invested_weight=before_total-previous+new_weight,
                    accepted=accepted, risk_status=result.status, risk_reasons=result.reasons))
                if phase == "SEED": last_seed_reasons[i] = result.reasons
                if accepted:
                    weights[i] = new_weight
                    if phase == "TOP_UP": top_ups[i] = top_ups.get(i, 0)+1
                return accepted

            while True:
                rounds += 1
                progress = False
                for i in eligible:
                    if i in weights or len(weights) >= policy.max_positions:
                        continue
                    if total()+policy.min_initial_position_weight > policy.target_invested_weight:
                        continue
                    progress = attempt(i, "SEED", policy.min_initial_position_weight) or progress
                for i in ordered:
                    if i not in weights: continue
                    increased = weights[i]+policy.allocation_increment
                    if (total()+policy.allocation_increment > policy.target_invested_weight or
                            increased > risk_batch.policy.max_position_weight):
                        continue
                    progress = attempt(i, "TOP_UP", increased) or progress
                if total() == policy.target_invested_weight or not progress:
                    break

            final = PortfolioRiskEvaluator.build(risk_batch, proposal(weights))
            _require(final.status == "PASS" and final.proposal == proposal(weights))
            _require(len(weights) <= policy.max_positions and total() <= policy.target_invested_weight)
            replay = {}
            for t in trace:
                _require(replay.get(t.bond_id, Decimal("0")) == t.previous_weight)
                _require(sum((replay[i] for i in sorted(replay)), Decimal("0")) == t.previous_invested_weight)
                if t.accepted: replay[t.bond_id] = t.attempted_weight
            _require(replay == weights)
            for i, weight in weights.items():
                _require(candidates[i].status == "ELIGIBLE" and evaluations[i].status == "READY")
                _require(weight >= policy.min_initial_position_weight and weight % policy.allocation_increment == 0)
            risk_positions = {p.bond_id:p for p in final.positions}
            _require(set(risk_positions) == set(weights))
            positions = []
            for i in ordered:
                if i not in weights: continue
                e, c, p = evaluations[i], candidates[i], risk_positions[i]
                _require(p.requested_weight == weights[i])
                positions.append(PortfolioStrategyPosition(bond_id=i, isin=e.isin, secid=e.secid,
                    investment_rank=e.rank, investment_score_v1=e.investment_score_v1,
                    legal_issuer_id=c.legal_issuer_id, target_weight=weights[i], target_amount_rub=p.position_amount_rub,
                    initial_seed_weight=policy.min_initial_position_weight, accepted_top_up_count=top_ups.get(i,0),
                    liquidity_capacity_amount_rub=p.liquidity_capacity_amount_rub,
                    modified_duration_years=c.modified_duration_years,
                    relative_price_sensitivity_per_1bp=c.relative_price_sensitivity_per_1bp,
                    position_dv01_rub_per_1bp=p.position_dv01_rub_per_1bp,
                    source_evaluation=e, source_risk_candidate=c))
            non_selected = []
            for e in sorted(investment_batch.evaluations, key=lambda e:(e.rank is None, e.rank or 0, e.bond_id)):
                i, c = e.bond_id, candidates[e.bond_id]
                if i in weights: continue
                if e.status != "READY": reason = "INVESTMENT_MODEL_NOT_READY"
                elif c.status != "ELIGIBLE": reason = "RISK_CANDIDATE_BLOCKED"
                elif total() == policy.target_invested_weight: reason = "TARGET_INVESTED_WEIGHT_REACHED"
                elif len(weights) == policy.max_positions: reason = "MAX_POSITIONS_REACHED"
                elif last_seed_reasons.get(i): reason = "MIN_INITIAL_POSITION_NOT_ADMISSIBLE"
                else: reason = "NOT_SELECTED_AFTER_CONVERGENCE"
                non_selected.append(PortfolioStrategyNonSelection(bond_id=i, investment_rank=e.rank,
                    investment_status=e.status, risk_candidate_status=c.status, reason=reason,
                    risk_reasons=c.reasons, final_attempt_risk_reasons=last_seed_reasons.get(i,())))
            m = final.metrics
            accepted_count = sum(t.accepted for t in trace)
            summary = PortfolioStrategySummary(capital_rub=m.capital_rub, candidate_count=investment_batch.candidate_count,
                investment_ready_count=investment_batch.ready_count, risk_eligible_count=risk_batch.eligible_count,
                selected_count=m.position_count, non_selected_count=len(non_selected), invested_weight=m.invested_weight,
                cash_weight=m.cash_weight, invested_capital_rub=m.invested_capital_rub, cash_rub=m.cash_rub,
                allocation_attempt_count=len(trace), accepted_attempt_count=accepted_count,
                rejected_attempt_count=len(trace)-accepted_count, round_count=rounds,
                final_max_position_weight=m.max_position_weight_observed, final_max_issuer_weight=m.max_issuer_weight_observed,
                final_invested_weighted_modified_duration_years=m.invested_weighted_modified_duration_years,
                final_portfolio_relative_dv01_per_1bp=m.portfolio_relative_dv01_per_1bp,
                final_portfolio_dv01_per_100k_rub=m.portfolio_dv01_per_100k_rub)
        return PortfolioStrategyView(as_of_date=investment_batch.as_of_date, market_source=investment_batch.market_source,
            status="FULLY_INVESTED" if m.invested_weight == policy.target_invested_weight else
                   "PARTIALLY_INVESTED" if m.invested_weight else "EMPTY",
            request=request, policy=policy, positions=tuple(positions), non_selections=tuple(non_selected),
            allocation_attempts=tuple(trace), summary=summary,
            provenance=PortfolioStrategyProvenance(candidate_bond_ids=investment_batch.candidate_bond_ids,
                ranked_bond_ids=ordered, risk_eligible_bond_ids=eligible, selected_bond_ids=tuple(p.bond_id for p in positions),
                as_of_date=investment_batch.as_of_date, market_source=investment_batch.market_source,
                capital_rub=request.capital_rub, policy=policy), final_risk_evaluation=final,
            source_investment_batch=investment_batch, source_risk_batch=risk_batch)
