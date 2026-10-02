"""Pure evaluation of caller-supplied weights; never construct or modify allocations."""

from decimal import Decimal, Context, ROUND_HALF_EVEN, localcontext

from app.schemas.risk_engine import (ProposedRiskPortfolio, RiskPositionResult, RiskIssuerConcentration,
    PortfolioRiskMetrics, PortfolioRiskEvaluationView)
from app.services.risk_candidate_reducer import _validated, _require, _finite, validate_risk_batch


def validate_proposal(proposal):
    _validated(proposal, ProposedRiskPortfolio)
    _finite(proposal.capital_rub)
    _require(proposal.capital_rub > 0)
    ids = []
    for position in proposal.positions:
        _require(type(position.bond_id) is int and position.bond_id > 0)
        _finite(position.target_weight)
        _require(0 < position.target_weight <= 1)
        ids.append(position.bond_id)
    _require(len(ids) == len(set(ids)))
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        _require(sum((p.target_weight for p in sorted(proposal.positions, key=lambda p:p.bond_id)), Decimal("0")) <= 1)
    return ProposedRiskPortfolio(capital_rub=proposal.capital_rub,
        positions=tuple(sorted(proposal.positions, key=lambda p:p.bond_id)))


class PortfolioRiskEvaluator:
    @staticmethod
    def build(risk_batch, proposed_portfolio):
        validate_risk_batch(risk_batch)
        proposal = validate_proposal(proposed_portfolio)
        candidates = {c.bond_id:c for c in risk_batch.candidates}
        _require(all(p.bond_id in candidates for p in proposal.positions))
        policy, capital = risk_batch.policy, proposal.capital_rub
        positions, groups, ungrouped, reasons = [], {}, [], set()
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            invested = duration_numerator = dv01 = Decimal("0")
            for p in proposal.positions:
                c = candidates[p.bond_id]
                amount = capital * p.target_weight
                capacity_weight = c.liquidity_capacity_amount_rub / capital
                bound = min(policy.max_position_weight, capacity_weight)
                position_reasons = []
                if c.status != "ELIGIBLE": position_reasons.append("CANDIDATE_NOT_RISK_ELIGIBLE")
                if p.target_weight > policy.max_position_weight: position_reasons.append("POSITION_WEIGHT_LIMIT_EXCEEDED")
                if amount > c.liquidity_capacity_amount_rub: position_reasons.append("POSITION_LIQUIDITY_CAPACITY_EXCEEDED")
                position_dv01 = amount * c.relative_price_sensitivity_per_1bp
                positions.append(RiskPositionResult(bond_id=p.bond_id, legal_issuer_id=c.legal_issuer_id,
                    requested_weight=p.target_weight, position_amount_rub=amount, position_dv01_rub_per_1bp=position_dv01,
                    policy_max_position_weight=policy.max_position_weight, liquidity_capacity_amount_rub=c.liquidity_capacity_amount_rub,
                    liquidity_capacity_weight=capacity_weight, capacity_bound=bound,
                    max_admissible_position_weight=bound if c.status == "ELIGIBLE" else Decimal("0"),
                    status="BLOCKED" if position_reasons else "PASS", reasons=tuple(sorted(position_reasons)), candidate=c))
                reasons.update(position_reasons)
                invested += p.target_weight
                duration_numerator += p.target_weight * c.modified_duration_years
                dv01 += position_dv01
                if c.legal_issuer_id is None: ungrouped.append(p.bond_id)
                else: groups.setdefault(c.legal_issuer_id, []).append(p)
            issuers = []
            for issuer_id in sorted(groups):
                rows = groups[issuer_id]
                weight = sum((p.target_weight for p in rows), Decimal("0"))
                breached = weight > policy.max_issuer_weight
                if breached: reasons.add("ISSUER_WEIGHT_LIMIT_EXCEEDED")
                issuers.append(RiskIssuerConcentration(legal_issuer_id=issuer_id, bond_ids=tuple(p.bond_id for p in rows),
                    total_weight=weight, limit=policy.max_issuer_weight, headroom=policy.max_issuer_weight-weight,
                    status="BLOCKED" if breached else "PASS"))
            sleeve_duration = duration_numerator / invested if invested else None
            relative_dv01 = dv01 / capital
            per_100k = relative_dv01 * Decimal("100000")
            _require(policy.max_portfolio_relative_dv01_per_1bp * Decimal("100000") == policy.max_portfolio_dv01_per_100k_rub)
            if sleeve_duration is not None and sleeve_duration > policy.max_invested_weighted_modified_duration_years:
                reasons.add("PORTFOLIO_DURATION_LIMIT_EXCEEDED")
            if relative_dv01 > policy.max_portfolio_relative_dv01_per_1bp:
                reasons.add("PORTFOLIO_DV01_LIMIT_EXCEEDED")
            flags = set()
            if not positions: flags.add("EMPTY_PORTFOLIO")
            if ungrouped: flags.add("ISSUER_CONCENTRATION_INCOMPLETE")
            metrics = PortfolioRiskMetrics(capital_rub=capital, invested_weight=invested, cash_weight=Decimal("1")-invested,
                invested_capital_rub=capital*invested, cash_rub=capital*(Decimal("1")-invested),
                position_count=len(positions), issuer_count=len(issuers), issuer_concentration_complete=not ungrouped,
                ungrouped_bond_ids=tuple(ungrouped), max_position_weight_observed=max((p.target_weight for p in proposal.positions),default=Decimal("0")),
                max_issuer_weight_observed=max((i.total_weight for i in issuers),default=Decimal("0")),
                invested_weighted_modified_duration_years=sleeve_duration, capital_weighted_modified_duration_years=duration_numerator,
                portfolio_dv01_rub_per_1bp=dv01, portfolio_relative_dv01_per_1bp=relative_dv01, portfolio_dv01_per_100k_rub=per_100k)
        return PortfolioRiskEvaluationView(as_of_date=risk_batch.as_of_date, market_source=risk_batch.market_source,
            status="BLOCKED" if reasons else "PASS", reasons=tuple(sorted(reasons)), quality_flags=tuple(sorted(flags)),
            proposal=proposal, positions=tuple(positions), issuer_concentrations=tuple(issuers), metrics=metrics,
            source_risk_batch=risk_batch, provenance=risk_batch.provenance.model_copy(update={
                "proposed_bond_ids":tuple(p.bond_id for p in proposal.positions), "legal_issuer_ids":tuple(i.legal_issuer_id for i in issuers)}))
